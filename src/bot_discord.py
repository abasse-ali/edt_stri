"""
Bot Discord : formulaire d'inscription aux agendas, et validation.

Le salon d'inscription porte un panneau permanent avec un seul bouton. Un clic
ouvre, VISIBLE DE LA SEULE PERSONNE QUI A CLIQUÉ, une liste où elle coche les
agendas voulus, puis un formulaire pour son adresse Google. Rien n'est écrit
dans le salon : il ne s'encombre pas, et personne ne lit l'adresse d'un autre.

La demande t'arrive EN MESSAGE PRIVÉ, sous forme de fiche : tu peux corriger
les agendas demandés — en retirer un auquel la personne n'a pas droit, en
ajouter un autre — puis valider. Le partage Google est appliqué au clic, et la
personne est prévenue en message privé à son tour.

Rien ne transite donc par un salon : ni l'adresse de la personne, ni ta
décision. Un salon de repli reste possible pour le cas où Discord refuserait le
message privé — beaucoup de comptes bloquent ceux venant d'un serveur.

Pose le panneau une fois, dans le salon voulu, avec `/edt-panneau`. Il survit
aux redémarrages. `/edt` ouvre la même liste, pour qui ne le retrouve pas.

Une fiche traitée porte un bouton « Supprimer », et `/edt-menage` les efface
toutes d'un coup. Discord ne laisse personne effacer les messages d'autrui,
même dans un message privé : seul l'auteur le peut, donc seul le bot.

    python src/bot_discord.py

⚠️ Contrairement au reste du projet, ce script ne se lance pas par une tâche
planifiée : Discord n'envoie une commande ou un clic de bouton qu'à un
programme DÉJÀ connecté. Il doit donc tourner en permanence, sur une machine
allumée. GitHub Actions ne convient pas — un job y a une durée maximale et
démarre trop tard pour répondre.

Réglages, dans `.env` :

    DISCORD_BOT_TOKEN      le jeton du bot (Developer Portal → Bot → Reset Token)
    DISCORD_ADMINS         les identifiants Discord autorisés à valider,
                           séparés par des virgules. Le PREMIER reçoit les
                           fiches en message privé.
    DISCORD_VALIDEUR       (facultatif) pour envoyer les fiches à quelqu'un
                           d'autre que le premier des ADMINS
    DISCORD_SALON_DEMANDES (facultatif) salon de repli, utilisé seulement si le
                           message privé est refusé
    DISCORD_SERVEUR        (facultatif) l'identifiant du serveur : les commandes
                           y apparaissent tout de suite, au lieu d'attendre
                           jusqu'à une heure la propagation mondiale

Pour obtenir un identifiant : Discord → Paramètres → Avancés → Mode développeur,
puis clic droit sur un salon, un serveur ou une personne → « Copier l'ID ».
"""

import asyncio
import json
import sys
from pathlib import Path

import chemins
import partager
from telechargement import variable_env

try:
    import discord
    from discord import app_commands
except ImportError:
    sys.exit("⛔ discord.py n'est pas installé.\n"
             "   pip install -r requirements-bot.txt")

for _flux in (sys.stdout, sys.stderr):
    try:
        _flux.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

JETON = variable_env("DISCORD_BOT_TOKEN")
SALON_DEMANDES = int(variable_env("DISCORD_SALON_DEMANDES", "0") or 0)
SERVEUR = int(variable_env("DISCORD_SERVEUR", "0") or 0)


def identifiants(texte):
    """Les identifiants Discord d'une liste écrite à la main.

    Séparateurs libres — virgules, espaces, retours à la ligne — et les
    morceaux qui ne sont pas des nombres sont écartés : un identifiant se colle
    depuis Discord, et il arrive qu'on colle une mention « <@123> » à la place.
    """
    trouves = []
    for morceau in (texte or "").replace(",", " ").replace(";", " ").split():
        morceau = morceau.strip("<>@!&#")
        if morceau.isdigit() and int(morceau) not in trouves:
            trouves.append(int(morceau))
    return trouves


# Liste et non ensemble : le PREMIER reçoit les fiches, il faut donc un ordre
# stable. Un ensemble en aurait donné un différent à chaque démarrage.
ADMINS = identifiants(variable_env("DISCORD_ADMINS"))
VALIDEUR = next(iter(identifiants(variable_env("DISCORD_VALIDEUR"))), None) \
    or (ADMINS[0] if ADMINS else None)

# Les fiches en attente survivent à un redémarrage : sans ce fichier, les
# boutons d'une demande déjà postée deviendraient inertes et la personne
# attendrait un accès qui ne viendrait jamais.
FICHIER_ETAT = Path(variable_env("DISCORD_DEMANDES",
                                 str(chemins.donnee("demandes_discord.json"))))

VERT, ROUGE, ORANGE, GRIS = 0x2E9E5B, 0xC0392B, 0xE67E22, 0x7F8C8D

# Droits dont le bot a besoin, pour reconstruire un lien d'invitation correct :
# voir le salon (1024), y écrire (2048), y mettre un encadré (16384) et lire
# l'historique (65536), sans quoi il ne peut pas modifier une fiche postée
# avant son dernier redémarrage.
DROITS = 1024 + 2048 + 16384 + 65536


def version():
    """La version déployée, telle que `git archive` l'a inscrite.

    Le fichier contient un motif que Git remplace par le commit au moment de
    fabriquer l'archive. Dans un dépôt cloné, le motif reste intact — on le
    reconnaît à ses `$` — et on interroge Git directement.

    Sans ce repère, rien ne distingue une archive à jour d'une ancienne
    renvoyée par mégarde : la mise à jour semble réussir et le code ne change
    pas.
    """
    try:
        brut = (chemins.SRC / "version.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return "inconnue"

    if brut and not brut.startswith("$Format"):
        return brut

    import subprocess
    try:
        return subprocess.run(
            ["git", "-C", str(chemins.RACINE), "log", "-1", "--format=%H %cI"],
            capture_output=True, text=True, timeout=5).stdout.strip() or "developpement"
    except Exception:
        return "developpement"


def expliquer_droits(client, quoi):
    """Explique un refus d'accès, et donne le lien qui le corrige.

    Fonction et non méthode : elle sert au démarrage comme au moment d'un envoi
    refusé. Elle a d'ailleurs passé plusieurs versions orpheline — écrite pour
    un « Missing Access » réel, puis débranchée par un remaniement. Un
    diagnostic qui ne se déclenche plus est pire qu'absent : il donne
    l'illusion que le cas est couvert.
    """
    print(f"⛔ {quoi}.")
    print("   Deux causes possibles, dans cet ordre de fréquence :")
    print("   1. le bot a été invité avec le seul scope « applications.commands ».")
    print("      Les commandes apparaissent, mais il n'est pas membre du serveur")
    print("      et ne peut rien y poster. Réinvite-le avec ce lien :")
    print(f"      {lien_invitation(client.application_id or client.user.id)}")
    print("   2. le salon lui refuse l'accès. Paramètres du salon →")
    print("      Permissions → ajoute le rôle du bot.")


def lien_invitation(identifiant):
    """L'adresse qui ajoute le bot AVEC les droits qu'il lui faut.

    Le piège classique : inviter avec le seul scope `applications.commands`.
    Les commandes apparaissent alors — donc tout semble marcher — mais le bot
    n'est pas membre du serveur et chaque envoi échoue en « Missing Access ».
    Il faut les deux scopes.
    """
    return (f"https://discord.com/api/oauth2/authorize?client_id={identifiant}"
            f"&permissions={DROITS}&scope=bot%20applications.commands")


# =====================================================================
# ÉTAT
# =====================================================================

def charger_etat():
    """Les demandes en attente, indexées par identifiant de message."""
    if not FICHIER_ETAT.exists():
        return {}
    try:
        return json.loads(FICHIER_ETAT.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"⚠️ {FICHIER_ETAT.name} illisible ({e}) : on repart de zéro.")
        return {}


def enregistrer_etat(etat):
    """Écrit l'état. Une panne d'écriture ne doit pas tuer le bot."""
    try:
        FICHIER_ETAT.write_text(json.dumps(etat, ensure_ascii=False, indent=2),
                                encoding="utf-8")
    except OSError as e:
        print(f"⚠️ État non enregistré ({e}).")


ETAT = charger_etat()


# =====================================================================
# PARTAGE (synchrone, donc à l'écart de la boucle asynchrone)
# =====================================================================

def _appliquer(courriel, cles):
    """Applique les partages. Rend (lignes de compte rendu, erreur ou None).

    Tourne dans un thread : googleapiclient est synchrone, et l'appeler
    directement figerait le bot — plus aucune commande ne répondrait pendant
    les quelques secondes de l'appel.
    """
    service = partager._service()
    if service is None:
        return [], "autorisation Google indisponible (jeton à régénérer)"

    faits = []
    for cle in cles:
        try:
            ajoutes = partager.partager(service, courriel, cle)
        except Exception as e:
            return faits, f"{cle} : {str(e)[:200]}"
        intitule = partager.CATALOGUE[cle][0]
        faits.append(f"**{intitule}** — {'accès donné' if ajoutes else 'accès déjà en place'}")
    return faits, None


async def destination(client):
    """Où poster une fiche : le message privé du valideur, ou le salon de repli.

    Rend (destinataire, description) ou (None, raison). Le message privé est
    tenté d'abord ; beaucoup de comptes refusent ceux venant d'un serveur, et
    dans ce cas seulement on retombe sur le salon — si l'on en a configuré un.
    """
    if VALIDEUR:
        try:
            personne = client.get_user(VALIDEUR) or await client.fetch_user(VALIDEUR)
            return await personne.create_dm(), f"message privé à {personne}"
        except Exception as e:
            print(f"⚠️ Message privé au valideur impossible ({type(e).__name__}).")

    if SALON_DEMANDES:
        salon = client.get_channel(SALON_DEMANDES)
        if salon is not None:
            return salon, f"salon #{salon.name} (repli)"

    return None, ("aucune destination : ni valideur joignable en message privé, "
                  "ni salon de repli")


# =====================================================================
# FICHE DE DEMANDE
# =====================================================================

def identite(personne):
    """Ce qu'on retient de la personne qui demande : pseudo, nom affiché, rôles.

    Les rôles sont relevés au moment de la demande, pas à celui de la
    validation. La fiche reste ainsi un compte rendu fidèle de ce qui était
    vrai quand la personne a cliqué — c'est ce qu'on veut d'une trace.

    `roles` n'existe que sur un membre de serveur. Une interaction venue d'un
    message privé donne un simple utilisateur, sans rôle : d'où le `getattr`.
    """
    roles = [r.name for r in getattr(personne, "roles", [])
             if r.name != "@everyone"]
    return {
        "discord_id": str(personne.id),
        "pseudo": str(personne),
        "affiche": getattr(personne, "display_name", str(personne)),
        "roles": roles,
    }


def fiche(demande, etat="attente", par=None, detail=None):
    """L'encadré affiché dans le salon de validation."""
    cles = demande["cles"]
    titres = {
        "attente": "🕓 Demande en attente",
        "valide": "✅ Demande validée",
        "refus": "❌ Demande refusée",
        "erreur": "⚠️ Validation impossible",
    }
    couleurs = {"attente": ORANGE, "valide": VERT, "refus": GRIS, "erreur": ROUGE}

    embed = discord.Embed(title=titres[etat], color=couleurs[etat])

    qui = f"<@{demande['discord_id']}>"
    affiche = demande.get("affiche")
    pseudo = demande.get("pseudo")
    # Le nom affiché est celui du serveur, souvent le vrai prénom ; le pseudo
    # est l'identifiant Discord. Les deux ne coïncident presque jamais, et
    # c'est le premier qui permet de reconnaître quelqu'un.
    if affiche and affiche != pseudo:
        qui += f"\n{affiche}"
    if pseudo:
        qui += f"\n`{pseudo}`"
    embed.add_field(name="Demandeur", value=qui, inline=True)

    roles = demande.get("roles") or []
    embed.add_field(name="Rôles",
                    value=", ".join(roles) if roles else "*aucun*",
                    inline=True)

    embed.add_field(name="Adresse Google", value=f"`{demande['courriel']}`", inline=False)
    embed.add_field(
        name="Agendas",
        value="\n".join(f"• {partager.CATALOGUE[c][0]}" for c in cles) or "*aucun*",
        inline=False)
    if demande.get("cles_demandees") and demande["cles_demandees"] != cles:
        demandes = ", ".join(partager.CATALOGUE[c][0] for c in demande["cles_demandees"])
        embed.add_field(name="Demandé à l'origine", value=demandes, inline=False)
    if detail:
        embed.add_field(name="Résultat", value=detail[:1000], inline=False)
    if par:
        embed.set_footer(text=f"Par {par}")
    return embed


def _options():
    """Les agendas proposés, dans un ordre stable."""
    return [discord.SelectOption(label=intitule, value=cle)
            for cle, (intitule, _) in sorted(partager.CATALOGUE.items())]


class SelecteurEtudiant(discord.ui.Select):
    """La liste que coche la personne. Plusieurs agendas à la fois."""

    def __init__(self, vue):
        options = _options()
        super().__init__(placeholder="Coche le ou les agendas qui te concernent…",
                         min_values=1, max_values=len(options), options=options)
        self.vue = vue

    async def callback(self, interaction):
        self.vue.choix = sorted(self.values)
        # Le message étant éphémère et propre à cette personne, la sélection
        # vit dans la vue : rien à écrire sur disque, rien à partager.
        await interaction.response.edit_message(view=self.vue)


class VueChoix(discord.ui.View):
    """Le formulaire éphémère : la liste, puis le bouton d'envoi.

    Volontairement NON persistante : elle n'appartient qu'à une personne et à
    un instant. Passé le délai, elle s'éteint et il suffit de recliquer.
    """

    def __init__(self):
        super().__init__(timeout=600)
        self.choix = []
        self.add_item(SelecteurEtudiant(self))

    @discord.ui.button(label="Envoyer ma demande",
                       style=discord.ButtonStyle.primary, row=1)
    async def envoyer(self, interaction, bouton):
        if not self.choix:
            await interaction.response.send_message(
                "Coche d'abord au moins un agenda.", ephemeral=True)
            return
        await interaction.response.send_modal(ModalCourriel(self.choix))


class ModalCourriel(discord.ui.Modal, title="Ton adresse Google"):
    """La dernière étape : l'adresse, saisie dans une fenêtre à elle."""

    courriel = discord.ui.TextInput(
        label="Adresse du compte Google de ton téléphone",
        placeholder="prenom.nom@gmail.com",
        max_length=120)

    def __init__(self, cles):
        super().__init__()
        self.cles = cles

    async def on_submit(self, interaction):
        adresse = str(self.courriel.value).strip().lower()
        if not partager.REGEX_COURRIEL.match(adresse):
            await interaction.response.send_message(
                f"`{adresse}` ne ressemble pas à une adresse. Reclique sur le "
                "bouton pour réessayer.", ephemeral=True)
            return

        ou, description = await destination(interaction.client)
        if ou is None:
            await interaction.response.send_message(
                "⚠️ Ta demande n'a pas pu être transmise : le bot n'a aucun "
                "moyen de joindre la personne qui valide. Signale-le, ce n'est "
                "pas de ton fait.", ephemeral=True)
            print(f"⛔ Fiche non postée — {description}.")
            return

        demande = {
            **identite(interaction.user),
            "courriel": adresse,
            "cles": list(self.cles),
            "cles_demandees": list(self.cles),
        }
        try:
            message = await ou.send(embed=fiche(demande), view=VueDemande())
        except discord.Forbidden:
            # La demande serait perdue en silence : mieux vaut le dire à la
            # personne, qui pourra signaler la panne, que la laisser attendre.
            expliquer_droits(interaction.client,
                             f"fiche non postée ({description})")
            await interaction.response.send_message(
                "⚠️ Ta demande n'a pas pu être transmise : le bot n'a pas pu "
                "joindre la personne qui valide. Signale-le, ce n'est pas de "
                "ton fait.", ephemeral=True)
            return

        ETAT[str(message.id)] = demande
        enregistrer_etat(ETAT)

        agendas = ", ".join(partager.CATALOGUE[c][0] for c in self.cles)
        await interaction.response.send_message(
            f"📨 Demande envoyée : **{agendas}**, adresse `{adresse}`.\n"
            "Tu recevras un message privé dès qu'elle est validée. "
            "Erreur de saisie ? Recommence, la nouvelle demande remplacera "
            "l'ancienne.", ephemeral=True)


class VuePanneau(discord.ui.View):
    """Le bouton qui reste dans le salon. Persistant, comme la fiche admin."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Recevoir mon emploi du temps",
                       style=discord.ButtonStyle.success, emoji="📅",
                       custom_id="edt:inscrire")
    async def inscrire(self, interaction, bouton):
        await interaction.response.send_message(
            content="**Quels agendas te concernent ?**\n"
                    "Coche-les, puis « Envoyer ma demande ». "
                    "Cette fenêtre n'est visible que par toi.",
            view=VueChoix(), ephemeral=True)

    @discord.ui.button(label="Comment l'installer", emoji="📖",
                       style=discord.ButtonStyle.secondary,
                       custom_id="edt:tuto")
    async def tutoriel(self, interaction, bouton):
        vue = VueTuto()
        # Le premier écran doit arriver avec « Précédent » déjà grisé : sans
        # cet ajustement, le bouton semblerait cliquable et ne ferait rien.
        vue._ajuster()
        await interaction.response.send_message(
            embed=vue.embed(), view=vue, ephemeral=True)


# =====================================================================
# TUTORIEL
# =====================================================================

# Le contenu de docs/TUTO.txt, découpé en écrans. Chaque parcours commence par
# l'étape commune — accepter les invitations — parce que c'est là que la
# quasi-totalité des « ça marche pas chez moi » se règle.
#
# Un parcours = (intitulé du menu, [(titre, corps), ...]).

_ACCEPTER = (
    "Accepter les deux invitations",
    "Tu reçois **deux courriels** intitulés « *… a partagé un agenda avec "
    "vous* ». Ouvre chacun, clique sur **Ajouter cet agenda**.\n\n"
    "⚠️ **Tant que tu n'as pas cliqué, tu ne vois rien.** Pas un agenda vide : "
    "rien du tout, et aucun message d'erreur. C'est la cause numéro un des "
    "« ça marche pas chez moi ».\n\n"
    "Rien reçu ? Regarde dans les **spams**, puis redemande ici.")

_POURQUOI_DEUX = (
    "Pourquoi deux agendas",
    "Tes **cours** et tes **examens** arrivent séparément, et c'est voulu : "
    "comme ce sont deux agendas distincts, ton téléphone leur donne deux "
    "couleurs, et tu repères tes examens d'un coup d'œil.\n\n"
    "Pense donc bien à **accepter les deux**.")

PARCOURS = {
    "IPHONE": ("📱 iPhone", [
        _ACCEPTER,
        _POURQUOI_DEUX,
        ("Vérifier ton compte Google",
         "**Réglages → Apps → Calendrier → Comptes**\n\n"
         "Ton adresse Gmail doit y apparaître, avec « Calendriers » activé.\n"
         "Si elle n'y est pas : **Ajouter un compte → Google**, puis "
         "connecte-toi."),
        ("L'étape que tout le monde rate",
         "iPhone ne synchronise que les agendas Google cochés sur une page "
         "spéciale, **qu'aucun menu n'affiche**. Un agenda qu'on vient de te "
         "partager y est **décoché par défaut**.\n\n"
         "Ouvre cette adresse dans ton navigateur :\n"
         "https://calendar.google.com/calendar/syncselect\n\n"
         "Coche tes **deux** agendas, puis **Enregistrer**. C'est immédiat.\n\n"
         "*Comment savoir que c'est ton problème ?* Tu vois tes cours sur "
         "calendar.google.com dans un navigateur, mais rien dans l'app "
         "Calendrier. C'est exactement ça."),
        ("Les afficher",
         "Ouvre l'app **Calendrier**, touche **Calendriers** en bas de "
         "l'écran, coche tes deux agendas.\n\nTerminé — tu n'auras plus "
         "jamais rien à faire."),
    ]),

    "ANDROID": ("🤖 Android", [
        _ACCEPTER,
        _POURQUOI_DEUX,
        ("Les afficher",
         "Ouvre l'application **Google Agenda**.\n\n"
         "Menu (les trois barres en haut à gauche) → fais défiler tout en "
         "bas → coche tes deux agendas.\n\n"
         "Terminé — tu n'auras plus jamais rien à faire."),
        ("S'ils n'apparaissent pas",
         "Va d'abord sur **calendar.google.com** dans un navigateur pour "
         "vérifier qu'ils y sont.\n\n"
         "• **Ils y sont** → ferme et rouvre l'application.\n"
         "• **Ils n'y sont pas** → l'invitation n'a pas été acceptée, "
         "reprends la première étape."),
    ]),

    "ORDI": ("💻 Ordinateur", [
        _ACCEPTER,
        ("Rien à installer",
         "Va sur **calendar.google.com**. Tes agendas sont dans la colonne "
         "de gauche, déjà cochés.\n\n"
         "C'est aussi là que tu peux **changer leur couleur** : survole le nom "
         "de l'agenda, clique sur les trois points, choisis ta teinte."),
    ]),

    "SANS_GOOGLE": ("✉️ Je n'ai pas d'adresse Google", [
        ("Le partage exige un compte Google",
         "Si ton adresse est en `@yahoo.fr`, `@proton.me`, `@outlook.com` ou "
         "autre, le partage par compte ne fonctionnera pas tel quel."),
        ("Deux solutions",
         "**1. Créer un compte Google avec ton adresse actuelle.** C'est "
         "gratuit, et tu n'es pas obligé de prendre une adresse Gmail : Google "
         "accepte d'ouvrir un compte sur une adresse existante.\n\n"
         "**2. Demander un lien d'abonnement.** Il marche avec n'importe "
         "quelle adresse et n'importe quel téléphone. Préviens dans ce cas : "
         "la mise à jour n'est alors plus immédiate, elle peut prendre "
         "plusieurs heures."),
    ]),

    "PANNE": ("🔧 Ça ne marche pas", [
        ("Trois gestes, dans cet ordre",
         "Ils règlent la quasi-totalité des cas.\n\n"
         "**1.** Décoche l'agenda dans l'application, attends dix secondes, "
         "recoche-le. Ça force une resynchronisation.\n\n"
         "**2.** Vérifie sur **calendar.google.com**, dans un navigateur, que "
         "tes cours y sont.\n\n"
         "**3.** Redémarre le téléphone. Oui, vraiment."),
        ("Ce que dit l'étape 2",
         "• **Tes cours sont sur calendar.google.com** → le problème est sur "
         "le téléphone. Sur iPhone, c'est presque toujours la page "
         "`syncselect` : reprends le parcours iPhone.\n\n"
         "• **Ils n'y sont pas** → l'invitation n'a pas été acceptée. Cherche "
         "les deux courriels, spams compris."),
    ]),

    "FAQ": ("❓ Questions fréquentes", [
        ("Mes couleurs sont différentes de celles des autres",
         "C'est normal, et personne n'y peut rien : Google attribue une teinte "
         "au hasard à chaque personne. Tu peux choisir la tienne sur "
         "calendar.google.com — survole le nom de l'agenda, trois points, ta "
         "couleur.\n\n"
         "Les examens, eux, resteront **toujours** d'une couleur différente "
         "des cours, puisque ce sont deux agendas séparés."),
        ("Je vois des cours qui ne sont pas les miens",
         "Chaque demi-promo a son propre agenda. Si tu vois ceux d'à côté, "
         "c'est que tu as accepté le mauvais partage : signale-le ici."),
        ("Faut-il refaire quelque chose quand l'emploi du temps change ?",
         "**Non, jamais.** Une fois installé, tout arrive tout seul. Dès qu'un "
         "cours change dans le PDF de l'école, ton téléphone le sait quelques "
         "secondes plus tard."),
        ("Est-ce que je peux modifier un cours ?",
         "Non, tu es en lecture seule — et de toute façon une modification "
         "serait effacée à la mise à jour suivante."),
    ]),
}

DEPART = "IPHONE"


class SelecteurParcours(discord.ui.Select):
    """Le choix de l'appareil, ou du sujet."""

    def __init__(self, vue):
        super().__init__(placeholder="Choisis ton appareil ou ton problème…",
                         options=[discord.SelectOption(label=intitule, value=cle)
                                  for cle, (intitule, _) in PARCOURS.items()],
                         row=0)
        self.vue = vue

    async def callback(self, interaction):
        self.vue.parcours = self.values[0]
        self.vue.index = 0
        await self.vue.afficher(interaction)


class VueTuto(discord.ui.View):
    """Le tutoriel, un écran à la fois.

    Éphémère comme le formulaire : chacun avance à son rythme sans que le
    salon en garde la moindre trace.
    """

    def __init__(self):
        super().__init__(timeout=900)
        self.parcours = DEPART
        self.index = 0
        self.add_item(SelecteurParcours(self))

    def etapes(self):
        return PARCOURS[self.parcours][1]

    def embed(self):
        intitule, etapes = PARCOURS[self.parcours]
        titre, corps = etapes[self.index]
        embed = discord.Embed(title=titre, description=corps, color=VERT)
        embed.set_author(name=intitule)
        embed.set_footer(text=f"Étape {self.index + 1} sur {len(etapes)}")
        return embed

    def _ajuster(self):
        self.precedent.disabled = self.index == 0
        self.suivant.disabled = self.index >= len(self.etapes()) - 1

    async def afficher(self, interaction):
        self._ajuster()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Précédent", emoji="◀", row=1,
                       style=discord.ButtonStyle.secondary, disabled=True)
    async def precedent(self, interaction, bouton):
        self.index = max(0, self.index - 1)
        await self.afficher(interaction)

    @discord.ui.button(label="Suivant", emoji="▶", row=1,
                       style=discord.ButtonStyle.primary)
    async def suivant(self, interaction, bouton):
        self.index = min(len(self.etapes()) - 1, self.index + 1)
        await self.afficher(interaction)


class SelecteurAdmin(discord.ui.Select):
    """Menu qui permet de corriger les agendas AVANT de valider.

    C'est le cœur du besoin : quelqu'un demande un agenda auquel il n'a pas
    droit, on le retire et on met le bon, sans faire recommencer la personne.
    """

    def __init__(self):
        options = _options()
        super().__init__(placeholder="Corriger les agendas…", min_values=0,
                         max_values=len(options), options=options,
                         custom_id="edt:choix", row=0)

    async def callback(self, interaction):
        if not await _refuser_si_pas_admin(interaction):
            return
        demande = ETAT.get(str(interaction.message.id))
        if demande is None:
            await interaction.response.send_message(
                "Cette demande n'est plus suivie (bot redémarré sans son état).",
                ephemeral=True)
            return
        demande["cles"] = sorted(self.values)
        enregistrer_etat(ETAT)
        await interaction.response.edit_message(embed=fiche(demande), view=VueDemande())


class VueTerminee(discord.ui.View):
    """Ce qui reste sur une fiche traitée : de quoi la faire disparaître.

    Discord n'autorise personne à supprimer le message d'un autre, y compris
    dans un message privé — c'est donc au bot de le faire, à la demande.
    """

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Supprimer", emoji="🗑️",
                       style=discord.ButtonStyle.secondary,
                       custom_id="edt:supprimer")
    async def supprimer(self, interaction, bouton):
        if not await _refuser_si_pas_admin(interaction):
            return
        await interaction.message.delete()


class VueDemande(discord.ui.View):
    """Les commandes de la fiche. `timeout=None` + `custom_id` la rendent
    persistante : les boutons répondent encore après un redémarrage."""

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(SelecteurAdmin())

    @discord.ui.button(label="Valider", style=discord.ButtonStyle.success,
                       custom_id="edt:valider", row=1)
    async def valider(self, interaction, bouton):
        if not await _refuser_si_pas_admin(interaction):
            return
        demande = ETAT.get(str(interaction.message.id))
        if demande is None:
            await interaction.response.send_message(
                "Cette demande n'est plus suivie (bot redémarré sans son état).",
                ephemeral=True)
            return
        if not demande["cles"]:
            await interaction.response.send_message(
                "Aucun agenda sélectionné : rien à valider.", ephemeral=True)
            return

        # Le partage prend quelques secondes : Discord attend une réponse en
        # trois, on prend donc date tout de suite.
        await interaction.response.defer()
        faits, erreur = await asyncio.to_thread(
            _appliquer, demande["courriel"], demande["cles"])

        if erreur:
            await interaction.message.edit(
                embed=fiche(demande, "erreur", str(interaction.user), erreur),
                view=VueDemande())
            return

        await interaction.message.edit(
            embed=fiche(demande, "valide", str(interaction.user), "\n".join(faits)),
            view=VueTerminee())
        ETAT.pop(str(interaction.message.id), None)
        enregistrer_etat(ETAT)
        await _prevenir(interaction, demande, accepte=True)

    @discord.ui.button(label="Refuser", style=discord.ButtonStyle.secondary,
                       custom_id="edt:refuser", row=1)
    async def refuser(self, interaction, bouton):
        if not await _refuser_si_pas_admin(interaction):
            return
        demande = ETAT.pop(str(interaction.message.id), None)
        if demande is None:
            await interaction.response.send_message(
                "Cette demande n'est plus suivie.", ephemeral=True)
            return
        enregistrer_etat(ETAT)
        await interaction.response.edit_message(
            embed=fiche(demande, "refus", str(interaction.user)), view=VueTerminee())
        await _prevenir(interaction, demande, accepte=False)


async def _refuser_si_pas_admin(interaction):
    """Vrai si la personne a le droit de valider. Sinon, le lui dit."""
    if not ADMINS or interaction.user.id in ADMINS:
        return True
    await interaction.response.send_message(
        "Seule la personne qui gère les agendas peut valider une demande.",
        ephemeral=True)
    return False


async def _prevenir(interaction, demande, accepte):
    """Prévient le demandeur en message privé, sans jamais faire échouer le clic.

    Beaucoup de comptes refusent les messages privés venant d'un serveur : ce
    n'est pas une erreur, c'est un réglage. La fiche reste la trace.
    """
    try:
        personne = await interaction.client.fetch_user(int(demande["discord_id"]))
        if accepte:
            agendas = ", ".join(partager.CATALOGUE[c][0] for c in demande["cles"])
            await personne.send(
                f"✅ Ton accès est en place : **{agendas}**.\n"
                f"Tu vas recevoir une invitation par courriel à `{demande['courriel']}` "
                "— **il faut cliquer sur « Ajouter cet agenda »**, sinon tu ne verras "
                "rien du tout.\n"
                "Sur iPhone, pense ensuite à cocher tes agendas sur "
                "<https://calendar.google.com/calendar/syncselect>.")
        else:
            await personne.send(
                "❌ Ta demande d'emploi du temps n'a pas été retenue. "
                "Redemande dans le salon en précisant ta promotion et ton groupe.")
    except Exception as e:
        print(f"   ℹ️ Message privé non remis ({type(e).__name__}).")


# =====================================================================
# BOT
# =====================================================================

class Bot(discord.Client):
    def __init__(self):
        # Aucune intention privilégiée : le bot ne lit pas les messages, il ne
        # répond qu'aux commandes et aux clics qui lui sont adressés. Cela évite
        # d'avoir à demander « Message Content » dans le portail développeur.
        super().__init__(intents=discord.Intents.default())
        self.arbre = app_commands.CommandTree(self)

    async def setup_hook(self):
        # Réenregistre la vue pour que les fiches postées AVANT le redémarrage
        # gardent des boutons vivants.
        self.add_view(VueDemande())
        self.add_view(VuePanneau())
        self.add_view(VueTerminee())
        if SERVEUR:
            serveur = discord.Object(id=SERVEUR)
            self.arbre.copy_global_to(guild=serveur)
            await self.arbre.sync(guild=serveur)
        else:
            await self.arbre.sync()

    async def on_ready(self):
        print(f"✅ Connecté comme {self.user}.")
        print(f"   Version : {version()}")
        print(f"   Valideurs : {', '.join(map(str, ADMINS)) or '(tout le monde !)'}")
        print(f"   {len(ETAT)} demande(s) en attente.")
        await self._verifier_destination()

    async def _verifier_destination(self):
        """Vérifie tout de suite qu'une fiche pourra être remise.

        Sans ce contrôle, la panne se découvrirait au pire moment : un étudiant
        remplit le formulaire, sa demande n'arrive nulle part, et il attend un
        accès que personne ne verra jamais passer.
        """
        if VALIDEUR is None:
            print("⛔ Aucun valideur : renseigne DISCORD_ADMINS.")
            return
        ou, description = await destination(self)
        if ou is None:
            print(f"⛔ {description}.")
            print("   Le valideur doit autoriser les messages privés venant du")
            print("   serveur : Paramètres du serveur → Confidentialité.")
            print("   Ou renseigne DISCORD_SALON_DEMANDES comme repli.")
            if SALON_DEMANDES:
                # Un salon est configuré et reste inatteignable : ce n'est plus
                # un réglage de confidentialité, c'est un problème de droits.
                expliquer_droits(self, "le salon de repli est inaccessible")
            return
        print(f"   Les fiches partent en {description} ✅")




bot = Bot()

@bot.arbre.command(name="edt",
                   description="Recevoir son emploi du temps STRI dans son agenda")
async def edt(interaction):
    """Ouvre la liste des agendas, visible de la seule personne qui l'a demandée."""
    await interaction.response.send_message(
        content="**Quels agendas te concernent ?**\n"
                "Coche-les, puis « Envoyer ma demande ». "
                "Cette fenêtre n'est visible que par toi.",
        view=VueChoix(), ephemeral=True)


@bot.arbre.command(name="edt-panneau",
                   description="Poser le panneau d'inscription dans ce salon")
async def edt_panneau(interaction):
    """Poste le message permanent portant le bouton d'inscription."""
    if ADMINS and interaction.user.id not in ADMINS:
        await interaction.response.send_message(
            "Commande réservée à la personne qui gère les agendas.", ephemeral=True)
        return

    embed = discord.Embed(
        title="📅 Ton emploi du temps, à jour tout seul",
        description=(
            "Clique sur le bouton, coche les agendas qui te concernent, donne "
            "l'adresse du compte Google de ton téléphone. C'est tout.\n\n"
            "Ta demande est vérifiée avant d'être acceptée, et tu reçois un "
            "message privé dès qu'elle l'est. **Rien de ce que tu saisis "
            "n'apparaît dans ce salon.**\n\n"
            "Chaque promotion donne DEUX agendas, les cours et les examens : "
            "pense à accepter les deux invitations, sinon tu ne verras rien.\n\n"
            "Le second bouton explique l'installation pas à pas, selon que tu "
            "aies un iPhone ou un Android."),
        color=VERT)
    embed.add_field(
        name="Disponibles",
        value="\n".join(f"• {intitule}" for _, (intitule, _)
                        in sorted(partager.CATALOGUE.items())),
        inline=False)
    # Posté comme RÉPONSE à la commande, pas par un envoi ordinaire : une
    # réponse d'interaction ne demande pas le droit d'écrire dans le salon.
    # Le panneau apparaît donc même là où le bot ne pourrait pas parler de
    # lui-même, et ses boutons fonctionnent de la même façon.
    try:
        await interaction.response.send_message(embed=embed, view=VuePanneau())
    except discord.Forbidden:
        await interaction.response.send_message(
            "Le bot n'a pas accès à ce salon. Regarde la console : le lien de "
            "réinvitation y est affiché.", ephemeral=True)


@bot.arbre.command(name="edt-menage",
                   description="Effacer les fiches déjà traitées (réservé aux valideurs)")
@app_commands.describe(
    combien="Nombre de messages à parcourir en remontant (500 par défaut)")
async def edt_menage(interaction, combien: int = 500):
    """Efface les messages du bot dans le message privé du valideur.

    Les fiches ENCORE EN ATTENTE sont épargnées : les supprimer laisserait la
    personne qui a fait la demande sans réponse possible, et sans trace.

    La commande s'exécute où on veut — elle va chercher le message privé du
    valideur. Les commandes étant enregistrées sur le serveur, elles
    n'apparaîtraient pas dans un message privé.
    """
    if ADMINS and interaction.user.id not in ADMINS:
        await interaction.response.send_message(
            "Commande réservée à la personne qui gère les agendas.", ephemeral=True)
        return
    if VALIDEUR is None:
        await interaction.response.send_message(
            "Aucun valideur configuré.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    try:
        personne = interaction.client.get_user(VALIDEUR) \
            or await interaction.client.fetch_user(VALIDEUR)
        canal = await personne.create_dm()
    except Exception as e:
        await interaction.followup.send(
            f"Message privé inaccessible ({type(e).__name__}).", ephemeral=True)
        return

    efface, gardees = 0, 0
    async for message in canal.history(limit=max(1, min(combien, 2000))):
        if message.author.id != interaction.client.user.id:
            continue
        if str(message.id) in ETAT:
            gardees += 1  # fiche en attente : on n'y touche pas
            continue
        try:
            await message.delete()
            efface += 1
        except discord.HTTPException:
            pass  # message déjà supprimé, ou trop ancien : sans importance

    bilan = f"🗑️ {efface} message(s) effacé(s)."
    if gardees:
        bilan += f"\n{gardees} fiche(s) en attente conservée(s)."
    await interaction.followup.send(bilan, ephemeral=True)


@bot.arbre.command(name="edt-liste",
                   description="Qui a accès à quel agenda (réservé aux valideurs)")
async def edt_liste(interaction):
    """Affiche les abonnés de chaque agenda."""
    if ADMINS and interaction.user.id not in ADMINS:
        await interaction.response.send_message(
            "Commande réservée à la personne qui gère les agendas.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    def relever():
        service = partager._service()
        if service is None:
            return None
        releve = {}
        for cle in sorted(partager.CATALOGUE):
            identifiant = partager.google_agenda.trouver_agenda(
                service, partager.CATALOGUE[cle][1][0][0], partager.CATALOGUE[cle][0])
            if identifiant is None:
                continue
            regles = partager._regles(service, identifiant)
            releve[cle] = sorted(a for a, r in regles.items()
                                 if r.get("role") == partager.ROLE)
        return releve

    releve = await asyncio.to_thread(relever)
    if releve is None:
        await interaction.followup.send("Autorisation Google indisponible.", ephemeral=True)
        return

    embed = discord.Embed(title="Abonnés par agenda", color=VERT)
    for cle, adresses in releve.items():
        embed.add_field(
            name=f"{partager.CATALOGUE[cle][0]} — {len(adresses)}",
            value="\n".join(f"`{a}`" for a in adresses) or "*personne*",
            inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)


def principale():
    """Vérifie la configuration puis lance le bot."""
    if not JETON:
        print("⛔ DISCORD_BOT_TOKEN n'est pas défini.")
        print("   Developer Portal → ton application → Bot → Reset Token.")
        return 1
    if not ADMINS and VALIDEUR is None:
        print("⛔ DISCORD_ADMINS est vide : personne à qui envoyer les demandes,")
        print("   et n'importe qui pourrait les valider.")
        print("   Mode développeur activé, clic droit sur toi → Copier l'ID.")
        return 1
    if not ADMINS:
        print("⚠️ DISCORD_ADMINS est vide : N'IMPORTE QUI pourra valider une")
        print("   demande, donc s'octroyer un agenda. À définir avant usage réel.")

    try:
        bot.run(JETON)
    except discord.LoginFailure:
        print("⛔ Jeton refusé par Discord. Régénère-le dans le portail.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(principale())

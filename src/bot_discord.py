"""
Bot Discord : formulaire d'inscription aux agendas, et validation.

Le salon d'inscription porte un panneau permanent avec un seul bouton. Un clic
ouvre, VISIBLE DE LA SEULE PERSONNE QUI A CLIQUÉ, une liste où elle coche les
agendas voulus, puis un formulaire pour son adresse Google. Rien n'est écrit
dans le salon : il ne s'encombre pas, et personne ne lit l'adresse d'un autre.

La demande arrive dans TON salon de validation, sous forme de fiche : tu peux
corriger les agendas demandés — en retirer un auquel la personne n'a pas droit,
en ajouter un autre — puis valider. Le partage Google est appliqué au clic, et
la personne est prévenue en message privé.

Pose le panneau une fois, dans le salon voulu, avec `/edt-panneau`. Il survit
aux redémarrages. `/edt` ouvre la même liste, pour qui ne le retrouve pas.

    python src/bot_discord.py

⚠️ Contrairement au reste du projet, ce script ne se lance pas par une tâche
planifiée : Discord n'envoie une commande ou un clic de bouton qu'à un
programme DÉJÀ connecté. Il doit donc tourner en permanence, sur une machine
allumée. GitHub Actions ne convient pas — un job y a une durée maximale et
démarre trop tard pour répondre.

Réglages, dans `.env` :

    DISCORD_BOT_TOKEN      le jeton du bot (Developer Portal → Bot → Reset Token)
    DISCORD_SALON_DEMANDES l'identifiant du salon où arrivent les demandes
    DISCORD_ADMINS         les identifiants Discord autorisés à valider,
                           séparés par des virgules
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
    trouves = set()
    for morceau in (texte or "").replace(",", " ").replace(";", " ").split():
        morceau = morceau.strip("<>@!&#")
        if morceau.isdigit():
            trouves.add(int(morceau))
    return trouves


ADMINS = identifiants(variable_env("DISCORD_ADMINS"))

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


# =====================================================================
# FICHE DE DEMANDE
# =====================================================================

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
    embed.add_field(name="Demandeur", value=f"<@{demande['discord_id']}>", inline=True)
    embed.add_field(name="Adresse Google", value=f"`{demande['courriel']}`", inline=True)
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

        salon = interaction.client.get_channel(SALON_DEMANDES)
        if salon is None:
            await interaction.response.send_message(
                "Le salon de validation n'est pas configuré : préviens la "
                "personne qui gère les agendas.", ephemeral=True)
            print("⛔ DISCORD_SALON_DEMANDES absent ou salon invisible pour le bot.")
            return

        demande = {
            "discord_id": str(interaction.user.id),
            "pseudo": str(interaction.user),
            "courriel": adresse,
            "cles": list(self.cles),
            "cles_demandees": list(self.cles),
        }
        try:
            message = await salon.send(embed=fiche(demande), view=VueDemande())
        except discord.Forbidden:
            # La demande serait perdue en silence : mieux vaut le dire à la
            # personne, qui pourra signaler la panne, que la laisser attendre.
            print("⛔ Fiche non postée : le bot ne peut pas écrire dans le "
                  f"salon {SALON_DEMANDES}. Voir les droits au démarrage.")
            await interaction.response.send_message(
                "⚠️ Ta demande n'a pas pu être transmise : le bot n'a pas accès "
                "au salon de validation. Signale-le, ce n'est pas de ton fait.",
                ephemeral=True)
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
            view=None)
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
            embed=fiche(demande, "refus", str(interaction.user)), view=None)
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
        if SERVEUR:
            serveur = discord.Object(id=SERVEUR)
            self.arbre.copy_global_to(guild=serveur)
            await self.arbre.sync(guild=serveur)
        else:
            await self.arbre.sync()

    async def on_ready(self):
        print(f"✅ Connecté comme {self.user}.")
        print(f"   Valideurs : {', '.join(map(str, ADMINS)) or '(tout le monde !)'}")
        print(f"   {len(ETAT)} demande(s) en attente.")
        await self._verifier_salon()

    async def _verifier_salon(self):
        """Vérifie tout de suite que le salon de validation est utilisable.

        Sans ce contrôle, la panne se découvrirait au pire moment : un étudiant
        remplit le formulaire, sa demande n'arrive nulle part, et il attend un
        accès que personne ne verra jamais passer.
        """
        try:
            salon = self.get_channel(SALON_DEMANDES) or await self.fetch_channel(SALON_DEMANDES)
        except discord.Forbidden:
            self._expliquer_droits("le bot ne voit pas le salon de validation")
            return
        except discord.NotFound:
            print(f"⛔ Salon {SALON_DEMANDES} introuvable. Vérifie "
                  "DISCORD_SALON_DEMANDES (clic droit sur le salon → Copier l'ID).")
            return
        except Exception as e:
            print(f"⚠️ Salon de validation non vérifié ({type(e).__name__}).")
            return

        droits = salon.permissions_for(salon.guild.me)
        manquants = [nom for nom, ok in (
            ("voir le salon", droits.view_channel),
            ("y écrire", droits.send_messages),
            ("y mettre un encadré", droits.embed_links),
        ) if not ok]
        if manquants:
            self._expliquer_droits("il manque au bot le droit de "
                                   + ", ".join(manquants))
            return
        print(f"   Salon de validation : #{salon.name} ✅")

    def _expliquer_droits(self, quoi):
        print(f"⛔ {quoi}.")
        print("   Deux causes possibles, dans cet ordre de fréquence :")
        print("   1. le bot a été invité avec le seul scope « applications.commands ».")
        print("      Les commandes apparaissent, mais il n'est pas membre du serveur")
        print("      et ne peut rien y poster. Réinvite-le avec ce lien :")
        print(f"      {lien_invitation(self.application_id or self.user.id)}")
        print("   2. le salon lui refuse l'accès. Paramètres du salon →")
        print("      Permissions → ajoute le rôle du bot.")


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
            "pense à accepter les deux invitations, sinon tu ne verras rien."),
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
    if not SALON_DEMANDES:
        print("⛔ DISCORD_SALON_DEMANDES n'est pas défini.")
        print("   Mode développeur activé, clic droit sur le salon → Copier l'ID.")
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

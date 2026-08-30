"""
Bot Discord : formulaire d'inscription aux agendas, et validation.

Un étudiant tape `/edt` dans le salon, choisit sa promotion dans un menu
déroulant et saisit son adresse Google. Sa demande arrive dans TON salon de
validation, sous forme de fiche : tu peux corriger les agendas demandés — en
retirer un auquel il n'a pas droit, en ajouter un autre — puis valider. Le
partage Google est appliqué au clic, et l'étudiant est prévenu.

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


class SelecteurAdmin(discord.ui.Select):
    """Menu qui permet de corriger les agendas AVANT de valider.

    C'est le cœur du besoin : quelqu'un demande un agenda auquel il n'a pas
    droit, on le retire et on met le bon, sans faire recommencer la personne.
    """

    def __init__(self):
        options = [
            discord.SelectOption(label=intitule, value=cle)
            for cle, (intitule, _) in sorted(partager.CATALOGUE.items())
        ]
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
        if SERVEUR:
            serveur = discord.Object(id=SERVEUR)
            self.arbre.copy_global_to(guild=serveur)
            await self.arbre.sync(guild=serveur)
        else:
            await self.arbre.sync()

    async def on_ready(self):
        print(f"✅ Connecté comme {self.user}.")
        print(f"   Salon de validation : {SALON_DEMANDES or '(non configuré)'}")
        print(f"   Valideurs : {', '.join(map(str, ADMINS)) or '(tout le monde !)'}")
        print(f"   {len(ETAT)} demande(s) en attente.")


bot = Bot()

CHOIX = [
    app_commands.Choice(name=intitule, value=cle)
    for cle, (intitule, _) in sorted(partager.CATALOGUE.items())
]


@bot.arbre.command(name="edt",
                   description="Recevoir son emploi du temps STRI dans son agenda")
@app_commands.describe(
    promotion="Ta promotion et ton groupe",
    courriel="L'adresse du compte Google que tu utilises sur ton téléphone")
@app_commands.choices(promotion=CHOIX)
async def edt(interaction, promotion: app_commands.Choice[str], courriel: str):
    """Enregistre une demande et l'envoie en validation."""
    courriel = courriel.strip().lower()
    if not partager.REGEX_COURRIEL.match(courriel):
        await interaction.response.send_message(
            f"`{courriel}` ne ressemble pas à une adresse. Réessaie.", ephemeral=True)
        return

    salon = interaction.client.get_channel(SALON_DEMANDES)
    if salon is None:
        await interaction.response.send_message(
            "Le salon de validation n'est pas configuré : préviens la personne "
            "qui gère les agendas.", ephemeral=True)
        print("⛔ DISCORD_SALON_DEMANDES absent ou salon invisible pour le bot.")
        return

    demande = {
        "discord_id": str(interaction.user.id),
        "pseudo": str(interaction.user),
        "courriel": courriel,
        "cles": [promotion.value],
        "cles_demandees": [promotion.value],
    }
    message = await salon.send(embed=fiche(demande), view=VueDemande())
    ETAT[str(message.id)] = demande
    enregistrer_etat(ETAT)

    await interaction.response.send_message(
        f"📨 Demande envoyée pour **{promotion.name}**, adresse `{courriel}`.\n"
        "Tu recevras un message privé dès qu'elle est validée. "
        "Erreur de saisie ? Relance `/edt`, l'ancienne sera écartée.",
        ephemeral=True)


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

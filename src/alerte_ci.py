"""
Prévient sur Discord quand le workflow s'interrompt.

Filet de dernier recours : tout ce qui casse AVANT que `edt_stri.py` ne démarre
— téléchargement refusé par le WAF, installation des dépendances, secret
absent, push rejeté — échouait jusqu'ici dans un onglet d'Actions que personne
n'ouvre. Les autres alertes viennent du script lui-même et ne peuvent rien dire
de ces cas-là.

N'importe QUE la bibliothèque standard : il doit fonctionner même quand
l'installation des dépendances est précisément ce qui a échoué.

`signaler()` est aussi le point de passage de TOUTES les alertes du projet
(edt_stri, rendus, verif_edt). Avec DISCORD_BOT_TOKEN et DISCORD_ADMINS, elle
écrit en message privé, au nom du bot, avec un bouton « Demander à Claude » :
le bot en marche reconnaît ce bouton et ouvre une issue GitHub à laquelle
Claude répond (voir `.github/workflows/claude.yml`). Un webhook ne peut pas
porter de bouton cliquable : il ne sert plus que de repli.

    python alerte_ci.py [message]
"""

import json
import os
import sys
import urllib.request

# Une console Windows en cp1252 fait planter le moindre print contenant un
# emoji. Un filet de securite qui echoue lui-meme ne sert a rien.
for _flux in (sys.stdout, sys.stderr):
    try:
        _flux.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

TITRE = "⛔ La synchronisation automatique a échoué"
CORPS = ("Une étape du workflow s'est interrompue. Les emplois du temps publiés "
         "n'ont pas été modifiés — ils gardent leur dernière version saine.")


API_DISCORD = "https://discord.com/api/v10"

# Le bot reconnaît ce bouton à son identifiant, quel que soit le programme qui
# a posté le message : la CI l'envoie, le bot (bot_discord.VueAlerte) y répond.
# Ne pas le changer sans changer les deux.
BOUTON_CLAUDE = "edt:claude"


def _lien_execution():
    """Lien vers l'exécution GitHub en cours, si l'on tourne en CI."""
    lien = os.environ.get("LIEN_EXECUTION", "").strip()
    depot, numero = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_RUN_ID")
    if not lien and depot and numero:
        serveur = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
        lien = f"{serveur}/{depot}/actions/runs/{numero}"
    return lien


def _destinataire():
    """Qui reçoit les alertes : le même que les fiches du bot — DISCORD_VALIDEUR,
    sinon le premier de DISCORD_ADMINS."""
    for nom in ("DISCORD_VALIDEUR", "DISCORD_ADMINS"):
        texte = os.environ.get(nom, "").replace(",", " ").replace(";", " ")
        for morceau in texte.split():
            morceau = morceau.strip("<>@!")
            if morceau.isdigit():
                return morceau
    return None


def _poster(url, charge, jeton=None):
    """POST JSON ; rend la réponse décodée. Discord exige ce User-Agent."""
    entetes = {"Content-Type": "application/json",
               "User-Agent": "DiscordBot (edt_stri, 1.0)"}
    if jeton:
        entetes["Authorization"] = f"Bot {jeton}"
    requete = urllib.request.Request(
        url, data=json.dumps(charge).encode("utf-8"), headers=entetes)
    with urllib.request.urlopen(requete, timeout=20) as reponse:
        corps = reponse.read()
    return json.loads(corps) if corps else {}


def signaler(titre, description, couleur=10038562):
    """Envoie une alerte sur Discord. Rend True si elle est partie.

    En message privé par le bot, avec le bouton Claude, quand il est configuré ;
    sinon par le webhook, sans bouton. Ne lève jamais : une alerte qui échoue
    ne doit pas masquer la panne qu'elle signale.
    """
    lien = _lien_execution()
    if lien and lien not in description:
        description += f"\n\n[Voir le détail de l'exécution]({lien})"
    embed = {"title": titre[:250], "description": description[:3900], "color": couleur}

    jeton, qui = os.environ.get("DISCORD_BOT_TOKEN", "").strip(), _destinataire()
    if jeton and qui:
        try:
            salon = _poster(f"{API_DISCORD}/users/@me/channels",
                            {"recipient_id": qui}, jeton)
            _poster(f"{API_DISCORD}/channels/{salon['id']}/messages", {
                "embeds": [embed],
                "components": [{"type": 1, "components": [{
                    "type": 2, "style": 1, "emoji": {"name": "🤖"},
                    "label": "Demander à Claude de régler ça",
                    "custom_id": BOUTON_CLAUDE}]}],
            }, jeton)
            print("✅ Alerte envoyée en message privé, avec le bouton Claude.")
            return True
        except Exception as e:
            print(f"⚠️  Message privé impossible ({e}) : repli sur le webhook.")

    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        print("ℹ️  Ni bot ni webhook Discord configuré : rien à signaler.")
        return False
    try:
        _poster(webhook, {"username": "Bot EDT STRI", "embeds": [embed]})
        print("✅ Alerte postée sur Discord (webhook, sans bouton).")
        return True
    except Exception as e:
        print(f"❌ Signalement Discord impossible : {e}")
        return False


def prevenir(message=None):
    """Poste l'échec sur Discord. Rend toujours 0.

    Elle tourne quand tout a déjà échoué : rendre un code non nul
    remplacerait la vraie cause par celle-ci dans le rapport du workflow.
    """
    signaler(TITRE, message or CORPS)
    return 0


if __name__ == "__main__":
    sys.exit(prevenir(" ".join(sys.argv[1:]) or None))

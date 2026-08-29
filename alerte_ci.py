"""
Prévient sur Discord quand le workflow s'interrompt.

Filet de dernier recours : tout ce qui casse AVANT que `edt_stri.py` ne démarre
— téléchargement refusé par le WAF, installation des dépendances, secret
absent, push rejeté — échouait jusqu'ici dans un onglet d'Actions que personne
n'ouvre. Les autres alertes viennent du script lui-même et ne peuvent rien dire
de ces cas-là.

N'importe QUE la bibliothèque standard : il doit fonctionner même quand
l'installation des dépendances est précisément ce qui a échoué.

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


def prevenir(message=None):
    """Poste l'échec sur Discord. Rend toujours 0.

    Elle tourne quand tout a déjà échoué : rendre un code non nul
    remplacerait la vraie cause par celle-ci dans le rapport du workflow.
    """
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        print("ℹ️  Aucun webhook Discord configuré : rien à signaler.")
        return 0

    description = message or CORPS
    lien = os.environ.get("LIEN_EXECUTION", "").strip()
    if lien:
        description += f"\n\n[Voir le détail de l'exécution]({lien})"

    charge = json.dumps({
        "username": "Bot EDT STRI",
        "embeds": [{"title": TITRE, "description": description[:3900],
                    "color": 10038562}],
    }).encode("utf-8")

    try:
        requete = urllib.request.Request(
            webhook, data=charge, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(requete, timeout=20)
        print("✅ Échec signalé sur Discord.")
        return 0
    except Exception as e:
        # Ne jamais masquer l'échec d'origine derrière celui de l'alerte.
        print(f"❌ Signalement Discord impossible : {e}")
        return 0


if __name__ == "__main__":
    sys.exit(prevenir(" ".join(sys.argv[1:]) or None))

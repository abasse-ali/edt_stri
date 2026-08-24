"""
Téléchargement du PDF de l'emploi du temps, derrière le WAF « Tiger Protect ».

Module volontairement séparé et **léger** : il n'importe que `requests` et
`playwright`. La CI l'appelle en premier, AVANT d'installer `requirements.txt`,
pour n'payer numpy, OpenCV et pdfplumber que si le PDF a réellement changé —
c'est-à-dire quelques fois par mois plutôt qu'à chacune des vingt-quatre
exécutions quotidiennes.

Le tout début de `edt_stri.py` importe numpy ; l'appeler pour télécharger
échouait donc en `ModuleNotFoundError: No module named 'numpy'`.

    python telechargement.py [chemin_de_sortie]
"""

import os
import sys
from pathlib import Path

import requests

def variable_env(nom, defaut=""):
    """Lit une variable d'environnement en traitant le vide comme l'absence.

    `os.environ.get(nom, defaut)` ne rend le défaut que si la CLÉ manque. Or
    GitHub Actions définit toujours les variables citées dans `env:`, même
    lorsque la variable de dépôt correspondante n'existe pas — avec une valeur
    vide. `EDT_PDF_URL: ${{ vars.EDT_PDF_URL }}` non défini donnait donc une
    URL vide, et le téléchargement échouait sur « No scheme supplied ».
    """
    valeur = os.environ.get(nom, "").strip()
    return valeur or defaut


# Une seule et unique source pour l'URL du PDF (CI + local), importée par
# edt_stri.py. Surchargeable sans toucher au code :
#     EDT_PDF_URL=... python telechargement.py
EDT_PDF_URL = variable_env(
    "EDT_PDF_URL",
    "https://stri.fr/Gestion_STRI/TAV/M1/EDT_STRI4A-M1RT_TAV.pdf",
)
EDT_BASE_URL = "https://stri.fr/"

FICHIER_PDF = variable_env("EDT_PDF", "edt.pdf")

# Plus aucun appel réseau sans délai maximum.
TIMEOUT_HTTP = 20

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Sur une console Windows en cp1252, le moindre print contenant un emoji lève
# UnicodeEncodeError et tue le script en cours de traitement.
for _flux in (sys.stdout, sys.stderr):
    try:
        _flux.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def telecharger_pdf(chemin_sauvegarde=None):
    """Récupère le PDF derrière le WAF « Tiger Protect ». Retourne True si OK."""
    chemin_sauvegarde = chemin_sauvegarde or FICHIER_PDF
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("❌ playwright n'est pas installé (pip install playwright && playwright install chromium).")
        return False

    print(f"🌐 Téléchargement de {EDT_PDF_URL}")
    try:
        with sync_playwright() as p:
            navigateur = p.chromium.launch(headless=True)
            contexte = navigateur.new_context(user_agent=USER_AGENT)
            page = contexte.new_page()

            print("🛡️ Passage de la vérification anti-bot (Tiger Protect)...")
            page.goto(EDT_BASE_URL)
            page.wait_for_timeout(10000)  # laisse le JavaScript valider la session

            print("🍪 Récupération du cookie d'accès...")
            cookies = {c['name']: c['value'] for c in contexte.cookies()}
            navigateur.close()

        print("📥 Téléchargement du fichier PDF...")
        reponse = requests.get(
            EDT_PDF_URL,
            headers={'User-Agent': USER_AGENT},
            cookies=cookies,
            timeout=TIMEOUT_HTTP,
        )
        reponse.raise_for_status()

        if not reponse.content.startswith(b'%PDF'):
            print("❌ Erreur : le fichier téléchargé n'est pas un PDF valide !")
            return False

        Path(chemin_sauvegarde).write_bytes(reponse.content)
        print(f"✅ Téléchargement terminé ({len(reponse.content) // 1024} Ko).")
        return True

    except Exception as e:
        print(f"❌ Erreur lors du téléchargement : {e}")
        return False


if __name__ == "__main__":
    cibles = [a for a in sys.argv[1:] if not a.startswith("--")]
    sys.exit(0 if telecharger_pdf(cibles[0] if cibles else None) else 1)

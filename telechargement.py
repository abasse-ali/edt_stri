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

# Le .env est chargé ici, dans le module que tous les autres importent : sinon
# seul test_local.py le lisait, et les scripts lancés directement ne voyaient
# aucune variable. Sans effet en CI, où le fichier n'existe pas et où tout
# vient de l'environnement.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass


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


# --- Promotions ------------------------------------------------------------
# Chaque promotion a son PDF, ses agendas et ses fichiers. Les noms ne servent
# qu'à la CRÉATION : ensuite, les agendas sont retrouvés par leur marqueur, si
# bien qu'un renommage dans l'interface Google ne casse rien. En revanche les
# clés et les suffixes, eux, ne doivent jamais changer — ils étiquettent des
# agendas déjà partagés et des fichiers déjà versionnés.
#
# Ajouter une promotion = ajouter une entrée. Rien d'autre à toucher.
PROMOS = {
    "M1": {
        "url": "https://stri.fr/Gestion_STRI/TAV/M1/EDT_STRI4A-M1RT_TAV.pdf",
        "pdf": "edt.pdf",
        "agendas": {"BAS": "STRI M1 G2", "HAUT": "STRI Ingé2 G1"},
        "suffixes": {"BAS": "", "HAUT": "_inge"},
        # Clés historiques, sans préfixe : elles étiquettent déjà les agendas
        # existants et doivent le rester.
        "cles": {"BAS": "BAS", "HAUT": "HAUT"},
        "couleurs": {"BAS": ("pistache", "basilic"), "HAUT": ("raisin", "raisin")},
    },
    "L3": {
        "url": "https://stri.fr/Gestion_STRI/TAV/L3/EDT_STRI1A_L3IRT_TAV.pdf",
        "pdf": "edt_l3.pdf",
        # La moitié haute de la L3, c'est la promotion Ingé1 — exactement comme
        # la moitié haute du M1 est celle des Ingé.
        "agendas": {"BAS": "IRT L3", "HAUT": "STRI Ingé1"},
        "suffixes": {"BAS": "_l3", "HAUT": "_inge1"},
        "cles": {"BAS": "L3-BAS", "HAUT": "L3-HAUT"},
        "couleurs": {"BAS": ("myrtille", "myrtille"), "HAUT": ("amethyste", "lavande")},
    },
}

EDT_BASE_URL = "https://stri.fr/"

# URL et fichier par défaut, surchargeables sans toucher au code :
#     EDT_PDF_URL=... python telechargement.py
#     python telechargement.py --promo L3 sortie.pdf
EDT_PDF_URL = variable_env("EDT_PDF_URL", PROMOS["M1"]["url"])
FICHIER_PDF = variable_env("EDT_PDF", PROMOS["M1"]["pdf"])

# Plus aucun appel réseau sans délai maximum.
TIMEOUT_HTTP = 20

# Navigation vers stri.fr : le WAF est nettement plus lent depuis un runner
# GitHub que depuis une machine personnelle (30 s ne suffisaient pas).
DELAI_NAVIGATION = 90_000   # ms
ATTENTE_WAF = 10_000        # ms laissés au JavaScript pour valider la session
ESSAIS_MAX = 3

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


def _ouvrir_accueil(page):
    """Ouvre stri.fr en laissant au WAF le temps de répondre.

    Depuis un runner GitHub, `page.goto()` échouait en « Timeout 30000ms
    exceeded » : le défaut de Playwright attend l'événement `load`, donc la
    totalité des images et scripts du site, et « Tiger Protect » sert d'abord
    une page de contrôle depuis un datacenter qu'il traite avec méfiance.

    `domcontentloaded` suffit — la validation se fait ensuite en JavaScript,
    pendant l'attente qui suit. Trois essais couvrent les lenteurs passagères.
    """
    derniere = None
    for essai in range(1, ESSAIS_MAX + 1):
        try:
            page.goto(EDT_BASE_URL, wait_until="domcontentloaded",
                      timeout=DELAI_NAVIGATION)
            return
        except Exception as e:
            derniere = e
            print(f"   ⚠️ Accès à {EDT_BASE_URL} en échec "
                  f"(essai {essai}/{ESSAIS_MAX}) : {str(e).splitlines()[0]}")
            if essai < ESSAIS_MAX:
                page.wait_for_timeout(5000 * essai)
    raise derniere


def telecharger_pdf(chemin_sauvegarde=None, url=None):
    """Récupère le PDF derrière le WAF « Tiger Protect ». Retourne True si OK."""
    chemin_sauvegarde = chemin_sauvegarde or FICHIER_PDF
    url = url or EDT_PDF_URL
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("❌ playwright n'est pas installé (pip install playwright && playwright install chromium).")
        return False

    print(f"🌐 Téléchargement de {url}")
    try:
        with sync_playwright() as p:
            navigateur = p.chromium.launch(headless=True)
            contexte = navigateur.new_context(user_agent=USER_AGENT)
            page = contexte.new_page()

            print("🛡️ Passage de la vérification anti-bot (Tiger Protect)...")
            _ouvrir_accueil(page)
            page.wait_for_timeout(ATTENTE_WAF)  # laisse le JavaScript valider la session

            print("🍪 Récupération du cookie d'accès...")
            cookies = {c['name']: c['value'] for c in contexte.cookies()}
            navigateur.close()

        print("📥 Téléchargement du fichier PDF...")
        reponse = requests.get(
            url,
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
    arguments = sys.argv[1:]
    promo = None
    if "--promo" in arguments:
        i = arguments.index("--promo")
        promo = arguments[i + 1].upper() if i + 1 < len(arguments) else ""
        del arguments[i:i + 2]
        if promo not in PROMOS:
            sys.exit(f"❌ --promo {promo!r} inconnu. Valeurs : {', '.join(PROMOS)}.")

    cibles = [a for a in arguments if not a.startswith("--")]
    choisie = PROMOS[promo] if promo else None
    sys.exit(0 if telecharger_pdf(
        cibles[0] if cibles else (choisie["pdf"] if choisie else None),
        url=choisie["url"] if choisie else None) else 1)

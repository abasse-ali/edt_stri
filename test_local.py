"""
Lanceur local du bot EDT.

Contrairement à l'ancien test_local.py (qui dupliquait ~850 lignes et avait
divergé du script de production : mauvaise URL de PDF, chemin poppler erroné,
fonction de téléchargement jamais appelée), ce fichier ne fait que configurer
l'environnement puis appeler edt_stri.py — il ne peut donc plus diverger.

Usage (depuis la racine du dépôt) :
    python new_test/test_local.py                # télécharge le PDF puis traite
    python new_test/test_local.py --no-download  # réutilise le edt.pdf existant
    python new_test/test_local.py --no-debug     # sans export des images
"""

import os
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent

# Console Windows en cp1252 : sans ceci, le premier print contenant un emoji
# lève UnicodeEncodeError. À faire avant tout affichage, donc avant d'importer
# edt_stri (qui applique le même réglage de son côté).
for _flux in (sys.stdout, sys.stderr):
    try:
        _flux.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

def _verifier_venv():
    """Échoue immédiatement si le venv du projet n'est pas activé.

    Sans lui, l'import de numpy part chercher les paquets du Python du Microsoft
    Store (plusieurs dizaines de secondes) avant d'échouer sur cv2, absent.
    """
    if sys.prefix != sys.base_prefix:
        return  # déjà dans un environnement virtuel

    for relatif in (Path("venv") / "Scripts" / "python.exe", Path("venv") / "bin" / "python"):
        if (RACINE / relatif).exists():
            print("⛔ L'environnement virtuel du projet n'est pas activé.")
            print("   (sans lui, cv2, pdfplumber, ics et pdf2image sont introuvables)")
            print()
            print("   Active-le :")
            print("     .\\venv\\Scripts\\Activate.ps1")
            print("   ou lance directement :")
            print(f"     .\\{relatif} new_test\\{Path(__file__).name}")
            sys.exit(1)

_verifier_venv()

try:
    from dotenv import load_dotenv
    load_dotenv(RACINE / ".env")  # DISCORD_WEBHOOK_URL / GOOGLE_CALENDAR_ID
except ImportError:
    print("ℹ️  python-dotenv absent : les variables doivent être déjà exportées.")

# CORRECTIF #11 : les exports de debug sont activés ici, pas en dur dans le
# script de production (qui gaspillait des copies d'images en CI).
if "--no-debug" not in sys.argv:
    os.environ.setdefault("EDT_DEBUG", "1")

sys.path.insert(0, str(Path(__file__).resolve().parent))

import edt_stri  # noqa: E402  (import après configuration de l'environnement)

def main():
    print(f"📂 Dossier de travail : {Path.cwd()}")
    print(f"🔧 Poppler : {edt_stri.POPPLER_PATH or 'PATH système'}")

    # CORRECTIF #12 : le téléchargement est réellement appelé (l'ancienne
    # version définissait telecharger_pdf() sans jamais l'utiliser, on
    # retraitait donc indéfiniment un PDF périmé).
    if "--no-download" not in sys.argv:
        if not edt_stri.telecharger_pdf():
            print("⛔ Téléchargement impossible. Utilise --no-download pour "
                  "retraiter le PDF déjà présent.")
            return 1
    else:
        print("⏭️  Téléchargement ignoré (--no-download).")

    return edt_stri.principale()

if __name__ == "__main__":
    sys.exit(main())
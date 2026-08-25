"""
Lanceur local du bot EDT.

Fait exactement ce que fait la CI : télécharge les PDF de chaque promotion, puis
traite les quatre combinaisons promotion × demi-promo. Sans lui, `python
edt_stri.py` ne traite QUE le M1 en moitié basse — la configuration étant lue au
chargement du module, une exécution ne peut couvrir qu'une combinaison.

Chaque passe tourne dans son propre processus, comme en CI, pour la même raison.

Usage (depuis la racine du dépôt) :
    python test_local.py                  # tout : 2 téléchargements, 4 passes
    python test_local.py --no-download    # réutilise les PDF déjà présents
    python test_local.py --promo L3       # une seule promotion
    python test_local.py --moitie HAUT    # une seule demi-promo
    python test_local.py --no-debug       # sans export des images
"""

import os
import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent

# Console Windows en cp1252 : sans ceci, le premier print contenant un emoji
# lève UnicodeEncodeError. À faire avant tout affichage.
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
            print(f"     .\\{relatif} {Path(__file__).name}")
            sys.exit(1)


_verifier_venv()

# Les exports de debug sont activés ici, pas en dur dans le script de
# production, qui gaspillerait des copies d'images en CI.
if "--no-debug" not in sys.argv:
    os.environ.setdefault("EDT_DEBUG", "1")

sys.path.insert(0, str(RACINE))

# telechargement est le module léger : il porte la table des promotions et ne
# tire ni numpy ni OpenCV.
from telechargement import PROMOS, telecharger_pdf  # noqa: E402


def _option(nom, valeurs):
    """Lit `--nom VALEUR` dans la ligne de commande."""
    if nom not in sys.argv:
        return None
    i = sys.argv.index(nom)
    valeur = sys.argv[i + 1].upper() if i + 1 < len(sys.argv) else ""
    if valeur not in valeurs:
        sys.exit(f"⛔ {nom} {valeur!r} inconnu. Valeurs : {', '.join(valeurs)}.")
    return valeur


def main():
    promo_voulue = _option("--promo", PROMOS)
    moitie_voulue = _option("--moitie", ("BAS", "HAUT"))

    promos = [promo_voulue] if promo_voulue else list(PROMOS)
    moities = [moitie_voulue] if moitie_voulue else ["BAS", "HAUT"]

    print(f"📂 Dossier de travail : {Path.cwd()}")
    print(f"🎓 Promotions : {', '.join(promos)}   demi-promos : {', '.join(moities)}")

    if "--no-download" in sys.argv:
        print("⏭️  Téléchargement ignoré (--no-download).")
    else:
        for promo in promos:
            print(f"\n─── Téléchargement {promo} ───")
            if not telecharger_pdf(PROMOS[promo]["pdf"], url=PROMOS[promo]["url"]):
                print(f"⛔ Téléchargement {promo} impossible. Utilise --no-download "
                      "pour retraiter les PDF déjà présents.")
                return 1

    echecs = []
    for promo in promos:
        for moitie in moities:
            nom = PROMOS[promo]["agendas"][moitie]
            print(f"\n─── {promo} / {moitie} → {nom} ───")
            code = subprocess.call(
                [sys.executable, str(RACINE / "edt_stri.py")],
                env={**os.environ, "EDT_PROMO": promo, "EDT_MOITIE": moitie},
            )
            if code != 0:
                echecs.append(f"{promo}/{moitie}")

    if echecs:
        print(f"\n❌ Échec sur : {', '.join(echecs)}")
        return 1
    print(f"\n✅ {len(promos) * len(moities)} passe(s) terminée(s) sans erreur.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

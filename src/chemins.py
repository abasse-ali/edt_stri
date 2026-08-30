"""
Emplacements des fichiers du projet.

Tout le code lisait des chemins relatifs — `edt_m1.pdf`, `journal.csv`,
`token.json` — qui se résolvaient donc par rapport au répertoire courant. Cela
marchait tant que tout vivait à la racine et qu'on lançait tout depuis là.
Avec `src/`, `tests/` et `donnees/`, ce n'est plus vrai : `python
tests/verif_edt.py` et `python src/rendus.py` doivent trouver les mêmes
fichiers, quel que soit l'endroit d'où on les appelle.

Les chemins sont donc calculés une fois ici, à partir de l'emplacement de ce
module, et jamais reconstruits ailleurs.

    RACINE     le dépôt
    SRC        le code
    TESTS      test_edt.py et verif_edt.py
    DONNEES    tout ce qui est régénéré : PDF, JSON, ICS, journal, images
    DOCS       HISTORIQUE.md et TUTO.txt

Les identifiants Google (`token.json`, `credentials.json`) restent à la racine :
ils ne sont pas versionnés, et la CI écrit le jeton avant de connaître la
disposition du dépôt.
"""

from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
SRC = RACINE / "src"
TESTS = RACINE / "tests"
DONNEES = RACINE / "donnees"
DOCS = RACINE / "docs"

# Créé à l'import plutôt qu'au premier écrit : une dizaine d'endroits écrivent
# dans ce dossier, et un `mkdir` oublié dans l'un d'eux ne se verrait qu'en
# production, sur une machine où le dossier n'a pas été cloné avec des données.
DONNEES.mkdir(parents=True, exist_ok=True)


def donnee(nom):
    """Chemin d'un fichier régénéré, sous `donnees/`."""
    return DONNEES / nom


def racine(nom):
    """Chemin d'un fichier de la racine du dépôt."""
    return RACINE / nom

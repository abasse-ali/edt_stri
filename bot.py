#!/usr/bin/env python
"""
Point d'entrée du bot pour les panneaux d'hébergement.

Katabump, FridayDev et les autres panneaux de ce genre reposent presque tous
sur Pterodactyl, qui lance **un fichier** posé à la racine — souvent `bot.py`
par défaut. Ce fichier existe donc pour eux : il ne fait qu'ajouter `src/` au
chemin de recherche et appeler le vrai programme.

Sur une machine où l'on choisit soi-même la commande, appelle directement :

    python -u src/bot_discord.py

Les deux reviennent au même. Les chemins de fichiers étant calculés à partir de
l'emplacement des modules (voir `src/chemins.py`), le répertoire courant n'a
aucune importance : le panneau peut lancer le programme d'où il veut.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import bot_discord  # noqa: E402

if __name__ == "__main__":
    sys.exit(bot_discord.principale())

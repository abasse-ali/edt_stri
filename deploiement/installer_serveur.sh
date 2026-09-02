#!/usr/bin/env bash
# Installe le bot Discord sur un serveur Linux (Oracle Cloud, Raspberry Pi, VPS).
#
# Le dépôt est public : le plus simple est de le cloner sur la machine.
#
#        git clone https://github.com/abasse-ali/edt_stri.git
#        bash edt_stri/deploiement/installer_serveur.sh
#
# Il se débrouille aussi avec des fichiers simplement déposés en scp, sans dépôt
# git du tout — utile quand la machine n'a pas accès à GitHub.
#
# Le script est IDEMPOTENT : le relancer met simplement à jour. Il ne touche
# jamais à .env ni à token.json, qui se déposent à la main — ces deux fichiers
# ne sont pas dans le dépôt, et n'ont rien à y faire.

set -euo pipefail

DEPOT="${DEPOT:-https://github.com/abasse-ali/edt_stri.git}"
RACINE="${RACINE:-/opt/edt_stri}"
UTILISATEUR="${UTILISATEUR:-$(id -un)}"
SERVICE="edt-bot"

echo "→ Installation dans $RACINE, pour l'utilisateur $UTILISATEUR"

# --- 1. Dépendances système ------------------------------------------------
# python3-venv est un paquet à part sur Debian et Ubuntu : sans lui, `python3
# -m venv` échoue avec un message qui n'explique rien.
if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3 python3-venv python3-pip git
elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y -q python3 python3-pip git
else
    echo "⛔ Ni apt ni dnf : installe python3, python3-venv et git à la main."
    exit 1
fi

# --- 1 bis. Un Python assez récent ----------------------------------------
# `zoneinfo` n'existe qu'à partir de 3.9, et tout le projet en dépend pour les
# fuseaux horaires. Ubuntu 20.04 livre 3.8 : sans ce rattrapage, l'installation
# se passerait bien et le bot planterait au premier import.
PYTHON="${PYTHON:-python3}"
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
    version="$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    echo "→ Python $version trop ancien (3.9 minimum), installation d'une version récente"

    for candidat in python3.12 python3.11 python3.10 python3.9; do
        if command -v "$candidat" >/dev/null 2>&1; then
            PYTHON="$candidat"
            break
        fi
    done

    if [ "$PYTHON" = "python3" ]; then
        if ! command -v apt-get >/dev/null 2>&1; then
            echo "⛔ Installe Python 3.9 ou plus récent, puis relance avec :"
            echo "     PYTHON=/chemin/vers/python3.x bash $0"
            exit 1
        fi
        # Le dépôt deadsnakes fournit des Python récents pour les Ubuntu LTS.
        sudo apt-get install -y -qq software-properties-common
        sudo add-apt-repository -y ppa:deadsnakes/ppa
        sudo apt-get update -qq
        sudo apt-get install -y -qq python3.11 python3.11-venv
        PYTHON=python3.11
    fi
    echo "→ Python retenu : $("$PYTHON" --version)"
fi

# --- 2. Le code ------------------------------------------------------------
# Trois cas, dans cet ordre : on tourne déjà dans l'arborescence installée ; on
# a été lancé depuis des fichiers déposés ailleurs ; ou il faut cloner.
ICI="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ "$ICI" = "$RACINE" ]; then
    if [ -d "$RACINE/.git" ]; then
        echo "→ Déjà installé ici, mise à jour depuis le dépôt"
        git -C "$RACINE" pull --ff-only
    else
        # Installé par copie de fichiers : il n'y a rien d'où tirer. Le dire,
        # plutôt que de laisser croire à une mise à jour qui n'a pas eu lieu —
        # le script réinstallerait alors le code déjà en place, à l'identique.
        echo "⚠️  Installé par copie, sans dépôt git : LE CODE N'EST PAS MIS À JOUR."
        echo "   Pour le mettre à jour, clone ailleurs et relance depuis le clone :"
        echo "     git clone $DEPOT ~/edt_stri"
        echo "     bash ~/edt_stri/deploiement/installer_serveur.sh"
        echo
    fi
elif [ -f "$ICI/src/bot_discord.py" ]; then
    echo "→ Fichiers déposés dans $ICI, installation vers $RACINE"
    sudo mkdir -p "$RACINE"
    # -a préserve les droits ; le point final copie le CONTENU, pas le dossier.
    sudo cp -a "$ICI/." "$RACINE/"
elif [ -d "$RACINE/.git" ]; then
    echo "→ Dépôt déjà présent, mise à jour"
    sudo git -C "$RACINE" pull --ff-only
elif [ -n "$DEPOT" ]; then
    sudo mkdir -p "$(dirname "$RACINE")"
    sudo git clone --depth 1 "$DEPOT" "$RACINE"
else
    echo "⛔ Aucun code trouvé, et aucun dépôt indiqué."
    echo "   Relance avec DEPOT=<adresse du dépôt>, ou dépose les fichiers en scp."
    exit 1
fi
sudo chown -R "$UTILISATEUR":"$UTILISATEUR" "$RACINE"

# --- 3. L'environnement Python --------------------------------------------
# requirements-bot.txt et NON requirements.txt : 161 Mo au lieu de 483, et le
# bot ne lit aucun PDF.
[ -d "$RACINE/venv" ] || "$PYTHON" -m venv "$RACINE/venv"
"$RACINE/venv/bin/pip" install --quiet --upgrade pip
"$RACINE/venv/bin/pip" install --quiet -r "$RACINE/requirements-bot.txt"

# --- 4. Le service ---------------------------------------------------------
# Le modèle vise /opt/edt_stri et l'utilisateur pi : on l'adapte à cette
# machine plutôt que de demander une édition manuelle qu'on oublie de faire.
sudo sed -e "s|^User=.*|User=$UTILISATEUR|" \
         -e "s|^WorkingDirectory=.*|WorkingDirectory=$RACINE|" \
         -e "s|^ExecStart=.*|ExecStart=$RACINE/venv/bin/python -u $RACINE/src/bot_discord.py|" \
         "$RACINE/deploiement/$SERVICE.service" \
    | sudo tee "/etc/systemd/system/$SERVICE.service" >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE" >/dev/null

# --- 4 bis. Le reveil des workflows GitHub --------------------------------
# GitHub abandonne la plupart des declenchements planifies d'un depot gratuit.
# Cette machine tourne en permanence : elle sert d'horloge fiable. Le timer ne
# demarre que si GITHUB_TOKEN est renseigne — sans jeton il n'y a rien a faire,
# et une unite qui echoue toutes les heures ne ferait que polluer le journal.
for unite in "$SERVICE-reveil.service" "$SERVICE-reveil.timer"; do
    sudo sed -e "s|^User=.*|User=$UTILISATEUR|" \
             -e "s|^WorkingDirectory=.*|WorkingDirectory=$RACINE|" \
             -e "s|^ExecStart=.*|ExecStart=$RACINE/venv/bin/python -u $RACINE/src/reveil.py|" \
             "$RACINE/deploiement/$unite" \
        | sudo tee "/etc/systemd/system/$unite" >/dev/null
done

# Le jeton GitHub vit dans .env, comme le reste des secrets.
sudo sed -i "/^\[Service\]/a EnvironmentFile=$RACINE/.env" \
    "/etc/systemd/system/$SERVICE-reveil.service"
sudo systemctl daemon-reload

if grep -qs '^GITHUB_TOKEN=.' "$RACINE/.env"; then
    sudo systemctl enable --now "$SERVICE-reveil.timer" >/dev/null
    echo "⏰ Réveil horaire des workflows activé."
else
    sudo systemctl disable --now "$SERVICE-reveil.timer" >/dev/null 2>&1 || true
    echo "ℹ️  Réveil des workflows en veille : GITHUB_TOKEN absent de .env."
fi

# --- 5. Ce qui reste à faire à la main ------------------------------------
manquants=()
[ -f "$RACINE/.env" ]        || manquants+=(".env")
[ -f "$RACINE/token.json" ]  || manquants+=("token.json")

echo
if [ ${#manquants[@]} -gt 0 ]; then
    echo "⚠️  Il manque : ${manquants[*]}"
    echo
    echo "    Depuis TON PC, dépose-les :"
    echo "      scp .env token.json $UTILISATEUR@<adresse-du-serveur>:$RACINE/"
    echo
    echo "    Puis démarre :"
    echo "      sudo systemctl start $SERVICE"
else
    sudo systemctl restart "$SERVICE"
    echo "✅ Service démarré."
fi

echo
echo "Suivre les journaux :  journalctl -u $SERVICE -f"
echo "État                :  systemctl status $SERVICE"
echo "Mettre a jour       :  bash $RACINE/deploiement/installer_serveur.sh"

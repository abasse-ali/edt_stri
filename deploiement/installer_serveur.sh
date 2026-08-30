#!/usr/bin/env bash
# Installe le bot Discord sur un serveur Linux (Oracle Cloud, Raspberry Pi, VPS).
#
#   curl -fsSL https://raw.githubusercontent.com/abasse-ali/edt_stri/main/deploiement/installer_serveur.sh | bash
#
# ou, si le dépôt est déjà cloné :
#
#   bash deploiement/installer_serveur.sh
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

# --- 2. Le code ------------------------------------------------------------
if [ -d "$RACINE/.git" ]; then
    echo "→ Dépôt déjà présent, mise à jour"
    sudo git -C "$RACINE" pull --ff-only
else
    sudo mkdir -p "$(dirname "$RACINE")"
    sudo git clone --depth 1 "$DEPOT" "$RACINE"
fi
sudo chown -R "$UTILISATEUR":"$UTILISATEUR" "$RACINE"

# --- 3. L'environnement Python --------------------------------------------
# requirements-bot.txt et NON requirements.txt : 161 Mo au lieu de 483, et le
# bot ne lit aucun PDF.
[ -d "$RACINE/venv" ] || python3 -m venv "$RACINE/venv"
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
echo "Mettre à jour       :  bash $RACINE/deploiement/installer_serveur.sh"

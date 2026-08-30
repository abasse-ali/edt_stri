# Image du bot Discord d'inscription.
#
# Volontairement construite sur requirements-bot.txt et NON sur
# requirements.txt : le bot ne lit aucun PDF. L'image pèse une centaine de
# mégaoctets au lieu de plusieurs centaines, ce qui compte sur un Raspberry Pi
# ou une petite machine virtuelle.
#
#   docker compose up -d          démarrer
#   docker compose logs -f        suivre
#   docker compose pull && docker compose up -d --build   mettre à jour

FROM python:3.13-slim

# Sans ceci, un print contenant un emoji casse dans un conteneur dont la locale
# est POSIX — et tous les messages du bot en contiennent.
ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    TZ=Europe/Paris

WORKDIR /app

# Les dépendances d'abord : elles changent rarement, et Docker garde alors
# cette couche en cache quand seul le code bouge.
COPY requirements-bot.txt ./
RUN pip install --no-cache-dir -r requirements-bot.txt

COPY src/ ./src/
COPY docs/ ./docs/

# `donnees/` accueille les demandes en attente : c'est un volume, pour qu'un
# redémarrage du conteneur ne les perde pas.
RUN mkdir -p donnees

CMD ["python", "-u", "src/bot_discord.py"]

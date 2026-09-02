"""
Réveille les workflows GitHub que la planification n'honore pas.

Le cron d'un workflow demande une exécution par heure. GitHub le documente
franchement : un déclenchement planifié peut être **retardé, voire abandonné**
quand la file d'attente est chargée, et les dépôts gratuits passent en dernier.
Mesuré sur ce dépôt : environ cinq exécutions par jour au lieu de vingt-quatre,
avec des trous allant jusqu'à douze heures. Un emploi du temps publié le matin
pouvait n'être traité qu'en début d'après-midi.

Ce script ne fait que **sonner** : il demande à GitHub de lancer le workflow.
Tout le traitement reste là-bas — le téléchargement, la lecture des PDF,
l'écriture dans les agendas. La machine qui héberge le bot Discord, elle,
tourne en permanence et possède une horloge fiable : elle sert de réveil.

    python src/reveil.py            déclenche si nécessaire
    python src/reveil.py --forcer   déclenche sans condition
    python src/reveil.py --etat     les derniers passages — le dépôt étant
                                    public, cette lecture ne demande aucun jeton

⚠️ Il ne déclenche QUE si aucun passage récent n'a eu lieu. Le dépôt étant
public, les minutes d'Actions sont illimitées et l'économie n'est plus le sujet
— mais relancer un workflow que GitHub vient de lancer produirait deux
exécutions concurrentes sur les mêmes agendas, et des notifications Discord en
double. Le garde-fou reste donc nécessaire, pour une autre raison.

Sur un dépôt PRIVÉ, il redevient aussi une question de coût : 2 000 minutes par
mois, dont 1 700 seraient consommées par un réveil aveugle.

Réglages, dans `.env` :

    GITHUB_TOKEN        jeton à portée réduite, permission « Actions : write »
                        sur ce seul dépôt (Settings → Developer settings →
                        Fine-grained tokens)
    GITHUB_DEPOT        « proprietaire/depot », si ce n'est pas celui par défaut
    REVEIL_WORKFLOWS    les fichiers à réveiller, séparés par des virgules.
                        Les deux par défaut. Sur un dépôt privé, se limiter à
                        « edt_sync.yml » divise la dépense par deux.
    REVEIL_DELAI_MIN    âge au-delà duquel on considère un créneau manqué.
                        À garder sous l'intervalle du timer (30 min).
"""

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from telechargement import variable_env

DEPOT = variable_env("GITHUB_DEPOT", "abasse-ali/edt_stri")
JETON = variable_env("GITHUB_TOKEN")

# Les deux, le dépôt étant public : les minutes d'Actions y sont illimitées.
# Sur un dépôt privé, se limiter à « edt_sync.yml » divise la dépense par deux
# pour un gain faible — une échéance de devoir ne se périme pas en trois heures.
WORKFLOWS = [w for w in variable_env("REVEIL_WORKFLOWS",
                                     "edt_sync.yml,rendus_sync.yml")
             .replace(",", " ").split() if w]

# Doit rester SOUS l'intervalle entre deux sonneries du timer, qui est de
# 30 minutes : à 50, le second réveil de chaque heure trouverait toujours un
# passage « récent » et ne ferait rien. Et pas trop bas non plus — les
# exécutions de GitHub ne tombent jamais à la minute demandée, une marge trop
# serrée doublerait celles qu'il honore.
DELAI = timedelta(minutes=int(variable_env("REVEIL_DELAI_MIN", "25")))

TIMEOUT = 30
API = "https://api.github.com"


def _appeler(chemin, corps=None):
    """Appelle l'API GitHub. Rend (code HTTP, données) ; données à None si vide."""
    entetes = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "edt-stri-reveil",
    }
    # Le dépôt est public : LIRE l'historique des exécutions ne demande aucun
    # jeton. Seul le déclenchement en exige un. Cela permet de diagnostiquer la
    # cadence avec `--etat` avant même d'avoir créé le jeton.
    if JETON:
        entetes["Authorization"] = f"Bearer {JETON}"

    requete = urllib.request.Request(
        API + chemin,
        data=json.dumps(corps).encode() if corps is not None else None,
        method="POST" if corps is not None else "GET",
        headers=entetes)
    try:
        with urllib.request.urlopen(requete, timeout=TIMEOUT) as reponse:
            brut = reponse.read()
            return reponse.status, (json.loads(brut) if brut else None)
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        print(f"   ⚠️ Appel impossible ({type(e).__name__}: {e}).")
        return None, None


def dernier_passage(workflow):
    """Date de départ du dernier passage, ou None.

    On regarde le DÉBUT et non la fin : un passage en cours compte, sinon on en
    lancerait un second par-dessus.
    """
    code, donnees = _appeler(
        f"/repos/{DEPOT}/actions/workflows/{workflow}/runs?per_page=1")
    if code != 200 or not donnees:
        if code == 404:
            print(f"   ⚠️ {workflow} introuvable sur {DEPOT}.")
        elif code in (401, 403):
            print(f"   ⚠️ Jeton refusé ({code}) : vérifie la permission "
                  "« Actions » en écriture.")
        return None
    passages = donnees.get("workflow_runs") or []
    if not passages:
        return None
    try:
        return datetime.fromisoformat(
            passages[0]["created_at"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None


def reveiller(workflow, branche="main"):
    """Demande un déclenchement. Rend True si GitHub l'a accepté."""
    code, _ = _appeler(
        f"/repos/{DEPOT}/actions/workflows/{workflow}/dispatches",
        {"ref": branche})
    if code == 204:
        return True
    print(f"   ❌ Déclenchement refusé pour {workflow} (HTTP {code}).")
    if code == 422:
        print("      Le workflow doit déclarer `workflow_dispatch:`.")
    return False


def principale():
    """Réveille ce qui doit l'être. Rend 0 même sans rien faire.

    Un réveil qui ne trouve rien à faire est le cas NORMAL — celui où GitHub a
    honoré sa planification. Rendre un code d'erreur ferait passer le succès
    pour une panne dans les journaux de systemd.
    """
    etat_seulement = "--etat" in sys.argv
    if not JETON and not etat_seulement:
        print("ℹ️ GITHUB_TOKEN absent : rien à réveiller.")
        print("   Settings → Developer settings → Fine-grained tokens,")
        print("   permission « Actions : Read and write » sur ce seul dépôt.")
        print("   « python src/reveil.py --etat » fonctionne sans jeton.")
        return 0
    if not WORKFLOWS:
        print("ℹ️ REVEIL_WORKFLOWS est vide : rien à réveiller.")
        return 0

    forcer = "--forcer" in sys.argv
    maintenant = datetime.now(timezone.utc)

    for workflow in WORKFLOWS:
        depuis = dernier_passage(workflow)
        if depuis is None:
            age = "inconnu"
        else:
            minutes = int((maintenant - depuis).total_seconds() // 60)
            age = f"il y a {minutes // 60} h {minutes % 60:02d}"

        if etat_seulement:
            print(f"   {workflow:<20} dernier passage {age}")
            continue

        if not forcer and depuis is not None and maintenant - depuis < DELAI:
            print(f"   {workflow:<20} passage récent ({age}), on ne réveille pas")
            continue

        if reveiller(workflow):
            print(f"   {workflow:<20} réveillé (dernier passage {age})")

    return 0


if __name__ == "__main__":
    for _flux in (sys.stdout, sys.stderr):
        try:
            _flux.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(principale())

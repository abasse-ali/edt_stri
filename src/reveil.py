"""
Déclenche les workflows GitHub quand la source a changé — et à défaut, rarement.

Le cron d'un workflow demande une exécution par heure. GitHub le documente
franchement : un déclenchement planifié peut être **retardé, voire abandonné**
quand la file d'attente est chargée, et les dépôts gratuits passent en dernier.
Mesuré sur ce dépôt : environ cinq exécutions par jour au lieu de vingt-quatre,
avec des trous allant jusqu'à douze heures.

La première version sonnait deux fois par heure, à l'aveugle. C'était doublement
maladroit : la quasi-totalité des exécutions ne trouvaient rien à faire — les
PDF changent quelques fois par semaine — et malgré cela un changement survenu
juste après une sonnerie attendait la suivante.

Ce script fait l'inverse : il **surveille** les sources, ce qui ne coûte
presque rien, et ne déclenche que sur changement réel.

    HEAD sur les deux PDF        → Last-Modified + Content-Length
    GET sur les exports Moodle   → empreinte du contenu, hors champs volatils

Mesuré le 2 septembre 2026 : les PDF exposent bien `Last-Modified`, mais
répondent 200 à une requête conditionnelle — d'où le HEAD plutôt que
`If-Modified-Since`. Les exports Moodle, eux, sont du PHP : leur `Last-Modified`
vaut « maintenant » à chaque appel et ne dit rien. Seul leur contenu est stable,
une fois `DTSTAMP` et consorts écartés.

    python src/reveil.py            surveille, déclenche si nécessaire
    python src/reveil.py --forcer   déclenche sans condition
    python src/reveil.py --etat     ce qui est vu, sans rien déclencher — le
                                    dépôt étant public, aucun jeton nécessaire

⚠️ Le filet de sécurité reste indispensable. Une exécution périodique ne sert
pas qu'à publier des nouveautés : elle rattrape une publication échouée, répare
un agenda modifié à la main, applique les rappels d'un nouvel inscrit. Surtout,
la signature est enregistrée dès que GitHub accepte le déclenchement, sans
attendre l'issue du workflow : si celui-ci échoue, le changement serait perdu.
D'où REVEIL_FILET_H, qui borne cette perte.

Réglages, dans `.env` :

    GITHUB_TOKEN        jeton à portée réduite, permission « Actions : write »
                        sur ce seul dépôt (Settings → Developer settings →
                        Fine-grained tokens)
    GITHUB_DEPOT        « proprietaire/depot », si ce n'est pas celui par défaut
    REVEIL_WORKFLOWS    les fichiers à réveiller, séparés par des virgules
    REVEIL_FILET_H      heures au-delà desquelles on déclenche même sans
                        changement. Ne pas monter trop haut : c'est le délai
                        maximal de rattrapage d'un workflow en échec.
"""

import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import chemins
import moodle
import telechargement
from telechargement import variable_env

DEPOT = variable_env("GITHUB_DEPOT", "abasse-ali/edt_stri")
JETON = variable_env("GITHUB_TOKEN")

WORKFLOWS = [w for w in variable_env("REVEIL_WORKFLOWS",
                                     "edt_sync.yml,rendus_sync.yml")
             .replace(",", " ").split() if w]

# Ce que chaque workflow lit, donc ce qu'il faut surveiller pour lui. Un
# workflow absent de cette table n'est déclenché que par le filet de sécurité.
SURVEILLE = {
    "edt_sync.yml": "pdf",
    "rendus_sync.yml": "moodle",
}

# Sans changement détecté, on déclenche quand même de temps en temps. Voir
# l'avertissement de l'en-tête : ce n'est pas une précaution décorative.
FILET = timedelta(hours=int(variable_env("REVEIL_FILET_H", "6")))

# Les signatures observées la dernière fois. Leur perte est sans gravité : elle
# provoque un déclenchement de plus, pas un oubli.
FICHIER_SIGNATURES = chemins.donnee("reveil_signatures.json")

# Champs régénérés à chaque export Moodle, sans rapport avec le contenu.
# Les garder ferait changer l'empreinte à chaque appel, et déclencher sans fin.
VOLATILS = re.compile(r"^(DTSTAMP|PRODID|CREATED|LAST-MODIFIED):.*$", re.M)

TIMEOUT = 30
API = "https://api.github.com"


# --- L'API GitHub ----------------------------------------------------------

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
    """Rend (départ du dernier passage, s'il est encore en cours).

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
        return None, False
    passages = donnees.get("workflow_runs") or []
    if not passages:
        return None, False
    try:
        depart = datetime.fromisoformat(
            passages[0]["created_at"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None, False
    return depart, passages[0].get("status") != "completed"


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


# --- La surveillance des sources -------------------------------------------

def _signature_pdf():
    """Empreinte des PDF d'emploi du temps, sans les télécharger. None si échec.

    Un HEAD suffit : le serveur annonce Last-Modified et Content-Length. La
    taille accompagne la date car une réécriture à la même seconde reste
    concevable, et parce qu'elle ne coûte rien de plus.
    """
    marques = []
    for cle, promo in sorted(telechargement.PROMOS.items()):
        requete = urllib.request.Request(
            promo["url"], method="HEAD",
            headers={"User-Agent": "edt-stri-reveil"})
        try:
            with urllib.request.urlopen(requete, timeout=TIMEOUT) as reponse:
                marques.append("{}:{}:{}".format(
                    cle,
                    reponse.headers.get("Last-Modified", ""),
                    reponse.headers.get("Content-Length", "")))
        except Exception as e:
            print(f"   ⚠️ PDF {cle} injoignable ({type(e).__name__}).")
            return None
    return hashlib.sha256("|".join(marques).encode()).hexdigest()


def _signature_moodle():
    """Empreinte des exports Moodle configurés. None si l'un échoue.

    Ils sont petits — moins de 25 ko à eux tous — et leur Last-Modified ne dit
    rien, étant régénéré à chaque appel. On lit donc le contenu, débarrassé des
    champs volatils.
    """
    empreintes = []
    for cle, config in sorted(moodle.SOURCES.items()):
        adresse = variable_env(config["variable"])
        if not adresse:
            continue          # source non configurée ici : rien à surveiller
        requete = urllib.request.Request(
            moodle.imposer_preset(adresse, config.get("preset_what")),
            headers={"User-Agent": "edt-stri-reveil"})
        try:
            with urllib.request.urlopen(requete, timeout=TIMEOUT) as reponse:
                texte = reponse.read().decode("utf-8", "replace")
        except Exception as e:
            print(f"   ⚠️ Export « {config['nom']} » injoignable "
                  f"({type(e).__name__}).")
            return None
        empreintes.append(cle + ":" + VOLATILS.sub("", texte))
    if not empreintes:
        return None           # aucune adresse ici : le filet prendra le relais
    return hashlib.sha256("|".join(empreintes).encode()).hexdigest()


def signature(quoi):
    """L'empreinte d'un type de source, ou None si on n'a pas pu la calculer."""
    if quoi == "pdf":
        return _signature_pdf()
    if quoi == "moodle":
        return _signature_moodle()
    return None


def _lire_signatures():
    try:
        return json.loads(FICHIER_SIGNATURES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _ecrire_signatures(signatures):
    try:
        FICHIER_SIGNATURES.write_text(
            json.dumps(signatures, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except OSError as e:
        # Sans enregistrement, le même changement serait redétecté à chaque
        # passage et déclencherait en boucle : il faut le dire, pas le subir.
        print(f"   ⚠️ {FICHIER_SIGNATURES.name} non enregistré ({e}) : "
              "le même changement sera redétecté au prochain passage.")


# --- Le programme ----------------------------------------------------------

def _age(depuis, maintenant):
    if depuis is None:
        return "inconnu"
    minutes = int((maintenant - depuis).total_seconds() // 60)
    return f"il y a {minutes // 60} h {minutes % 60:02d}"


def principale():
    """Réveille ce qui doit l'être. Rend 0 même sans rien faire.

    Un réveil qui ne trouve rien à faire est le cas NORMAL — c'est même
    devenu le cas très majoritaire. Rendre un code d'erreur ferait passer le
    succès pour une panne dans les journaux de systemd.
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
    connues = _lire_signatures()
    a_enregistrer = dict(connues)

    for workflow in WORKFLOWS:
        depuis, en_cours = dernier_passage(workflow)
        age = _age(depuis, maintenant)

        quoi = SURVEILLE.get(workflow)
        vue = signature(quoi) if quoi else None
        change = vue is not None and vue != connues.get(workflow)

        if etat_seulement:
            if vue is None:
                source = "source non surveillée" if not quoi else "source illisible"
            else:
                source = "source CHANGÉE" if change else "source inchangée"
            print(f"   {workflow:<20} dernier passage {age:<16} {source}"
                  + ("  (en cours)" if en_cours else ""))
            continue

        if forcer:
            raison = "forcé"
        elif en_cours:
            # Ne pas empiler une exécution sur une autre. La signature n'est
            # pas enregistrée : on redétectera le changement au prochain
            # passage, une fois celle-ci terminée.
            print(f"   {workflow:<20} déjà en cours, on attend")
            continue
        elif change:
            raison = "source modifiée"
        elif depuis is None or maintenant - depuis > FILET:
            raison = f"filet de sécurité, dernier passage {age}"
        else:
            print(f"   {workflow:<20} rien de neuf ({age})")
            continue

        if reveiller(workflow):
            print(f"   {workflow:<20} réveillé — {raison}")
            if vue is not None:
                a_enregistrer[workflow] = vue

    if not etat_seulement and a_enregistrer != connues:
        _ecrire_signatures(a_enregistrer)

    return 0


if __name__ == "__main__":
    for _flux in (sys.stdout, sys.stderr):
        try:
            _flux.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(principale())

"""
Horaires réels du réseau Tisséo, lus dans son GTFS — sans clé ni quota.

Le GTFS est l'archive d'horaires que Tisséo publie sur le portail open data de
Toulouse Métropole. Elle contient les passages réels de chaque course, ce que
ni une durée moyenne ni une API à clé ne donnent gratuitement.

Le calcul n'est pas un calculateur d'itinéraires généraliste : il suit un
trajet CONNU, décrit dans `ITINERAIRES`, et cherche le dernier départ qui
arrive à l'heure. C'est très largement suffisant pour un trajet quotidien, et
ça évite d'écrire un moteur de routage.

Le raisonnement se fait à rebours, depuis l'heure d'arrivée voulue :

    arrivée en cours 07h45
      - marche depuis l'arrêt          -> arrivée à Sports Universitaires 07h35
      - dernier bus 37 arrivant avant  -> départ de Jolimont 06h53
      - dernier métro arrivant avant   -> départ de Mermoz 06h27
      - marche jusqu'à l'arrêt         -> sortie de chez soi 06h24

Les temps de marche viennent des coordonnées, avec un facteur de détour : une
distance à vol d'oiseau sous-estime toujours le chemin réel.
"""

import csv
import io
import sys
import time
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import requests

# L'archive change rarement ; on la retélécharge au-delà de cet âge.
URL_GTFS = ("https://data.toulouse-metropole.fr/api/explore/v2.1/catalog/"
            "datasets/tisseo-gtfs/files/fc1dda89077cf37e4f7521760e0ef4e9")
FICHIER_GTFS = Path("tisseo_gtfs.zip")
AGE_MAX_JOURS = 14

# Vitesse de marche et facteur de détour : à vol d'oiseau, le chemin réel est
# environ 30 % plus long.
VITESSE_MARCHE = 1.111  # m/s, soit 4 km/h
DETOUR = 1.3

# Itinéraires possibles, essayés tous et départagés par l'heure de sortie la
# plus tardive. Chaque étape est (ligne, arrêt de montée, arrêt de descente).
ITINERAIRES = [
    {"nom": "métro A + bus 37",
     "etapes": [("A", "Mermoz", "Jolimont"),
                ("37", "Jolimont", "Sports Universitaires")]},
    {"nom": "métro A + bus 27",
     "etapes": [("A", "Mermoz", "Marengo-SNCF"),
                ("27", "Riquet", "Sports Universitaires")]},
]

# Minutes de battement exigées à chaque correspondance : descendre du métro,
# rejoindre le quai du bus, ne pas courir. S'ajoute au temps de marche quand la
# correspondance change d'arrêt — Marengo-SNCF et Riquet sont à 200 m l'un de
# l'autre, les ignorer ferait rater le bus.
CORRESPONDANCE = 4

_CACHE = {}


# =====================================================================
# ARCHIVE
# =====================================================================

def archive(chemin=FICHIER_GTFS, forcer=False):
    """Chemin de l'archive GTFS, téléchargée si absente ou périmée."""
    chemin = Path(chemin)
    if not forcer and chemin.exists():
        age = (time.time() - chemin.stat().st_mtime) / 86400
        if age < AGE_MAX_JOURS:
            return chemin
        print(f"   GTFS vieux de {age:.0f} jours, actualisation...")
    else:
        print("   Téléchargement du GTFS Tisséo (~19 Mo)...")

    reponse = requests.get(URL_GTFS, timeout=180,
                           headers={"User-Agent": "edt-stri/1.0"})
    reponse.raise_for_status()
    if not reponse.content.startswith(b"PK"):
        raise RuntimeError("le fichier téléchargé n'est pas une archive ZIP")
    chemin.write_bytes(reponse.content)
    print(f"   GTFS enregistré ({len(reponse.content) // 1024} Ko).")
    return chemin


def _lire(zf, nom):
    return csv.DictReader(io.TextIOWrapper(zf.open(nom), "utf-8-sig"))


# =====================================================================
# INDEXATION
# =====================================================================

def indexer(dates, chemin=FICHIER_GTFS):
    """Prépare tout ce qu'il faut pour les `dates` données.

    `stop_times.txt` fait 93 Mo : il n'est parcouru qu'UNE fois, en ne gardant
    que les lignes et les arrêts de `ITINERAIRES`. Le résultat est mis en cache
    pour que préparer vingt jours ne relise pas l'archive vingt fois.
    """
    dates = frozenset(dates)
    if dates in _CACHE:
        return _CACHE[dates]

    lignes_utiles = {e[0] for i in ITINERAIRES for e in i["etapes"]}
    arrets_utiles = {n for i in ITINERAIRES for e in i["etapes"] for n in e[1:]}

    with zipfile.ZipFile(archive(chemin)) as zf:
        # calendar.txt est vide chez Tisséo : tout passe par calendar_dates.
        services = defaultdict(set)
        couverture = []
        for r in _lire(zf, "calendar_dates.txt"):
            if r["exception_type"] != "1":
                continue
            couverture.append(r["date"])
            if r["date"] in dates:
                services[r["date"]].add(r["service_id"])

        routes = {r["route_id"]: r["route_short_name"]
                  for r in _lire(zf, "routes.txt")
                  if r["route_short_name"] in lignes_utiles}

        courses = {}
        for r in _lire(zf, "trips.txt"):
            if r["route_id"] in routes:
                courses[r["trip_id"]] = (routes[r["route_id"]], r["service_id"])

        zones, coord_arret = {}, {}
        for r in _lire(zf, "stops.txt"):
            if r["stop_name"] in arrets_utiles:
                zones[r["stop_id"]] = r["stop_name"]
                coord_arret.setdefault(r["stop_name"],
                                       (float(r["stop_lat"]), float(r["stop_lon"])))

        passages = defaultdict(list)
        for r in _lire(zf, "stop_times.txt"):
            if r["trip_id"] in courses and r["stop_id"] in zones:
                passages[r["trip_id"]].append(
                    (int(r["stop_sequence"]), zones[r["stop_id"]],
                     r["arrival_time"], r["departure_time"]))

    # Par (ligne, montée, descente) : la liste des (départ, arrivée, service).
    liaisons = defaultdict(list)
    for trip, arrets in passages.items():
        ligne, service = courses[trip]
        arrets.sort()
        noms = [a[1] for a in arrets]
        for i, depart in enumerate(arrets):
            for arrivee in arrets[i + 1:]:
                liaisons[(ligne, depart[1], arrivee[1])].append(
                    (depart[3], arrivee[2], service))

    for cle in liaisons:
        liaisons[cle].sort()

    # Un GTFS ne couvre qu'une fenêtre glissante — cinq semaines chez Tisséo.
    # Au-delà, il faut le dire plutôt que d'annoncer « aucun itinéraire ».
    _CACHE[dates] = {"liaisons": dict(liaisons), "services": dict(services),
                     "coord": coord_arret,
                     "couverture": (min(couverture), max(couverture)) if couverture
                                   else (None, None)}
    return _CACHE[dates]


# =====================================================================
# CALCUL
# =====================================================================

def _minutes(hhmmss):
    """« 26:03:00 » -> 1563. Le GTFS dépasse 24h pour les services de nuit."""
    h, m, s = (int(x) for x in hhmmss.split(":"))
    return h * 60 + m + (1 if s >= 30 else 0)


def marche(depuis, vers):
    """Minutes de marche entre deux points (lat, lon), détour compris."""
    rayon = 6371000
    la1, lo1 = radians(depuis[0]), radians(depuis[1])
    la2, lo2 = radians(vers[0]), radians(vers[1])
    h = sin((la2 - la1) / 2) ** 2 + cos(la1) * cos(la2) * sin((lo2 - lo1) / 2) ** 2
    metres = 2 * rayon * asin(sqrt(h)) * DETOUR
    return max(1, round(metres / VITESSE_MARCHE / 60))


def _dernier_depart(index, jour, ligne, depuis, vers, arriver_avant):
    """Départ le plus tardif de `depuis` arrivant à `vers` avant la limite."""
    actifs = index["services"].get(jour, set())
    candidats = [(depart, arrivee)
                 for depart, arrivee, service in
                 index["liaisons"].get((ligne, depuis, vers), [])
                 if service in actifs and _minutes(arrivee) <= arriver_avant]
    if not candidats:
        return None
    depart, arrivee = max(candidats)
    return _minutes(depart), _minutes(arrivee)


def planifier(arrivee, domicile, destination, chemin=FICHIER_GTFS):
    """Heure de sortie du domicile pour être à `destination` à `arrivee`.

    Renvoie (minutes de trajet total, description) ou (None, raison).
    """
    jour = arrivee.strftime("%Y%m%d")
    index = indexer([jour], chemin)
    # Les derniers jours d'un GTFS ne portent qu'une poignée de services
    # résiduels : les traiter comme couverts produirait des horaires absurdes.
    if len(index["services"].get(jour, ())) < 100:
        debut, fin = index["couverture"]
        if debut and not (debut <= jour <= fin):
            return None, (f"hors couverture du GTFS "
                          f"({debut[6:]}/{debut[4:6]} au {fin[6:]}/{fin[4:6]})")
        return None, f"aucun service au {arrivee:%d/%m} dans le GTFS"

    limite = arrivee.hour * 60 + arrivee.minute
    meilleures = []

    for itineraire in ITINERAIRES:
        etapes = itineraire["etapes"]
        premier_arret = index["coord"].get(etapes[0][1])
        dernier_arret = index["coord"].get(etapes[-1][2])
        if not (premier_arret and dernier_arret):
            continue

        instant = limite - marche(dernier_arret, destination)
        detail = []
        echec = False
        for numero, (ligne, depuis, vers) in reversed(list(enumerate(etapes))):
            trouve = _dernier_depart(index, jour, ligne, depuis, vers, instant)
            if trouve is None:
                echec = True
                break
            depart, fin = trouve
            detail.append(f"{ligne} {depuis} {depart // 60:02d}h{depart % 60:02d}"
                          f" → {vers} {fin // 60:02d}h{fin % 60:02d}")
            if numero:
                # Arriver au quai de cette étape suppose d'avoir quitté la
                # précédente, à pied si la correspondance change d'arrêt.
                descente = etapes[numero - 1][2]
                trajet_a_pied = (0 if descente == depuis else
                                 marche(index["coord"][descente], index["coord"][depuis]))
                instant = depart - CORRESPONDANCE - trajet_a_pied
            else:
                instant = depart
        if echec:
            continue

        sortie = instant - marche(domicile, premier_arret)
        meilleures.append((sortie, itineraire["nom"], list(reversed(detail))))

    if not meilleures:
        return None, "aucun itinéraire n'arrive à l'heure"

    sortie, nom, detail = max(meilleures)
    return limite - sortie, f"{nom} — " + ", puis ".join(detail)

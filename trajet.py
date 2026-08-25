"""
Réveil et départ calculés depuis l'agenda et le temps de trajet réel.

Pour chaque journée qui commence par un cours, le script écrit deux événements
dans un agenda dédié :

    Réveil 06h20   — le raccourci iOS le lit pour programmer une vraie alarme
    Départ 07h05   — de l'heure de départ à l'arrivée, adresse en lieu

Aucun script ne peut créer une alarme sur un téléphone : ni iOS ni Android
n'exposent d'API pour l'app Horloge. L'événement « Réveil » est donc la donnée
d'entrée d'une automatisation Raccourcis qui, elle, tourne sur l'iPhone.

Le temps de trajet vient de Navitia par défaut : jeton gratuit sans carte
bancaire, et il couvre le réseau Tisséo via les données nationales de
transport.data.gouv.fr. TRAJET_FOURNISSEUR bascule vers « tisseo » ou
« google » sans rien réinstaller.

Dans tous les cas la requête porte l'heure d'ARRIVÉE voulue : en transports en
commun, c'est elle qui détermine le passage à prendre, pas une durée moyenne.
Sans clé, une durée fixe prend le relais et le script reste utilisable.

    python trajet.py            -> prépare les prochains jours
    python trajet.py --essai    -> affiche le calcul sans rien écrire
    python trajet.py --diagnostic -> teste la clé API et explique tout refus
"""

import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

import google_agenda
import gtfs
from telechargement import variable_env

for _flux in (sys.stdout, sys.stderr):
    try:
        _flux.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

FUSEAU = ZoneInfo("Europe/Paris")

# --- Configuration (dans .env, jamais versionnée : c'est une adresse perso) ---
DOMICILE = variable_env("TRAJET_DOMICILE")
UNIVERSITE = variable_env("TRAJET_UNIVERSITE")
CLE_MAPS = variable_env("GOOGLE_MAPS_API_KEY")

# Minutes entre le réveil et le départ effectif.
PREPARATION = int(variable_env("TRAJET_PREPARATION", "45"))
# Tout part de l'heure d'ARRIVÉE voulue, qui est l'heure de début du premier
# cours. `TRAJET_MARGE` permet d'arriver en avance : à 0, on vise l'heure du
# cours à la minute près.
MARGE = int(variable_env("TRAJET_MARGE", "0"))
# Durée retenue quand l'API n'est pas joignable ou pas configurée.
DUREE_SECOURS = int(variable_env("TRAJET_DUREE_SECOURS", "40"))
# Horizon de préparation.
JOURS = int(variable_env("TRAJET_JOURS", "14"))

NOM_AGENDA = variable_env("TRAJET_AGENDA", "STRI — Trajet")
CLE_MARQUEUR = "TRAJET"
COULEUR = variable_env("TRAJET_COULEUR", "myrtille")

# Fournisseur : « gtfs », « navitia », « tisseo » ou « google ».
#
# GTFS par défaut : les horaires publiés par Tisséo lui-même, sans clé, sans
# quota et sans inscription. Il suit un itinéraire connu (voir gtfs.py) au
# lieu de calculer un trajet quelconque, ce qui suffit largement pour un
# trajet quotidien et donne les passages réels plutôt qu'une moyenne.
#
# Les trois autres restent disponibles : navitia (jeton gratuit en libre-
# service), tisseo (clé sur demande à opendata@tisseo.fr) et google (API
# Routes, facturation requise).
FOURNISSEUR = variable_env("TRAJET_FOURNISSEUR", "gtfs").lower()
CLE_NAVITIA = variable_env("NAVITIA_TOKEN")
CLE_TISSEO = variable_env("TISSEO_API_KEY")

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
TISSEO_URL = "https://api.tisseo.fr/v2"
# Tisséo ne sait partir que d'une heure de DÉPART : on balaie cette fenêtre
# avant l'heure d'arrivée voulue pour trouver le dernier départ qui convient.
FENETRE_TISSEO = int(variable_env("TRAJET_FENETRE", "120"))
TIMEOUT_HTTP = 20

# Coordonnées résolues une fois par exécution : une vingtaine de jours à
# préparer ne doit pas déclencher quarante géocodages identiques.
_COORDONNEES = {}


# =====================================================================
# TEMPS DE TRAJET
# =====================================================================

def duree_trajet(arrivee):
    """Durée domicile -> université pour arriver à `arrivee`, en minutes.

    Renvoie (minutes, source). C'est l'heure d'ARRIVÉE qui est envoyée, jamais
    une heure de départ : en transports en commun, arriver à 7h45 ne se déduit
    pas d'une durée moyenne, il faut le passage qui convient.
    """
    if not (DOMICILE and UNIVERSITE):
        return DUREE_SECOURS, "durée fixe (adresses non configurées)"

    # Le GTFS ne demande aucune clé : il est traité à part.
    if FOURNISSEUR == "gtfs":
        return _gtfs(arrivee)

    calculs = {"navitia": (_navitia, CLE_NAVITIA),
               "tisseo": (_tisseo, CLE_TISSEO),
               "google": (_google, CLE_MAPS)}
    if FOURNISSEUR not in calculs:
        return DUREE_SECOURS, f"durée fixe (fournisseur « {FOURNISSEUR} » inconnu)"
    calcul, cle = calculs[FOURNISSEUR]
    if not cle:
        return DUREE_SECOURS, f"durée fixe ({FOURNISSEUR} sans clé)"
    return calcul(arrivee)


def _gtfs(arrivee):
    """Horaires réels Tisséo, sans clé. Voir gtfs.py."""
    try:
        minutes, detail = gtfs.planifier(arrivee, _point(DOMICILE), _point(UNIVERSITE))
        if minutes is None:
            return DUREE_SECOURS, f"durée fixe ({detail})"
        return minutes, detail
    except Exception as e:
        print(f"   ⚠️ GTFS indisponible ({str(e)[:110]}).")
        return DUREE_SECOURS, "durée fixe (GTFS en échec)"


def cle_du_fournisseur():
    """(nom de la variable, valeur) de la clé attendue par le fournisseur actif."""
    return {"gtfs": ("(aucune clé requise)", "sans objet"),
            "navitia": ("NAVITIA_TOKEN", CLE_NAVITIA),
            "tisseo": ("TISSEO_API_KEY", CLE_TISSEO),
            "google": ("GOOGLE_MAPS_API_KEY", CLE_MAPS)}.get(
                FOURNISSEUR, ("(fournisseur inconnu)", ""))


def _point(adresse):
    """Coordonnées (latitude, longitude) d'une adresse, mises en cache.

    « 43.6045,1.4442 » est pris tel quel — c'est l'ordre affiché par Google
    Maps. Sinon l'adresse est cherchée dans l'annuaire de lieux du fournisseur.
    """
    if adresse in _COORDONNEES:
        return _COORDONNEES[adresse]

    morceaux = adresse.replace(" ", "").split(",")
    if len(morceaux) == 2:
        try:
            _COORDONNEES[adresse] = (float(morceaux[0]), float(morceaux[1]))
            return _COORDONNEES[adresse]
        except ValueError:
            pass

    if FOURNISSEUR == "navitia":
        reponse = requests.get("https://api.navitia.io/v1/places", timeout=TIMEOUT_HTTP,
                               auth=(CLE_NAVITIA, ""), params={"q": adresse, "count": 1})
        cles = ("places",)
    else:
        reponse = requests.get(f"{TISSEO_URL}/places.json", timeout=TIMEOUT_HTTP,
                               params={"key": CLE_TISSEO, "term": adresse})
        cles = ("places", "place")

    if reponse.status_code != 200:
        raise RuntimeError(f"géocodage refusé ({reponse.status_code} : "
                           f"{reponse.text[:90]})")
    lieux = _premiere_liste(reponse.json(), cles)
    if not lieux:
        raise RuntimeError(f"adresse introuvable : « {adresse} »")

    coord = _chercher_coord(lieux[0])
    if coord is None:
        raise RuntimeError(f"lieu sans coordonnées : {str(lieux[0])[:110]}")
    _COORDONNEES[adresse] = coord
    return coord


def _chercher_coord(objet):
    """Repère un couple (lat, lon) dans une structure, quel que soit son chemin.

    Navitia niche les coordonnées sous le type du lieu, Tisséo les met à plat :
    on cherche plutôt que de figer un chemin qui changerait au premier
    changement d'API.
    """
    if isinstance(objet, dict):
        lat = objet.get("lat") or objet.get("y") or objet.get("latitude")
        lon = objet.get("lon") or objet.get("x") or objet.get("longitude")
        if lat is not None and lon is not None:
            try:
                return float(lat), float(lon)
            except (TypeError, ValueError):
                pass
        for valeur in objet.values():
            trouve = _chercher_coord(valeur)
            if trouve:
                return trouve
    return None


def _premiere_liste(donnees, cles):
    """Première liste non vide sous l'une des clés, à n'importe quel niveau."""
    if isinstance(donnees, list):
        return donnees
    if not isinstance(donnees, dict):
        return []
    for cle in cles:
        if isinstance(donnees.get(cle), list) and donnees[cle]:
            return donnees[cle]
    for valeur in donnees.values():
        trouve = _premiere_liste(valeur, cles)
        if trouve:
            return trouve
    return []


# --- Navitia -----------------------------------------------------------------
# Jeton gratuit sans facturation, couvre le réseau Tisséo via les données
# nationales. Coordonnées attendues en LONGITUDE;LATITUDE.

def _navitia(arrivee):
    try:
        params = {
            "from": "{1};{0}".format(*_point(DOMICILE)),
            "to": "{1};{0}".format(*_point(UNIVERSITE)),
            "datetime": arrivee.strftime("%Y%m%dT%H%M%S"),
            "datetime_represents": "arrival",
            "count": 1,
        }
        reponse = requests.get("https://api.navitia.io/v1/journeys", params=params,
                               auth=(CLE_NAVITIA, ""), timeout=TIMEOUT_HTTP)
        if reponse.status_code != 200:
            try:
                detail = reponse.json().get("error", {}).get("message", "")
            except Exception:
                detail = reponse.text[:120]
            print(f"   ⚠️ Navitia : {reponse.status_code} — {detail[:140]}")
            return DUREE_SECOURS, "durée fixe (Navitia a refusé)"

        trajets = reponse.json().get("journeys") or []
        if not trajets:
            return DUREE_SECOURS, "durée fixe (aucun itinéraire)"
        secondes = trajets[0].get("duration")
        if secondes is None:
            print(f"   ⚠️ Réponse Navitia inattendue : {str(trajets[0])[:160]}")
            return DUREE_SECOURS, "durée fixe (réponse illisible)"
        return max(1, round(int(secondes) / 60)), "Navitia"
    except Exception as e:
        print(f"   ⚠️ Navitia indisponible ({str(e)[:110]}).")
        return DUREE_SECOURS, "durée fixe (Navitia en échec)"


# --- Tisséo ------------------------------------------------------------------
# Conservé si tu obtiens une clé : c'est le réseau toulousain à la source.
# Coordonnées attendues en LONGITUDE,LATITUDE.

def _tisseo(arrivee):
    """Itinéraire Tisséo arrivant au plus tard à `arrivee`.

    L'API ne connaît QUE l'heure de départ : il n'existe pas de paramètre
    d'arrivée (doc v2 du 21/05/2025, § 4.9.2). On demande donc les huit
    prochaines solutions à partir de `arrivee - FENETRE_TISSEO`, puis on retient
    celle qui part le plus tard tout en arrivant à temps.

    La durée rendue est l'écart entre ce départ et l'heure voulue : elle inclut
    donc l'attente à l'arrêt, ce que la seule durée du trajet ignorerait.
    """
    try:
        params = {
            "key": CLE_TISSEO,
            # § 3.4 : coordonnées en WGS84, LONGITUDE puis LATITUDE.
            "departurePlaceXY": "{1},{0}".format(*_point(DOMICILE)),
            "arrivalPlaceXY": "{1},{0}".format(*_point(UNIVERSITE)),
            "firstDepartureDatetime":
                (arrivee - timedelta(minutes=FENETRE_TISSEO)).strftime("%Y-%m-%d %H:%M"),
            "number": 8,
        }
        reponse = requests.get(f"{TISSEO_URL}/journeys.json", params=params,
                               timeout=TIMEOUT_HTTP)
        if reponse.status_code != 200:
            print(f"   ⚠️ Tisséo : {reponse.status_code} — {reponse.text[:120]}")
            return DUREE_SECOURS, "durée fixe (Tisséo a refusé)"

        solutions = []
        for entree in _premiere_liste(reponse.json(), ("journeys",)):
            trajet = entree.get("journey", entree) if isinstance(entree, dict) else {}
            depart = _instant_tisseo(trajet.get("departureDateTime"))
            fin_trajet = _instant_tisseo(trajet.get("arrivalDateTime"))
            if depart and fin_trajet:
                solutions.append((depart, fin_trajet))

        if not solutions:
            return DUREE_SECOURS, "durée fixe (aucun itinéraire)"

        a_temps = [s for s in solutions if s[1] <= arrivee]
        if a_temps:
            depart = max(a_temps)[0]
            return max(1, round((arrivee - depart).total_seconds() / 60)), "Tisséo"

        # Aucune solution n'arrive à l'heure : on prend la plus tôt et on le dit,
        # plutôt que de laisser croire à un horaire tenable.
        depart, fin_trajet = min(solutions)
        retard = round((fin_trajet - arrivee).total_seconds() / 60)
        print(f"   ⚠️ Tisséo : aucune solution avant {arrivee:%Hh%M}, "
              f"la plus tôt arrive {retard} min trop tard.")
        return max(1, round((fin_trajet - depart).total_seconds() / 60)), "Tisséo (en retard)"
    except Exception as e:
        print(f"   ⚠️ Tisséo indisponible ({str(e)[:110]}).")
        return DUREE_SECOURS, "durée fixe (Tisséo en échec)"


def _instant_tisseo(texte):
    """« 2014-12-10 18:11:25 » -> datetime. Format confirmé par la doc § 4.9.4.2."""
    if not texte:
        return None
    for forme in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(texte, forme).replace(tzinfo=FUSEAU)
        except ValueError:
            continue
    try:
        moment = datetime.fromisoformat(texte)
        return moment if moment.tzinfo else moment.replace(tzinfo=FUSEAU)
    except ValueError:
        return None


# --- Google Routes -----------------------------------------------------------

def _google(arrivee):
    corps = {
        "origin": {"address": DOMICILE},
        "destination": {"address": UNIVERSITE},
        "travelMode": "TRANSIT",
        "arrivalTime": arrivee.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "computeAlternativeRoutes": False,
        "languageCode": "fr-FR",
        "units": "METRIC",
    }
    try:
        reponse = requests.post(
            ROUTES_URL, json=corps, timeout=TIMEOUT_HTTP,
            headers={"X-Goog-Api-Key": CLE_MAPS,
                     "X-Goog-FieldMask": "routes.duration"},
        )
        if reponse.status_code != 200:
            print(f"   ⚠️ API Routes : {_expliquer(reponse)}")
            return DUREE_SECOURS, "durée fixe (API refusée)"
        routes = reponse.json().get("routes") or []
        if not routes:
            return DUREE_SECOURS, "durée fixe (aucun itinéraire trouvé)"
        secondes = int(str(routes[0]["duration"]).rstrip("s"))
        return max(1, round(secondes / 60)), "Google Routes"
    except Exception as e:
        print(f"   ⚠️ API Routes injoignable ({str(e)[:90]}).")
        return DUREE_SECOURS, "durée fixe (API en échec)"


def _expliquer(reponse):
    """Traduit le refus de l'API en cause concrète et en geste à faire."""
    try:
        erreur = reponse.json().get("error", {})
    except Exception:
        return f"HTTP {reponse.status_code} — {reponse.text[:120]}"

    message = erreur.get("message", "")
    statut = erreur.get("status", "")
    causes = [
        ("has not been used in project", "l'API Routes n'est pas activée sur le projet."
         " Console → API et services → Bibliothèque → Routes API → Activer."),
        ("billing", "la facturation n'est pas activée sur le projet."
         " Console → Facturation → Associer un compte."),
        ("API key not valid", "la clé est invalide ou mal recopiée."),
        ("not authorized to use this API", "la clé est restreinte à d'autres API."
         " Console → Identifiants → ta clé → Restrictions relatives aux API →"
         " autoriser Routes API."),
        ("referer", "la clé est restreinte à des sites web."
         " Une clé appelée depuis un script ne doit avoir aucune restriction"
         " d'application, ou une restriction par adresse IP."),
    ]
    for motif, explication in causes:
        if motif.lower() in message.lower():
            return explication
    return f"{statut or reponse.status_code} — {message[:160]}"


def diagnostic():
    """Une requête, un verdict. `python trajet.py --diagnostic`."""
    print(f"🔎 Diagnostic du calcul de trajet — fournisseur « {FOURNISSEUR} »")

    nom_cle, cle = cle_du_fournisseur()
    champs = [("TRAJET_DOMICILE", DOMICILE, False),
              ("TRAJET_UNIVERSITE", UNIVERSITE, False)]
    if FOURNISSEUR == "gtfs":
        print("   horaires              archive GTFS Tisséo, aucune clé requise")
    else:
        champs.append((nom_cle, cle, True))

    for nom, valeur, secrete in champs:
        if not valeur:
            etat = "ABSENTE"
        elif secrete:
            etat = f"définie ({len(valeur)} caractères)"
        else:
            etat = f"« {valeur} »"
        print(f"   {nom:22s} {etat}")

    if not (DOMICILE and UNIVERSITE and cle):
        print("   → Complète ces trois variables dans .env avant d'aller plus loin.")
        return 1

    for nom, adresse in (("domicile", DOMICILE), ("université", UNIVERSITE)):
        try:
            lat, lon = _point(adresse)
            print(f"   {nom:11s} → latitude {lat}, longitude {lon}")
        except Exception as e:
            print(f"   ❌ {nom} : {e}")
            return 1

    demain = datetime.now(FUSEAU) + timedelta(days=1)
    cible = demain.replace(hour=8, minute=0, second=0, microsecond=0)
    minutes, source = duree_trajet(cible)
    if not source.startswith("durée fixe"):
        depart = cible - timedelta(minutes=minutes)
        print(f"   ✅ {minutes} min via {source} : partir à {depart:%Hh%M} "
              f"pour arriver à {cible:%d/%m %Hh%M}.")
        return 0
    print(f"   ❌ Pas de donnée réelle. Repli sur {minutes} min ({source}).")
    return 1


# =====================================================================
# LECTURE DE L'AGENDA
# =====================================================================

def premiers_cours(service, jours=JOURS, cle_agenda="BAS"):
    """Premier événement de chaque journée, cours et examens confondus."""
    debut = datetime.now(FUSEAU).replace(hour=0, minute=0, second=0, microsecond=0)
    fin = debut + timedelta(days=jours)

    agendas = []
    for cle in (cle_agenda, f"{cle_agenda}-EXAMENS"):
        etiquette = google_agenda.marqueur(cle)
        for a in service.calendarList().list().execute().get("items", []):
            if etiquette in (a.get("description") or ""):
                agendas.append(a["id"])

    if not agendas:
        raise RuntimeError(f"Aucun agenda portant {google_agenda.marqueur(cle_agenda)}.")

    par_jour = {}
    for agenda_id in agendas:
        page = service.events().list(
            calendarId=agenda_id, singleEvents=True, orderBy="startTime",
            timeMin=debut.isoformat(), timeMax=fin.isoformat(), maxResults=2500,
        ).execute()
        for evt in page.get("items", []):
            depart = evt.get("start", {}).get("dateTime")
            if not depart:
                continue  # journée entière : pas d'heure exploitable
            moment = datetime.fromisoformat(depart).astimezone(FUSEAU)
            jour = moment.date()
            if jour not in par_jour or moment < par_jour[jour][0]:
                par_jour[jour] = (moment, evt.get("summary", "Cours"),
                                  evt.get("location") or "")
    return dict(sorted(par_jour.items()))


# =====================================================================
# ÉCRITURE DES ÉVÉNEMENTS
# =====================================================================

def _identifiant(prefixe, jour):
    import hashlib
    return hashlib.md5(f"trajet|{prefixe}|{jour}".encode("utf-8")).hexdigest()


def _construire(jour, premier, minutes, source):
    """Les deux événements d'une journée, prêts pour l'API."""
    debut_cours, titre_cours, salle = premier
    arrivee = debut_cours - timedelta(minutes=MARGE)
    depart = arrivee - timedelta(minutes=minutes)
    reveil = depart - timedelta(minutes=PREPARATION)

    def horaire(m):
        return {"dateTime": m.isoformat(), "timeZone": "Europe/Paris"}

    detail = (f"Premier cours : {titre_cours}"
              f"{f' en {salle}' if salle else ''} à {debut_cours:%Hh%M}.\n"
              f"Trajet estimé : {minutes} min ({source}).\n"
              f"Arrivée visée : {arrivee:%Hh%M}"
              f"{f' ({MARGE} min avant le cours)' if MARGE else ' (heure du cours)'}.\n"
              f"Préparation avant le départ : {PREPARATION} min.")

    return [
        {
            "id": _identifiant("reveil", jour),
            "summary": f"Réveil — départ {depart:%Hh%M}",
            "description": detail,
            "start": horaire(reveil),
            "end": horaire(reveil + timedelta(minutes=5)),
            "reminders": {"useDefault": False,
                          "overrides": [{"method": "popup", "minutes": 0}]},
        },
        {
            "id": _identifiant("depart", jour),
            "summary": f"Départ → {titre_cours}",
            "location": UNIVERSITE or salle,
            "description": detail,
            "start": horaire(depart),
            "end": horaire(arrivee),
            "reminders": {"useDefault": False,
                          "overrides": [{"method": "popup", "minutes": 5}]},
        },
    ]


def synchroniser(service, jours=JOURS, cle_agenda="BAS", essai=False):
    """Aligne l'agenda de trajet sur les cours à venir."""
    premiers = premiers_cours(service, jours, cle_agenda)
    if not premiers:
        print("   Aucun cours dans la période : rien à préparer.")
        return 0, 0, 0

    voulus = {}
    for jour, premier in premiers.items():
        arrivee = premier[0] - timedelta(minutes=MARGE)
        minutes, source = duree_trajet(arrivee)
        for evt in _construire(jour, premier, minutes, source):
            voulus[evt["id"]] = evt
        if essai:
            depart = arrivee - timedelta(minutes=minutes)
            print(f"   {jour}  cours {premier[0]:%Hh%M}  "
                  f"départ {depart:%Hh%M}  réveil "
                  f"{depart - timedelta(minutes=PREPARATION):%Hh%M}  "
                  f"({minutes} min, {source})")

    if essai:
        print(f"   → {len(voulus)} événement(s) seraient écrits. Rien n'a été modifié.")
        return 0, 0, 0

    agenda_id = google_agenda.trouver_ou_creer_agenda(
        service, nom=NOM_AGENDA, cle=CLE_MARQUEUR)
    google_agenda.appliquer_couleur(service, agenda_id, COULEUR)

    # Le rapprochement est limité à la fenêtre préparée : au-delà, les
    # événements plus anciens ne doivent pas être supprimés.
    debut = datetime.now(FUSEAU).replace(hour=0, minute=0, second=0, microsecond=0)
    existants = {}
    page = service.events().list(
        calendarId=agenda_id, singleEvents=True, showDeleted=False,
        timeMin=debut.isoformat(),
        timeMax=(debut + timedelta(days=jours)).isoformat(), maxResults=2500,
    ).execute()
    for evt in page.get("items", []):
        existants[evt["id"]] = evt

    ajouts = modifs = retraits = 0
    for cle, evt in voulus.items():
        if cle not in existants:
            try:
                service.events().insert(calendarId=agenda_id, body=evt).execute()
            except Exception as e:
                if "409" not in str(e):
                    raise
                service.events().update(calendarId=agenda_id, eventId=cle, body=evt).execute()
            ajouts += 1
        elif not google_agenda._identique(existants[cle], evt):
            service.events().update(calendarId=agenda_id, eventId=cle, body=evt).execute()
            modifs += 1

    for cle in existants:
        if cle not in voulus:
            service.events().delete(calendarId=agenda_id, eventId=cle).execute()
            retraits += 1

    return ajouts, modifs, retraits


def principale():
    import edt_stri
    from googleapiclient.discovery import build

    if "--diagnostic" in sys.argv:
        return diagnostic()

    essai = "--essai" in sys.argv
    _, cle = cle_du_fournisseur()
    if not (cle and DOMICILE and UNIVERSITE):
        print(f"ℹ️  Fournisseur « {FOURNISSEUR} » non configuré : "
              f"durée fixe de {DUREE_SECOURS} min. "
              "Voir `python trajet.py --diagnostic`.")

    creds = edt_stri.obtenir_identifiants()
    if creds is None:
        return 1

    print("🧭 Préparation des trajets...")
    service = build("calendar", "v3", credentials=creds)
    try:
        ajouts, modifs, retraits = synchroniser(
            service, cle_agenda=variable_env("EDT_MOITIE", "BAS").upper(), essai=essai)
    except Exception as e:
        print(f"❌ Préparation impossible : {e}")
        return 1

    if not essai:
        print(f"✅ Trajets à jour : {ajouts} ajout(s), {modifs} modification(s), "
              f"{retraits} suppression(s).")
    return 0


if __name__ == "__main__":
    sys.exit(principale())

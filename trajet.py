"""
Réveil et départ calculés depuis l'agenda et le temps de trajet réel.

Pour chaque journée qui commence par un cours, le script écrit deux événements
dans un agenda dédié :

    Réveil 06h20   — le raccourci iOS le lit pour programmer une vraie alarme
    Départ 07h05   — de l'heure de départ à l'arrivée, adresse en lieu

Aucun script ne peut créer une alarme sur un téléphone : ni iOS ni Android
n'exposent d'API pour l'app Horloge. L'événement « Réveil » est donc la donnée
d'entrée d'une automatisation Raccourcis qui, elle, tourne sur l'iPhone.

Le temps de trajet vient de l'API Google Routes, interrogée avec l'heure
d'ARRIVÉE voulue : en transports en commun, c'est elle qui détermine le
passage à prendre, pas une durée moyenne. Sans clé d'API, une durée fixe prend
le relais et le script reste utilisable.

    python trajet.py            -> prépare les prochains jours
    python trajet.py --essai    -> affiche le calcul sans rien écrire
    python trajet.py --diagnostic -> teste la clé API et explique tout refus
"""

import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

import google_agenda
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

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
TIMEOUT_HTTP = 20


# =====================================================================
# TEMPS DE TRAJET
# =====================================================================

def duree_trajet(arrivee):
    """Durée domicile -> université pour arriver à `arrivee`, en minutes.

    Renvoie (minutes, source). L'heure d'ARRIVÉE est envoyée à l'API plutôt
    qu'une heure de départ : en transports en commun, arriver à 7h45 ne se
    déduit pas d'une durée moyenne, il faut le passage qui convient.
    """
    if not (CLE_MAPS and DOMICILE and UNIVERSITE):
        return DUREE_SECOURS, "durée fixe (API non configurée)"

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
    print("🔎 Diagnostic de l'API Routes")
    for nom, valeur in (("TRAJET_DOMICILE", DOMICILE),
                        ("TRAJET_UNIVERSITE", UNIVERSITE),
                        ("GOOGLE_MAPS_API_KEY", CLE_MAPS)):
        etat = f"« {valeur} »" if valeur and nom != "GOOGLE_MAPS_API_KEY" else (
            f"définie ({len(valeur)} caractères)" if valeur else "ABSENTE")
        print(f"   {nom:22s} {etat}")
    if not (CLE_MAPS and DOMICILE and UNIVERSITE):
        print("   → Complète ces trois variables dans .env avant d'aller plus loin.")
        return 1

    demain = datetime.now(FUSEAU) + timedelta(days=1)
    cible = demain.replace(hour=8, minute=0, second=0, microsecond=0)
    minutes, source = duree_trajet(cible)
    if source == "Google Routes":
        print(f"   ✅ Itinéraire obtenu : {minutes} min pour arriver à "
              f"{cible:%d/%m %Hh%M}.")
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
    if not (CLE_MAPS and DOMICILE and UNIVERSITE):
        print("ℹ️  TRAJET_DOMICILE, TRAJET_UNIVERSITE ou GOOGLE_MAPS_API_KEY "
              f"absents : durée fixe de {DUREE_SECOURS} min.")

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

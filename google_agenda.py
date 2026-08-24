"""
Écriture directe des cours dans Google Agenda (API Calendar v3).

Remplace l'abonnement à un fichier ICS, qui posait deux problèmes insolubles :

  - Google Agenda relit une URL externe quand il le décide (8 à 24 h, parfois
    plus) et rien ne permet de forcer ce rythme ;
  - iOS réécrit l'URL d'abonnement en `http://`, or Drive refuse le HTTP en
    direct (403) et ne répond qu'en redirigeant vers un autre domaine en
    HTTPS — redirection qu'iOS refuse de suivre pour un calendrier.

En écrivant dans l'agenda, les cours apparaissent en quelques secondes, et
sur l'iPhone via la synchronisation normale du compte Google : aucun
abonnement, aucune URL, aucun réglage SSL.

La synchronisation est un rapprochement complet : chaque cours porte un
identifiant déterministe, donc un cours déplacé est modifié sur place et un
cours supprimé du PDF disparaît de l'agenda.
"""

import hashlib
from datetime import datetime

from googleapiclient.errors import HttpError

SCOPE = "https://www.googleapis.com/auth/calendar"

NOM_AGENDA = "EDT STRI M1"
FUSEAU = "Europe/Paris"

# L'API refuse plus de 2500 événements par page.
TAILLE_PAGE = 2500

# Couleur des examens. L'API n'accepte pas un code hexadécimal mais un numéro
# dans sa palette fixe de onze teintes :
#   1 Lavande   2 Sauge     3 Raisin    4 Flamant  5 Banane   6 Mandarine
#   7 Paon      8 Graphite  9 Myrtille 10 Basilic 11 Tomate (rouge)
# Les autres cours n'en portent aucune et gardent la couleur de l'agenda.
COULEUR_EXAMEN = "11"
MARQUEUR_EXAMEN = "[EXAMEN]"


def _identifiant(cours):
    """Identifiant stable d'un cours, accepté tel quel par l'API.

    Google impose l'alphabet base32hex (`a-v` et `0-9`) et 5 caractères au
    minimum : les 32 caractères hexadécimaux d'un MD5 conviennent sans
    transformation. Même empreinte que l'UID du fichier ICS, pour que les deux
    sorties restent cohérentes.
    """
    empreinte = f"{cours['date']}|{cours['start']}|{cours['end']}|{cours['titre']}"
    return hashlib.md5(empreinte.encode("utf-8")).hexdigest()


def _horodatage(date_str, heure_str):
    heures, minutes = map(int, heure_str.split('h'))
    jour = datetime.strptime(date_str, '%Y-%m-%d')
    return jour.replace(hour=heures, minute=minutes).isoformat()


def _en_evenement(cours):
    """Traduit un cours en ressource Event de l'API."""
    evenement = {
        "id": _identifiant(cours),
        "summary": cours['titre'],
        "location": cours.get('room') or "",
        "description": f"Enseignant : {cours.get('prof') or 'inconnu'}",
        "start": {"dateTime": _horodatage(cours['date'], cours['start']), "timeZone": FUSEAU},
        "end": {"dateTime": _horodatage(cours['date'], cours['end']), "timeZone": FUSEAU},
        "source": {"title": "EDT STRI", "url": "https://stri.fr/"},
    }
    if cours['titre'].startswith(MARQUEUR_EXAMEN):
        evenement["colorId"] = COULEUR_EXAMEN
    return evenement


def _identique(existant, voulu):
    """Compare uniquement ce que le bot pilote : le reste appartient à Google."""
    # `colorId` est comparé comme le reste : un cours qui devient un examen se
    # recolore, un examen annulé retrouve la couleur de l'agenda. La
    # synchronisation utilisant `update` (remplacement complet), l'absence de
    # la clé suffit à effacer la couleur.
    for champ in ("summary", "location", "description", "colorId"):
        if (existant.get(champ) or "") != (voulu.get(champ) or ""):
            return False
    for borne in ("start", "end"):
        a, b = existant.get(borne, {}), voulu[borne]
        # L'API renvoie un décalage explicite (« +02:00 ») là où on envoie un
        # fuseau nommé : on compare les instants, pas les chaînes.
        if _instant(a.get("dateTime")) != _instant(b["dateTime"], b["timeZone"]):
            return False
    return True


def _instant(valeur, fuseau=None):
    if not valeur:
        return None
    from zoneinfo import ZoneInfo
    moment = datetime.fromisoformat(valeur)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo(fuseau or FUSEAU))
    return moment.timestamp()


def trouver_ou_creer_agenda(service, nom=NOM_AGENDA, identifiant=None):
    """Renvoie l'ID de l'agenda dédié, en le créant à la première exécution.

    La correspondance sur le seul nom ne suffit pas : le fichier ICS publié par
    ce même script porte `X-WR-CALNAME:EDT STRI M1`, donc un abonnement à ce
    fichier apparaît dans la liste sous exactement ce nom. On tombait dessus en
    premier, et l'écriture échouait en 403 — un agenda auquel on est abonné est
    en lecture seule. Seul un agenda dont on est propriétaire convient.
    """
    if identifiant:
        return identifiant

    jeton = None
    while True:
        page = service.calendarList().list(pageToken=jeton).execute()
        for agenda in page.get("items", []):
            if (agenda.get("summary") == nom
                    and agenda.get("accessRole") == "owner"
                    and not agenda.get("id", "").endswith("@import.calendar.google.com")):
                return agenda["id"]
        jeton = page.get("nextPageToken")
        if not jeton:
            break

    print(f"   Création de l'agenda « {nom} »...")
    cree = service.calendars().insert(
        body={"summary": nom, "timeZone": FUSEAU,
              "description": "Emploi du temps STRI, mis à jour automatiquement."}
    ).execute()
    return cree["id"]


def _evenements_existants(service, agenda_id):
    existants, jeton = {}, None
    while True:
        page = service.events().list(
            calendarId=agenda_id, showDeleted=False, singleEvents=True,
            maxResults=TAILLE_PAGE, pageToken=jeton,
        ).execute()
        for evt in page.get("items", []):
            existants[evt["id"]] = evt
        jeton = page.get("nextPageToken")
        if not jeton:
            return existants


def synchroniser(service, cours_list, nom=NOM_AGENDA, identifiant_agenda=None):
    """Aligne l'agenda sur la liste de cours. Renvoie (ajouts, modifs, retraits)."""
    agenda_id = trouver_ou_creer_agenda(service, nom, identifiant_agenda)

    voulus = {}
    for cours in cours_list:
        try:
            evt = _en_evenement(cours)
        except (ValueError, KeyError) as e:
            print(f"   ⚠️ Cours ignoré ({cours.get('titre', '?')}) : {e}")
            continue
        voulus[evt["id"]] = evt

    existants = _evenements_existants(service, agenda_id)

    ajouts = modifs = retraits = 0

    for cle, evt in voulus.items():
        if cle not in existants:
            try:
                service.events().insert(calendarId=agenda_id, body=evt).execute()
            except HttpError as e:
                # 409 : l'identifiant a déjà servi puis a été supprimé. Google le
                # garde en réserve ; `update` ressuscite l'événement au lieu de
                # refuser l'insertion à chaque exécution.
                if e.resp.status != 409:
                    raise
                service.events().update(calendarId=agenda_id, eventId=cle, body=evt).execute()
            ajouts += 1
        elif not _identique(existants[cle], evt):
            service.events().update(calendarId=agenda_id, eventId=cle, body=evt).execute()
            modifs += 1

    for cle in existants:
        if cle not in voulus:
            service.events().delete(calendarId=agenda_id, eventId=cle).execute()
            retraits += 1

    return ajouts, modifs, retraits

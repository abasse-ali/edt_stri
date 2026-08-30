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
import sys
from datetime import datetime, timedelta

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.errors import HttpError

import chemins
from telechargement import variable_env

SCOPE = "https://www.googleapis.com/auth/calendar"

# Identité de l'agenda des rendus Moodle. Elle vit ICI plutôt que dans
# `rendus.py` pour une raison très concrète : `partager.py` doit pouvoir le
# proposer sans importer toute la chaîne de traitement des PDF. Le bot Discord
# n'a alors besoin ni d'OpenCV, ni de NumPy, ni de pdfplumber — trois cents
# mégaoctets de moins à installer sur la machine qui l'héberge.
NOM_RENDUS = variable_env("MOODLE_AGENDA", "Rendu M1")
CLE_RENDUS = "MOODLE-RENDUS"


def obtenir_identifiants(scopes=None, interactif=None):
    """Identifiants Google pour l'API Calendar.

    Renvoie None plutôt que de bloquer : en CI, `run_local_server()` attendrait
    une autorisation navigateur qui ne viendra jamais et le job tournerait
    jusqu'à son délai maximum.
    """
    scopes = scopes or [SCOPE]
    if interactif is None:
        # EDT_AUTORISER=1 force le mode interactif quand le script est lancé
        # par un outil qui ne fournit pas de vrai terminal.
        interactif = (variable_env("EDT_AUTORISER") == "1"
                      or (not variable_env("CI") and sys.stdin.isatty()))

    jeton, client = chemins.racine('token.json'), chemins.racine('credentials.json')

    creds = None
    if jeton.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(jeton), scopes)
        except ValueError as e:
            print(f"⚠️ token.json illisible ({e}).")

    # Un jeton qui ne couvre pas l'agenda se rafraîchit sans erreur mais fait
    # échouer l'API Calendar en 403 : mieux vaut redemander l'autorisation tout
    # de suite que laisser l'agenda muet sans explication.
    if creds and not creds.has_scopes(scopes):
        print("🔑 Autorisation à renouveler : le jeton ne couvre pas l'agenda.")
        creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            jeton.write_text(creds.to_json(), encoding="utf-8")
            return creds
        except Exception as e:
            print(f"⚠️ Rafraîchissement du jeton impossible ({e}).")

    if not interactif:
        print("❌ Autorisation Google absente ou périmée, et environnement non interactif.")
        print("   Relance en local avec EDT_AUTORISER=1 pour régénérer token.json,")
        print("   puis mets à jour le secret GDRIVE_TOKEN du dépôt.")
        return None

    if not client.exists():
        print("❌ Le fichier credentials.json est introuvable.")
        return None

    flow = InstalledAppFlow.from_client_secrets_file(str(client), scopes)
    creds = flow.run_local_server(port=0)
    jeton.write_text(creds.to_json(), encoding="utf-8")
    print("✅ Autorisation enregistrée dans token.json.")
    return creds

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
# Préfixe d'un examen dans le titre. Il sert aussi à le reconnaître pour le
# colorer : une seule source, les deux ne peuvent pas diverger.
MARQUEUR_EXAMEN = "[EXAMEN]"

# Correspondance nom -> numéro dans la palette des ÉVÉNEMENTS.
COULEURS_EVENEMENT = {
    "lavande": ("1", "#a4bdfc"), "sauge": ("2", "#7ae7bf"),
    "raisin": ("3", "#dbadff"), "flamant": ("4", "#ff887c"),
    "banane": ("5", "#fbd75b"), "mandarine": ("6", "#ffb878"),
    "paon": ("7", "#46d6db"), "graphite": ("8", "#e1e1e1"),
    "myrtille": ("9", "#5484ed"), "basilic": ("10", "#51b749"),
    "tomate": ("11", "#dc2127"),
}


def couleur_evenement(nom):
    """Numéro de palette d'une couleur d'événement, ou None si le nom est inconnu."""
    if not nom:
        return None
    trouve = COULEURS_EVENEMENT.get(nom.strip().lower())
    if trouve is None:
        print(f"   ⚠️ Couleur de cours « {nom} » inconnue. "
              f"Valeurs admises : {', '.join(sorted(COULEURS_EVENEMENT))}.")
        return None
    return trouve[0]

# Couleur de FOND d'un agenda. Attention : ce n'est pas la même palette que
# celle des événements ci-dessus. Google en expose deux, de tailles
# différentes, et le même numéro n'y désigne pas la même teinte :
#   - événement : 11 couleurs (`colors().get()['event']`)
#   - agenda    : 24 couleurs (`colors().get()['calendar']`)
# « Pistache » et « Raisin » n'existent que dans la seconde.
COULEURS_AGENDA = {
    "pistache": ("9", "#7bd148"),
    "avocat": ("10", "#b3dc6c"),
    "raisin": ("23", "#cd74e6"),
    "amethyste": ("24", "#a47ae2"),
    "basilic": ("8", "#16a765"),
    "myrtille": ("16", "#4986e7"),
    "paon": ("14", "#9fe1e7"),
    "tomate": ("3", "#f83a22"),
    "mangue": ("6", "#ffad46"),
    "graphite": ("19", "#c2c2c2"),
}


def appliquer_couleur(service, agenda_id, nom_couleur):
    """Donne sa couleur de fond à l'agenda. Sans effet si elle est déjà bonne.

    La couleur vit dans `calendarList`, pas dans `calendars` : elle appartient
    à l'abonnement de l'utilisateur, pas à l'agenda lui-même.
    """
    if not nom_couleur:
        return None
    couleur = COULEURS_AGENDA.get(nom_couleur.strip().lower())
    if couleur is None:
        print(f"   ⚠️ Couleur d'agenda « {nom_couleur} » inconnue. "
              f"Valeurs admises : {', '.join(sorted(COULEURS_AGENDA))}.")
        return None

    identifiant, teinte = couleur
    entree = service.calendarList().get(calendarId=agenda_id).execute()
    if entree.get("colorId") == identifiant:
        return teinte

    service.calendarList().patch(
        calendarId=agenda_id, body={"colorId": identifiant}).execute()
    print(f"   🎨 Couleur de l'agenda : {nom_couleur} ({teinte}).")
    return teinte


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
    """« 2026-09-08 » et « 08h00 » -> ISO 8601 sans fuseau.

    Le fuseau est déclaré à part dans la ressource, pour que Google applique
    lui-même le changement d'heure.
    """
    heures, minutes = map(int, heure_str.split('h'))
    jour = datetime.strptime(date_str, '%Y-%m-%d')
    return jour.replace(hour=heures, minute=minutes).isoformat()


def _borne(cours, cote):
    """Début ou fin d'un événement, au format attendu par l'API.

    Un cours occupe un créneau horaire, mais une échéance Moodle peut couvrir
    une journée entière : Google distingue les deux par la clé employée,
    `dateTime` ou `date`. Une journée entière se termine à une date EXCLUSIVE,
    d'où le lendemain par défaut.
    """
    if cours.get('start'):
        return {"dateTime": _horodatage(cours['date'], cours[cote]),
                "timeZone": FUSEAU}
    if cote == 'start':
        return {"date": cours['date']}
    fin = cours.get('date_fin')
    if not fin:
        veille = datetime.strptime(cours['date'], '%Y-%m-%d')
        fin = (veille + timedelta(days=1)).strftime('%Y-%m-%d')
    return {"date": fin}


def _rappels(minutes):
    """Bloc `reminders` d'un événement, ou None pour ne rien imposer.

    `useDefault: False` est indispensable : sans lui Google applique les
    réglages par défaut de l'agenda et ignore la liste. `minutes = 0` sert donc
    à dire « aucun rappel », ce qui n'est pas la même chose que ne pas envoyer
    le champ du tout — ce dernier cas laisse l'événement tel quel.

    Le rappel vaut pour le propriétaire de l'agenda : Google range les rappels
    dans la part privée de l'événement, propre à chaque compte. Les personnes
    avec qui l'agenda est partagé gardent les leurs, et aucune API ne permet de
    leur en imposer un — comme pour la couleur de fond.
    """
    if minutes is None:
        return None
    if not minutes:
        return {"useDefault": False, "overrides": []}
    return {"useDefault": False,
            "overrides": [{"method": "popup", "minutes": int(minutes)}]}


def _cle_rappels(bloc):
    """Forme comparable d'un bloc `reminders`.

    L'API rend `{"useDefault": false}` sans `overrides` quand il n'y en a
    aucun, et l'ordre de la liste n'est pas garanti : comparer les
    dictionnaires bruts ferait réécrire les événements à chaque exécution.
    """
    bloc = bloc or {}
    if bloc.get("useDefault"):
        return ("defaut",)
    return tuple(sorted((o.get("method"), o.get("minutes"))
                        for o in bloc.get("overrides", [])))


def _en_evenement(cours, couleur_cours=None, rappel_minutes=None):
    """Traduit un cours en ressource Event de l'API.

    `couleur_cours` est posé sur CHAQUE événement, et pas seulement sur les
    examens. C'est le seul moyen d'imposer une couleur aux personnes avec qui
    l'agenda est partagé : la teinte de fond d'un agenda appartient à
    l'abonnement de chacun (`calendarList`), et aucune API ne permet de la
    fixer à leur place. La couleur d'un événement, elle, est stockée sur
    l'événement — donc identique pour tout le monde.
    """
    evenement = {
        "id": _identifiant(cours),
        "summary": cours['titre'],
        "location": cours.get('room') or "",
        "description": cours.get('description')
                       or f"Enseignant : {cours.get('prof') or 'inconnu'}",
        "start": _borne(cours, 'start'),
        "end": _borne(cours, 'end'),
        "source": {"title": "EDT STRI", "url": "https://stri.fr/"},
    }
    if cours['titre'].startswith(MARQUEUR_EXAMEN):
        evenement["colorId"] = COULEUR_EXAMEN
    elif couleur_cours:
        evenement["colorId"] = couleur_cours

    rappels = _rappels(rappel_minutes)
    if rappels is not None:
        evenement["reminders"] = rappels
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
    # Comparé UNIQUEMENT si l'appelant en a demandé un. Les agendas de cours
    # n'en posent pas : comparer le champ les ferait tous réécrire à la
    # première exécution, pour rien.
    if "reminders" in voulu:
        if _cle_rappels(existant.get("reminders")) != _cle_rappels(voulu["reminders"]):
            return False

    for borne in ("start", "end"):
        a, b = existant.get(borne, {}), voulu[borne]
        # Journée entière d'un côté, créneau horaire de l'autre : deux formes
        # différentes, donc un événement à réécrire.
        if "date" in a or "date" in b:
            if a.get("date") != b.get("date"):
                return False
            continue
        # L'API renvoie un décalage explicite (« +02:00 ») là où on envoie un
        # fuseau nommé : on compare les instants, pas les chaînes.
        if _instant(a.get("dateTime")) != _instant(b["dateTime"], b["timeZone"]):
            return False
    return True


def _instant(valeur, fuseau=None):
    """Ramène un horodatage à un instant comparable.

    L'API rend un décalage explicite (« +02:00 ») là où on envoie un fuseau
    nommé : comparer les chaînes ferait réécrire tous les événements à chaque
    exécution.
    """
    if not valeur:
        return None
    from zoneinfo import ZoneInfo
    moment = datetime.fromisoformat(valeur)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo(fuseau or FUSEAU))
    return moment.timestamp()


def marqueur(cle):
    """Étiquette invisible posée dans la description de l'agenda."""
    return f"[edt-stri:{cle}]"


def trouver_ou_creer_agenda(service, nom=NOM_AGENDA, identifiant=None, cle="BAS",
                            description="Emploi du temps STRI, mis à jour automatiquement."):
    """Renvoie l'ID de l'agenda dédié, en le créant à la première exécution.

    La recherche se fait sur un MARQUEUR posé dans la description, pas sur le
    nom : renommer son agenda dans l'interface Google est parfaitement légitime
    et ne doit pas faire perdre sa trace. Chercher « EDT STRI M1 » après un
    renommage en « STRI M1 G2 » créait un second agenda et dupliquait tous les
    cours.

    Le repli par nom sert aux agendas créés avant l'introduction du marqueur ;
    ils sont étiquetés au passage. Un agenda auquel on est simplement abonné
    est écarté : il est en lecture seule, l'écriture y échouerait en 403 — et
    le fichier ICS publié par ce script porte justement `X-WR-CALNAME`.
    """
    if identifiant:
        return identifiant

    trouve = trouver_agenda(service, cle, nom)
    if trouve:
        return trouve

    etiquette = marqueur(cle)
    print(f"   Création de l'agenda « {nom} »...")
    cree = service.calendars().insert(
        body={"summary": nom, "timeZone": FUSEAU,
              "description": f"{description} {etiquette}"}
    ).execute()
    return cree["id"]


def agendas_possedes(service):
    """Les agendas dont ce compte est propriétaire.

    Un agenda auquel on est seulement abonné est écarté : il est en lecture
    seule, y écrire échouerait en 403 — et le fichier ICS publié par ce projet
    porte justement le même nom que l'agenda.
    """
    possedes, jeton = [], None
    while True:
        page = service.calendarList().list(pageToken=jeton).execute()
        possedes += [a for a in page.get("items", [])
                     if a.get("accessRole") == "owner"
                     and not a.get("id", "").endswith("@import.calendar.google.com")]
        jeton = page.get("nextPageToken")
        if not jeton:
            return possedes


def trouver_agenda(service, cle, nom=None):
    """Identifiant de l'agenda portant ce marqueur, ou None. Ne crée rien.

    La recherche se fait sur le MARQUEUR, pas sur le nom : renommer son agenda
    dans l'interface Google est légitime et ne doit pas faire perdre sa trace.
    Le repli par nom sert aux agendas antérieurs au marqueur ; ils sont
    étiquetés au passage.
    """
    etiquette = marqueur(cle)
    possedes = agendas_possedes(service)

    for agenda in possedes:
        if etiquette in (agenda.get("description") or ""):
            return agenda["id"]

    if nom:
        for agenda in possedes:
            if agenda.get("summary") == nom:
                _etiqueter(service, agenda["id"], etiquette)
                return agenda["id"]
    return None


def _etiqueter(service, agenda_id, etiquette):
    """Ajoute le marqueur à un agenda existant, sans toucher à son nom."""
    agenda = service.calendars().get(calendarId=agenda_id).execute()
    description = (agenda.get("description") or "").strip()
    if etiquette in description:
        return
    service.calendars().patch(
        calendarId=agenda_id,
        body={"description": f"{description} {etiquette}".strip()}).execute()
    print(f"   🏷️  Agenda « {agenda.get('summary')} » étiqueté {etiquette}.")


def _evenements_existants(service, agenda_id):
    """Tous les événements de l'agenda, indexés par identifiant.

    L'API plafonne à 2500 par page : la pagination est indispensable, sans
    quoi les événements non listés seraient recréés puis supprimés en boucle.
    """
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


def _debut(evenement):
    """Date de début d'un événement de l'API, journée entière comprise."""
    borne = evenement.get("start", {})
    return (borne.get("dateTime") or borne.get("date") or "")[:10]


def synchroniser(service, cours_list, nom=NOM_AGENDA, identifiant_agenda=None,
                 couleur_cours=None, depuis=None, rappel_minutes=None):
    """Aligne l'agenda sur la liste de cours. Renvoie (ajouts, modifs, retraits).

    `rappel_minutes` pose une notification tant de minutes avant l'événement.
    Laissé à None, le champ n'est pas envoyé du tout et les rappels existants
    ne sont pas touchés.

    `depuis` limite les SUPPRESSIONS aux événements à partir de cette date. Le
    PDF de l'emploi du temps couvre toute l'année et n'en a pas besoin ; le
    calendrier Moodle, lui, s'exporte sur une fenêtre glissante, et sans cette
    borne chaque exécution effacerait les échéances passées puis les
    signalerait comme des annulations.
    """
    agenda_id = trouver_ou_creer_agenda(service, nom, identifiant_agenda)

    voulus = {}
    for cours in cours_list:
        try:
            evt = _en_evenement(cours, couleur_cours, rappel_minutes)
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

    for cle, evt in existants.items():
        if cle in voulus:
            continue
        if depuis and _debut(evt) < depuis:
            continue  # hors de la fenêtre couverte par la source
        service.events().delete(calendarId=agenda_id, eventId=cle).execute()
        retraits += 1

    return ajouts, modifs, retraits

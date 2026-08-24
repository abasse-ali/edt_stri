"""
Synchronisation de l'emploi du temps STRI (PDF -> JSON -> ICS -> Google Drive).

Ce module sert à la fois en CI (GitHub Actions) et en local :
  python edt_stri.py               -> traite le edt.pdf présent dans le dossier courant
  python edt_stri.py --telecharger -> télécharge le PDF puis s'arrête
  EDT_DEBUG=1 python edt_stri.py   -> exporte les images de debug dans export_cours/
"""

import os
import sys
import json
import re
import hashlib
from itertools import combinations
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import numpy as np
import cv2
import pdfplumber
from ics import Calendar, Event
from ics.contentline import ContentLine
from pdf2image import convert_from_path

import lecture_pdf
import google_agenda

# --- BIBLIOTHÈQUES GOOGLE DRIVE ---
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# CORRECTIF : sur une console Windows en cp1252, le moindre print contenant un
# emoji lève UnicodeEncodeError et tue le script en cours de traitement.
for _flux in (sys.stdout, sys.stderr):
    try:
        _flux.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# =====================================================================
# CONFIGURATION
# =====================================================================

# CORRECTIF #5 : une seule et unique source pour l'URL du PDF (CI + local).
# Surchargeable sans toucher au code : EDT_PDF_URL=... python edt_stri.py
EDT_PDF_URL = os.environ.get(
    "EDT_PDF_URL",
    "https://stri.fr/Gestion_STRI/TAV/M1/EDT_STRI4A-M1RT_TAV.pdf",
)
EDT_BASE_URL = "https://stri.fr/"

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
DRIVE_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID", "1ID97m9gVzOqcLvdYBAabUo5wZKzZ5Nj-")
# Agenda cible. Vide = recherché par son nom, puis créé s'il n'existe pas.
CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "") or None

# L'emploi du temps M1 n'indique aucun groupe (« /GB », « /GC » : zéro
# occurrence). Quand deux cours sont empilés dans un même créneau, seul celui du
# BAS est retenu. Mettre à True pour publier aussi ceux du haut.
GARDER_COURS_DU_HAUT = os.environ.get("EDT_COURS_HAUT", "0") not in ("0", "false", "False")

# CORRECTIF #6 : plus d'année en dur. None = déduction automatique depuis le PDF.
ANNEE_FORCEE = None

# CORRECTIF #9 : plus aucun appel réseau sans timeout.
TIMEOUT_HTTP = 20

DEBUG = os.environ.get("EDT_DEBUG", "").strip() not in ("", "0", "false", "False")
DOSSIER_DEBUG = Path("export_cours")

FICHIER_PDF = os.environ.get("EDT_PDF", "edt.pdf")
FICHIER_JSON = "edt_data.json"
FICHIER_ICS = "edt.ics"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

DPI = 200

# Marge de 10 px ajoutée autour de la grille recadrée (repère de REFERENCES_TEMPS).
PADDING = 10
# Marge supplémentaire à droite : le libellé de la dernière salle déborde de la
# grille et était tronqué à la découpe.
MARGE_DROITE = 40
# Écart mesuré entre le bord gauche d'une cellule et le x0 de son libellé « 8h ».
DECALAGE_LIBELLE = 4
REGEX_HEURE = re.compile(r'\d{1,2}h')

MOIS_MAP = {
    'janv': 1, 'févr': 2, 'fevr': 2, 'mars': 3, 'avr': 4, 'mai': 5, 'juin': 6,
    'juil': 7, 'août': 8, 'aout': 8, 'sept': 9, 'oct': 10, 'nov': 11, 'déc': 12, 'dec': 12,
}
REGEX_DATE = re.compile(
    r'(\d{1,2})/(janv|févr|fevr|mars|avr|mai|juin|juil|août|aout|sept|oct|nov|déc|dec)'
)

REFERENCES_TEMPS = []
GLOBAL_START_X = 0
GLOBAL_END_X = 0


def _trouver_poppler():
    """CORRECTIF #10 : détection automatique de poppler (le chemin était faux)."""
    if os.environ.get("POPPLER_PATH"):
        return os.environ["POPPLER_PATH"]
    if sys.platform != "win32":
        return None  # poppler-utils est dans le PATH sous Linux/macOS
    ici = Path(__file__).resolve().parent
    for racine in (Path.cwd(), ici, ici.parent):
        candidat = racine / "poppler" / "Library" / "bin"
        if (candidat / "pdftoppm.exe").exists():
            return str(candidat)
    return None


POPPLER_PATH = _trouver_poppler()

def _sauver_debug(image, *parties):
    """Écrit une image de debug uniquement si EDT_DEBUG est activé."""
    if not DEBUG:
        return
    chemin = DOSSIER_DEBUG.joinpath(*parties)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(chemin), image)


# =====================================================================
# TÉLÉCHARGEMENT DU PDF (contournement anti-bot)
# =====================================================================

def telecharger_pdf(chemin_sauvegarde=None):
    """Récupère le PDF derrière le WAF « Tiger Protect ». Retourne True si OK."""
    chemin_sauvegarde = chemin_sauvegarde or FICHIER_PDF
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("❌ playwright n'est pas installé (pip install playwright && playwright install chromium).")
        return False

    print(f"🌐 Téléchargement de {EDT_PDF_URL}")
    try:
        with sync_playwright() as p:
            navigateur = p.chromium.launch(headless=True)
            contexte = navigateur.new_context(user_agent=USER_AGENT)
            page = contexte.new_page()

            print("🛡️ Passage de la vérification anti-bot (Tiger Protect)...")
            page.goto(EDT_BASE_URL)
            page.wait_for_timeout(10000)  # laisse le JavaScript valider la session

            print("🍪 Récupération du cookie d'accès...")
            cookies = {c['name']: c['value'] for c in contexte.cookies()}
            navigateur.close()

        print("📥 Téléchargement du fichier PDF...")
        reponse = requests.get(
            EDT_PDF_URL,
            headers={'User-Agent': USER_AGENT},
            cookies=cookies,
            timeout=TIMEOUT_HTTP,
        )
        reponse.raise_for_status()

        if not reponse.content.startswith(b'%PDF'):
            print("❌ Erreur : le fichier téléchargé n'est pas un PDF valide !")
            return False

        Path(chemin_sauvegarde).write_bytes(reponse.content)
        print(f"✅ Téléchargement terminé ({len(reponse.content) // 1024} Ko).")
        return True

    except Exception as e:
        print(f"❌ Erreur lors du téléchargement : {e}")
        return False


# =====================================================================
# NOTIFICATIONS DISCORD
# =====================================================================

def _envoyer_embed(titre, description, couleur):
    if not DISCORD_WEBHOOK_URL:
        return
    if len(description) > 3900:
        description = description[:3900] + "\n... (trop de changements pour tout afficher)."

    payload = {
        "username": "Bot EDT STRI",
        "avatar_url": "https://cdn-icons-png.flaticon.com/512/2602/2602282.png",
        "embeds": [{"title": titre, "description": description, "color": couleur}],
    }
    try:
        reponse = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=TIMEOUT_HTTP)
        reponse.raise_for_status()
        print("✅ Notification Discord envoyée !")
    except Exception as e:
        print(f"❌ Erreur lors de l'envoi Discord : {e}")


def envoyer_notification_discord(modifications):
    if not modifications:
        return

    description = "L'emploi du temps a été mis à jour ! Voici les changements :\n\n"

    for modif in modifications:
        try:
            date_fr = datetime.strptime(modif['date'], '%Y-%m-%d').strftime('%d/%m/%Y')
        except (ValueError, KeyError):
            date_fr = modif.get('date', '?')

        titre = modif.get('titre', 'Cours inconnu')
        heures = f"{modif.get('start', '?')} - {modif.get('end', '?')}"

        if modif['type'] == 'ajout':
            description += (f"🟢 **AJOUT** : {titre} le {date_fr} ({heures}) en salle "
                            f"{modif.get('room', 'Non attribuée')} avec {modif.get('prof', 'Inconnu')}\n")
        elif modif['type'] == 'suppression':
            description += f"🔴 **ANNULATION** : {titre} le {date_fr} ({heures})\n"
        elif modif['type'] == 'modification':
            description += f"🟠 **MODIFICATION** : {titre} le {date_fr} ({heures})\n"
            for champ, valeurs in modif.get('changements', {}).items():
                nom_champ = {"room": "Salle", "prof": "Prof", "end": "Heure de fin",
                             "titre": "Nom du cours"}.get(champ, champ)
                description += f"   ↳ *{nom_champ}* : ~~{valeurs['ancien']}~~ ➔ **{valeurs['nouveau']}**\n"
        description += "\n"

    _envoyer_embed("🚨 Changements détectés dans l'emploi du temps !", description, 16753920)


def envoyer_alerte_discord(message):
    """CORRECTIF #2 bis : prévenir en cas d'abandon, plutôt que rester silencieux."""
    _envoyer_embed("⚠️ Synchronisation EDT interrompue", message, 15158332)


# =====================================================================
# REPÈRES HORAIRES
# =====================================================================

def obtenir_heure_proche(x_detecte):
    """Renvoie le libellé horaire le plus proche, ou None si hors grille."""
    if not REFERENCES_TEMPS:
        return None
    _ref_x, label = min(REFERENCES_TEMPS, key=lambda item: abs(item[0] - x_detecte))
    return label if label != "?" else None


# =====================================================================
# GOOGLE DRIVE
# =====================================================================

SCOPES_GOOGLE = [
    'https://www.googleapis.com/auth/drive.file',
    google_agenda.SCOPE,
]


def obtenir_identifiants(scopes=None, interactif=None):
    """Identifiants Google partagés par l'agenda et le Drive.

    Renvoie None plutôt que de bloquer : en CI, `run_local_server()` attendrait
    une autorisation navigateur qui ne viendra jamais et le job tournerait
    jusqu'à son délai maximum.
    """
    scopes = scopes or SCOPES_GOOGLE
    if interactif is None:
        # EDT_AUTORISER=1 force le mode interactif quand le script est lancé
        # par un outil qui ne fournit pas de vrai terminal.
        interactif = (os.environ.get("EDT_AUTORISER") == "1"
                      or (not os.environ.get("CI") and sys.stdin.isatty()))

    creds = None
    if Path('token.json').exists():
        try:
            creds = Credentials.from_authorized_user_file('token.json', scopes)
        except ValueError as e:
            print(f"⚠️ token.json illisible ({e}).")

    # Le périmètre a grandi (l'agenda s'ajoute au Drive) : un jeton qui ne
    # couvre que le Drive se rafraîchit sans erreur mais fait échouer l'API
    # Calendar en 403. Mieux vaut redemander l'autorisation tout de suite.
    if creds and not creds.has_scopes(scopes):
        print("🔑 Autorisation à renouveler : l'accès à l'agenda s'ajoute à celui du Drive.")
        creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            Path('token.json').write_text(creds.to_json(), encoding="utf-8")
            return creds
        except Exception as e:
            print(f"⚠️ Rafraîchissement du jeton impossible ({e}).")

    if not interactif:
        print("❌ Autorisation Google absente ou périmée, et environnement non interactif.")
        print("   Relance en local avec EDT_AUTORISER=1 pour régénérer token.json,")
        print("   puis mets à jour le secret GDRIVE_TOKEN du dépôt.")
        return None

    if not Path('credentials.json').exists():
        print("❌ Le fichier credentials.json est introuvable.")
        return None

    flow = InstalledAppFlow.from_client_secrets_file('credentials.json', scopes)
    creds = flow.run_local_server(port=0)
    Path('token.json').write_text(creds.to_json(), encoding="utf-8")
    print("✅ Autorisation enregistrée dans token.json.")
    return creds


def synchroniser_agenda(cours_list, creds):
    """Écrit les cours dans Google Agenda. Retourne l'ID de l'agenda ou None."""
    if creds is None:
        return None
    print("📅 Synchronisation Google Agenda...")
    try:
        service = build('calendar', 'v3', credentials=creds)
        agenda_id = google_agenda.trouver_ou_creer_agenda(service, identifiant=CALENDAR_ID)
        ajouts, modifs, retraits = google_agenda.synchroniser(
            service, cours_list, identifiant_agenda=agenda_id)
        print(f"✅ Agenda à jour : {ajouts} ajout(s), {modifs} modification(s), "
              f"{retraits} suppression(s).")
        return agenda_id
    except Exception as e:
        print(f"❌ Erreur de synchronisation de l'agenda : {e}")
        return None


def afficher_lien_abonnement(agenda_id, creds):
    """Affiche l'adresse ICS de l'agenda, servie par calendar.google.com.

    Contrairement au fichier posé sur le Drive, cette adresse est servie en
    `text/calendar`, sans redirection vers un autre domaine : c'est la seule
    forme qu'iOS accepte quand il réécrit l'URL d'abonnement en `http://`.

    L'adresse n'existe que si l'agenda est lisible publiquement. On ne bascule
    ce réglage que sur demande explicite (EDT_AGENDA_PUBLIC=1), jamais tout
    seul : rendre un agenda public est une décision qui appartient à son
    propriétaire.
    """
    try:
        service = build('calendar', 'v3', credentials=creds)
        regles = service.acl().list(calendarId=agenda_id).execute().get('items', [])
        public = any(r.get('scope', {}).get('type') == 'default'
                     and r.get('role') in ('reader', 'freeBusyReader')
                     for r in regles)

        if not public and os.environ.get("EDT_AGENDA_PUBLIC") == "1":
            service.acl().insert(
                calendarId=agenda_id,
                body={'scope': {'type': 'default'}, 'role': 'reader'},
            ).execute()
            print("🔓 Agenda rendu lisible par toute personne ayant l'adresse.")
            public = True

        print(f"   Identifiant de l'agenda : {agenda_id}")
        if public:
            from urllib.parse import quote
            print("   Adresse d'abonnement (iOS, Outlook, Thunderbird) :")
            print(f"   https://calendar.google.com/calendar/ical/"
                  f"{quote(agenda_id, safe='')}/public/basic.ics")
        else:
            print("   Agenda privé : il apparaît déjà sur les appareils reliés à ce")
            print("   compte Google, sans abonnement. Pour obtenir une adresse ICS")
            print("   partageable, relancer avec EDT_AGENDA_PUBLIC=1.")
    except Exception as e:
        print(f"⚠️ Lien d'abonnement indisponible ({e}).")


def televerser_sur_google_drive(nom_fichier, dossier_id, creds):
    """Publie aussi l'ICS sur le Drive, pour les abonnements déjà en place."""
    if creds is None:
        return False
    print("☁️  Téléversement Drive...")
    try:
        service = build('drive', 'v3', credentials=creds)
        query = f"name = '{nom_fichier}' and '{dossier_id}' in parents and trashed = false"
        resultats = service.files().list(q=query, fields="files(id)").execute()
        items = resultats.get('files', [])
        media = MediaFileUpload(nom_fichier, mimetype='text/calendar')

        if not items:
            service.files().create(
                body={'name': nom_fichier, 'parents': [dossier_id]},
                media_body=media, fields='id').execute()
        else:
            service.files().update(
                fileId=items[0]['id'], body={'name': nom_fichier},
                media_body=media, fields='id').execute()

        print("✅ Fichier mis à jour sur Drive.")
        return True

    except Exception as e:
        print(f"❌ Erreur Upload : {e}")
        return False


# =====================================================================
# EXTRACTION DES REPÈRES HORAIRES
# =====================================================================

def _charger_valeurs_secours(largeur_page, dpi):
    """Plus de repli codé en dur.

    Les anciennes valeurs décrivaient la grille du L3 (07h45→20h00). Appliquées
    au M1 (qui s'arrête à 19h00) elles produisaient des horaires faux en
    silence. Mieux vaut abandonner : `principale()` conserve alors les données
    précédentes et alerte sur Discord.
    """
    print("❌ Repères horaires introuvables et aucun repli disponible.")
    return None


def _traits_entete(chemin_pdf, page_num, y_start_px, y_end_px, dpi):
    """Traits verticaux de l'en-tête, lus dans le PDF (en pixels, repère page).

    Bien plus fiable que la détection morphologique, qui laissait échapper le
    trait de bordure — d'où une dernière colonne (19h15) inexistante.
    """
    ech = dpi / 72
    haut_pt, bas_pt = y_start_px / ech, y_end_px / ech
    try:
        with pdfplumber.open(chemin_pdf) as pdf:
            page = pdf.pages[page_num]
            bruts = sorted(r['x0'] * ech for r in page.rects
                           if haut_pt - 3 <= r['top'] <= bas_pt + 3
                           and r['width'] < 3 and r['height'] > 3)
    except Exception as e:
        print(f"   ⚠️ Traits de l'en-tête illisibles dans le PDF ({e}).")
        return []

    traits = []
    for x in bruts:
        if not traits or x - traits[-1] > 8:
            traits.append(x)
    return [int(round(x)) for x in traits]


def _bord_droit_entete(chemin_pdf, page_num, y_start_px, y_end_px, dpi):
    traits = _traits_entete(chemin_pdf, page_num, y_start_px, y_end_px, dpi)
    return traits[-1] if traits else None


def _lire_ancres_horaires(chemin_pdf, page_num, y_start_px, y_end_px, dpi, start_x):
    """Position X (repère grille) de chaque libellé d'heure « 8h », « 9h »… de l'en-tête.

    Ces libellés sont la seule vérité terrain fiable : contrairement aux traits
    de séparation, ils ne peuvent pas « manquer » à la détection.
    """
    ech = dpi / 72  # points PDF -> pixels
    haut_pt, bas_pt = y_start_px / ech, y_end_px / ech
    ancres = {}

    with pdfplumber.open(chemin_pdf) as pdf:
        page = pdf.pages[page_num]
        for mot in page.extract_words():
            if not REGEX_HEURE.fullmatch(mot['text']):
                continue
            # L'en-tête est répété à chaque bloc de semaine : on ne garde que
            # celui de la bande horaire analysée.
            if not (haut_pt - 2 <= mot['top'] <= bas_pt + 2):
                continue
            heure = int(mot['text'][:-1])
            x = int(round(mot['x0'] * ech - start_x + PADDING - DECALAGE_LIBELLE))
            ancres.setdefault(heure, x)

    return sorted(ancres.items())


def _repartir_quarts(x_debut, x_fin, interieurs):
    """Place les trois quarts d'heure d'une heure pleine.

    CORRECTIF : interpoler linéairement les trois dès qu'il n'y en avait pas
    exactement trois de détectés jetait celles qui étaient justes — sur le M1,
    10h15 se retrouvait 31 px à côté, près d'un quart d'heure d'erreur.
    """
    interieurs = sorted(interieurs)

    # Compte exact : la détection fait autorité.
    if len(interieurs) == 3:
        return [int(round(x)) for x in interieurs]

    def completer(choix):
        """Interpole les quarts absents entre leurs voisins connus."""
        connus = [(-1, x_debut)] + sorted(choix.items()) + [(3, x_fin)]
        places = []
        for i in range(3):
            if i in choix:
                places.append(float(choix[i]))
                continue
            avant = max((c for c in connus if c[0] < i), key=lambda c: c[0])
            apres = min((c for c in connus if c[0] > i), key=lambda c: c[0])
            ratio = (i - avant[0]) / (apres[0] - avant[0])
            places.append(avant[1] + (apres[1] - avant[1]) * ratio)
        return places

    def irregularite(places):
        bornes = [x_debut] + places + [x_fin]
        ecarts = [b - a for a, b in zip(bornes, bornes[1:])]
        moyenne = sum(ecarts) / len(ecarts)
        return sum((e - moyenne) ** 2 for e in ecarts)

    if not interieurs:
        return [int(round(v)) for v in completer({})]

    # « Au plus proche » se trompe là où la grille est comprimée : à midi un
    # quart d'heure fait 18 px contre 38 ailleurs, et le trait réel de 13h30 se
    # retrouve plus près de la position théorique de 13h15. On retient donc
    # l'affectation qui rend l'espacement le plus régulier.
    meilleur = None
    combien = min(len(interieurs), 3)
    for valeurs in combinations(interieurs, combien):
        for indices in combinations(range(3), combien):
            places = completer(dict(zip(indices, valeurs)))
            if any(b <= a for a, b in zip([x_debut] + places, places + [x_fin])):
                continue
            score = irregularite(places)
            if meilleur is None or score < meilleur[0]:
                meilleur = (score, places)

    if meilleur is None:
        return [int(round(v)) for v in completer({})]
    return [int(round(v)) for v in meilleur[1]]


def _construire_references(ancres, separateurs, largeur):
    """Associe un horaire à chaque trait vertical, en s'appuyant sur les heures
    pleines et en interpolant les quarts d'heure là où un trait manque.

    CORRECTIF : l'ancienne version supposait « i-ème trait = i-ème quart
    d'heure ». Un seul séparateur non détecté décalait donc tout le reste de la
    journée de 15 min (constaté sur l'emploi du temps M1, entre 10h et 11h).
    """
    if len(ancres) < 2:
        raise ValueError(f"En-tête horaire illisible ({len(ancres)} heure(s) trouvée(s)).")

    tol = 6
    refs = []

    for (h1, x1), (h2, x2) in zip(ancres, ancres[1:]):
        refs.append((x1, f"{h1:02d}h00"))
        interieurs = [x for x in separateurs if x1 + tol < x < x2 - tol]
        for k, xq in enumerate(_repartir_quarts(x1, x2, interieurs), start=1):
            refs.append((xq, f"{h1:02d}h{15 * k:02d}"))

    h_fin, x_fin = ancres[-1]
    refs.append((x_fin, f"{h_fin:02d}h00"))

    # Quarts d'heure avant la première heure pleine (la grille démarre à 07h45).
    h_debut, x_debut = ancres[0]
    avant = sorted(x for x in separateurs if x < x_debut - tol)
    for rang, xq in enumerate(reversed(avant[-3:])):
        minutes = 45 - 15 * rang
        refs.append((xq, f"{h_debut - 1:02d}h{minutes:02d}"))

    # Quarts d'heure après la dernière heure pleine.
    apres = sorted(x for x in separateurs if x > x_fin + tol)
    for rang, xq in enumerate(apres):
        total = (h_fin * 60) + 15 * (rang + 1)
        if xq > largeur:
            break
        refs.append((xq, f"{total // 60:02d}h{total % 60:02d}"))

    return sorted(set(refs))


def extraire_positions_heures_pdf(chemin_pdf, page_num=0, dpi=DPI):
    """Déduit la position X de chaque quart d'heure depuis l'en-tête du PDF."""
    global REFERENCES_TEMPS, GLOBAL_START_X, GLOBAL_END_X

    largeur_page = 0
    try:
        pages = convert_from_path(chemin_pdf, dpi=dpi, poppler_path=POPPLER_PATH)
        img = cv2.cvtColor(np.array(pages[page_num]), cv2.COLOR_RGB2BGR)
        largeur_page = img.shape[1]

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)

        min_line_length = img.shape[1] // 3
        horiz_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (min_line_length, 1))
        horiz_lines = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, horiz_kernel)
        contours_h, _ = cv2.findContours(horiz_lines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        y_coords = sorted(cv2.boundingRect(c)[1] for c in contours_h)

        unique_y = []
        for y in y_coords:
            if not unique_y or y - unique_y[-1] > 10:
                unique_y.append(y)
        if len(unique_y) < 2:
            raise ValueError("Pas assez de lignes horizontales détectées.")

        y_start, y_end = unique_y[0] - 2, unique_y[1] + 2
        crop_h = y_end - y_start

        kernel_v_long = cv2.getStructuringElement(cv2.MORPH_RECT, (1, crop_h - 10))
        major_v_lines = cv2.morphologyEx(thresh[y_start:y_end, :], cv2.MORPH_OPEN, kernel_v_long)
        cnts_v, _ = cv2.findContours(major_v_lines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        major_x = sorted(cv2.boundingRect(c)[0] for c in cnts_v)

        ux = []
        for x in major_x:
            if not ux or x - ux[-1] > 15:
                ux.append(x)
        # CORRECTIF : ux[1] levait un IndexError silencieux (rattrapé par le
        # repli) quand les bordures n'étaient pas détectées.
        if len(ux) < 3:
            raise ValueError(f"Bordures verticales introuvables ({len(ux)} détectée(s)).")

        start_x = ux[1] - 3

        # CORRECTIF : la détection morphologique manquait le dernier trait de la
        # grille (2186 retenu alors qu'il y en a un à 2209 sur le M1), si bien
        # que la colonne 19h15 était coupée et n'existait pas. Le PDF donne ce
        # bord exactement.
        end_x = ux[-1] + 3
        bord_pdf = _bord_droit_entete(chemin_pdf, page_num, y_start, y_end, dpi)
        if bord_pdf and bord_pdf + 3 > end_x:
            print(f"   Bord droit étendu {end_x} -> {bord_pdf + 3} (dernier trait du PDF)")
            end_x = bord_pdf + 3

        GLOBAL_START_X = start_x
        GLOBAL_END_X = end_x

        final_crop = img[y_start:y_end, start_x:end_x]
        padded_img = cv2.copyMakeBorder(final_crop, PADDING, PADDING, PADDING, PADDING,
                                        cv2.BORDER_CONSTANT, value=[255, 255, 255])

        gray_padded = cv2.cvtColor(padded_img, cv2.COLOR_BGR2GRAY)
        _, thresh_padded = cv2.threshold(gray_padded, 210, 255, cv2.THRESH_BINARY_INV)

        kernel_h_borders = cv2.getStructuringElement(cv2.MORPH_RECT, (40, 1))
        horizontal_borders = cv2.morphologyEx(thresh_padded, cv2.MORPH_OPEN, kernel_h_borders)
        thresh_no_borders = cv2.subtract(thresh_padded, horizontal_borders)

        kernel_close_v = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 20))
        connected_v_lines = cv2.morphologyEx(thresh_no_borders, cv2.MORPH_CLOSE, kernel_close_v)

        kernel_open_v = cv2.getStructuringElement(cv2.MORPH_RECT, (1, int(crop_h * 0.6)))
        clean_v_lines = cv2.morphologyEx(connected_v_lines, cv2.MORPH_OPEN, kernel_open_v)

        contours_all, _ = cv2.findContours(clean_v_lines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        raw_x_found = sorted(
            x + w // 2 for x, _y, w, _h in map(cv2.boundingRect, contours_all) if w < 10
        )

        final_x_positions = []
        for x in raw_x_found:
            if not final_x_positions or x - final_x_positions[-1] > 10:
                final_x_positions.append(x)

        # Les traits détectés servent de positions précises, mais ce sont les
        # libellés « 8h », « 9h »… qui donnent le sens : un trait manquant ne
        # décale donc plus toute la suite de la journée.
        ancres = _lire_ancres_horaires(chemin_pdf, page_num, y_start, y_end, dpi, start_x)

        # Traits lus dans le PDF (exacts) ; la détection par morphologie ne sert
        # que de repli si le PDF n'en fournit pas.
        traits_pdf = [x - start_x + PADDING
                      for x in _traits_entete(chemin_pdf, page_num, y_start, y_end, dpi)]
        traits_pdf = [x for x in traits_pdf if 0 <= x <= padded_img.shape[1]]
        separateurs = traits_pdf if len(traits_pdf) >= len(final_x_positions) else final_x_positions

        REFERENCES_TEMPS = _construire_references(ancres, separateurs, padded_img.shape[1])

        heures_pleines = [h for h, _ in ancres]
        print(f"   Heures pleines repérées : {heures_pleines[0]}h → {heures_pleines[-1]}h "
              f"({len(final_x_positions)} traits détectés)")

        if DEBUG:
            debug_out = padded_img.copy()
            for x, _label in REFERENCES_TEMPS:
                cv2.line(debug_out, (x, 1), (x, padded_img.shape[0]), (0, 0, 255), 1)
            _sauver_debug(debug_out, "debug_lignes_heures.png")

        print(f"✅ {len(REFERENCES_TEMPS)} repères horaires extraits "
              f"(grille : x={GLOBAL_START_X} → {GLOBAL_END_X}).")
        return REFERENCES_TEMPS

    except Exception as e:
        print(f"⚠️ Échec de l'extraction dynamique ({e}). Chargement des valeurs de secours.")
        return _charger_valeurs_secours(largeur_page, dpi)


# =====================================================================
# EXTRACTION DES JOURNÉES
# =====================================================================

def deviner_annee(jour, mois, aujourdhui=None):
    """CORRECTIF #6 : le PDF n'indique que « 6/avr ». On retient l'année qui
    place la date au plus près d'aujourd'hui (l'EDT est un document glissant)."""
    if ANNEE_FORCEE:
        return ANNEE_FORCEE
    aujourdhui = aujourdhui or datetime.now()
    meilleure, meilleur_ecart = None, None
    for annee in (aujourdhui.year - 1, aujourdhui.year, aujourdhui.year + 1):
        try:
            candidate = datetime(annee, mois, jour)
        except ValueError:
            continue  # 29 février d'une année non bissextile
        ecart = abs((candidate - aujourdhui).days)
        if meilleur_ecart is None or ecart < meilleur_ecart:
            meilleure, meilleur_ecart = annee, ecart
    return meilleure or aujourdhui.year


def extraire_zones_jours_pdf(chemin_pdf):
    """Repère la bande verticale de chaque journée et sa date réelle."""
    final_day_zones = []
    annee_courante = None
    mois_precedent = None

    with pdfplumber.open(chemin_pdf) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            words = page.extract_words()
            h_lines = [l['top'] for l in page.lines if l['width'] > 100 and l['orientation'] == 'h']
            r_lines = [r['top'] for r in page.rects if r['width'] > 100 and r['height'] < 5]
            all_y_lines = sorted({round(y, 1) for y in h_lines + r_lines})

            current_monday_date = None
            for w in sorted(words, key=lambda w: (w['top'], w['x0'])):
                text = w['text'].lower()

                match_date = REGEX_DATE.match(text)
                if match_date:
                    jour_str, mois_str = match_date.groups()
                    mois = MOIS_MAP[mois_str]
                    jour = int(jour_str)

                    if annee_courante is None:
                        annee_courante = deviner_annee(jour, mois)
                    elif mois_precedent is not None and mois < mois_precedent:
                        annee_courante += 1  # passage décembre -> janvier
                    mois_precedent = mois

                    try:
                        current_monday_date = datetime(annee_courante, mois, jour)
                    except ValueError:
                        print(f"⚠️ Date illisible ignorée : {text}")

                day_offset = -1
                for i, nom in enumerate(("lundi", "mardi", "mercredi", "jeudi", "vendredi")):
                    if nom in text:
                        day_offset = i
                        break

                if day_offset != -1 and current_monday_date:
                    actual_date = current_monday_date + timedelta(days=day_offset)
                    lines_above = [y for y in all_y_lines if y < w['top']]
                    exact_top = lines_above[-1] if lines_above else w['top'] - 10
                    lines_below = [y for y in all_y_lines if y > w['bottom']]
                    exact_bottom = lines_below[0] + 1 if lines_below else w['bottom'] + 70

                    if exact_bottom - exact_top > 300:
                        exact_bottom = exact_top + 150

                    final_day_zones.append({
                        'date': actual_date, 'top': exact_top, 'bottom': exact_bottom,
                        'page': page_idx + 1, 'pdf_height': page.height,
                    })

    return final_day_zones


# =====================================================================
# TRAITEMENT D'UNE JOURNÉE
# =====================================================================

def _vers_pdf(x_px):
    """Repère grille (pixels, 200 dpi) -> points PDF."""
    return (x_px - PADDING + GLOBAL_START_X) * 72 / DPI


def _exporter_debug_journee(image_page, cellules, grille, date_str):
    """Une imagette rognée par cellule + la vue d'ensemble annotée.

    Le cadre épouse la case elle-même. L'imagette, elle, est élargie à droite
    pour rattraper le libellé de salle qui déborde de sa bordure : tracer le
    cadre sur cette largeur-là le faisait mordre sur la grille vide et donnait
    l'impression d'un recadrage faux.
    """
    ech = DPI / 72
    hauteur, largeur = image_page.shape[:2]

    marge_px = int(round(MARGE_DROITE))
    haut = max(int(round(grille.haut * ech)) - 2, 0)
    bas = min(int(round(grille.bas * ech)) + 2, hauteur)
    gauche = max(int(round(grille.x_min * ech)) - 2, 0)
    droite = min(int(round(grille.x_max * ech)) + marge_px, largeur)

    # Les horaires sont écrits dans un bandeau ajouté au-dessus de la journée,
    # sur deux lignes en alternance : posés sur la case, ils recouvraient le
    # titre du cours et rendaient la vue illisible.
    bandeau = 26
    vue = cv2.copyMakeBorder(image_page[haut:bas, gauche:droite], bandeau, 0, 0, 0,
                             cv2.BORDER_CONSTANT, value=[255, 255, 255])

    for i, info in enumerate(cellules):
        y0_pdf, y1_pdf = grille.bornes_verticales(info['position'])
        y0 = max(int(round(y0_pdf * ech)), 0)
        y1 = min(int(round(y1_pdf * ech)), hauteur)
        x0 = max(int(round(info['x0'] * ech)), 0)
        x1 = min(int(round(info['x1'] * ech)), largeur)
        x_fin = min(int(round(info['x_fin'] * ech)), largeur)
        if x1 <= x0 or y1 <= y0:
            continue

        _sauver_debug(image_page[y0:y1, x0:x_fin], date_str,
                      f"cours_{i:02d}_{info['start']}-{info['end']}_{info['position']}.jpg")

        couleur = {"FULL": (0, 0, 255), "TOP": (255, 0, 0),
                   "BOTTOM": (0, 150, 255)}[info['position']]
        cx0, cx1 = x0 - gauche, x1 - gauche
        cy0, cy1 = y0 - haut + bandeau, y1 - haut + bandeau
        cv2.rectangle(vue, (cx0, cy0), (cx1, cy1), couleur, 2)

        ligne = bandeau - 14 if i % 2 else bandeau - 4
        cv2.putText(vue, f"{info['start']}-{info['end']}", (cx0 + 2, ligne),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.34, couleur, 1, cv2.LINE_AA)
        cv2.line(vue, (cx0, ligne + 2), (cx0, bandeau), couleur, 1)

    _sauver_debug(vue, date_str, "overview_debug.jpg")


def _horaires_cellules(grille, cellules, mots, vers_heure):
    """Ajoute horaires et bord droit d'export à chaque cellule."""
    marge_pdf = MARGE_DROITE * 72 / DPI
    horodatees = []

    for cellule in cellules:
        debut, fin = vers_heure(cellule['x0']), vers_heure(cellule['x1'])
        if not debut or not fin or debut >= fin:
            print(f"    ⏭️  Cellule ignorée (horaires illisibles : {debut} -> {fin})")
            continue

        # Le libellé de salle déborde parfois à droite de sa bordure
        # (« U3-307/308 » finit 8 pt après la case). On ne rallonge l'imagette
        # que de ce débordement-là, mesuré sur les mots qui commencent dans la
        # cellule : une marge fixe capturait 40 px de grille vide dès que la
        # salle tenait dans la case, comme « U3-Amphi ».
        y0, y1 = grille.bornes_verticales(cellule['position'])
        dedans = [m['x1'] for m in mots
                  if cellule['x0'] - 2 <= m['x0'] < cellule['x1']
                  and y0 - 0.5 <= m['top'] < y1 + 1.5]
        x_texte = max(dedans) + 1 if dedans else cellule['x1']

        # Sans jamais mordre sur la cellule suivante de la même moitié.
        voisins = [c['x0'] for c in cellules
                   if c['x0'] > cellule['x1'] + 1
                   and (c['position'] == cellule['position']
                        or "FULL" in (c['position'], cellule['position']))]
        x_fin = min([max(cellule['x1'], x_texte), grille.x_max + marge_pdf] + voisins)

        horodatees.append({**cellule, "start": debut, "end": fin, "x_fin": x_fin})

    return horodatees


def traiter_journee(zone, images_pdf, page_pdf, liste_cours_json):
    """Extrait les cours d'une journée. Retourne True si la journée est fiable.

    Géométrie ET contenu viennent du vectoriel du PDF : les bordures noires des
    cases donnent les limites exactes des cours, la couche texte leur contenu.
    Aucune IA, aucune morphologie d'image.
    """
    page_idx = zone['page'] - 1
    if page_idx >= len(images_pdf):
        print(f"  ⚠️ Page {zone['page']} absente du rendu.")
        return False

    date_str_fmt = zone['date'].strftime('%Y-%m-%d')
    print(f"  📅 {date_str_fmt}")

    x_min_pdf = _vers_pdf(PADDING)
    x_max_pdf = _vers_pdf(GLOBAL_END_X - GLOBAL_START_X + PADDING)
    vers_heure = lambda x_pdf: obtenir_heure_proche(x_pdf * DPI / 72 - GLOBAL_START_X + PADDING)

    try:
        grille = lecture_pdf.GrilleJour(page_pdf, zone, x_min_pdf, x_max_pdf)
        mots = lecture_pdf.mots_de_la_bande(page_pdf, zone, x_min_pdf)
        cellules = _horaires_cellules(grille, grille.cellules(mots), mots, vers_heure)
    except Exception as e:
        print(f"  ❌ Découpage impossible pour {date_str_fmt} : {e}")
        return False

    if DEBUG:
        image_page = cv2.cvtColor(np.array(images_pdf[page_idx]), cv2.COLOR_RGB2BGR)
        _exporter_debug_journee(image_page, cellules, grille, date_str_fmt)

    for cellule in cellules:
        block = lecture_pdf.lire_cellule(grille, cellule['x0'], cellule['x1'],
                                         cellule['position'], mots, vers_heure)
        if block is None:
            continue

        # La géométrie des bordures fait autorité sur les horaires.
        block['start'], block['end'] = cellule['start'], cellule['end']

        col_txt = (block.get('color') or 'BLANC').upper()

        if 'ORANGE' in col_txt:
            continue

        # Toute cellule de la moitié HAUTE est écartée, qu'un cours occupe ou
        # non la moitié basse : le haut appartient à l'autre demi-promo.
        # Mettre EDT_COURS_HAUT=1 pour les publier quand même.
        if cellule['position'] == "TOP" and not GARDER_COURS_DU_HAUT:
            continue

        c_txt = (block.get('course') or "Cours").strip()
        if len(c_txt) < 2 or c_txt.lower() in ("inconnu", "cours inconnu", "cours"):
            continue

        p_full = (block.get('prof') or "").strip()
        grp = (block.get('group') or '')
        grp_str = f"[{grp}]" if grp else ""

        if p_full and p_full not in c_txt:
            titre = f"{grp_str} {c_txt} ({p_full})".strip()
        else:
            titre = f"{grp_str} {c_txt}".strip()

        titre = titre.replace("[] ", "")
        if 'JAUNE' in col_txt:
            titre = "[EXAMEN] " + titre

        salle = block.get('room') or "Non attribuée"
        print(f"    [+] {titre} ({cellule['start']}-{cellule['end']}) en {salle}")

        liste_cours_json.append({
            "date": date_str_fmt,
            "start": cellule["start"],
            "end": cellule["end"],
            "titre": titre,
            "room": salle,
            "prof": p_full or "Inconnu",
        })

    return True


# =====================================================================
# COMPARAISON / SORTIES
# =====================================================================

def deduplicer(cours_list):
    """Écarte le même cours détecté deux fois sur des étendues qui se recouvrent.

    Une case englobante et la case réelle donnent parfois le même titre et la
    même salle sur deux créneaux différents (« Tél. Spat. (MA) » en 12h00-15h45
    ET en 13h15-16h15). On conserve la plus courte : c'est la case du cours,
    l'autre déborde sur la zone vide qui l'entoure.
    """
    def minutes(h):
        return int(h[:2]) * 60 + int(h[3:5])

    gardes = []
    for c in sorted(cours_list, key=lambda c: minutes(c['end']) - minutes(c['start'])):
        double = any(
            g['date'] == c['date'] and g['titre'] == c['titre'] and g['room'] == c['room']
            and minutes(c['start']) < minutes(g['end']) and minutes(g['start']) < minutes(c['end'])
            for g in gardes
        )
        if double:
            print(f"    ⏭️  Doublon écarté : {c['date']} {c['start']}-{c['end']} {c['titre']}")
        else:
            gardes.append(c)
    return sorted(gardes, key=lambda c: (c['date'], c['start']))


def _cle_cours(cours):
    """CORRECTIF #7 : la clé inclut le titre, sinon les créneaux dédoublés
    (SPLIT : un cours en haut + un en bas à la même heure) s'écrasent l'un
    l'autre et leurs modifications passent inaperçues."""
    return f"{cours['date']}_{cours['start']}_{cours.get('titre', '')}"


def comparer_emplois_du_temps(anciennes_donnees, nouvelles_donnees):
    modifications = []
    anciens = {_cle_cours(c): c for c in anciennes_donnees}
    nouveaux = {_cle_cours(c): c for c in nouvelles_donnees}

    for cle, nouveau in nouveaux.items():
        if cle not in anciens:
            ajout = dict(nouveau)
            ajout['type'] = 'ajout'
            modifications.append(ajout)
            continue

        ancien = anciens[cle]
        changements = {
            attr: {'ancien': ancien.get(attr), 'nouveau': nouveau.get(attr)}
            for attr in ('titre', 'room', 'prof', 'end')
            if nouveau.get(attr) != ancien.get(attr)
        }
        if changements:
            modifications.append({
                "type": "modification",
                "date": nouveau['date'], "start": nouveau['start'], "end": nouveau['end'],
                "titre": nouveau['titre'], "changements": changements,
            })

    for cle, ancien in anciens.items():
        if cle not in nouveaux:
            suppression = dict(ancien)
            suppression['type'] = 'suppression'
            modifications.append(suppression)

    return modifications


def construire_ics(cours_list, chemin=FICHIER_ICS):
    calendrier = Calendar()
    tz = ZoneInfo('Europe/Paris')

    # Cadence de rafraîchissement demandée aux clients abonnés. Apple Calendar
    # et Outlook la respectent ; Google Agenda l'ignore et garde son propre
    # rythme (8 à 24 h), qu'aucun en-tête ne permet de forcer.
    calendrier.extra.append(ContentLine(name='X-WR-CALNAME', value='EDT STRI M1'))
    calendrier.extra.append(ContentLine(name='X-PUBLISHED-TTL', value='PT1H'))
    calendrier.extra.append(
        ContentLine(name='REFRESH-INTERVAL', params={'VALUE': ['DURATION']}, value='PT1H'))

    for cours in cours_list:
        try:
            h_start, m_start = map(int, cours['start'].split('h'))
            h_end, m_end = map(int, cours['end'].split('h'))
            date_obj = datetime.strptime(cours['date'], '%Y-%m-%d')

            debut = date_obj.replace(hour=h_start, minute=m_start, tzinfo=tz)
            fin = date_obj.replace(hour=h_end, minute=m_end, tzinfo=tz)
            if fin <= debut:
                print(f"⚠️ Horaires incohérents ignorés : {cours['titre']} "
                      f"({cours['start']}-{cours['end']})")
                continue

            evt = Event()
            evt.summary = cours['titre']
            evt.location = cours.get('room', '')
            evt.begin = debut
            evt.end = fin
            # CORRECTIF #8 : UID stable -> les agendas abonnés mettent à jour
            # l'événement au lieu de le supprimer puis le recréer à chaque run.
            empreinte = f"{cours['date']}|{cours['start']}|{cours['end']}|{cours['titre']}"
            evt.uid = hashlib.md5(empreinte.encode("utf-8")).hexdigest() + "@edt-stri"

            calendrier.events.append(evt)

        except Exception as e:
            print(f"⚠️ Erreur lors de l'ajout du cours {cours.get('titre', 'inconnu')} : {e}")

    # `newline=""` est indispensable : la bibliothèque sérialise déjà en CRLF,
    # et le mode texte de Windows retraduisait chaque \n en \r\n — le fichier
    # partait donc avec des fins de ligne \r\r\n, invalides au regard de la
    # RFC 5545. Google Agenda les tolérait, les clients stricts (iOS, Outlook)
    # refusaient l'abonnement avec « Échec de la validation ».
    Path(chemin).write_text(calendrier.serialize(), encoding='utf-8', newline='')
    return len(calendrier.events)


def charger_anciennes_donnees(chemin=FICHIER_JSON):
    if not Path(chemin).exists():
        return []
    try:
        with open(chemin, "r", encoding="utf-8") as f:
            donnees = json.load(f)
        return donnees if isinstance(donnees, list) else []
    except (json.JSONDecodeError, OSError) as e:
        print(f"⚠️ {chemin} illisible ({e}), il sera régénéré.")
        return []


# =====================================================================
# PROGRAMME PRINCIPAL
# =====================================================================

def principale():
    if not Path(FICHIER_PDF).exists():
        print(f"❌ {FICHIER_PDF} introuvable.")
        return 1

    print("📏 Calcul dynamique des références horaires...")
    if not extraire_positions_heures_pdf(FICHIER_PDF, dpi=DPI):
        print("❌ Impossible d'établir les repères horaires. Abandon (données conservées).")
        envoyer_alerte_discord("Impossible d'extraire les repères horaires du PDF : "
                               "la mise en page a probablement changé.")
        return 1

    print("✂️ Traitement du PDF...")
    zones = extraire_zones_jours_pdf(FICHIER_PDF)
    if not zones:
        print("❌ Aucune journée détectée dans le PDF. Abandon (données conservées).")
        envoyer_alerte_discord("Aucune journée n'a pu être détectée dans le PDF.")
        return 1

    images_pdf = convert_from_path(FICHIER_PDF, poppler_path=POPPLER_PATH, dpi=DPI)

    nouvelles_donnees = []
    jours_en_echec = []

    # Plus d'appel réseau par créneau : la lecture est locale et instantanée,
    # donc plus besoin de pauses entre les semaines.
    with pdfplumber.open(FICHIER_PDF) as pdf:
        for zone in zones:
            if not traiter_journee(zone, images_pdf, pdf.pages[zone['page'] - 1],
                                   nouvelles_donnees):
                jours_en_echec.append(zone['date'].strftime('%Y-%m-%d'))

    nouvelles_donnees = deduplicer(nouvelles_donnees)

    anciennes_donnees = charger_anciennes_donnees()

    # CORRECTIF #2 : on n'écrase JAMAIS des données valides par un résultat
    # partiel — sinon une extraction ratée annonce l'annulation de tous les
    # cours et vide l'agenda publié sur Drive.
    if anciennes_donnees and (jours_en_echec or not nouvelles_donnees):
        detail = ", ".join(jours_en_echec) if jours_en_echec else "aucun cours extrait"
        print(f"❌ Extraction incomplète ({detail}). Données précédentes conservées.")
        envoyer_alerte_discord(
            f"L'extraction a échoué pour : **{detail}**.\n"
            "L'emploi du temps publié n'a pas été modifié ; nouvel essai à la prochaine exécution."
        )
        return 1

    if jours_en_echec:
        print(f"⚠️ Première exécution avec des journées incomplètes : {', '.join(jours_en_echec)}")

    if anciennes_donnees:
        envoyer_notification_discord(comparer_emplois_du_temps(anciennes_donnees, nouvelles_donnees))

    with open(FICHIER_JSON, "w", encoding="utf-8") as f:
        json.dump(nouvelles_donnees, f, ensure_ascii=False, indent=2)

    nb_evenements = construire_ics(nouvelles_donnees)
    print(f"✅ Terminé ! {FICHIER_ICS} généré avec {nb_evenements} cours.")

    creds = obtenir_identifiants()
    agenda_id = synchroniser_agenda(nouvelles_donnees, creds)
    if agenda_id:
        afficher_lien_abonnement(agenda_id, creds)
    televerser_sur_google_drive(FICHIER_ICS, DRIVE_FOLDER_ID, creds)
    return 0


if __name__ == "__main__":
    if "--telecharger" in sys.argv:
        cibles = [a for a in sys.argv[1:] if not a.startswith("--")]
        sys.exit(0 if telecharger_pdf(cibles[0] if cibles else None) else 1)
    sys.exit(principale())

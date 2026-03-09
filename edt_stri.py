import os
import json
import shutil
from pathlib import Path
import re
import platform
import time
from datetime import datetime, timedelta
import requests
from ics import Calendar, Event
import pdfplumber
from pdf2image import convert_from_path
import pytz
import numpy as np
import cv2

# --- NOUVELLE BIBLIOTHÈQUE GOOGLE ---
from google import genai
from google.genai import types

# --- BIBLIOTHÈQUES GOOGLE DRIVE ---
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# --- CONFIGURATION SÉCURISÉE ---
cles_brutes = os.environ.get("GEMINI_API_KEYS", "")
API_KEYS = [cle.strip() for cle in cles_brutes.split(",")] if cles_brutes else []

if not API_KEYS or not API_KEYS[0]:
    print("❌ ERREUR : Aucune clé API Gemini trouvée dans les variables d'environnement.")
    exit(1)

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

MY_GROUPS = ["GB", "GC"]
ANNEE = 2026

MODELS = [
    "gemini-2.5-flash",
    "gemini-3-flash-preview",
    "gemini-2.5-flash-preview-09-2025",
    "gemini-2.0-flash",
    "gemini-2.0-flash-001",
    "gemini-flash-latest"
]

DRIVE_FOLDER_ID = "1ID97m9gVzOqcLvdYBAabUo5wZKzZ5Nj-"

REFERENCES_TEMPS = []

if platform.system() == "Windows":
    POPPLER_PATH = r"D:\Mes Projets\edt_stri\poppler\Library\bin"
else:
    POPPLER_PATH = None
    
PROFS = {
    "AnAn": "Andréi ANDRÉI", "AA": "André AOUN", "AB": "Abdelmalek BENZEKRI",
    "AL": "Abir LARABA", "BC": "Bilal CHEBARO", "BTJ": "Boris TIOMELA JOU",
    "CC": "Cédric CHAMBAULT", "CG": "Christine GALY", "CT": "Cédric TEYSSIE",
    "EG": "Eric GONNEAU", "EL": "Emmanuel LAVINAL", "FM": "Frédéric MOUTIER",
    "GR": "Gérard ROUZIES", "JGT": "Jean-Guy TARTARIN", "JS": "Jérôme SOKOLOFF",
    "KB": "Ketty BRAVO", "LC": "Louisa COT", "MCL": "Marie-Christine LAGASQUIÉ",
    "MM": "MUSTAPHA MOJAHID", "OC": "Olivier CRIVELLARO", "OM": "Olfa MECHI",
    "PA": "Philippe ARGUEL", "PIL": "Pierre LOTTE",
    "PL": "Philippe LATU", "PT": "Patrice TORGUET", "RK": "Rahim KACIMI",
    "RL": "Romain LABORDE", "SB": "Sonia BADENE", "SL": "Séverine LALANDE",
    "TD": "Thierry DESPRATS", "TG": "Thierry GAYRAUD", "BA": "BA"
}

# =====================================================================
# FONCTIONS DISCORD
# =====================================================================

def envoyer_notification_discord(modifications):
    if not DISCORD_WEBHOOK_URL or not modifications:
        return

    description = "L'emploi du temps a été mis à jour ! Voici les changements :\n\n"
    
    for modif in modifications:
        try:
            date_fr = datetime.strptime(modif['date'], '%Y-%m-%d').strftime('%d/%m/%Y')
        except:
            date_fr = modif['date']
            
        titre = modif.get('titre', 'Cours inconnu')
        heures = f"{modif.get('start', '?')} - {modif.get('end', '?')}"
        
        if modif['type'] == 'ajout':
            description += f"🟢 **AJOUT** : {titre} le {date_fr} ({heures}) en salle {modif.get('room', 'Non attribuée')} avec {modif.get('prof', 'Inconnu')}\n"
        elif modif['type'] == 'suppression':
            description += f"🔴 **ANNULATION** : {titre} le {date_fr} ({heures})\n"
        elif modif['type'] == 'modification':
            description += f"🟠 **MODIFICATION** : {titre} le {date_fr} ({heures})\n"
            for champ, valeurs in modif.get('changements', {}).items():
                nom_champ = {"room": "Salle", "prof": "Prof", "end": "Heure de fin", "titre": "Nom du cours"}.get(champ, champ)
                description += f"   ↳ *{nom_champ}* : ~~{valeurs['ancien']}~~ ➔ **{valeurs['nouveau']}**\n"
        description += "\n"

    if len(description) > 4000:
        description = description[:3900] + "\n... (trop de changements pour tout afficher)."

    payload = {
        "username": "Bot EDT STRI",
        "avatar_url": "https://cdn-icons-png.flaticon.com/512/2602/2602282.png",
        "embeds": [{
            "title": "🚨 Changements détectés dans l'emploi du temps !",
            "description": description,
            "color": 16753920 
        }]
    }

    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload)
        print("✅ Notification Discord envoyée !")
    except Exception as e:
        print(f"❌ Erreur lors de l'envoi Discord : {e}")

# =====================================================================
# FONCTIONS UTILITAIRES ET OPENCV
# =====================================================================

def obtenir_heure_proche(x_detecte):
    meilleur_match = min(REFERENCES_TEMPS, key=lambda item: abs(item[0] - x_detecte))
    return meilleur_match[1]

def analyser_heure_chaine(heure_str):
    h, m = heure_str.split('h')
    return int(h), int(m)

def filtrer_et_dessiner_rectangles(liste_rects, tolerance=10):
    liste_rects.sort(key=lambda k: k['x1'])
    rects_valides = []
    indices_traites = set()

    for i in range(len(liste_rects)):
        if i in indices_traites: continue
        current = liste_rects[i]
        for j in range(i + 1, len(liste_rects)):
            if j in indices_traites: continue
            other = liste_rects[j]
            if abs(current['x1'] - other['x1']) < tolerance:
                if abs(current['x2'] - other['x2']) < tolerance:
                    y_min = min(current['y'], other['y'])
                    y_max = max(current['y'] + current['h'], other['y'] + other['h'])
                    current['y'] = y_min
                    current['h'] = y_max - y_min
                    indices_traites.add(j)
                else:
                    if other['y'] > current['y']: current = other
                    indices_traites.add(j)
        rects_valides.append(current)
    return rects_valides

def tracer_grand_rectangle(img):
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 100, 255, cv2.THRESH_BINARY_INV)
    
    col_sums = np.sum(thresh, axis=0) / 255 
    x_lines = []
    
    for x in range(w):
        if col_sums[x] > h * 0.6: 
            if not x_lines or x - x_lines[-1] > 15: 
                x_lines.append(x)
                
    row_sums = np.sum(thresh, axis=1) / 255
    y_lines = []
    
    for y in range(h):
        if row_sums[y] > w * 0.5:
            if not y_lines or y - y_lines[-1] > 15:
                y_lines.append(y)
                
    if len(x_lines) >= 2 and len(y_lines) >= 2:
        x_droite_jour = x_lines[1]
        x_droite_planning = x_lines[-1]
        y_haut = y_lines[0]
        y_bas = y_lines[-1]
        cv2.rectangle(img, (x_droite_jour, y_haut), (x_droite_planning, y_bas), (0, 0, 0), 5)
    
    return img

def detecter_et_remplir_cellules_pointillees(img):
    if img is None:
        return

    try:
        img = tracer_grand_rectangle(img)
    except NameError:
        pass

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)
    h, w = img.shape[:2]

    kernel_v = cv2.getStructuringElement(cv2.MORPH_RECT, (1, h // 2))
    solid_v = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel_v)
    
    kernel_h = cv2.getStructuringElement(cv2.MORPH_RECT, (w // 10, 1))
    solid_h = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel_h)
    
    no_solid = cv2.subtract(thresh, cv2.bitwise_or(solid_v, solid_h))

    contours, _ = cv2.findContours(no_solid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    tirets = []
    for cnt in contours:
        x, y, w_rect, h_rect = cv2.boundingRect(cnt)
        rect = cv2.minAreaRect(cnt)
        (w_rot, h_rot) = rect[1]
        
        if w_rot == 0 or h_rot == 0:
            continue
            
        epaisseur = min(w_rot, h_rot)
        longueur = max(w_rot, h_rot)

        if longueur >= 4 and epaisseur <= 6 and w_rect <= 20:
            x_center = x + (w_rect // 2)
            tirets.append((x_center, y, y + h_rect))

    tirets.sort(key=lambda t: t[0])
    groupes_lignes = []
    groupe_actuel = []

    for tiret in tirets:
        if not groupe_actuel:
            groupe_actuel.append(tiret)
        else:
            avg_x = sum(t[0] for t in groupe_actuel) / len(groupe_actuel)
            if abs(tiret[0] - avg_x) <= 6:
                groupe_actuel.append(tiret)
            else:
                groupes_lignes.append(groupe_actuel)
                groupe_actuel = [tiret]
    if groupe_actuel:
        groupes_lignes.append(groupe_actuel)

    result_img = img.copy()
    for groupe in groupes_lignes:
        if 2 <= len(groupe) <= 7:
            min_y = min(t[1] for t in groupe)
            max_y = max(t[2] for t in groupe)
            hauteur_totale = max_y - min_y

            if hauteur_totale >= h * 0.45:
                final_x = int(sum(t[0] for t in groupe) / len(groupe))
                cv2.line(result_img, (final_x, 0), (final_x, h), (0, 0, 0), 3)

    return result_img

def detecter_creneaux_cours_opencv(pil_image, date_str):
    img_cv = np.array(pil_image)
    if img_cv.shape[2] == 3:
        img_cv = cv2.cvtColor(img_cv, cv2.COLOR_RGB2BGR)
        
    img_raw = img_cv.copy()
    
    hsv = cv2.cvtColor(img_cv, cv2.COLOR_BGR2HSV)
    mask1 = cv2.inRange(hsv, np.array([0, 70, 50]), np.array([10, 255, 255]))
    mask2 = cv2.inRange(hsv, np.array([170, 70, 50]), np.array([180, 255, 255]))
    red_mask = mask1 + mask2
    img_cv[red_mask > 0] = [0, 255, 0]
    
    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]

    total_h, total_w = img_cv.shape[:2]

    ver_kernel_long = cv2.getStructuringElement(cv2.MORPH_RECT, (1, total_h // 3))
    vertical_lines_only = cv2.erode(thresh, ver_kernel_long, iterations=1)
    vertical_lines_only = cv2.dilate(vertical_lines_only, ver_kernel_long, iterations=1)
    
    contours_v, _ = cv2.findContours(vertical_lines_only, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    x_positions = [cv2.boundingRect(c)[0] for c in contours_v if cv2.boundingRect(c)[3] > total_h/2]
    x_positions.sort()

    if len(x_positions) < 3: 
        x_start, x_end = 0, total_w
    else: 
        x_start, x_end = x_positions[1], x_positions[-1] + 5

    img_crop = img_cv[:, x_start:x_end]
    img_crop_raw = img_raw[:, x_start:x_end]
    
    img_crop = cv2.copyMakeBorder(img_crop, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=[255, 255, 255])
    img_crop_raw = cv2.copyMakeBorder(img_crop_raw, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=[255, 255, 255])
    
    gray_crop = cv2.cvtColor(img_crop, cv2.COLOR_BGR2GRAY)
    thresh_crop = cv2.threshold(gray_crop, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    
    hor_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (20, 1))
    ver_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 15))
    
    h_lines = cv2.dilate(cv2.erode(thresh_crop, hor_kernel), hor_kernel)
    v_lines = cv2.dilate(cv2.erode(thresh_crop, ver_kernel), ver_kernel)
    grid_mask = cv2.addWeighted(h_lines, 1, v_lines, 1, 0)
    grid_mask = cv2.morphologyEx(grid_mask, cv2.MORPH_CLOSE, np.ones((5,5), np.uint8))

    contours, _ = cv2.findContours(grid_mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

    liste_brute = []
    current_w = img_crop.shape[1]
    
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        if w * h < 1000 or w > (current_w * 0.80): continue
        
        roi = gray_crop[y:y+h, x:x+w]
        if roi.size > 0 and cv2.countNonZero(cv2.threshold(roi[5:-5, 5:-5], 150, 255, cv2.THRESH_BINARY_INV)[1]) < 50:
            continue
        liste_brute.append({'x1': x, 'x2': x + w, 'y': y, 'h': h, 'w': w})

    liste_finale = filtrer_et_dessiner_rectangles(liste_brute)
    liste_finale.sort(key=lambda k: k['x1'])

    detected_slots = []
    folder_path = Path("export_cours") / date_str
    folder_path.mkdir(parents=True, exist_ok=True)
    
    if liste_finale:
        y_top_global = min(c['y'] for c in liste_finale)
        y_bottom_global = max(c['y'] + c['h'] for c in liste_finale)
        h_global = max(57, y_bottom_global - y_top_global)
        
        debug_img = img_crop_raw.copy()
        
        for i, cours in enumerate(liste_finale):
            real_x1 = cours['x1']
            real_x2 = cours['x2']
            start_str = obtenir_heure_proche(real_x1)
            end_str = obtenir_heure_proche(real_x2)
            
            cv2.rectangle(debug_img, (cours['x1'], y_top_global), (cours['x2'], y_top_global + h_global), (0, 0, 255), 2)
            roi_cours = img_crop_raw[y_top_global:y_top_global+h_global, cours['x1']:cours['x2']]
            
            if roi_cours.size > 0:
                safe_start = start_str.replace(':', 'h')
                safe_end = end_str.replace(':', 'h')
                filename = folder_path / f"slot_{i}_{safe_start}_{safe_end}.jpg"
                cv2.imwrite(str(filename), roi_cours)
                success, encoded_img = cv2.imencode('.jpg', roi_cours)
                if success:
                    detected_slots.append({
                        "bytes": encoded_img.tobytes(),
                        "start": start_str,
                        "end": end_str
                    })
            
        cv2.imwrite(str(folder_path / "overview_debug.jpg"), debug_img)
    
    return detected_slots

def analyser_image_creneau_avec_ia(image_bytes, start_model_idx, start_key_idx):
    prompt = """
    Analyse cette image de cours (créneau unique).
    === Données d'entrée ===
    - Une image JPEG d'un créneau de cours extrait d'un emploi du temps.
    - PROFS = {
        "AnAn": "Andréi ANDRÉI", "AA": "André AOUN", "AB": "Abdelmalek BENZEKRI",
        "AL": "Abir LARABA", "BC": "Bilal CHEBARO", "BTJ": "Boris TIOMELA JOU",
        "CC": "Cédric CHAMBAULT", "CG": "Christine GALY", "CT": "Cédric TEYSSIE",
        "EG": "Eric GONNEAU", "EL": "Emmanuel LAVINAL", "FM": "Frédéric MOUTIER",
        "GR": "Gérard ROUZIES", "JGT": "Jean-Guy TARTARIN", "JS": "Jérôme SOKOLOFF",
        "KB": "Ketty BRAVO", "LC": "Louisa COT", "MCL": "Marie-Christine LAGASQUIÉ",
        "MM": "MUSTAPHA MOJAHID", "OC": "Olivier CRIVELLARO", "OM": "Olfa MECHI",
        "PA": "Philippe ARGUEL", "PIL": "Pierre LOTTE",
        "PL": "Philippe LATU", "PT": "Patrice TORGUET", "RK": "Rahim KACIMI",
        "RL": "Romain LABORDE", "SB": "Sonia BADENE", "SL": "Séverine LALANDE",
        "TD": "Thierry DESPRATS", "TG": "Thierry GAYRAUD", "BA": "BA"
    } (Initiales possibles des profs et leur nom complet)
    - Tu dois transformer les initiales des profs en leur nom complet grâce à ce mapping. Si tu ne reconnais pas les initiales, laisse le texte tel quel. Avant de rendre le résultat, vérifie bien que le nom du prof est correct et complet (pas d'initiales restantes).
    
    === DÉTECTION STRUCTURE ===
    Regarde s'il y a une **ligne horizontale noire** de séparation au milieu.
    - OUI (Ligne noire) -> C'est "SPLIT". Il y a deux cours : un en **HAUT** (TOP), un en **BAS** (BOTTOM).
    - Si l'image contient seulemtent un TOP et en BAS une celulle blanche avec des traits verticals, c'est "TOP".
    - Si l'image contient seulemtent un BAS et en HAUT une celulle blanche avec des traits verticals, c'est "BOTTOM".
    - NON (Pas de ligne) -> C'est "FULL". C'est un seul cours sur toute la hauteur de l'image.
    - Si l'image est complètement blanche ou illisible, réponds avec un seul élément FULL avec course="Inconnu".
    - Si la case de la salle est rouge ou vert vide, room="Non attribuée".

    === EXTRACTION ===
    Pour chaque élément :
    - `position`: "FULL", "TOP", "BOTTOM".
    - `color`: "ORANGE"(#FFA800), "JAUNE"(#FFE800), "BLANC".
    - `course`: Texte principal.
    - `prof`: Nom.
    - `group`: "GB", "GC", "GA" ou null.
    - `room`: Salle.

    JSON attendu :
    [ { "position": "...", "color": "...", "course": "...", "prof": "...", "group": "...", "room": "..." } ]
    """

    response_schema = {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "position": {"type": "STRING"},
                "color": {"type": "STRING"},
                "course": {"type": "STRING"},
                "prof": {"type": "STRING", "nullable": True},
                "group": {"type": "STRING", "nullable": True},
                "room": {"type": "STRING", "nullable": True},
            },
            "required": ["position", "color", "course"]
        }
    }

    current_model_idx = start_model_idx
    current_key_idx = start_key_idx
    
    while True:
        try:
            active_key = API_KEYS[current_key_idx]
            client = genai.Client(api_key=active_key)
            model_name = MODELS[current_model_idx]
            
            response = client.models.generate_content(
                model=model_name,
                contents=[types.Content(parts=[types.Part.from_text(text=prompt), types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")])],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", 
                    response_schema=response_schema,
                    temperature=0.1,
                    safety_settings=[types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE")]
                )
            )
            
            if not response.text:
                time.sleep(1)
                continue
            
            return json.loads(response.text), current_model_idx, current_key_idx

        except Exception as e:
            err_str = str(e)
            if "429" in err_str:
                current_key_idx = (current_key_idx + 1) % len(API_KEYS)
                if current_key_idx == start_key_idx:
                    exit()
                else: continue 
            elif "503" in err_str or "500" in err_str:
                current_model_idx = (current_model_idx + 1) % len(MODELS)
                time.sleep(2)
            else:
                current_model_idx = (current_model_idx + 1) % len(MODELS)
                time.sleep(5)
                if current_model_idx == start_model_idx:
                      return [], current_model_idx, current_key_idx

def televerser_sur_google_drive(nom_fichier, dossier_id):
    print(f"☁️  Téléversement Drive...")
    try:
        SCOPES = ['https://www.googleapis.com/auth/drive.file']
        creds = None
        if os.path.exists('token.json'): creds = Credentials.from_authorized_user_file('token.json', SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token: creds.refresh(Request())
            else:
                if not os.path.exists('credentials.json'): return
                flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                creds = flow.run_local_server(port=0)
            with open('token.json', 'w') as token: token.write(creds.to_json())
            
        service = build('drive', 'v3', credentials=creds)
        query = f"name = '{nom_fichier}' and '{dossier_id}' in parents and trashed = false"
        results = service.files().list(q=query, fields="files(id)").execute()
        items = results.get('files', [])
        file_metadata = {'name': nom_fichier, 'parents': [dossier_id]}
        media = MediaFileUpload(nom_fichier, mimetype='text/calendar')
        
        if not items: 
            service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        else:
            del file_metadata['parents']
            service.files().update(fileId=items[0]['id'], body=file_metadata, media_body=media, fields='id').execute()
        print(f"✅ Fichier mis à jour sur Drive.")
    except Exception as e: 
        print(f"❌ Erreur Upload : {e}")

def extraire_positions_heures_pdf(chemin_pdf, page_num=0, dpi=200):
    pages = convert_from_path(chemin_pdf, dpi=dpi, poppler_path=POPPLER_PATH)
    img = np.array(pages[page_num])
    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)
    
    min_line_length = img.shape[1] // 3
    horiz_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (min_line_length, 1))
    horiz_lines = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, horiz_kernel)
    contours_h, _ = cv2.findContours(horiz_lines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    y_coords = sorted([cv2.boundingRect(c)[1] for c in contours_h])
    
    unique_y = []
    for y in y_coords:
        if not unique_y or y - unique_y[-1] > 10:
            unique_y.append(y)
            
    if len(unique_y) < 2: return []
    y_start, y_end = unique_y[0] - 2, unique_y[1] + 2
    
    crop_h = y_end - y_start
    kernel_v_long = cv2.getStructuringElement(cv2.MORPH_RECT, (1, crop_h - 10))
    major_v_lines = cv2.morphologyEx(thresh[y_start:y_end, :], cv2.MORPH_OPEN, kernel_v_long)
    cnts_v, _ = cv2.findContours(major_v_lines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    major_x = sorted([cv2.boundingRect(c)[0] for c in cnts_v])
    
    ux = []
    for x in major_x:
        if not ux or x - ux[-1] > 15: ux.append(x)
    
    start_x = ux[1] - 3 
    end_x = ux[-1] + 3
    
    final_crop = img[y_start:y_end, start_x:end_x]
    padding = 10
    padded_img = cv2.copyMakeBorder(final_crop, padding, padding, padding, padding, 
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
    
    raw_x_found = []
    for c in contours_all:
        x, y, w, h = cv2.boundingRect(c)
        if w < 10:
            raw_x_found.append(x + w//2)
            
    raw_x_found.sort()
    
    final_x_positions = []
    for x in raw_x_found:
        if not final_x_positions or x - final_x_positions[-1] > 10:
            final_x_positions.append(x)

    if len(final_x_positions) > 18:
        x_12h00 = final_x_positions[17]
        if final_x_positions[18] - x_12h00 > 50:
            final_x_positions.insert(18, x_12h00 + 35)

    if len(final_x_positions) > 22:
        x_13h00 = final_x_positions[21]
        if final_x_positions[22] - x_13h00 > 40:
            final_x_positions.insert(22, x_13h00 + 20)

    heures_attendues = [
        "07h45", "08h00", "08h15", "08h30", "08h45", "09h00", "09h15", "09h30", "09h45", "10h00", "10h15", "10h30",
        "10h45", "11h00", "11h15", "11h30", "11h45", "12h00", "12h15", "12h30", "12h45", "13h00", "13h15", "13h30",
        "13h45", "14h00", "14h15", "14h30", "14h45", "15h00", "15h15", "15h30", "15h45", "16h00", "16h15", "16h30",
        "16h45", "17h00", "17h15", "17h30", "17h45", "18h00", "18h15", "18h30", "18h45", "19h00", "19h15", "19h30",
        "19h45", "20h00"
    ]
    
    refs_temps = []
    for i, x in enumerate(final_x_positions):
        label = heures_attendues[i] if i < len(heures_attendues) else f"?"
        refs_temps.append((x, label))
        
    return refs_temps

def extraire_zones_jours_pdf(chemin_pdf):
    final_day_zones = []
    with pdfplumber.open(chemin_pdf) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            words = page.extract_words()
            h_lines = [l['top'] for l in page.lines if l['width'] > 100 and l['orientation'] == 'h']
            r_lines = [r['top'] for r in page.rects if r['width'] > 100 and r['height'] < 5]
            all_y_lines = sorted(list(set([round(y, 1) for y in h_lines + r_lines])))
            
            current_monday_date = None
            sorted_words = sorted(words, key=lambda w: (w['top'], w['x0']))
            
            for w in sorted_words:
                text = w['text'].lower()
                match_date = re.match(r'(\d{1,2})/(janv|févr|mars|avr|mai|juin|sept|oct|nov|déc)', text)
                if match_date:
                    d, m_str = match_date.groups()
                    m_map = {'janv':1, 'févr':2, 'mars':3, 'avr':4, 'mai':5, 'juin':6, 'sept':9, 'oct':10, 'nov':11, 'déc':12}
                    try: current_monday_date = datetime(ANNEE, m_map[m_str], int(d))
                    except: pass
                
                day_offset = -1
                if "lundi" in text: day_offset = 0
                elif "mardi" in text: day_offset = 1
                elif "mercredi" in text: day_offset = 2
                elif "jeudi" in text: day_offset = 3
                elif "vendredi" in text: day_offset = 4
                
                if day_offset != -1 and current_monday_date:
                    actual_date = current_monday_date + timedelta(days=day_offset)
                    lines_above = [y for y in all_y_lines if y < w['top']]
                    exact_top = lines_above[-1] if lines_above else w['top'] - 10
                    lines_below = [y for y in all_y_lines if y > w['bottom']]
                    exact_bottom = lines_below[0]+1 if lines_below else w['bottom'] + 70
                    
                    if exact_bottom - exact_top > 300: exact_bottom = exact_top + 150 
                    final_day_zones.append({'date': actual_date, 'top': exact_top, 'bottom': exact_bottom, 'page': page_idx + 1, 'pdf_height': page.height})

    return final_day_zones

# =====================================================================
# TRAITEMENT DES JOURS
# =====================================================================

def traiter_journee(zone, images_pdf, current_model_idx, current_key_idx, liste_cours_json):
    page_idx = zone['page'] - 1
    if page_idx >= len(images_pdf): 
        return current_model_idx, current_key_idx
        
    page_img = images_pdf[page_idx]
    scale_y = page_img.height / zone['pdf_height']
    
    day_img = page_img.crop((0, zone['top'] * scale_y, page_img.width, zone['bottom'] * scale_y))
    print(f"   📅 {zone['date'].strftime('%Y-%m-%d')}")

    date_str_fmt = zone['date'].strftime('%Y-%m-%d')
    day_img_np = np.array(day_img)
    day_img_cv = cv2.cvtColor(day_img_np, cv2.COLOR_RGB2BGR)
    
    day_img_rgb = cv2.cvtColor(day_img_cv, cv2.COLOR_BGR2RGB)
    slots_trouves = detecter_creneaux_cours_opencv(day_img_rgb, date_str_fmt)

    for slot_data in slots_trouves:
        img_bytes = slot_data["bytes"]
        start_str = slot_data["start"]
        end_str = slot_data["end"]

        raw_blocks, current_model_idx, current_key_idx = analyser_image_creneau_avec_ia(
            img_bytes, current_model_idx, current_key_idx
        )
        
        for block in raw_blocks:
            col_txt = (block.get('color') or 'BLANC').upper()
            pos_txt = (block.get('position') or 'FULL').upper()
            
            if 'ORANGE' in col_txt: continue
            
            grp = (block.get('group') or '')
            is_my_group = grp and any(g in grp for g in MY_GROUPS)
            
            keep = False
            if pos_txt == 'TOP': keep = is_my_group
            elif pos_txt == 'BOTTOM': keep = True
            else:
                if grp and "GA" in grp and not is_my_group: keep = False
                else: keep = True
            
            if keep:
                c_txt = (block.get('course') or "Cours").strip()
                if len(c_txt) < 2 : continue
                if c_txt.lower() in ["inconnu", "cours inconnu", "cours"]: continue
                
                grp_str = f"[{grp}]" if grp else ""
                p_txt = (block.get('prof') or "").strip()
                p_full = p_txt
                
                if p_full and p_full not in c_txt:
                    title = f"{grp_str} {c_txt} ({p_full})".strip()
                else:
                    title = f"{grp_str} {c_txt}".strip()
                
                title = title.replace("[] ", "")
                if 'JAUNE' in col_txt: title = "[EXAMEN] " + title
                
                print(f"      [+] Ajout : {title} ({start_str}-{end_str})")

                # Sauvegarde exclusive en JSON (l'ICS sera généré à la fin)
                liste_cours_json.append({
                    "date": date_str_fmt,
                    "start": start_str,
                    "end": end_str,
                    "titre": title,
                    "room": block.get('room') or "Non attribuée",
                    "prof": p_full or "Inconnu"
                })

    return current_model_idx, current_key_idx

def principale():
    global REFERENCES_TEMPS

    print("📏 Calcul dynamique des références horaires...")
    REFERENCES_TEMPS = extraire_positions_heures_pdf("edt.pdf", dpi=200)
    
    if not REFERENCES_TEMPS:
        print("❌ Erreur lors de l'extraction des heures.")
        return
        
    print("✂️ Traitement du PDF...")
    final_day_zones = extraire_zones_jours_pdf("edt.pdf")
    images_pdf = convert_from_path("edt.pdf", poppler_path=POPPLER_PATH, dpi=200)
    
    current_model_idx = 0 
    current_key_idx = 0
    nouvelles_donnees_cours = [] 

    for i, zone in enumerate(final_day_zones):
        if zone['date'].weekday() == 0 and i > 0:
            print("☕ Nouvelle semaine. Pause 60s...")
            time.sleep(60)
            
        current_model_idx, current_key_idx = traiter_journee(
            zone, images_pdf, current_model_idx, current_key_idx, nouvelles_donnees_cours
        )

    # --- LOGIQUE DE FUSION ET DE COMPARAISON DISCORD ---
    fichier_json = "edt_data.json"
    anciennes_donnees = []
    
    if os.path.exists(fichier_json):
        with open(fichier_json, "r", encoding="utf-8") as f:
            try:
                anciennes_donnees = json.load(f)
            except:
                pass

    # Déterminer la période couverte par le nouveau PDF
    dates_nouveau = [c['date'] for c in nouvelles_donnees_cours]
    if dates_nouveau:
        date_min = min(dates_nouveau)
        date_max = max(dates_nouveau)
    else:
        date_min = "9999-12-31"
        date_max = "0000-01-01"

    donnees_historiques = []
    donnees_a_comparer = []
    
    for c in anciennes_donnees:
        # Si le cours tombe pendant les semaines visibles dans le PDF
        if date_min <= c['date'] <= date_max:
            donnees_a_comparer.append(c)
        else:
            # S'il est en dehors du PDF (ex: semaines passées), on le garde précieusement
            donnees_historiques.append(c)

    if anciennes_donnees: 
        modifications = []
        anciens_dict = {f"{c['date']}_{c['start']}": c for c in donnees_a_comparer}
        nouveaux_dict = {f"{c['date']}_{c['start']}": c for c in nouvelles_donnees_cours}

        for cle, nouveau_cours in nouveaux_dict.items():
            if cle not in anciens_dict:
                nouveau_copy = nouveau_cours.copy()
                nouveau_copy['type'] = 'ajout'
                modifications.append(nouveau_copy)
            else:
                ancien_cours = anciens_dict[cle]
                changements = {}
                for attr in ['titre', 'room', 'prof', 'end']:
                    if nouveau_cours.get(attr) != ancien_cours.get(attr):
                        changements[attr] = {'ancien': ancien_cours.get(attr), 'nouveau': nouveau_cours.get(attr)}
                
                if changements:
                    modifications.append({
                        "type": "modification",
                        "date": nouveau_cours['date'],
                        "start": nouveau_cours['start'],
                        "end": nouveau_cours['end'],
                        "titre": nouveau_cours['titre'],
                        "changements": changements
                    })

        for cle, ancien_cours in anciens_dict.items():
            if cle not in nouveaux_dict:
                ancien_copy = ancien_cours.copy()
                ancien_copy['type'] = 'suppression'
                modifications.append(ancien_copy)

        envoyer_notification_discord(modifications)

    # Fusionner l'historique préservé avec les toutes nouvelles données
    toutes_les_donnees = donnees_historiques + nouvelles_donnees_cours

    with open(fichier_json, "w", encoding="utf-8") as f:
        json.dump(toutes_les_donnees, f, ensure_ascii=False, indent=2)
    # ------------------------------------------------

    # --- RECONSTRUCTION DE L'ICS COMPLET ---
    calendrier_global = Calendar()
    tz = pytz.timezone('Europe/Paris')
    
    for cours in toutes_les_donnees:
        ics_evt = Event()
        ics_evt.name = cours['titre']
        ics_evt.location = cours.get('room', '')
        
        try:
            h_start, m_start = map(int, cours['start'].split('h'))
            h_end, m_end = map(int, cours['end'].split('h'))
            date_obj = datetime.strptime(cours['date'], '%Y-%m-%d')
            
            # Application correcte du fuseau horaire
            start_dt = tz.localize(date_obj.replace(hour=h_start, minute=m_start))
            end_dt = tz.localize(date_obj.replace(hour=h_end, minute=m_end))
            
            ics_evt.begin = start_dt
            ics_evt.end = end_dt
            calendrier_global.events.add(ics_evt)
        except Exception as e:
            pass

    with open("edt.ics", 'w', encoding='utf-8') as f:
        f.writelines(calendrier_global.serialize())
        
    print("✅ Terminé avec succès ! Fichier edt.ics reconstruit et complet.")
    
    televerser_sur_google_drive("edt.ics", DRIVE_FOLDER_ID)
    shutil.rmtree("__pycache__", ignore_errors=True)

if __name__ == "__main__":
    principale()
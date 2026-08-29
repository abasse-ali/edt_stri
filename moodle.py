"""
Lecture du calendrier Moodle du STRI (le site eFormation).

L'emploi du temps vient d'un PDF ; les **rendus** — devoirs à déposer, dates
limites, examens déclarés dans un cours — vivent ailleurs : dans le calendrier
Moodle de https://www.stri.fr/eformation/. Moodle sait l'exporter au format
iCalendar, à une adresse personnelle et permanente.

Comment obtenir cette adresse (une seule fois) :

    1. https://www.stri.fr/eformation/calendar/view.php
    2. bouton « Exporter le calendrier »
    3. Événements à exporter : « Tous les événements »
       Durée : « Intervalle personnalisé » (la plus large : ~1 an)
    4. bouton « URL du calendrier » — et non « Exporter », qui télécharge un
       fichier figé
    5. copier l'adresse obtenue dans la variable MOODLE_ICS_URL

⚠️ Cette adresse contient `authtoken=…`, qui donne accès au calendrier
personnel sans mot de passe. Elle se traite comme un mot de passe : dans un
`.env` en local, dans un secret de dépôt en CI, jamais dans le code ni dans un
message affiché. C'est pourquoi ce module ne journalise jamais l'URL entière.

Le module ne fait que lire et traduire : il ne parle ni à Google, ni à Discord.
"""

import html
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from telechargement import variable_env

FUSEAU = ZoneInfo("Europe/Paris")
TIMEOUT_HTTP = 20

# Adresse d'export personnelle, cf. l'en-tête. Sans elle, rien n'est fait.
URL_ICS = variable_env("MOODLE_ICS_URL")

# Filtre facultatif : expression régulière testée sur le titre, le cours et la
# description. Sert à ne garder qu'un cours (« Rendu M1 ») quand le calendrier
# Moodle en mélange plusieurs. Vide = tout garder.
FILTRE = variable_env("MOODLE_FILTRE")

# Une date limite Moodle est un instant, pas une plage : `DURATION:PT0S`. Un
# événement de durée nulle est refusé par l'API Google, et illisible de toute
# façon dans une grille horaire — on lui donne une épaisseur.
DUREE_MINIMALE = timedelta(minutes=30)

# `P1DT2H30M` -> 1 jour, 2 h, 30 min. Les semaines (`W`) sont exclusives des
# autres champs dans la norme, mais les accepter ensemble ne coûte rien.
REGEX_DUREE = re.compile(
    r'^[+-]?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$')

REGEX_BALISE = re.compile(r'<[^>]+>')


def _masquer(url):
    """Rend l'URL affichable dans un journal : le jeton est remplacé.

    Les journaux de GitHub Actions sont lisibles par tout le monde sur un dépôt
    public. Un `authtoken` qui y traîne donne accès au calendrier personnel.
    """
    return re.sub(r'(authtoken|username|userid)=[^&]*', r'\1=***', url or "")


def telecharger(url=None):
    """Récupère le calendrier Moodle. Rend son texte, ou None en cas d'échec."""
    url = url or URL_ICS
    if not url:
        print("ℹ️ MOODLE_ICS_URL n'est pas définie : rien à récupérer.")
        return None
    try:
        reponse = requests.get(url, timeout=TIMEOUT_HTTP)
        reponse.raise_for_status()
    except Exception as e:
        print(f"❌ Calendrier Moodle inaccessible ({e}).")
        print(f"   Adresse utilisée : {_masquer(url)}")
        return None

    texte = reponse.text
    if "BEGIN:VCALENDAR" not in texte:
        # Moodle répond 200 avec une page de connexion quand le jeton a été
        # régénéré : sans ce contrôle, on conclurait « zéro événement » et on
        # viderait l'agenda.
        print("❌ La réponse n'est pas un calendrier iCalendar.")
        print("   Le jeton a probablement été régénéré : refais « URL du "
              "calendrier » sur Moodle et remets à jour MOODLE_ICS_URL.")
        return None
    return texte


def deplier(texte):
    """Reconstitue les lignes coupées par le pliage iCalendar.

    La norme impose de replier au-delà de 75 octets : la suite reprend à la
    ligne suivante, précédée d'une espace ou d'une tabulation. Un titre long
    arrive donc en morceaux, et les recoller n'est pas optionnel.
    """
    lignes = []
    for brute in texte.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        if brute[:1] in (' ', '\t') and lignes:
            lignes[-1] += brute[1:]
        else:
            lignes.append(brute)
    return lignes


def _decouper(ligne):
    """« DTSTART;VALUE=DATE:20260915 » -> ('DTSTART', {'VALUE': 'DATE'}, '20260915').

    Le deux-points séparateur est cherché hors des guillemets : un paramètre a
    le droit d'en contenir un (`TZID="Europe/Paris"` n'en a pas, mais
    `ALTREP="http://..."` si).
    """
    entre_guillemets = False
    for i, c in enumerate(ligne):
        if c == '"':
            entre_guillemets = not entre_guillemets
        elif c == ':' and not entre_guillemets:
            gauche, valeur = ligne[:i], ligne[i + 1:]
            break
    else:
        return None, {}, ""

    morceaux = gauche.split(';')
    nom = morceaux[0].upper()
    parametres = {}
    for p in morceaux[1:]:
        if '=' in p:
            cle, val = p.split('=', 1)
            parametres[cle.upper()] = val.strip('"')
    return nom, parametres, valeur


def _decoder(valeur):
    """Défait les échappements iCalendar (`\\,` `\\;` `\\n` `\\\\`)."""
    sortie, i = [], 0
    while i < len(valeur):
        c = valeur[i]
        if c == '\\' and i + 1 < len(valeur):
            suivant = valeur[i + 1]
            sortie.append({'n': '\n', 'N': '\n'}.get(suivant, suivant))
            i += 2
        else:
            sortie.append(c)
            i += 1
    return ''.join(sortie)


def _lire_horodatage(valeur, parametres):
    """Rend (datetime local, journée entière) ou (None, False) si illisible.

    Trois formes coexistent : `20260915` (une journée), `20260915T220000Z`
    (UTC — la forme que produit Moodle), et `20260916T000000` accompagné d'un
    `TZID`. Les instants UTC sont ramenés à l'heure de Paris, sans quoi une
    échéance de minuit tomberait la veille à 22 h.
    """
    valeur = valeur.strip()
    if parametres.get('VALUE') == 'DATE' or len(valeur) == 8:
        try:
            return datetime.strptime(valeur[:8], '%Y%m%d'), True
        except ValueError:
            return None, False

    try:
        moment = datetime.strptime(valeur[:15], '%Y%m%dT%H%M%S')
    except ValueError:
        return None, False

    if valeur.endswith('Z'):
        moment = moment.replace(tzinfo=timezone.utc)
    elif parametres.get('TZID'):
        try:
            moment = moment.replace(tzinfo=ZoneInfo(parametres['TZID']))
        except Exception:
            moment = moment.replace(tzinfo=FUSEAU)
    else:
        # Sans indication, la norme dit « heure locale du lecteur ». Ici, Paris.
        return moment, False

    return moment.astimezone(FUSEAU).replace(tzinfo=None), False


def _lire_duree(valeur):
    """« PT1H30M » -> timedelta. Rend None si la forme est inconnue."""
    trouve = REGEX_DUREE.match(valeur.strip())
    if not trouve:
        return None
    semaines, jours, heures, minutes, secondes = (
        int(v) if v else 0 for v in trouve.groups())
    duree = timedelta(weeks=semaines, days=jours, hours=heures,
                      minutes=minutes, seconds=secondes)
    return -duree if valeur.strip().startswith('-') else duree


def _texte_simple(html_brut, longueur=400):
    """Description Moodle (du HTML) ramenée à du texte lisible."""
    texte = REGEX_BALISE.sub(' ', html_brut)
    # `&nbsp;` devient U+00A0, invisible mais différent d'une espace : sans
    # cette normalisation il survit au resserrement des espaces qui suit.
    texte = html.unescape(texte).replace(' ', ' ')
    texte = re.sub(r'[ \t]+', ' ', texte)
    texte = re.sub(r'\n\s*\n+', '\n', texte).strip()
    return texte[:longueur].strip()


def _blocs(lignes, nom="VEVENT"):
    """Découpe le calendrier en blocs BEGIN:VEVENT … END:VEVENT."""
    courant = None
    for ligne in lignes:
        if ligne.strip().upper() == f"BEGIN:{nom}":
            courant = []
        elif ligne.strip().upper() == f"END:{nom}":
            if courant is not None:
                yield courant
            courant = None
        elif courant is not None:
            courant.append(ligne)


def _en_evenement(champs):
    """Traduit un VEVENT en dictionnaire d'événement, ou None s'il est inutilisable.

    Le dictionnaire suit la forme employée partout dans le projet
    (`date`, `start`, `end`, `titre`), pour que `google_agenda` le traite
    exactement comme un cours. Deux clés s'y ajoutent : `description`, qui
    remplace la ligne « Enseignant : … » sans objet ici, et `date_fin`, qui
    n'existe que pour un événement d'une journée entière.
    """
    titre = _decoder(champs.get('SUMMARY', '')).strip()
    if not titre:
        return None

    debut, journee = _lire_horodatage(*champs.get('_DTSTART', ('', {})))
    if debut is None:
        return None

    fin, _ = _lire_horodatage(*champs.get('_DTEND', ('', {})))
    if fin is None:
        duree = _lire_duree(champs.get('DURATION', '')) or timedelta(0)
        fin = debut + duree

    cours = champs.get('CATEGORIES', '').strip()
    description = _texte_simple(_decoder(champs.get('DESCRIPTION', '')))
    lignes_description = [l for l in (f"Cours : {cours}" if cours else "",
                                      description) if l]

    uid = champs.get('UID', '').strip()

    if journee:
        # Google veut une date de fin EXCLUSIVE : un événement d'un seul jour
        # se termine le lendemain.
        if fin <= debut:
            fin = debut + timedelta(days=1)
        return {
            "date": debut.strftime('%Y-%m-%d'),
            "date_fin": fin.strftime('%Y-%m-%d'),
            "start": None, "end": None, "uid": uid,
            "titre": titre, "room": "", "prof": cours,
            "description": "\n".join(lignes_description),
        }

    if fin <= debut:
        fin = debut + DUREE_MINIMALE
    if fin.date() != debut.date():
        # Un événement à cheval sur deux jours ne rentre pas dans la forme
        # « une date + deux heures » : on le ramène à la fin de sa journée
        # plutôt que de produire une heure de fin antérieure à son début.
        fin = debut.replace(hour=23, minute=59)
        if fin <= debut:
            # Cas courant : une échéance fixée à 23h59. L'épaissir vers l'avant
            # déborde sur le lendemain, alors on l'épaissit vers l'arrière —
            # l'événement se TERMINE à l'heure limite, ce qui la décrit bien.
            debut = fin - DUREE_MINIMALE

    return {
        "date": debut.strftime('%Y-%m-%d'),
        "start": debut.strftime('%Hh%M'),
        "end": fin.strftime('%Hh%M'), "uid": uid,
        "titre": titre, "room": "", "prof": cours,
        "description": "\n".join(lignes_description),
    }


def analyser(texte, filtre=None):
    """Rend la liste des événements du calendrier, triée par date.

    `filtre` est une expression régulière facultative, testée sans tenir compte
    de la casse sur le titre, le cours et la description : elle permet de ne
    retenir qu'un cours quand Moodle en exporte plusieurs.
    """
    filtre = FILTRE if filtre is None else filtre
    motif = re.compile(filtre, re.IGNORECASE) if filtre else None

    evenements = []
    for bloc in _blocs(deplier(texte)):
        champs = {}
        for ligne in bloc:
            nom, parametres, valeur = _decouper(ligne)
            if not nom:
                continue
            # Les bornes gardent leurs paramètres (VALUE, TZID) ; le reste n'en
            # a pas besoin et se lit plus simplement en chaîne.
            if nom in ('DTSTART', 'DTEND'):
                champs[f'_{nom}'] = (valeur, parametres)
            else:
                champs[nom] = valeur

        evenement = _en_evenement(champs)
        if evenement is None:
            continue
        if motif and not motif.search(
                f"{evenement['titre']} {evenement['prof']} {evenement['description']}"):
            continue
        evenements.append(evenement)

    evenements.sort(key=lambda e: (e['date'], e['start'] or ''))
    return evenements


def repartition(evenements):
    """Compte les événements par cours, pour savoir ce que contient l'export.

    Sert à choisir MOODLE_FILTRE en connaissance de cause : sans cet affichage,
    on ne sait pas quels cours alimentent le calendrier Moodle.
    """
    comptes = {}
    for e in evenements:
        comptes[e['prof'] or "(sans cours)"] = comptes.get(e['prof'] or "(sans cours)", 0) + 1
    return sorted(comptes.items(), key=lambda kv: (-kv[1], kv[0]))


def recuperer(url=None, filtre=None):
    """Télécharge puis analyse. Rend None si la source est inexploitable.

    None et liste vide sont deux réponses différentes : la première dit « je
    n'ai pas pu lire », la seconde « il n'y a rien ». Confondre les deux
    viderait l'agenda à la première panne réseau.
    """
    texte = telecharger(url)
    if texte is None:
        return None
    return analyser(texte, filtre)


if __name__ == "__main__":
    import sys

    for _flux in (sys.stdout, sys.stderr):
        try:
            _flux.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    liste = recuperer()
    if liste is None:
        sys.exit(1)
    print(f"📚 {len(liste)} événement(s) dans le calendrier Moodle.")
    for nom, nombre in repartition(liste):
        print(f"   {nombre:3d}  {nom}")
    print()
    for e in liste[:40]:
        creneau = f"{e['start']}-{e['end']}" if e['start'] else "journée"
        print(f"   {e['date']}  {creneau:>12}  {e['titre']}")
    if len(liste) > 40:
        print(f"   … et {len(liste) - 40} autre(s).")

"""
Vérification complète de la chaîne EDT : PDF -> données -> ICS -> agendas.

Ce script ne réutilise PAS la logique de tri de `edt_stri.py` : il relit le PDF
et redéduit lui-même où chaque cours devrait atterrir, puis compare au résultat
produit. Une vérification qui appellerait le code vérifié ne ferait que
confirmer ses propres erreurs.

Chaque contrôle correspond à une panne réellement rencontrée, pas à une règle
imaginée : examens invisibles faute de fond assez large, cellules fantômes sur
la grille vide, fins de ligne ICS invalides, PDF local périmé réécrivant les
agendas avec des cours annulés.

    python tests/verif_edt.py                 tout, agendas Google compris
    python tests/verif_edt.py --hors-ligne    sans réseau : PDF, données, ICS
    python tests/verif_edt.py --promo L3      une seule promotion
    python tests/verif_edt.py --sans-fraicheur  sans recomparer les PDF
    python tests/verif_edt.py --silencieux    sans alerte Discord

Les rendus Moodle sont contrôlés en plus, s'ils ont été publiés.

Une anomalie déclenche un message Discord, sauf en --silencieux.

Code de sortie : 0 si tout passe, 1 s'il reste une anomalie.
"""

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pdfplumber

import chemins  # noqa: E402
import edt_stri  # noqa: E402
import lecture_pdf  # noqa: E402
import rendus  # noqa: E402
from telechargement import PROMOS  # noqa: E402

for _flux in (sys.stdout, sys.stderr):
    try:
        _flux.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HORS_LIGNE = "--hors-ligne" in sys.argv
# En CI le PDF vient d'etre telecharge : le recomparer couterait un second
# passage anti-bot pour rien.
SANS_FRAICHEUR = HORS_LIGNE or "--sans-fraicheur" in sys.argv
SILENCIEUX = "--silencieux" in sys.argv
MOITIES = ("BAS", "HAUT")

# Amplitude maximale de la grille STRI : de 07h45 a 20h00. Toutes les
# promotions commencent a 07h45 ; seule la fin varie selon les semaines.
#
# Ces bornes sont serrees exprès. Un decalage des reperes horaires laisse les
# cours coherents ENTRE EUX — tous decales du meme nombre de colonnes — et
# aucun controle de coherence interne ne peut le voir. Seule une reference
# exterieure le trahit, et c'est celle-ci.
GRILLE_DEBUT = 7 * 60 + 45
GRILLE_FIN_MAX = 20 * 60


# =====================================================================
# RAPPORT
# =====================================================================

class Rapport:
    """Accumule les contrôles et rend un bilan lisible."""

    def __init__(self):
        self.lignes = []
        self.anomalies = 0
        self.reserves = 0

    def section(self, titre):
        self.lignes.append(("section", titre, ""))

    def bloc(self, titre):
        self.lignes.append(("bloc", titre, ""))

    def ok(self, intitule, detail=""):
        self.lignes.append(("ok", intitule, detail))

    def reserve(self, intitule, detail=""):
        """Signalé sans être une erreur : le PDF lui-même est en cause."""
        self.reserves += 1
        self.lignes.append(("reserve", intitule, detail))

    def anomalie(self, intitule, detail=""):
        self.anomalies += 1
        self.lignes.append(("ko", intitule, detail))

    def verifier(self, condition, intitule, detail_ok="", detail_ko=""):
        if condition:
            self.ok(intitule, detail_ok)
        else:
            self.anomalie(intitule, detail_ko or detail_ok)
        return condition

    def afficher(self):
        symboles = {"ok": "✅", "reserve": "⚠️ ", "ko": "❌"}
        for genre, intitule, detail in self.lignes:
            if genre == "section":
                print(f"\n╔═ {intitule} " + "═" * max(0, 62 - len(intitule)))
            elif genre == "bloc":
                print(f"║\n║  {intitule}")
            else:
                print(f"║    {symboles[genre]} {intitule:<44s} {detail}")

        total = sum(1 for g, _, _ in self.lignes if g in ("ok", "reserve", "ko"))
        print("\n" + "─" * 70)
        if self.anomalies:
            print(f"❌ {self.anomalies} anomalie(s) sur {total} contrôles, "
                  f"{self.reserves} réserve(s).")
        else:
            print(f"✅ {total} contrôles passés, {self.reserves} réserve(s) — "
                  "aucune anomalie.")
        return 1 if self.anomalies else 0


# =====================================================================
# LECTURE INDÉPENDANTE DU PDF
# =====================================================================

def relire_pdf(chemin):
    """Rend la liste des cellules du PDF, avec tout ce qui sert aux contrôles.

    Volontairement écrit à part de `traiter_journee` : c'est la contre-mesure
    de référence, elle ne doit pas hériter de ses éventuels défauts.
    """
    if not edt_stri.extraire_positions_heures_pdf(chemin, dpi=edt_stri.DPI):
        return None, None, None, None

    zones = edt_stri.extraire_zones_jours_pdf(chemin)
    x_min = edt_stri._vers_pdf(edt_stri.PADDING)
    x_max = edt_stri._vers_pdf(
        edt_stri.GLOBAL_END_X - edt_stri.GLOBAL_START_X + edt_stri.PADDING)

    def vers_heure(x_pdf):
        return edt_stri.obtenir_heure_proche(
            x_pdf * edt_stri.DPI / 72 - edt_stri.GLOBAL_START_X + edt_stri.PADDING)

    cellules, fonds = [], Counter()
    orphelins = {"mots": [], "salles": []}
    with pdfplumber.open(chemin) as pdf:
        for zone in zones:
            page = pdf.pages[zone["page"] - 1]
            grille = lecture_pdf.GrilleJour(page, zone, x_min, x_max)
            mots = lecture_pdf.mots_de_la_bande(page, zone, x_min)

            for r in page.rects:
                milieu = (r["top"] + r["bottom"]) / 2
                if not (zone["top"] - 1 < milieu < zone["bottom"] + 1):
                    continue
                if r["width"] < 5 or r["height"] < 5:
                    continue
                c = r["non_stroking_color"]
                if lecture_pdf.est_jaune(c):
                    fonds["JAUNE"] += 1
                elif lecture_pdf.est_orange(c):
                    fonds["ORANGE"] += 1
                elif lecture_pdf.est_olive(c):
                    fonds["OLIVE"] += 1

            trouvees = grille.cellules(mots)
            for cel in trouvees:
                # Une case pleine hauteur peut porter deux cours empilés : la
                # lecture en rend une liste. On garde une entrée par cours, avec
                # SA position — c'est elle qui dit à quelle demi-promotion il
                # s'adresse, et non celle de la case qui les contient.
                blocs = lecture_pdf.lire_cellule(
                    grille, cel["x0"], cel["x1"], cel["position"], mots, vers_heure)
                for bloc in (blocs or [None]):
                    cellules.append({
                        "date": zone["date"].strftime("%Y-%m-%d"),
                        "position": (bloc or cel)["position"],
                        "debut": vers_heure(cel["x0"]),
                        "fin": vers_heure(cel["x1"]),
                        "bloc": bloc,
                        "couleur": ((bloc.get("color") or "BLANC").upper()
                                    if bloc else None),
                    })

            jour = zone["date"].strftime("%Y-%m-%d")
            orphelins["mots"] += _mots_orphelins(grille, trouvees, mots, jour)
            orphelins["salles"] += _salles_orphelines(grille, trouvees, jour)

    return zones, cellules, fonds, orphelins


def _couvre(grille, cellule, x, y):
    """Le point (x, y) tombe-t-il dans cette cellule ?"""
    y0, y1 = grille.bornes_verticales(cellule["position"])
    return (cellule["x0"] - 2 <= x <= cellule["x1"] + 4
            and y0 - 0.5 <= y < y1 + 1.5)


def _mots_orphelins(grille, cellules, mots, jour):
    """Mots de la journée que AUCUNE cellule ne revendique.

    Contrôle indépendant des bordures : il part de la couche texte. Un cours
    dont la case n'aurait pas été détectée laisse ses mots sans propriétaire,
    et c'est le seul signal qui le trahit — tous les autres contrôles partent
    de `cellules()`, donc ne voient que ce qu'elle a déjà trouvé.
    """
    perdus = []
    for mot in mots:
        # Le libellé du jour vit à gauche de la grille, hors des cellules.
        if mot["x0"] < grille.x_min - 2:
            continue
        milieu = (mot["x0"] + mot["x1"]) / 2
        if not any(_couvre(grille, c, milieu, mot["top"]) for c in cellules):
            perdus.append(f"{jour} x{mot['x0']:.0f} « {mot['text'][:18]} »")
    return perdus


def _salles_orphelines(grille, cellules, jour):
    """Cases vertes de salle qu'aucune cellule ne recouvre.

    Une salle sans cours est impossible : c'est qu'une case a été manquée.
    Contrôle indépendant lui aussi — il part de la couleur, pas des bordures.
    """
    perdues = []
    for r in grille.salles:
        milieu_x = (r["x0"] + r["x1"]) / 2
        milieu_y = (r["top"] + r["bottom"]) / 2
        if not any(_couvre(grille, c, milieu_x, milieu_y) for c in cellules):
            perdues.append(f"{jour} x{r['x0']:.0f}-{r['x1']:.0f}")
    return perdues


def destinataires(cellule):
    """Les demi-promos qui doivent recevoir cette cellule.

    Réécriture délibérée de la règle de `traiter_journee` : si les deux
    divergent un jour, c'est exactement ce qu'on veut voir apparaître.
    """
    couleur = cellule["couleur"]
    if couleur == "ORANGE":
        return {"HAUT"}          # cours réservé aux Ingé
    if couleur == "OLIVE":
        return {"BAS"}           # cours réservé à l'autre demi-promo
    if cellule["position"] == "FULL":
        return {"BAS", "HAUT"}   # pleine hauteur : tout le monde
    return {"HAUT"} if cellule["position"] == "TOP" else {"BAS"}


# =====================================================================
# CONTRÔLES
# =====================================================================

def controler_completude(rap, orphelins):
    """Aucun cours n'a-t-il échappé ENTIÈREMENT à la détection ?

    Tous les autres contrôles partent de `cellules()` : ils ne peuvent pas voir
    une case jamais trouvée. Ces deux-ci partent de la couche texte et des
    couleurs, donc d'ailleurs.
    """
    rap.bloc("Complétude (contrôles indépendants des bordures)")

    mots = orphelins["mots"]
    rap.verifier(not mots, "aucun mot hors de toute cellule",
                 "chaque mot appartient à un cours",
                 f"{len(mots)} mot(s) orphelin(s) — case manquée ? : "
                 + " | ".join(mots[:4]))

    salles = orphelins["salles"]
    rap.verifier(not salles, "aucune case de salle orpheline",
                 "chaque salle est rattachée à un cours",
                 f"{len(salles)} salle(s) sans cours : " + " | ".join(salles[:4]))


def controler_pdf(rap, promo, zones, cellules, fonds):
    """Les journées et les cellules ont-elles été correctement repérées ?"""
    rap.bloc("Lecture du PDF")

    rap.verifier(bool(zones), "journées détectées",
                 f"{len(zones)} journées, "
                 f"{zones[0]['date']:%d/%m} → {zones[-1]['date']:%d/%m}"
                 if zones else "aucune")

    if zones:
        dates = [z["date"] for z in zones]
        rap.verifier(len(dates) == len(set(dates)), "aucune journée en double",
                     f"{len(set(dates))} dates distinctes")
        ouvres = [d for d in dates if d.weekday() < 5]
        rap.verifier(len(ouvres) == len(dates), "que des jours ouvrés",
                     "lundi à vendredi",
                     f"{len(dates) - len(ouvres)} week-end(s) détecté(s)")
        # Une lacune d'un jour ouvré signale une bande de journée manquée.
        manquants = []
        for veille, lendemain in zip(dates, dates[1:]):
            jour = veille + timedelta(days=1)
            while jour < lendemain:
                if jour.weekday() < 5:
                    manquants.append(jour.strftime("%d/%m"))
                jour += timedelta(days=1)
        rap.verifier(not manquants, "aucun jour ouvré manquant",
                     "suite continue",
                     f"{len(manquants)} absent(s) : {', '.join(manquants[:6])}")

    rap.verifier(bool(cellules), "cellules repérées", f"{len(cellules)} cases")

    illisibles = [c for c in cellules if c["bloc"] is None]
    rap.verifier(not illisibles, "toute cellule repérée est lue",
                 f"{len(cellules)}/{len(cellules)}",
                 f"{len(illisibles)} encadrée(s) sans contenu : "
                 + ", ".join(f"{c['date']} {c['debut']}" for c in illisibles[:4]))

    # Le fond jaune ne couvre pas toujours toute la cellule : trois examens
    # d'Adm. Linux passaient pour des cours ordinaires faute de 60 % de largeur.
    for teinte in ("JAUNE", "ORANGE", "OLIVE"):
        dans_pdf = fonds.get(teinte, 0)
        if not dans_pdf:
            continue
        reconnus = sum(1 for c in cellules if c["couleur"] == teinte)
        # Une cellule peut porter deux rectangles (titre + professeur).
        rap.verifier(reconnus > 0 and reconnus <= dans_pdf,
                     f"fonds {teinte.lower()} rattachés à une cellule",
                     f"{reconnus} cellule(s) pour {dans_pdf} rectangle(s)",
                     f"{dans_pdf} rectangle(s) dans le PDF, {reconnus} reconnu(s)")


def controler_horaires(rap, cellules):
    """Les horaires sont-ils lisibles, ordonnés et alignés sur la grille ?"""
    rap.bloc("Horaires")

    illisibles = [c for c in cellules if not c["debut"] or not c["fin"]]
    rap.verifier(not illisibles, "tous les horaires sont lisibles",
                 f"{len(cellules)} créneaux",
                 f"{len(illisibles)} illisible(s)")

    def minutes(h):
        return int(h[:2]) * 60 + int(h[3:5])

    lisibles = [c for c in cellules if c["debut"] and c["fin"]]
    inverses = [c for c in lisibles if minutes(c["debut"]) >= minutes(c["fin"])]
    rap.verifier(not inverses, "début toujours avant la fin", "",
                 f"{len(inverses)} incohérent(s)")

    hors_quart = [c for c in lisibles
                  if minutes(c["debut"]) % 15 or minutes(c["fin"]) % 15]
    rap.verifier(not hors_quart, "horaires alignés sur le quart d'heure",
                 "la grille est au quart d'heure",
                 f"{len(hors_quart)} hors grille : "
                 + ", ".join(f"{c['date']} {c['debut']}-{c['fin']}" for c in hors_quart[:4]))

    trop_longs = [c for c in lisibles
                  if minutes(c["fin"]) - minutes(c["debut"]) > 8 * 60]
    rap.verifier(not trop_longs, "aucune durée aberrante", "toutes sous 8 h",
                 f"{len(trop_longs)} au-delà de 8 h — "
                 "signe d'une case fantôme sur la grille vide")


def controler_plausibilite(rap, cellules, donnees):
    """Les horaires tiennent-ils debout dans l'absolu, pas seulement entre eux ?

    Les autres controles comparent les horaires les uns aux autres : un
    decalage global des reperes les laisserait tous coherents et tous faux.
    Ceux-ci les confrontent a la realite de la grille.
    """
    rap.bloc("Plausibilité")

    def minutes(h):
        return int(h[:2]) * 60 + int(h[3:5])

    # La plage utile vient de la grille du PDF, pas d'une constante : le M1
    # s'arrete a 19h15, la L3 va jusqu'a 19h45.
    reperes = [h for _x, h in edt_stri.REFERENCES_TEMPS if h and h != "?"]
    debut_grille, fin_grille = minutes(min(reperes)), minutes(max(reperes))

    rap.verifier(debut_grille >= GRILLE_DEBUT and fin_grille <= GRILLE_FIN_MAX,
                 "grille dans l'amplitude STRI",
                 f"{min(reperes)} → {max(reperes)}  (max. 07h45 → 20h00)",
                 f"la grille irait de {min(reperes)} à {max(reperes)}, hors de "
                 "l'amplitude 07h45 → 20h00 — repères horaires décalés")

    # Toutes les promotions commencent a 07h45 : un autre debut signale une
    # colonne perdue a la lecture de l'en-tete, pas un vrai changement d'horaire.
    if debut_grille != GRILLE_DEBUT:
        rap.reserve("la grille ne commence pas à 07h45",
                    f"{min(reperes)} — mise en page modifiée ou colonne manquée")

    lisibles = [c for c in cellules if c["debut"] and c["fin"]]
    dehors = [c for c in lisibles
              if minutes(c["debut"]) < debut_grille or minutes(c["fin"]) > fin_grille]
    rap.verifier(not dehors, "tous les cours dans la grille",
                 f"{min(reperes)} → {max(reperes)}",
                 f"{len(dehors)} hors grille : "
                 + ", ".join(f"{c['date']} {c['debut']}-{c['fin']}" for c in dehors[:4]))

    tous = [c for liste in donnees.values() for c in liste]
    week_end = [c for c in tous
                if datetime.strptime(c["date"], "%Y-%m-%d").weekday() >= 5]
    rap.verifier(not week_end, "aucun cours le week-end", f"{len(tous)} cours",
                 f"{len(week_end)} le samedi ou le dimanche")

    # Une journee entierement vide au milieu de la semaine est possible, mais
    # une promotion sans aucun cours ne l'est pas.
    for moitie, liste in donnees.items():
        rap.verifier(len(liste) >= 5, f"{moitie} : volume plausible",
                     f"{len(liste)} cours",
                     f"{len(liste)} cours seulement — extraction probablement "
                     "incomplète")


def controler_routage(rap, cellules, donnees):
    """Chaque cours est-il dans la bonne demi-promo, et seulement celle-là ?"""
    rap.bloc("Placement dans les demi-promos")

    publies = {m: {(c["date"], c["start"], c["end"]) for c in donnees[m]}
               for m in MOITIES}

    manques = defaultdict(list)
    intrus = defaultdict(list)
    for cel in cellules:
        if cel["bloc"] is None or not cel["debut"] or not cel["fin"]:
            continue
        titre = (cel["bloc"].get("course") or "").strip()
        if len(titre) < 2:
            continue
        creneau = (cel["date"], cel["debut"], cel["fin"])
        attendu = destinataires(cel)
        for moitie in MOITIES:
            if moitie in attendu and creneau not in publies[moitie]:
                manques[moitie].append(f"{cel['date']} {cel['debut']} {titre[:22]}")

    # Un créneau publié qui ne correspond à aucune cellule attendue.
    attendus = {m: set() for m in MOITIES}
    for cel in cellules:
        if cel["bloc"] is None or not cel["debut"] or not cel["fin"]:
            continue
        for moitie in destinataires(cel):
            attendus[moitie].add((cel["date"], cel["debut"], cel["fin"]))
    for moitie in MOITIES:
        intrus[moitie] = sorted(publies[moitie] - attendus[moitie])

    for moitie in MOITIES:
        rap.verifier(not manques[moitie], f"{moitie} : rien de manquant",
                     f"{len(publies[moitie])} cours publiés",
                     f"{len(manques[moitie])} absent(s) : "
                     + " | ".join(manques[moitie][:3]))
        rap.verifier(not intrus[moitie], f"{moitie} : rien en trop", "",
                     f"{len(intrus[moitie])} intrus : "
                     + " | ".join(str(x) for x in intrus[moitie][:3]))

    # Les cours d'une demi-promo ne doivent pas fuiter chez l'autre.
    orange = {(c["date"], c["debut"], c["fin"]) for c in cellules
              if c["couleur"] == "ORANGE" and c["debut"]}
    olive = {(c["date"], c["debut"], c["fin"]) for c in cellules
             if c["couleur"] == "OLIVE" and c["debut"]}
    if orange:
        fuites = orange & publies["BAS"] - attendus["BAS"]
        rap.verifier(not fuites, "cours Ingé (orange) absents de la moitié basse",
                     f"{len(orange)} créneau(x) orange",
                     f"{len(fuites)} fuite(s)")
    if olive:
        fuites = olive & publies["HAUT"] - attendus["HAUT"]
        rap.verifier(not fuites, "cours olive absents de la moitié haute",
                     f"{len(olive)} créneau(x) olive",
                     f"{len(fuites)} fuite(s)")


def controler_donnees(rap, moitie, cours, chemin_ics):
    """Le JSON et l'ICS produits sont-ils cohérents entre eux et en eux-mêmes ?"""
    rap.bloc(f"Sorties — moitié {moitie}")

    def minutes(h):
        return int(h[:2]) * 60 + int(h[3:5])

    # Un chevauchement dans une même demi-promo = deux cours au même moment.
    par_jour = defaultdict(list)
    for c in cours:
        par_jour[c["date"]].append(c)
    chevauchements = []
    for jour, liste in par_jour.items():
        liste.sort(key=lambda c: minutes(c["start"]))
        for premier, second in zip(liste, liste[1:]):
            if minutes(second["start"]) < minutes(premier["end"]):
                chevauchements.append(f"{jour} {premier['start']}/{second['start']}")
    rap.verifier(not chevauchements, "aucun chevauchement horaire",
                 f"{len(cours)} cours sur {len(par_jour)} journées",
                 f"{len(chevauchements)} : " + ", ".join(chevauchements[:4]))

    doublons = [k for k, n in Counter(
        (c["date"], c["start"], c["titre"]) for c in cours).items() if n > 1]
    rap.verifier(not doublons, "aucun cours en double", "",
                 f"{len(doublons)} : {doublons[:3]}")

    vides = [c for c in cours
             if (c["titre"] or "").strip().lower() in ("", "cours", "inconnu")]
    rap.verifier(not vides, "aucun titre vide ou générique", "",
                 f"{len(vides)} douteux")

    sans_prof = sum(1 for c in cours if c.get("prof") in (None, "", "Inconnu"))
    sans_salle = sum(1 for c in cours if c.get("room") in (None, "", "Non attribuée"))
    if sans_prof or sans_salle:
        rap.reserve("champs absents du PDF lui-même",
                    f"{sans_prof} sans professeur, {sans_salle} sans salle")

    # --- fichier ICS ---
    chemin = Path(chemin_ics)
    if not rap.verifier(chemin.exists(), "fichier ICS présent", chemin.name,
                        f"{chemin.name} introuvable"):
        return

    brut = chemin.read_bytes()
    # Le mode texte de Windows retraduisait \n en \r\n : les lignes partaient
    # en \r\r\n, invalides au regard de la RFC 5545, et iOS refusait le fichier.
    rap.verifier(b"\r\r\n" not in brut, "fins de ligne conformes à la RFC 5545",
                 f"{brut.count(chr(13).encode() + chr(10).encode())} lignes CRLF",
                 "fins de ligne \\r\\r\\n : fichier rejeté par les clients stricts")

    texte = brut.decode("utf-8")
    # L'ICS n'est pas versionné : la CI le regénère sur son runner et le jette.
    # En local, un `git pull` ramène donc un JSON neuf à côté d'un ICS resté à
    # la dernière exécution locale. Les compter l'un contre l'autre signalerait
    # une corruption là où il n'y a qu'un décalage d'époques — d'où la
    # comparaison des dates avant de crier au loup.
    json_correspondant = chemin.with_name(
        chemin.name.replace("edt", "edt_data", 1)).with_suffix(".json")
    perime = (json_correspondant.exists()
              and chemin.stat().st_mtime < json_correspondant.stat().st_mtime)

    if texte.count("BEGIN:VEVENT") == len(cours):
        rap.ok("ICS et données concordent", f"{len(cours)} événements")
    elif perime:
        rap.reserve("ICS local plus ancien que les données",
                    f"{texte.count('BEGIN:VEVENT')} contre {len(cours)} — "
                    "relancer test_local.py le régénère")
    else:
        rap.anomalie("ICS et données concordent",
                     f"{texte.count('BEGIN:VEVENT')} dans l'ICS, "
                     f"{len(cours)} attendus")

    uid_ics = set(re.findall(r"^UID:(.+)$", texte, re.M))
    rap.verifier(len(uid_ics) == texte.count("BEGIN:VEVENT"),
                 "identifiants ICS tous distincts",
                 f"{len(uid_ics)} UID",
                 f"{texte.count('BEGIN:VEVENT') - len(uid_ics)} doublon(s)")


def controler_agendas(rap, promo, moitie, cours):
    """Contrôles sur Google Agenda. Sautés en --hors-ligne."""
    from googleapiclient.discovery import build
    import google_agenda

    creds = edt_stri.obtenir_identifiants()
    if creds is None:
        rap.reserve("agendas Google non vérifiés", "aucune autorisation valide")
        return

    service = build("calendar", "v3", credentials=creds)
    cle = PROMOS[promo]["cles"][moitie]
    etiquette = google_agenda.marqueur(cle)

    possedes = [a for a in service.calendarList().list().execute().get("items", [])
                if a.get("accessRole") == "owner"]
    agenda = next((a for a in possedes
                   if etiquette in (a.get("description") or "")), None)
    if not rap.verifier(agenda is not None, f"agenda {etiquette} trouvé",
                        agenda["summary"] if agenda else "",
                        "aucun agenda ne porte ce marqueur"):
        return

    evenements, jeton = {}, None
    while True:
        page = service.events().list(calendarId=agenda["id"], singleEvents=True,
                                     maxResults=2500, pageToken=jeton).execute()
        for e in page.get("items", []):
            evenements[e["id"]] = e
        jeton = page.get("nextPageToken")
        if not jeton:
            break

    ordinaires = [c for c in cours
                  if not c["titre"].startswith(google_agenda.MARQUEUR_EXAMEN)]
    rap.verifier(len(evenements) == len(ordinaires),
                 f"« {agenda['summary']} » complet",
                 f"{len(evenements)} événements",
                 f"{len(evenements)} dans l'agenda, {len(ordinaires)} attendus")

    # Chaque cours doit être présent, à la bonne heure et dans la bonne salle.
    ecarts = []
    for c in ordinaires:
        evt = evenements.get(google_agenda._identifiant(c))
        if evt is None:
            ecarts.append(f"absent : {c['date']} {c['start']} {c['titre'][:20]}")
            continue
        debut = evt.get("start", {}).get("dateTime", "")
        attendu = f"{c['date']}T{c['start'][:2]}:{c['start'][3:5]}:00"
        if not debut.startswith(attendu):
            ecarts.append(f"horaire : {c['date']} {c['start']} ≠ {debut[:16]}")
        elif (evt.get("location") or "") != (c.get("room") or ""):
            ecarts.append(f"salle : {c['date']} {c['start']}")
    rap.verifier(not ecarts, "horaires et salles conformes",
                 f"{len(ordinaires)} vérifiés",
                 f"{len(ecarts)} écart(s) : " + " | ".join(ecarts[:3]))

    fuseaux = {e.get("start", {}).get("timeZone") for e in evenements.values()}
    rap.verifier(fuseaux <= {"Europe/Paris"}, "fuseau horaire correct",
                 "Europe/Paris", f"fuseaux trouvés : {fuseaux}")

    # Le partage doit couvrir cours ET examens : en oublier un est facile.
    lecteurs = {r["scope"]["value"] for r in
                service.acl().list(calendarId=agenda["id"]).execute().get("items", [])
                if r["role"] == "reader" and r["scope"]["type"] == "user"}
    examens = next((a for a in possedes
                    if google_agenda.marqueur(f"{cle}-EXAMENS")
                    in (a.get("description") or "")), None)
    if examens and lecteurs:
        lecteurs_ex = {r["scope"]["value"] for r in
                       service.acl().list(calendarId=examens["id"]).execute().get("items", [])
                       if r["role"] == "reader" and r["scope"]["type"] == "user"}
        oublies = lecteurs - lecteurs_ex
        rap.verifier(not oublies, "partage cours/examens cohérent",
                     f"{len(lecteurs)} personne(s)",
                     f"{len(oublies)} sans l'agenda d'examens : "
                     + ", ".join(sorted(oublies)[:3]))
    elif lecteurs:
        rap.reserve("agenda d'examens introuvable", f"{len(lecteurs)} abonné(s)")


def controler_fraicheur(rap, promo):
    """Le PDF local est-il celui publié en ligne ?

    Rejouer un PDF périmé réécrit les agendas avec des cours annulés — c'est
    arrivé en lançant le traitement avec --no-download alors que l'école avait
    publié une nouvelle version.
    """
    import telechargement

    rap.bloc("Fraîcheur")
    local = Path(PROMOS[promo]["pdf"])
    if not rap.verifier(local.exists(), "PDF local présent", local.name,
                        f"{local.name} introuvable"):
        return

    temporaire = chemins.donnee(f".verif_{local.name}")
    try:
        if not telechargement.telecharger_pdf(str(temporaire), url=PROMOS[promo]["url"]):
            rap.reserve("comparaison au PDF en ligne impossible", "téléchargement échoué")
            return
        identique = temporaire.read_bytes() == local.read_bytes()
        rap.verifier(identique, "PDF local à jour",
                     f"{local.stat().st_size // 1024} Ko, identique à celui en ligne",
                     "le PDF en ligne a changé — relancer le traitement, sinon "
                     "les agendas gardent des cours périmés")
    finally:
        temporaire.unlink(missing_ok=True)


# =====================================================================
# ENCHAÎNEMENT
# =====================================================================

def prevenir_discord(rap):
    """Previent sur Discord quand la verification trouve quelque chose.

    Sans cela il fallait lancer le script a la main pour savoir qu'un cours
    manquait : la CI echouait en silence, dans un onglet que personne n'ouvre.
    """
    webhook = edt_stri.DISCORD_WEBHOOK_URL
    if not webhook:
        return

    lignes = [f"• {intitule} — {detail}" if detail else f"• {intitule}"
              for genre, intitule, detail in rap.lignes if genre == "ko"]
    description = (f"**{rap.anomalies} anomalie(s)** relevée(s) par la "
                   f"vérification :\n\n" + "\n".join(lignes[:14]))
    if len(lignes) > 14:
        description += f"\n… et {len(lignes) - 14} autre(s)."

    try:
        import requests
        requests.post(webhook, timeout=edt_stri.TIMEOUT_HTTP, json={
            "username": "Bot EDT STRI",
            "embeds": [{"title": "\U0001f50e Vérification de l'emploi du temps",
                        "description": description[:3900], "color": 15158332}],
        }).raise_for_status()
        print("✅ Anomalies signalées sur Discord.")
    except Exception as e:
        print(f"❌ Signalement Discord impossible : {e}")


def controler_rendus(rap):
    """Contrôle les rendus Moodle publiés, s'il y en a.

    Section facultative : sans `rendus_data.json`, la fonctionnalité n'est pas
    utilisée et il n'y a rien à dire. Les contrôles portent sur ce qu'un
    calendrier Moodle mal formé pourrait produire — une échéance de durée
    nulle, une date aberrante, deux rendus impossibles à distinguer.
    """
    for agenda in rendus.AGENDAS:
        _controler_un_agenda(rap, agenda)


def _controler_un_agenda(rap, agenda):
    """Contrôle les rendus publiés pour UN agenda."""
    chemin = rendus.fichier_etat(agenda)
    if not chemin.exists():
        return

    rap.section(f"Rendus Moodle — {chemin.name}")
    try:
        evenements = json.loads(chemin.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        rap.anomalie("rendus illisibles", str(e))
        return

    if not isinstance(evenements, list):
        rap.anomalie("format inattendu", "une liste était attendue")
        return

    rap.ok("rendus chargés", f"{len(evenements)} événement(s)")
    if not evenements:
        return

    sans_titre = [e for e in evenements if not (e.get("titre") or "").strip()]
    rap.verifier(not sans_titre, "tous les rendus ont un intitulé",
                 detail_ko=f"{len(sans_titre)} sans intitulé")

    dates_invalides, creneaux_vides, journees_invalides = [], [], []
    for e in evenements:
        try:
            debut_jour = datetime.strptime(e.get("date", ""), "%Y-%m-%d")
        except ValueError:
            dates_invalides.append(e.get("titre", "?"))
            continue

        if e.get("start"):
            # Une date limite est ponctuelle : début et fin confondus, ce que
            # Google accepte. Seule une fin ANTÉRIEURE au début est une faute.
            if _minutes(e.get("end", "")) < _minutes(e["start"]):
                creneaux_vides.append(f"{e['date']} {e['titre']}")
        else:
            # Journée entière : la fin est EXCLUSIVE, donc postérieure.
            fin = e.get("date_fin", "")
            if not fin or fin <= e["date"]:
                journees_invalides.append(f"{e['date']} {e['titre']}")

    rap.verifier(not dates_invalides, "dates lisibles",
                 detail_ko=", ".join(dates_invalides[:5]))
    rap.verifier(not creneaux_vides, "aucune fin antérieure au début",
                 detail_ko=", ".join(creneaux_vides[:5]))
    rap.verifier(not journees_invalides, "journées entières bien bornées",
                 detail_ko=", ".join(journees_invalides[:5]))

    # Deux rendus de même date, même heure et même intitulé recevraient le même
    # identifiant Google : le second écraserait le premier, sans un mot.
    empreintes = Counter(
        f"{e.get('date')}|{e.get('start')}|{e.get('end')}|{e.get('titre')}"
        for e in evenements)
    doublons = [cle for cle, n in empreintes.items() if n > 1]
    rap.verifier(not doublons, "aucun rendu indistinguable d'un autre",
                 detail_ko=f"{len(doublons)} doublon(s) : " + ", ".join(doublons[:3]))

    sans_uid = [e for e in evenements if not e.get("uid")]
    if sans_uid:
        # Sans UID, un rendu déplacé est vu comme une suppression suivie d'un
        # ajout : bruyant sur Discord, mais sans conséquence sur l'agenda.
        rap.reserve("rendus sans identifiant Moodle",
                    f"{len(sans_uid)} sur {len(evenements)} — déplacements mal annoncés")

    aujourdhui = datetime.now()
    lointains = [e["titre"] for e in evenements
                 if e.get("date", "") and _hors_horizon(e["date"], aujourdhui)]
    rap.verifier(not lointains, "échéances dans un horizon plausible",
                 detail_ok="moins de deux ans d'écart",
                 detail_ko=", ".join(lointains[:5]))


def _minutes(heure):
    """« 08h30 » -> 510. Rend -1 si la forme est inattendue."""
    try:
        h, m = heure.split("h")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return -1


def _hors_horizon(date_str, aujourdhui, annees=2):
    """Une échéance à plus de deux ans trahit une année mal déduite."""
    try:
        ecart = datetime.strptime(date_str, "%Y-%m-%d") - aujourdhui
    except ValueError:
        return False
    return abs(ecart.days) > 365 * annees


def principale():
    """Enchaîne tous les contrôles et rend 0 si aucun n'a échoué."""
    promo_voulue = None
    if "--promo" in sys.argv:
        i = sys.argv.index("--promo")
        promo_voulue = sys.argv[i + 1].upper() if i + 1 < len(sys.argv) else ""
        if promo_voulue not in PROMOS:
            sys.exit(f"⛔ --promo {promo_voulue!r} inconnu. Valeurs : {', '.join(PROMOS)}.")

    rap = Rapport()
    print(f"Vérification de la chaîne EDT — {datetime.now():%d/%m/%Y %H:%M}")
    if HORS_LIGNE:
        print("Mode hors ligne : agendas Google et fraîcheur des PDF non vérifiés.")

    for promo in ([promo_voulue] if promo_voulue else list(PROMOS)):
        rap.section(f"{promo} — {Path(PROMOS[promo]['pdf']).name}")

        chemin = Path(PROMOS[promo]["pdf"])
        if not chemin.exists():
            rap.anomalie("PDF introuvable", str(chemin))
            continue

        zones, cellules, fonds, orphelins = relire_pdf(chemin)
        if zones is None:
            rap.anomalie("repères horaires introuvables",
                         "la mise en page a probablement changé")
            continue
        rap.ok("repères horaires extraits",
               f"{len(edt_stri.REFERENCES_TEMPS)} repères, "
               f"grille x={edt_stri.GLOBAL_START_X}→{edt_stri.GLOBAL_END_X}")

        controler_pdf(rap, promo, zones, cellules, fonds)
        controler_completude(rap, orphelins)
        controler_horaires(rap, cellules)

        donnees, manquant = {}, False
        for moitie in MOITIES:
            suffixe = PROMOS[promo]["suffixes"][moitie]
            fichier = chemins.donnee(f"edt_data{suffixe}.json")
            if not fichier.exists():
                rap.anomalie(f"données {moitie} absentes", str(fichier))
                manquant = True
                break
            donnees[moitie] = json.loads(fichier.read_text(encoding="utf-8"))
        if manquant:
            continue

        controler_plausibilite(rap, cellules, donnees)
        controler_routage(rap, cellules, donnees)

        for moitie in MOITIES:
            suffixe = PROMOS[promo]["suffixes"][moitie]
            controler_donnees(rap, moitie, donnees[moitie],
                              chemins.donnee(f"edt{suffixe}.ics"))
            if not HORS_LIGNE:
                controler_agendas(rap, promo, moitie, donnees[moitie])

        if not SANS_FRAICHEUR:
            controler_fraicheur(rap, promo)

    # Hors de la boucle : les rendus Moodle ne dépendent d'aucune promotion.
    controler_rendus(rap)

    code = rap.afficher()
    if code and not SILENCIEUX:
        prevenir_discord(rap)
    return code


if __name__ == "__main__":
    sys.exit(principale())

"""
Tests unitaires de la chaîne EDT — sans réseau, sans PDF, sans agenda.

Complément de `verif_edt.py`, qui contrôle les DONNÉES du jour. Ici on éprouve
la LOGIQUE, cas limites compris, sur des entrées fabriquées. Les deux se
répondent : la vérification dit si le résultat d'aujourd'hui est bon, les tests
disent si le code le restera après une modification.

Chaque test correspond à un défaut réellement rencontré. Les commentaires
rappellent lequel, pour qu'un futur lecteur sache ce qu'il casserait en
simplifiant.

    python tests/test_edt.py            tout
    python tests/test_edt.py couleur    seulement ceux dont le nom contient ça

Aucune dépendance de test : la bibliothèque standard suffit.
"""

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

_RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RACINE / "src"))
sys.path.insert(0, str(_RACINE / "tests"))

import chemins  # noqa: E402
import edt_stri  # noqa: E402
import google_agenda  # noqa: E402
import lecture_pdf  # noqa: E402
import moodle  # noqa: E402
import rendus  # noqa: E402
import telechargement  # noqa: E402
import verif_edt  # noqa: E402

for _flux in (sys.stdout, sys.stderr):
    try:
        _flux.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

TESTS = []


def test(fonction):
    TESTS.append(fonction)
    return fonction


def egal(obtenu, attendu, quoi=""):
    if obtenu != attendu:
        raise AssertionError(f"{quoi}\n      attendu : {attendu!r}\n      obtenu  : {obtenu!r}")


def _modules_importes(chemin):
    """Noms des modules importés par un fichier, sans l'exécuter."""
    import ast
    arbre = ast.parse(Path(chemin).read_text(encoding="utf-8"))
    noms = set()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Import):
            noms.update(a.name.split(".")[0] for a in noeud.names)
        elif isinstance(noeud, ast.ImportFrom) and noeud.module:
            noms.add(noeud.module.split(".")[0])
    return noms


def cours(date="2026-09-08", start="08h00", end="10h00", titre="BD",
          room="U3-04", prof="Karen PINEL-SAUVAGNAT"):
    return {"date": date, "start": start, "end": end, "titre": titre,
            "room": room, "prof": prof}


# =====================================================================
# Configuration et environnement
# =====================================================================

@test
def variable_env_traite_le_vide_comme_absent():
    """GitHub définit toujours les variables citées dans `env:`, même vides.
    `os.environ.get(nom, defaut)` rendait alors "" au lieu du défaut, et l'URL
    du PDF partait vide : « No scheme supplied »."""
    import os
    os.environ["ESSAI_EDT"] = ""
    egal(telechargement.variable_env("ESSAI_EDT", "repli"), "repli", "variable vide")
    os.environ["ESSAI_EDT"] = "   "
    egal(telechargement.variable_env("ESSAI_EDT", "repli"), "repli", "que des espaces")
    os.environ["ESSAI_EDT"] = " valeur "
    egal(telechargement.variable_env("ESSAI_EDT", "repli"), "valeur", "valeur entourée d'espaces")
    del os.environ["ESSAI_EDT"]
    egal(telechargement.variable_env("ESSAI_EDT", "repli"), "repli", "variable absente")


@test
def promos_sont_coherentes():
    """Deux promotions partageant une clé écriraient dans le même agenda ;
    deux partageant un suffixe, dans le même fichier."""
    cles, suffixes, agendas, pdfs = [], [], [], set()
    for promo, config in telechargement.PROMOS.items():
        pdfs.add(config["pdf"])
        for moitie in ("BAS", "HAUT"):
            for champ, panier in (("cles", cles), ("suffixes", suffixes),
                                  ("agendas", agendas)):
                assert moitie in config[champ], f"{promo}/{moitie} : {champ} manquant"
                panier.append(config[champ][moitie])
        assert config["url"].startswith("https://"), f"{promo} : URL non HTTPS"
    egal(len(set(cles)), len(cles), "clés de marquage en double")
    egal(len(set(suffixes)), len(suffixes), "suffixes de fichiers en double")
    egal(len(set(agendas)), len(agendas), "noms d'agendas en double")
    egal(len(pdfs), len(telechargement.PROMOS), "deux promotions sur le même PDF")


@test
def module_de_telechargement_reste_leger():
    """La CI l'appelle AVANT d'installer requirements.txt : il ne doit importer
    ni numpy, ni OpenCV, ni pdfplumber."""
    lourdes = {"numpy", "cv2", "pdfplumber", "ics", "pdf2image",
               "googleapiclient", "edt_stri"}
    # `chemins` est importé par telechargement : la garantie ne vaut que si lui
    # aussi reste léger, d'où les deux fichiers.
    for nom in ("telechargement.py", "chemins.py"):
        interdits = _modules_importes(chemins.SRC / nom) & lourdes
        egal(interdits, set(), f"dépendances lourdes dans {nom}")


# =====================================================================
# Dates et horaires
# =====================================================================

@test
def deviner_annee_choisit_la_plus_proche():
    """Le PDF n'écrit que « 6/avr » : l'année se déduit de la proximité."""
    aout = datetime(2026, 8, 24)
    egal(edt_stri.deviner_annee(7, 9, aout), 2026, "septembre vu en août")
    egal(edt_stri.deviner_annee(6, 4, aout), 2026, "avril vu en août")
    decembre = datetime(2026, 12, 15)
    egal(edt_stri.deviner_annee(5, 1, decembre), 2027, "janvier vu en décembre")


@test
def deviner_annee_survit_au_29_fevrier():
    """2026 n'est pas bissextile : datetime(2026, 2, 29) lève ValueError."""
    assert edt_stri.deviner_annee(29, 2, datetime(2026, 8, 24)) is not None


@test
def obtenir_heure_proche_rend_le_repere_le_plus_proche():
    sauvegarde = edt_stri.REFERENCES_TEMPS
    try:
        edt_stri.REFERENCES_TEMPS = [(100, "08h00"), (200, "08h15"), (300, "08h30")]
        egal(edt_stri.obtenir_heure_proche(105), "08h00", "juste après un repère")
        egal(edt_stri.obtenir_heure_proche(190), "08h15", "juste avant le suivant")
        egal(edt_stri.obtenir_heure_proche(9999), "08h30", "au-delà du dernier")
    finally:
        edt_stri.REFERENCES_TEMPS = sauvegarde


@test
def obtenir_heure_proche_rejette_les_reperes_inconnus():
    """Un « ? » signale un quart d'heure non identifié : mieux vaut rien
    qu'un horaire inventé."""
    sauvegarde = edt_stri.REFERENCES_TEMPS
    try:
        edt_stri.REFERENCES_TEMPS = [(100, "?")]
        assert edt_stri.obtenir_heure_proche(100) is None
        edt_stri.REFERENCES_TEMPS = []
        assert edt_stri.obtenir_heure_proche(100) is None, "liste vide"
    finally:
        edt_stri.REFERENCES_TEMPS = sauvegarde


@test
def repartir_quarts_garde_les_traits_exacts():
    """Quand le PDF fournit les trois séparateurs, on les prend tels quels."""
    egal(edt_stri._repartir_quarts(0, 400, [100, 200, 300]), [100, 200, 300])


@test
def repartir_quarts_complete_les_traits_manquants():
    """Un trait absent ne doit pas décaler toute la suite de la journée."""
    obtenu = edt_stri._repartir_quarts(0, 400, [100, 300])
    egal(len(obtenu), 3, "il faut toujours trois quarts d'heure")
    assert obtenu == sorted(obtenu), "les quarts doivent rester ordonnés"
    assert 100 in obtenu and 300 in obtenu, "les traits réels doivent être conservés"


# =====================================================================
# Règles de placement
# =====================================================================

@test
def destinataires_suivent_la_position():
    d = verif_edt.destinataires
    egal(d({"position": "FULL", "couleur": "BLANC"}), {"BAS", "HAUT"}, "pleine hauteur")
    egal(d({"position": "TOP", "couleur": "BLANC"}), {"HAUT"}, "moitié haute")
    egal(d({"position": "BOTTOM", "couleur": "BLANC"}), {"BAS"}, "moitié basse")


@test
def destinataires_la_couleur_prime_sur_la_position():
    """Orange marque les Ingé, olive l'autre demi-promo. Les 22 cases mesurées
    sont toutes en moitié haute ou basse selon leur couleur, mais si l'une
    passait en pleine hauteur elle resterait réservée à sa promo."""
    d = verif_edt.destinataires
    egal(d({"position": "FULL", "couleur": "ORANGE"}), {"HAUT"}, "orange en pleine hauteur")
    egal(d({"position": "FULL", "couleur": "OLIVE"}), {"BAS"}, "olive en pleine hauteur")
    egal(d({"position": "BOTTOM", "couleur": "ORANGE"}), {"HAUT"}, "orange contredit la position")


# =====================================================================
# Couleurs du PDF
# =====================================================================

@test
def couleurs_de_fond_sont_distinguees():
    egal(lecture_pdf.est_vert((0.0, 0.98, 0.0)), True, "vert des salles")
    egal(lecture_pdf.est_vert((0.0, 1.0, 0.0)), True, "vert pur, autre variante")
    egal(lecture_pdf.est_jaune((1.0, 1.0, 0.0)), True, "jaune des examens")
    egal(lecture_pdf.est_orange((1.0, 0.753, 0.0)), True, "orange des Ingé")
    egal(lecture_pdf.est_olive((0.573, 0.816, 0.314)), True, "olive de l'IRT L3")
    egal(lecture_pdf.est_noir((0.0, 0.0, 0.0)), True, "noir des bordures")


@test
def olive_ne_se_confond_pas_avec_le_vert_des_salles():
    """Une confusion ferait passer les titres de cours pour des salles."""
    egal(lecture_pdf.est_vert((0.573, 0.816, 0.314)), False, "olive pris pour du vert")
    egal(lecture_pdf.est_olive((0.0, 0.98, 0.0)), False, "vert pris pour de l'olive")
    egal(lecture_pdf.est_olive((1.0, 0.753, 0.0)), False, "orange pris pour de l'olive")


@test
def couleurs_ignorent_les_motifs_et_les_formats_inattendus():
    """Les pointillés de la grille ont une couleur nommée « P67 », pas un
    triplet : les prendre pour du noir ferait des cellules fantômes."""
    for valeur in ("P67", None, 0, (0.0, 0.0), (0, 0, 0, 0), []):
        egal(lecture_pdf.est_noir(valeur), False, f"{valeur!r} pris pour du noir")


# =====================================================================
# Lecture du texte
# =====================================================================

@test
def table_des_professeurs_est_chargee():
    profs = lecture_pdf.charger_profs()
    assert len(profs) >= 40, f"seulement {len(profs)} enseignants"
    egal(profs.get("AA"), "André AOUN", "initiales simples")
    egal(profs.get("EG"), "Eric GONNEAU", "EG seul")
    egal(profs.get("EG Sécurité"), "Etienne GÉRAIN", "EG Sécurité, homonyme piégeux")


@test
def nom_complet_gere_les_binomes_et_les_inconnus():
    egal(lecture_pdf._nom_complet("CC"), "Cédric CHAMBAULT", "initiales connues")
    egal(lecture_pdf._nom_complet("FM & AA"), "Frédéric MOUTIER & André AOUN", "binôme")
    egal(lecture_pdf._nom_complet("ZZZ"), "ZZZ", "initiales inconnues laissées telles quelles")
    egal(lecture_pdf._nom_complet("AA +"), "André AOUN", "le + du PDF est ignoré")
    egal(lecture_pdf._nom_complet(""), "", "chaîne vide")


@test
def regex_prof_accepte_la_parenthese_doublee():
    """Le PDF écrit « Tél. Spat. (MA & FM)) » : sans le +, le professeur
    n'était pas reconnu et les initiales restaient collées au titre."""
    trouve = lecture_pdf.REGEX_PROF.search("Tél. Spat. (MA & FM))")
    assert trouve is not None, "parenthèse doublée non reconnue"
    egal(trouve.group(1), "MA & FM")
    egal(lecture_pdf.REGEX_PROF.search("Adm. Windows (CC)").group(1), "CC", "cas simple")


@test
def analyser_texte_separe_titre_professeur_et_groupe():
    def mot(texte, x=0):
        return {"text": texte, "x0": x, "x1": x + 10, "top": 0, "fontname": "Helvetica"}
    titre, groupe, prof = lecture_pdf._analyser_texte(
        [mot("Adm.", 0), mot("Windows", 10), mot("(CC)", 20)])
    egal(titre, "Adm. Windows", "titre")
    egal(prof, "Cédric CHAMBAULT", "professeur développé")
    titre, groupe, prof = lecture_pdf._analyser_texte(
        [mot("TCP/IP", 0), mot("/GB", 10)])
    egal(groupe, "GB", "groupe extrait")
    assert "/GB" not in titre, "le groupe doit quitter le titre"


@test
def est_ligne_prof_ne_depend_pas_de_l_italique():
    """« AA » n'est pas en italique dans le PDF : s'appuyer sur la fonte seule
    faisait passer le professeur pour la suite du titre."""
    def mot(texte, italique=False):
        return {"text": texte, "x0": 0, "x1": 10, "top": 0,
                "fontname": "Helvetica-Oblique" if italique else "Helvetica"}
    assert lecture_pdf._est_ligne_prof([mot("AA")]), "initiales connues, sans italique"
    assert lecture_pdf._est_ligne_prof([mot("KPS")]), "trois lettres"
    assert lecture_pdf._est_ligne_prof([mot("Machin", italique=True)]), "italique seul"
    assert not lecture_pdf._est_ligne_prof([mot("Réseaux d'entreprise")]), "vrai titre"
    assert not lecture_pdf._est_ligne_prof([]), "ligne vide"


@test
def lignes_regroupe_par_hauteur():
    def mot(texte, top, x):
        return {"text": texte, "top": top, "x0": x, "x1": x + 5}
    lignes = lecture_pdf._lignes([mot("b", 10.0, 20), mot("a", 10.4, 5), mot("c", 20.0, 0)])
    egal(len(lignes), 2, "deux lignes distinctes")
    egal([m["text"] for m in lignes[0]], ["a", "b"], "mots triés par abscisse")


# =====================================================================
# Déduplication et comparaison
# =====================================================================

@test
def deduplicer_garde_le_creneau_le_plus_court():
    """Une case englobante et la vraie case donnent le même cours sur deux
    étendues : la plus courte est celle du cours."""
    liste = [cours(start="12h00", end="15h45"), cours(start="13h15", end="16h15")]
    obtenu = edt_stri.deduplicer(liste)
    egal(len(obtenu), 1, "doublon non écarté")
    egal(obtenu[0]["start"], "13h15", "mauvais créneau conservé")


@test
def deduplicer_conserve_les_cours_distincts():
    liste = [cours(start="08h00", end="10h00"),
             cours(start="10h00", end="12h00"),
             cours(titre="Interco", start="08h00", end="10h00", room="U3-Amphi")]
    egal(len(edt_stri.deduplicer(liste)), 3, "des cours distincts ont été fusionnés")


@test
def comparer_detecte_ajout_suppression_et_modification():
    avant = [cours(), cours(titre="Interco", start="10h00", end="12h00")]
    apres = [cours(room="U3-215"), cours(titre="Réseaux", start="14h00", end="16h00")]
    types = sorted(m["type"] for m in edt_stri.comparer_emplois_du_temps(avant, apres))
    egal(types, ["ajout", "modification", "suppression"])


@test
def comparer_ne_signale_rien_quand_rien_ne_bouge():
    liste = [cours(), cours(titre="Interco", start="10h00", end="12h00")]
    egal(edt_stri.comparer_emplois_du_temps(liste, list(liste)), [], "fausse alerte")


@test
def comparer_distingue_deux_cours_empiles():
    """Même date et même heure : seuls le titre et la salle les séparent.
    Une clé trop grossière signalait une modification fantôme."""
    avant = [cours(titre="[GB] Réseaux", room="U3-1"), cours(titre="[GC] Systèmes", room="U3-2")]
    apres = [cours(titre="[GB] Réseaux", room="U3-1"), cours(titre="[GC] Systèmes", room="U4-9")]
    mods = edt_stri.comparer_emplois_du_temps(avant, apres)
    egal(len(mods), 1, "une seule modification attendue")
    egal(mods[0]["changements"]["room"]["nouveau"], "U4-9")


# =====================================================================
# Garde-fou anti-effondrement
# =====================================================================

@test
def effondrement_laisse_passer_une_baisse_normale():
    assert edt_stri.effondrement([0] * 67, [0] * 86) is None, "86 → 67 est légitime"
    assert edt_stri.effondrement([0] * 86, [0] * 86) is None, "stable"
    assert edt_stri.effondrement([0] * 99, [0] * 86) is None, "hausse"


@test
def effondrement_refuse_une_chute_brutale():
    assert edt_stri.effondrement([0] * 12, [0] * 86) is not None, "86 → 12 doit bloquer"


@test
def effondrement_se_tait_faute_de_reference():
    """Une première exécution, ou un historique minuscule, ne permet pas de
    juger : bloquer là serait un faux positif garanti."""
    assert edt_stri.effondrement([0] * 1, [0] * 5) is None, "historique trop court"
    assert edt_stri.effondrement([], [0] * 86) is None, "aucun cours lu : autre garde-fou"


# =====================================================================
# Fichier ICS
# =====================================================================

@test
def ics_utilise_des_fins_de_ligne_conformes():
    """Le mode texte de Windows retraduisait \\n en \\r\\n : les lignes
    partaient en \\r\\r\\n, invalides RFC 5545, et iOS refusait l'abonnement."""
    with tempfile.TemporaryDirectory() as dossier:
        chemin = Path(dossier) / "essai.ics"
        edt_stri.construire_ics([cours()], chemin)
        brut = chemin.read_bytes()
        assert b"\r\r\n" not in brut, "fins de ligne doublées"
        assert brut.count(b"\r\n") > 5, "aucun CRLF"


@test
def ics_ecarte_les_horaires_incoherents():
    with tempfile.TemporaryDirectory() as dossier:
        chemin = Path(dossier) / "essai.ics"
        nombre = edt_stri.construire_ics(
            [cours(), cours(start="12h00", end="10h00", titre="Incohérent")], chemin)
        egal(nombre, 1, "le cours incohérent a été publié")


@test
def ics_produit_des_identifiants_stables():
    """Un UID instable fait supprimer puis recréer l'événement à chaque
    exécution : les rappels et les couleurs choisies sont perdus."""
    with tempfile.TemporaryDirectory() as dossier:
        def uids(nom):
            chemin = Path(dossier) / nom
            edt_stri.construire_ics([cours(), cours(start="10h00", end="12h00")], chemin)
            return sorted(l for l in chemin.read_text(encoding="utf-8").splitlines()
                          if l.startswith("UID"))
        egal(uids("a.ics"), uids("b.ics"), "UID différents d'une génération à l'autre")


@test
def ics_annonce_sa_cadence_de_rafraichissement():
    with tempfile.TemporaryDirectory() as dossier:
        chemin = Path(dossier) / "essai.ics"
        edt_stri.construire_ics([cours()], chemin)
        texte = chemin.read_text(encoding="utf-8")
        for champ in ("X-WR-CALNAME", "X-PUBLISHED-TTL", "REFRESH-INTERVAL"):
            assert champ in texte, f"{champ} absent"


# =====================================================================
# Google Agenda
# =====================================================================

@test
def identifiant_evenement_respecte_l_alphabet_de_google():
    """L'API n'accepte que base32hex (a-v et 0-9) et 5 caractères au minimum.
    Un identifiant refusé fait échouer toute la synchronisation."""
    identifiant = google_agenda._identifiant(cours())
    assert 5 <= len(identifiant) <= 1024, f"longueur {len(identifiant)}"
    interdits = set(identifiant) - set("abcdefghijklmnopqrstuv0123456789")
    egal(interdits, set(), "caractères hors base32hex")


@test
def identifiant_evenement_est_deterministe_et_discriminant():
    egal(google_agenda._identifiant(cours()), google_agenda._identifiant(cours()),
         "deux appels donnent des identifiants différents")
    distincts = {google_agenda._identifiant(c) for c in (
        cours(), cours(start="10h00"), cours(end="12h00"),
        cours(titre="Autre"), cours(date="2026-09-09"))}
    egal(len(distincts), 5, "deux cours différents partagent un identifiant")


@test
def identifiant_ignore_la_salle():
    """Un changement de salle doit MODIFIER l'événement, pas le remplacer :
    sinon les personnes abonnées voient une annulation puis une création."""
    egal(google_agenda._identifiant(cours(room="U3-04")),
         google_agenda._identifiant(cours(room="U4-999")),
         "la salle entre dans l'identifiant")


@test
def evenement_colore_les_examens_en_tomate():
    ordinaire = google_agenda._en_evenement(cours(), couleur_cours="10")
    egal(ordinaire["colorId"], "10", "couleur des cours ordinaires")
    examen = google_agenda._en_evenement(
        cours(titre=f"{google_agenda.MARQUEUR_EXAMEN} BD"), couleur_cours="10")
    egal(examen["colorId"], google_agenda.COULEUR_EXAMEN, "un examen doit rester tomate")


@test
def evenement_porte_le_fuseau_de_paris():
    evenement = google_agenda._en_evenement(cours())
    egal(evenement["start"]["timeZone"], "Europe/Paris")
    assert evenement["start"]["dateTime"].startswith("2026-09-08T08:00")


@test
def identique_compare_les_instants_pas_les_chaines():
    """L'API rend « +02:00 » là où on envoie un fuseau nommé : comparer les
    chaînes ferait réécrire les 169 événements à chaque exécution."""
    voulu = google_agenda._en_evenement(cours())
    existant = {
        "summary": voulu["summary"], "location": voulu["location"],
        "description": voulu["description"],
        "start": {"dateTime": "2026-09-08T08:00:00+02:00"},
        "end": {"dateTime": "2026-09-08T10:00:00+02:00"},
    }
    assert google_agenda._identique(existant, voulu), "même instant vu comme différent"
    existant["start"] = {"dateTime": "2026-09-08T09:00:00+02:00"}
    assert not google_agenda._identique(existant, voulu), "décalage d'une heure non vu"


@test
def identique_repere_un_changement_de_couleur():
    voulu = google_agenda._en_evenement(cours(), couleur_cours="10")
    existant = dict(voulu)
    existant["colorId"] = "3"
    assert not google_agenda._identique(existant, voulu), "couleur ignorée"


@test
def couleur_evenement_reste_dans_la_palette():
    egal(google_agenda.couleur_evenement("raisin"), "3")
    egal(google_agenda.couleur_evenement("basilic"), "10")
    assert google_agenda.couleur_evenement("turquoise") is None, "couleur inventée acceptée"
    assert google_agenda.couleur_evenement("") is None, "chaîne vide acceptée"


@test
def marqueurs_d_agenda_sont_distincts():
    """Deux agendas portant le même marqueur seraient confondus, et l'un
    écraserait les cours de l'autre."""
    vus = set()
    for promo, config in telechargement.PROMOS.items():
        for moitie in ("BAS", "HAUT"):
            for cle in (config["cles"][moitie], f"{config['cles'][moitie]}-EXAMENS"):
                marque = google_agenda.marqueur(cle)
                assert marque not in vus, f"marqueur en double : {marque}"
                vus.add(marque)


# =====================================================================
# Journal et alertes
# =====================================================================

@test
def journal_ecrit_un_entete_puis_des_lignes():
    import os
    with tempfile.TemporaryDirectory() as dossier:
        chemin = Path(dossier) / "journal.csv"
        sauvegarde = edt_stri.FICHIER_JOURNAL
        try:
            edt_stri.FICHIER_JOURNAL = str(chemin)
            edt_stri.journaliser(86, 85, "OK")
            edt_stri.journaliser(12, 86, "REFUS")
            lignes = chemin.read_text(encoding="utf-8").strip().splitlines()
        finally:
            edt_stri.FICHIER_JOURNAL = sauvegarde
    egal(len(lignes), 3, "en-tête + deux lignes attendus")
    assert lignes[0].startswith("horodatage,"), "en-tête absent"
    assert lignes[2].endswith(",REFUS"), "état non consigné"


@test
def journal_n_interrompt_jamais_le_traitement():
    """Un journal illisible ne doit pas empêcher la publication des cours."""
    sauvegarde = edt_stri.FICHIER_JOURNAL
    try:
        edt_stri.FICHIER_JOURNAL = "/chemin/inexistant/journal.csv"
        edt_stri.journaliser(1, 1, "OK")  # ne doit rien lever
    finally:
        edt_stri.FICHIER_JOURNAL = sauvegarde


@test
def alerte_ci_ne_masque_jamais_la_panne_signalee():
    """Elle tourne quand tout a déjà échoué : si elle rendait un code non nul,
    elle remplacerait la vraie cause dans le rapport."""
    import os
    import alerte_ci
    sauvegarde = os.environ.get("DISCORD_WEBHOOK_URL")
    try:
        os.environ["DISCORD_WEBHOOK_URL"] = ""
        egal(alerte_ci.prevenir(), 0, "sans webhook")
        os.environ["DISCORD_WEBHOOK_URL"] = "https://discord.com/api/webhooks/0/faux"
        egal(alerte_ci.prevenir("essai"), 0, "webhook injoignable")
    finally:
        if sauvegarde is None:
            os.environ.pop("DISCORD_WEBHOOK_URL", None)
        else:
            os.environ["DISCORD_WEBHOOK_URL"] = sauvegarde


@test
def alerte_ci_n_importe_que_la_bibliotheque_standard():
    """Elle doit fonctionner quand l'installation des dépendances est
    précisément ce qui a échoué."""
    importes = _modules_importes(chemins.SRC / "alerte_ci.py")
    egal(importes - {"json", "os", "sys", "urllib"}, set(), "dépendance externe")


# =====================================================================
# Documents destinés aux abonnés
# =====================================================================

@test
def tutoriel_reste_lisible_partout():
    """Il est collé dans Discord et ouvert dans le Bloc-notes. Les accents
    passent partout — ils tiennent dans Latin-1, donc dans une console
    Windows. Les emoji et les caractères semi-graphiques, non : ils y
    deviennent des points d'interrogation."""
    brut = (chemins.DOCS / "TUTO.txt").read_bytes()
    texte = brut.decode("utf-8")
    illisibles = set()
    for c in texte:
        try:
            c.encode("cp1252")
        except UnicodeEncodeError:
            illisibles.add(c)
    egal(illisibles, set(), "caractères illisibles en console Windows")
    trop_larges = [n for n, l in enumerate(texte.splitlines(), 1) if len(l) > 76]
    egal(trop_larges, [], "lignes trop larges pour une console")


@test
def tutoriel_cite_les_agendas_reels():
    """Un tutoriel nommant un agenda qui n'existe plus envoie les gens
    chercher quelque chose d'introuvable."""
    texte = (chemins.DOCS / "TUTO.txt").read_text(encoding="utf-8")
    def sans_accent(s):
        for a, b in (("é", "e"), ("É", "E"), ("è", "e"), ("ê", "e")):
            s = s.replace(a, b)
        return s
    for config in telechargement.PROMOS.values():
        for nom in config["agendas"].values():
            assert sans_accent(nom) in sans_accent(texte), f"« {nom} » absent du tutoriel"


# =====================================================================
# Calendrier Moodle (rendus)
# =====================================================================

def calendrier_moodle(*evenements):
    """Fabrique un export iCalendar comme en produit Moodle (lignes en CRLF)."""
    lignes = ["BEGIN:VCALENDAR", "VERSION:2.0",
              "PRODID:-//Moodle Pty Ltd//NONSGML Moodle//EN", "METHOD:PUBLISH"]
    for evenement in evenements:
        lignes += ["BEGIN:VEVENT"] + list(evenement) + ["END:VEVENT"]
    return "\r\n".join(lignes + ["END:VCALENDAR", ""])


@test
def moodle_deplie_les_lignes_coupees():
    # La norme replie au-delà de 75 octets. Sans recollage, un intitulé long
    # arrive tronqué et le reste devient une propriété inconnue.
    lignes = moodle.deplier("SUMMARY:Rendu du projet\r\n  de compilation\r\nUID:1")
    egal(lignes[0], "SUMMARY:Rendu du projet de compilation", "ligne recollée")
    egal(lignes[1], "UID:1", "ligne suivante intacte")


@test
def moodle_separe_nom_parametres_et_valeur():
    egal(moodle._decouper("DTSTART;VALUE=DATE:20260915"),
         ("DTSTART", {"VALUE": "DATE"}, "20260915"), "paramètre lu")
    # Un deux-points dans un paramètre entre guillemets ne doit pas être pris
    # pour le séparateur : la valeur serait coupée en plein milieu.
    nom, params, valeur = moodle._decouper('X;ALTREP="http://a/b":texte')
    egal((nom, valeur), ("X", "texte"), "deux-points protégé par les guillemets")


@test
def moodle_defait_les_echappements():
    egal(moodle._decoder("compte-rendu\\, format PDF"), "compte-rendu, format PDF")
    egal(moodle._decoder("ligne\\nsuivante"), "ligne\nsuivante")


@test
def moodle_ramene_utc_a_l_heure_de_paris():
    # Moodle exporte en UTC. Une échéance à 23h59 heure d'été est écrite
    # 21h59Z : la lire telle quelle la daterait de deux heures trop tôt, et
    # une échéance de minuit basculerait la veille.
    moment, journee = moodle._lire_horodatage("20260915T215900Z", {})
    egal(moment.strftime("%Y-%m-%d %H:%M"), "2026-09-15 23:59", "UTC -> Paris")
    egal(journee, False, "ce n'est pas une journée entière")


@test
def moodle_reconnait_une_journee_entiere():
    moment, journee = moodle._lire_horodatage("20261002", {"VALUE": "DATE"})
    egal((moment.strftime("%Y-%m-%d"), journee), ("2026-10-02", True))


@test
def moodle_lit_les_durees_iso():
    egal(moodle._lire_duree("PT0S").total_seconds(), 0.0, "durée nulle")
    egal(moodle._lire_duree("PT1H30M").total_seconds(), 5400.0, "1 h 30")
    egal(moodle._lire_duree("P1D").total_seconds(), 86400.0, "un jour")
    egal(moodle._lire_duree("n'importe quoi"), None, "forme inconnue")


@test
def moodle_garde_l_instant_exact_d_une_echeance():
    # DURATION:PT0S (ou DTEND égal à DTSTART) est la forme normale d'une date
    # limite. Vérifié contre l'API : Google accepte un événement de durée
    # nulle. L'épaissir décalerait le rappel, qui s'ancre sur le DÉBUT.
    ics = calendrier_moodle(["UID:1", "SUMMARY:DM_OSI doit être rendu",
                             "DTSTART:20260906T200000Z", "DTEND:20260906T200000Z"])
    evenement = moodle.analyser(ics)[0]
    egal(evenement["date"], "2026-09-06", "20h00 UTC est encore le 6 à Paris")
    egal((evenement["start"], evenement["end"]), ("22h00", "22h00"))
    egal(evenement["echeance"], True, "reconnue comme une date limite")


@test
def moodle_distingue_une_echeance_d_une_seance():
    # inetdoc publie les deux dans le même calendrier : 20 échéances et 49
    # séances de TP, ces dernières déjà présentes dans l'emploi du temps.
    ics = calendrier_moodle(
        ["UID:1", "SUMMARY:Validations TP1 doit être achevée",
         "DTSTART:20260910T203000Z", "DURATION:PT0S"],
        ["UID:2", "SUMMARY:M1 ASR TP1 - iSCSI - G1",
         "DTSTART:20260825T110000Z", "DTEND:20260825T140000Z"])
    egal([e["echeance"] for e in moodle.analyser(ics)], [False, True],
         "la séance du 25/08 n'en est pas une, la validation du 10/09 si")
    retenus = moodle.analyser(ics, echeances_seulement=True)
    egal([e["titre"] for e in retenus], ["Validations TP1 doit être achevée"])


@test
def moodle_epaissit_une_echeance_si_on_le_demande():
    # MOODLE_DUREE_ECHEANCE rend l'échéance visible dans la grille ; elle se
    # TERMINE alors à l'heure limite, elle n'y commence pas.
    sauvegarde = moodle.DUREE_ECHEANCE
    try:
        moodle.DUREE_ECHEANCE = timedelta(minutes=30)
        ics = calendrier_moodle(["UID:1", "SUMMARY:Rendu",
                                 "DTSTART:20260906T200000Z", "DURATION:PT0S"])
        egal_creneau = moodle.analyser(ics)[0]
        egal((egal_creneau["start"], egal_creneau["end"]), ("21h30", "22h00"))

        # 00h10 : reculer de 30 min changerait de jour, on s'arrête à minuit.
        ics = calendrier_moodle(["UID:1", "SUMMARY:Rendu",
                                 "DTSTART:20260915T221000Z", "DURATION:PT0S"])
        minuit = moodle.analyser(ics)[0]
        egal(minuit["date"], "2026-09-16", "00h10 le 16 à Paris")
        egal((minuit["start"], minuit["end"]), ("00h00", "00h10"))
    finally:
        moodle.DUREE_ECHEANCE = sauvegarde


@test
def moodle_nettoie_les_entites_html_des_titres():
    # inetdoc publie « Hub &amp\; Spoke » : un échappement iCalendar (\;) posé
    # sur une entité HTML restée dans la base Moodle.
    ics = calendrier_moodle(["UID:1", "SUMMARY:TP3 - Hub &amp\\; Spoke",
                             "DTSTART:20260928T100000Z", "DURATION:PT0S",
                             "CATEGORIES:Admin Sys &amp\\; Réseaux"])
    evenement = moodle.analyser(ics)[0]
    egal(evenement["titre"], "TP3 - Hub & Spoke", "titre lisible")
    egal(evenement["prof"], "Admin Sys & Réseaux", "cours lisible")


@test
def moodle_lit_la_salle_quand_elle_existe():
    # inetdoc renseigne LOCATION, le STRI non.
    ics = calendrier_moodle(["UID:1", "SUMMARY:TP", "LOCATION:U3 307/308",
                             "DTSTART:20260825T110000Z", "DTEND:20260825T140000Z"])
    egal(moodle.analyser(ics)[0]["room"], "U3 307/308")


@test
def moodle_est_tout_ou_rien_sur_plusieurs_sources():
    """Si une source est illisible, aucune n'est publiée.

    L'agenda est unique et la synchronisation est un rapprochement complet :
    publier les seules sources lisibles effacerait les rendus des autres.
    """
    egal(sorted(moodle.SOURCES), ["INETDOC", "STRI"], "les deux Moodle")
    for cle, config in moodle.SOURCES.items():
        assert config["variable"].startswith("MOODLE_"), f"{cle} : variable nommée"
        assert config["nom"], f"{cle} : source nommée"


@test
def moodle_transmet_le_cours_et_la_description():
    ics = calendrier_moodle([
        "UID:1", "SUMMARY:Rendu TP",
        "DESCRIPTION:<p>D\u00e9poser ici&nbsp;: <a href=\"http://x\">lien</a></p>",
        "DTSTART:20260915T100000Z", "DURATION:PT0S", "CATEGORIES:Rendu M1"])
    evenement = moodle.analyser(ics)[0]
    egal(evenement["prof"], "Rendu M1", "le cours Moodle")
    assert "<p>" not in evenement["description"], "HTML retiré"
    assert "\xa0" not in evenement["description"], "espace insécable normalisée"
    assert "Rendu M1" in evenement["description"], "cours rappelé dans la description"


@test
def moodle_filtre_sur_le_cours():
    ics = calendrier_moodle(
        ["UID:1", "SUMMARY:Rendu TP", "DTSTART:20260915T100000Z", "CATEGORIES:Rendu M1"],
        ["UID:2", "SUMMARY:Devoir", "DTSTART:20260916T100000Z", "CATEGORIES:Anglais"])
    egal(len(moodle.analyser(ics, "")), 2, "sans filtre, tout est gardé")
    egal(len(moodle.analyser(ics, "rendu m1")), 1, "filtre insensible à la casse")


@test
def moodle_ignore_un_evenement_sans_titre():
    ics = calendrier_moodle(["UID:1", "DTSTART:20260915T100000Z"])
    egal(moodle.analyser(ics), [], "un événement sans intitulé n'est pas publiable")


@test
def moodle_masque_le_jeton_dans_les_messages():
    # Les journaux d'un dépôt public sont lisibles par tout le monde, et
    # l'authtoken donne accès au calendrier personnel sans mot de passe.
    masquee = moodle._masquer(
        "https://stri.fr/eformation/calendar/export_execute.php"
        "?userid=42&authtoken=deadbeef&preset_what=all")
    assert "deadbeef" not in masquee, "jeton masqué"
    assert "42" not in masquee, "identifiant masqué"
    assert "preset_what=all" in masquee, "le reste de l'URL reste lisible"


@test
def moodle_distingue_panne_et_calendrier_vide():
    # None veut dire « je n'ai pas pu lire », [] veut dire « il n'y a rien ».
    # Les confondre viderait l'agenda à la première panne réseau.
    egal(moodle.analyser(calendrier_moodle()), [], "calendrier vide -> liste vide")


@test
def rappel_pose_une_notification_avant_l_echeance():
    evenement = google_agenda._en_evenement(
        cours(titre="DM_OSI doit être rendu"), rappel_minutes=300)
    egal(evenement["reminders"],
         {"useDefault": False, "overrides": [{"method": "popup", "minutes": 300}]})


@test
def rappel_a_zero_veut_dire_aucun_rappel():
    # « Aucun rappel » n'est pas « ne rien dire » : sans useDefault=False,
    # Google appliquerait les réglages par défaut de l'agenda.
    evenement = google_agenda._en_evenement(cours(), rappel_minutes=0)
    egal(evenement["reminders"], {"useDefault": False, "overrides": []})


@test
def rappel_absent_ne_touche_pas_l_evenement():
    # Les quatre agendas de cours n'en posent pas. Si le champ partait quand
    # même, la première exécution réécrirait leurs ~300 événements.
    assert "reminders" not in google_agenda._en_evenement(cours()), "champ non envoyé"


@test
def rappel_non_demande_n_est_jamais_compare():
    # Google renvoie toujours un bloc `reminders`, même quand on n'en a pas
    # envoyé. Le comparer sans l'avoir demandé ferait réécrire tous les cours.
    voulu = google_agenda._en_evenement(cours())
    existant = dict(voulu, reminders={"useDefault": True})
    assert google_agenda._identique(existant, voulu), "cours laissé tranquille"


@test
def rappel_modifie_est_bien_detecte():
    voulu = google_agenda._en_evenement(cours(), rappel_minutes=300)
    inchange = dict(voulu)
    assert google_agenda._identique(inchange, voulu), "identique à lui-même"

    autre = dict(voulu, reminders={"useDefault": False,
                                   "overrides": [{"method": "popup", "minutes": 60}]})
    assert not google_agenda._identique(autre, voulu), "délai changé"

    defaut = dict(voulu, reminders={"useDefault": True})
    assert not google_agenda._identique(defaut, voulu), "rappel effacé à la main"


@test
def rappel_compare_sans_tenir_compte_de_l_ordre():
    # L'API ne garantit pas l'ordre de `overrides` : le comparer tel quel
    # ferait réécrire l'événement à chaque exécution.
    a = {"useDefault": False, "overrides": [{"method": "popup", "minutes": 300},
                                            {"method": "email", "minutes": 60}]}
    b = {"useDefault": False, "overrides": [{"method": "email", "minutes": 60},
                                            {"method": "popup", "minutes": 300}]}
    egal(google_agenda._cle_rappels(a), google_agenda._cle_rappels(b))
    # Sans overrides, l'API omet la clé plutôt que d'envoyer une liste vide.
    egal(google_agenda._cle_rappels({"useDefault": False}),
         google_agenda._cle_rappels({"useDefault": False, "overrides": []}))


@test
def google_traduit_une_journee_entiere_en_dates():
    # Une journée entière s'exprime avec `date`, pas `dateTime` ; sa fin est
    # EXCLUSIVE, donc au lendemain.
    evenement = google_agenda._en_evenement(
        {"date": "2026-10-02", "start": None, "end": None, "titre": "Portes ouvertes"})
    egal(evenement["start"], {"date": "2026-10-02"}, "début")
    egal(evenement["end"], {"date": "2026-10-03"}, "fin exclusive")


@test
def google_compare_les_journees_entieres_sans_planter():
    # `_identique` ne lisait que `dateTime` : sur une journée entière il levait
    # une KeyError, et la synchronisation s'arrêtait au premier événement.
    evenement = google_agenda._en_evenement(
        {"date": "2026-10-02", "start": None, "end": None, "titre": "Portes ouvertes"})
    assert google_agenda._identique(evenement, evenement), "identique à lui-même"
    autre = google_agenda._en_evenement(
        {"date": "2026-10-03", "start": None, "end": None, "titre": "Portes ouvertes"})
    assert not google_agenda._identique(evenement, autre), "dates différentes"


@test
def google_ne_supprime_pas_avant_la_date_plancher():
    # L'export Moodle porte sur une fenêtre glissante : sans plancher, chaque
    # exécution effacerait les échéances passées et les annoncerait comme des
    # suppressions.
    egal(google_agenda._debut({"start": {"date": "2026-10-02"}}), "2026-10-02")
    egal(google_agenda._debut({"start": {"dateTime": "2026-10-02T08:00:00+02:00"}}),
         "2026-10-02")
    egal(google_agenda._debut({}), "", "événement sans début")


@test
def rendus_suit_un_rendu_deplace_par_son_uid():
    # L'UID Moodle survit à un changement de date : c'est ce qui permet
    # d'annoncer « échéance repoussée » plutôt qu'une suppression suivie d'un
    # ajout, deux lignes pour un seul événement.
    avant = [{"uid": "7@stri", "date": "2026-09-15", "start": "23h29",
              "end": "23h59", "titre": "Rendu TP"}]
    apres = [{"uid": "7@stri", "date": "2026-09-22", "start": "23h29",
              "end": "23h59", "titre": "Rendu TP"}]
    modifications = rendus.comparer(avant, apres)
    egal(len(modifications), 1, "un seul changement")
    egal(modifications[0]["type"], "modification", "et non ajout + suppression")
    egal(modifications[0]["changements"]["date"]["nouveau"], "2026-09-22")


@test
def rendus_refuse_de_publier_un_effondrement():
    anciens = [{"uid": str(i)} for i in range(10)]
    assert rendus.effondrement([{"uid": "1"}], anciens), "chute de 90 % signalée"
    assert rendus.effondrement(anciens, anciens) is None, "stabilité acceptée"
    assert rendus.effondrement([], []) is None, "première exécution acceptée"


@test
def rendus_ne_bloque_pas_sur_de_petits_effectifs():
    # En début d'année le calendrier Moodle ne contient qu'un ou deux devoirs.
    # Un pourcentage n'y veut rien dire : le seul devoir retiré ferait 100 %,
    # et le garde-fou bloquerait toutes les publications suivantes.
    assert rendus.effondrement([], [{"uid": "1"}]) is None, "1 -> 0 accepté"
    assert rendus.effondrement([], [{"uid": str(i)} for i in range(3)]) is None


# =====================================================================
# Exécution
# =====================================================================

def principale():
    filtre = next((a for a in sys.argv[1:] if not a.startswith("-")), None)
    choisis = [t for t in TESTS if not filtre or filtre in t.__name__]

    print(f"Tests de la chaîne EDT — {len(choisis)} cas\n")
    echecs = []
    for fonction in choisis:
        intitule = fonction.__name__.replace("_", " ")
        try:
            fonction()
            print(f"  ✅ {intitule}")
        except AssertionError as e:
            echecs.append(fonction.__name__)
            print(f"  ❌ {intitule}\n      {e}")
        except Exception as e:
            echecs.append(fonction.__name__)
            print(f"  💥 {intitule}\n      {type(e).__name__}: {e}")

    print("\n" + "─" * 70)
    if echecs:
        print(f"❌ {len(echecs)} échec(s) sur {len(choisis)} : {', '.join(echecs)}")
        return 1
    print(f"✅ {len(choisis)} tests passés.")
    return 0


if __name__ == "__main__":
    sys.exit(principale())

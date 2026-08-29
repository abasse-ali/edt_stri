"""
Synchronisation des rendus Moodle vers un agenda Google.

Pendant que `edt_stri.py` s'occupe des cours (un PDF), ce script s'occupe des
échéances : devoirs à déposer, dates limites, examens déclarés dans un cours du
site eFormation. La source est le calendrier Moodle exporté en iCalendar ; sa
lecture est dans `moodle.py`, qui explique aussi comment obtenir l'adresse.

    python rendus.py            # télécharge, compare, publie
    python rendus.py --lister   # affiche seulement ce que contient la source

Les deux chaînes sont séparées à dessein : le PDF et Moodle changent à des
moments différents, et un incident sur l'un ne doit pas empêcher l'autre de se
mettre à jour.
"""

import json
import sys
from datetime import date, datetime
from pathlib import Path

# edt_stri porte l'authentification Google, l'envoi Discord et le journal.
# L'importer configure au passage une promotion (M1/BAS par défaut) dont on ne
# se sert pas ici : sans effet, aucun traitement ne démarre à l'import.
import edt_stri
import google_agenda
import moodle
from telechargement import variable_env

for _flux in (sys.stdout, sys.stderr):
    try:
        _flux.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

NOM_AGENDA = variable_env("MOODLE_AGENDA", "Rendu M1")

# Étiquette posée dans la description de l'agenda : c'est elle, et non le nom,
# qui permet de le retrouver après un renommage.
CLE_AGENDA = "MOODLE-RENDUS"

# Couleurs libres, distinctes de celles des quatre agendas de cours.
COULEUR_AGENDA = variable_env("MOODLE_COULEUR", "mangue")
COULEUR_EVENEMENTS = variable_env("MOODLE_COULEUR_EVENEMENTS", "mandarine")

FICHIER_JSON = variable_env("MOODLE_JSON", "rendus_data.json")

# Même garde-fou que pour l'emploi du temps : une chute brutale du nombre
# d'échéances est plus probablement une panne qu'une vraie annulation générale.
CHUTE_MAX = int(variable_env("MOODLE_CHUTE_MAX", "50"))

# ... mais un pourcentage ne veut rien dire sur trois événements : un seul
# devoir retiré ferait 33 %, et en début d'année le calendrier n'en contient
# souvent qu'un. En dessous de ce seuil, on publie sans discuter.
EFFECTIF_MINIMAL = int(variable_env("MOODLE_EFFECTIF_MINIMAL", "5"))
FORCER = variable_env("EDT_FORCER") == "1"


def _cle(evenement):
    """Identifie un rendu d'une exécution à l'autre.

    L'UID Moodle est stable même quand la date bouge : c'est ce qui permet
    d'annoncer « échéance repoussée » au lieu d'une suppression suivie d'un
    ajout. Le repli sert aux rares événements exportés sans UID.
    """
    return (evenement.get('uid')
            or f"{evenement['date']}|{evenement['start']}|{evenement['titre']}")


def comparer(anciens, nouveaux):
    """Différence entre deux listes de rendus, pour l'annonce Discord."""
    avant = {_cle(e): e for e in anciens}
    apres = {_cle(e): e for e in nouveaux}
    modifications = []

    for cle, nouveau in apres.items():
        ancien = avant.get(cle)
        if ancien is None:
            modifications.append({**nouveau, "type": "ajout"})
            continue
        changements = {
            champ: {"ancien": ancien.get(champ), "nouveau": nouveau.get(champ)}
            for champ in ("titre", "date", "start", "end")
            if nouveau.get(champ) != ancien.get(champ)
        }
        if changements:
            modifications.append({**nouveau, "type": "modification",
                                  "changements": changements})

    for cle, ancien in avant.items():
        if cle not in apres:
            modifications.append({**ancien, "type": "suppression"})

    return modifications


def _quand(evenement):
    """« 15/09/2026 à 23h59 », ou « 15/09/2026 » pour une journée entière."""
    try:
        jour = datetime.strptime(evenement['date'], '%Y-%m-%d').strftime('%d/%m/%Y')
    except (ValueError, KeyError):
        jour = evenement.get('date', '?')
    return f"{jour} à {evenement['start']}" if evenement.get('start') else jour


def prevenir_discord(modifications):
    """Annonce les rendus ajoutés, déplacés ou retirés.

    Le vocabulaire est celui des échéances, pas celui des cours : un rendu qui
    disparaît n'est pas « annulé », il est retiré du calendrier Moodle — ce qui
    arrive aussi quand un enseignant corrige une erreur de saisie.
    """
    if not modifications:
        return

    lignes = [f"**{NOM_AGENDA}** — {len(modifications)} changement(s) :\n"]
    for modif in modifications:
        titre = modif.get('titre', 'Rendu inconnu')
        if modif['type'] == 'ajout':
            lignes.append(f"🟢 **NOUVEAU RENDU** : {titre} — {_quand(modif)}")
        elif modif['type'] == 'suppression':
            lignes.append(f"🔴 **RENDU RETIRÉ** : {titre} — {_quand(modif)}")
        else:
            lignes.append(f"🟠 **MODIFIÉ** : {titre} — {_quand(modif)}")
            noms = {"titre": "Intitulé", "date": "Date",
                    "start": "Heure de début", "end": "Heure de fin"}
            for champ, valeurs in modif.get('changements', {}).items():
                lignes.append(f"   ↳ *{noms.get(champ, champ)}* : "
                              f"~~{valeurs['ancien']}~~ ➔ **{valeurs['nouveau']}**")
        lignes.append("")

    edt_stri._envoyer_embed(f"📌 {NOM_AGENDA} — mise à jour", "\n".join(lignes), 0xE67E22)


def charger_anciens():
    """Rendus de l'exécution précédente. Liste vide si le fichier manque."""
    chemin = Path(FICHIER_JSON)
    if not chemin.exists():
        return []
    try:
        return json.loads(chemin.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"⚠️ {FICHIER_JSON} illisible ({e}) : on repart de zéro.")
        return []


def effondrement(nouveaux, anciens):
    """Message d'alerte si le nombre de rendus s'effondre, sinon None."""
    if len(anciens) < EFFECTIF_MINIMAL or FORCER:
        return None
    chute = 100 * (len(anciens) - len(nouveaux)) / len(anciens)
    if chute < CHUTE_MAX:
        return None
    return (f"{len(anciens)} rendu(s) la fois précédente, {len(nouveaux)} cette "
            f"fois — une chute de {chute:.0f} %, au-delà du seuil de {CHUTE_MAX} %.")


def synchroniser_agenda(evenements, creds):
    """Écrit les rendus dans l'agenda Google. Rend son identifiant, ou None."""
    if creds is None:
        return None
    print("📅 Synchronisation de l'agenda des rendus...")
    try:
        from googleapiclient.discovery import build
        service = build('calendar', 'v3', credentials=creds)

        agenda_id = google_agenda.trouver_ou_creer_agenda(
            service, nom=NOM_AGENDA, cle=CLE_AGENDA,
            description="Rendus et dates limites Moodle, mis à jour "
                        "automatiquement.")
        google_agenda.appliquer_couleur(service, agenda_id, COULEUR_AGENDA)

        # `depuis` = aujourd'hui : les échéances passées ne sont jamais
        # supprimées. L'export Moodle porte sur une fenêtre choisie au moment
        # où l'adresse a été créée ; si elle ne remonte pas assez loin, sans
        # cette borne chaque exécution effacerait l'historique puis
        # l'annoncerait comme autant de suppressions.
        bilan = google_agenda.synchroniser(
            service, evenements, identifiant_agenda=agenda_id,
            couleur_cours=google_agenda.couleur_evenement(COULEUR_EVENEMENTS),
            depuis=date.today().isoformat())
        edt_stri._rapporter("Rendus", bilan)
        return agenda_id
    except Exception as e:
        print(f"❌ Erreur de synchronisation de l'agenda des rendus : {e}")
        edt_stri.envoyer_alerte_discord(
            f"**{NOM_AGENDA} — échec de synchronisation.**\n`{str(e)[:600]}`")
        return None


def principale():
    """Récupère le calendrier Moodle et l'aligne sur l'agenda Google.

    Rend 0 si tout a abouti, 1 sinon. Comme pour l'emploi du temps, ce code
    conditionne la sauvegarde en CI : un échec laisse le JSON précédent en
    place, et l'exécution suivante réessaie.
    """
    print("=" * 60)
    print(f"📌 Rendus Moodle → « {NOM_AGENDA} »")
    print("=" * 60)

    if not moodle.URL_ICS:
        # Fonctionnalité facultative : sans adresse d'export, il n'y a rien à
        # faire, et ce n'est pas une panne. Rendre 1 ferait échouer la CI toutes
        # les heures et alerterait sur Discord pour une absence de réglage.
        print("ℹ️ MOODLE_ICS_URL n'est pas définie : rien à synchroniser.")
        print("   Marche à suivre pour l'obtenir : voir l'en-tête de moodle.py.")
        return 0

    evenements = moodle.recuperer()
    if evenements is None:
        edt_stri.envoyer_alerte_discord(
            f"**{NOM_AGENDA} — calendrier Moodle illisible.** "
            "Adresse d'export absente, expirée ou refusée.")
        return 1

    print(f"📚 {len(evenements)} événement(s) retenu(s).")
    for nom, nombre in moodle.repartition(evenements):
        print(f"   {nombre:3d}  {nom}")

    anciens = charger_anciens()

    alerte = effondrement(evenements, anciens)
    if alerte:
        print(f"⛔ {alerte}")
        print("   Données précédentes conservées. MOODLE_CHUTE_MAX ou "
              "EDT_FORCER=1 pour passer outre.")
        edt_stri.envoyer_alerte_discord(f"**{NOM_AGENDA} — publication refusée.**\n{alerte}")
        edt_stri.journaliser(len(evenements), len(anciens), "effondrement",
                             promo="MOODLE", moitie="-", agenda=NOM_AGENDA)
        return 1

    modifications = comparer(anciens, evenements)
    print(f"🔍 {len(modifications)} changement(s) depuis la dernière fois.")
    prevenir_discord(modifications)

    creds = edt_stri.obtenir_identifiants()
    if creds is None:
        edt_stri.envoyer_alerte_discord(
            f"**{NOM_AGENDA} — autorisation Google indisponible.** "
            "Le jeton doit être régénéré.")
        edt_stri.journaliser(len(evenements), len(anciens), "sans-agenda",
                             promo="MOODLE", moitie="-", agenda=NOM_AGENDA)
        return 1

    if synchroniser_agenda(evenements, creds) is None:
        edt_stri.journaliser(len(evenements), len(anciens), "echec-agenda",
                             promo="MOODLE", moitie="-", agenda=NOM_AGENDA)
        return 1

    # Le JSON n'est écrit qu'une fois l'agenda à jour : s'il l'était avant, un
    # échec de synchronisation ferait croire à la prochaine exécution que tout
    # est déjà publié, et les changements ne partiraient jamais.
    Path(FICHIER_JSON).write_text(
        json.dumps(evenements, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"💾 {FICHIER_JSON} enregistré.")

    edt_stri.journaliser(len(evenements), len(anciens), "ok",
                         promo="MOODLE", moitie="-", agenda=NOM_AGENDA)
    print("✅ Terminé.")
    return 0


if __name__ == "__main__":
    if "--lister" in sys.argv:
        liste = moodle.recuperer()
        if liste is None:
            sys.exit(1)
        print(f"📚 {len(liste)} événement(s) :")
        for nom, nombre in moodle.repartition(liste):
            print(f"   {nombre:3d}  {nom}")
        for e in liste:
            creneau = f"{e['start']}-{e['end']}" if e['start'] else "journée"
            print(f"   {e['date']}  {creneau:>12}  {e['titre']}")
        sys.exit(0)
    sys.exit(principale())

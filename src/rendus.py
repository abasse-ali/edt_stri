"""
Synchronisation des rendus Moodle vers un agenda Google.

Pendant que `edt_stri.py` s'occupe des cours (un PDF), ce script s'occupe des
échéances : devoirs à déposer, validations de TP, ouvertures et fermetures de
quiz. Elles viennent de PLUSIEURS calendriers Moodle — celui du STRI et celui
de `moodle.inetdoc.net` — décrits dans `moodle.SOURCES`, qui explique aussi
comment obtenir leurs adresses.

Tout atterrit dans un seul agenda : c'est un endroit unique où regarder ce
qu'il reste à rendre. La contrepartie est que la lecture est tout ou rien —
une source illisible interrompt la publication, faute de quoi le rapprochement
effacerait les échéances des autres.

    python src/rendus.py            # télécharge, compare, publie
    python src/rendus.py --lister   # affiche seulement ce que contient la source

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
import chemins
import edt_stri
import google_agenda
import moodle
from telechargement import variable_env

for _flux in (sys.stdout, sys.stderr):
    try:
        _flux.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Quel agenda puise dans quelles sources. Deux promotions suivent des cours en
# partie communs : chacune a son propre export eFormation, et toutes deux
# partagent celui d'inetdoc, où vivent les validations de TP et les quiz.
#
# Un agenda dont AUCUNE source n'est configurée est simplement sauté.
AGENDAS = {
    "RENDU": {"sources": ["STRI", "INETDOC"], "json": "rendus_data.json"},
    "RENDU_INGE2": {"sources": ["STRI_INGE2", "INETDOC"],
                    "json": "rendus_data_inge2.json"},
}

# L'agenda historique, pour les appels et les tests qui n'en connaissent qu'un.
NOM_AGENDA = google_agenda.NOM_RENDUS
CLE_AGENDA = google_agenda.CLE_RENDUS

# Couleurs libres, distinctes de celles des quatre agendas de cours.
COULEUR_AGENDA = variable_env("MOODLE_COULEUR", "mangue")
COULEUR_EVENEMENTS = variable_env("MOODLE_COULEUR_EVENEMENTS", "mandarine")

FICHIER_JSON = variable_env("MOODLE_JSON", str(chemins.donnee("rendus_data.json")))

# Notification poussée par le téléphone tant de minutes avant l'échéance.
# 300 = 5 h. Mettre 0 pour n'en poser aucun.
#
# C'est Google qui la déclenche, pas ce script : le rappel part même si la CI
# est en panne, et il n'y a rien à retenir entre deux exécutions. Un rappel
# appartient en revanche au propriétaire de l'agenda — les personnes avec qui
# il serait partagé garderaient les leurs.
RAPPEL_MINUTES = int(variable_env("MOODLE_RAPPEL_MINUTES", "300"))

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


def prevenir_discord(modifications, nom=None):
    """Annonce les rendus ajoutés, déplacés ou retirés.

    Le vocabulaire est celui des échéances, pas celui des cours : un rendu qui
    disparaît n'est pas « annulé », il est retiré du calendrier Moodle — ce qui
    arrive aussi quand un enseignant corrige une erreur de saisie.
    """
    if not modifications:
        return

    nom = nom or NOM_AGENDA
    lignes = [f"**{nom}** — {len(modifications)} changement(s) :\n"]
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

    edt_stri._envoyer_embed(f"📌 {nom} — mise à jour", "\n".join(lignes), 0xE67E22)


def fichier_etat(agenda):
    """Où vit l'état précédent d'un agenda donné.

    Le M1 garde `MOODLE_JSON` : ce réglage existait avant qu'il y ait plusieurs
    agendas, et le fichier est déjà commité sous ce nom.
    """
    if agenda == "RENDU":
        return Path(FICHIER_JSON)
    return chemins.donnee(AGENDAS[agenda]["json"])


def charger_anciens(chemin=None):
    """Rendus de l'exécution précédente. Liste vide si le fichier manque."""
    chemin = Path(chemin or FICHIER_JSON)
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


def synchroniser_agenda(evenements, creds, nom=None, cle=None):
    """Écrit les rendus dans l'agenda Google. Rend son identifiant, ou None."""
    if creds is None:
        return None
    nom, cle = nom or NOM_AGENDA, cle or CLE_AGENDA
    print(f"📅 Synchronisation de « {nom} »...")
    try:
        from googleapiclient.discovery import build
        service = build('calendar', 'v3', credentials=creds)

        agenda_id = google_agenda.trouver_ou_creer_agenda(
            service, nom=nom, cle=cle,
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
            depuis=date.today().isoformat(), rappel_minutes=RAPPEL_MINUTES)
        edt_stri._rapporter(nom, bilan)
        return agenda_id
    except Exception as e:
        print(f"❌ Erreur de synchronisation de « {nom} » : {e}")
        edt_stri.envoyer_alerte_discord(
            f"**{nom} — échec de synchronisation.**\n`{str(e)[:600]}`")
        return None


def traiter(agenda, creds):
    """Publie UN agenda de rendus. Rend 0 si tout a abouti, 1 sinon.

    Chaque agenda a ses propres sources, son propre état, ses propres
    notifications : un incident sur l'un ne doit rien changer à l'autre.
    """
    nom, cle = google_agenda.RENDUS[agenda]
    sources = AGENDAS[agenda]["sources"]
    etat = fichier_etat(agenda)

    print()
    print("=" * 60)
    print(f"📌 Rendus Moodle → « {nom} »")
    print("=" * 60)

    resultat = moodle.recuperer_tout(sources=sources)
    if resultat is None:
        edt_stri.envoyer_alerte_discord(
            f"**{nom} — calendrier Moodle illisible.** "
            "Adresse d'export absente, expirée ou refusée. Rien n'a été publié.")
        return 1

    evenements, bilan = resultat
    for source, nombre in bilan:
        print(f"   {source} : {nombre} événement(s)")
    print(f"📚 {len(evenements)} événement(s) retenu(s).")
    for cours, nombre in moodle.repartition(evenements):
        print(f"   {nombre:3d}  {cours}")

    anciens = charger_anciens(etat)

    alerte = effondrement(evenements, anciens)
    if alerte:
        print(f"⛔ {alerte}")
        print("   Données précédentes conservées. MOODLE_CHUTE_MAX ou "
              "EDT_FORCER=1 pour passer outre.")
        edt_stri.envoyer_alerte_discord(f"**{nom} — publication refusée.**\n{alerte}")
        edt_stri.journaliser(len(evenements), len(anciens), "effondrement",
                             promo="MOODLE", moitie="-", agenda=nom)
        return 1

    modifications = comparer(anciens, evenements)
    print(f"🔍 {len(modifications)} changement(s) depuis la dernière fois.")
    prevenir_discord(modifications, nom)

    if synchroniser_agenda(evenements, creds, nom, cle) is None:
        edt_stri.journaliser(len(evenements), len(anciens), "echec-agenda",
                             promo="MOODLE", moitie="-", agenda=nom)
        return 1

    # Le JSON n'est écrit qu'une fois l'agenda à jour : s'il l'était avant, un
    # échec de synchronisation ferait croire à la prochaine exécution que tout
    # est déjà publié, et les changements ne partiraient jamais.
    etat.write_text(json.dumps(evenements, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(f"💾 {etat.name} enregistré.")

    edt_stri.journaliser(len(evenements), len(anciens), "ok",
                         promo="MOODLE", moitie="-", agenda=nom)
    return 0


def agendas_actifs():
    """Les agendas dont au moins une source est configurée."""
    return [a for a, config in AGENDAS.items()
            if moodle.sources_configurees(config["sources"])]


def principale():
    """Publie tous les agendas de rendus configurés.

    Rend 0 si tous ont abouti, 1 dès qu'un seul échoue. Comme pour l'emploi du
    temps, ce code conditionne la sauvegarde en CI : un échec laisse les JSON
    précédents en place, et l'exécution suivante réessaie.
    """
    actifs = agendas_actifs()
    if not actifs:
        # Fonctionnalité facultative : sans adresse d'export, il n'y a rien à
        # faire, et ce n'est pas une panne. Rendre 1 ferait échouer la CI toutes
        # les heures et alerterait sur Discord pour une absence de réglage.
        print("ℹ️ Aucune source Moodle configurée : rien à synchroniser.")
        print("   Variables attendues : "
              + ", ".join(c["variable"] for c in moodle.SOURCES.values()))
        print("   Marche à suivre pour les obtenir : voir l'en-tête de moodle.py.")
        return 0

    # L'autorisation est obtenue UNE fois : elle vaut pour tous les agendas, et
    # la redemander par agenda multiplierait les écritures de token.json.
    creds = edt_stri.obtenir_identifiants()
    if creds is None:
        edt_stri.envoyer_alerte_discord(
            "**Rendus Moodle — autorisation Google indisponible.** "
            "Le jeton doit être régénéré.")
        return 1

    echecs = [a for a in actifs if traiter(a, creds) != 0]
    print()
    if echecs:
        print(f"❌ Échec sur : {', '.join(google_agenda.RENDUS[a][0] for a in echecs)}")
        return 1
    print(f"✅ Terminé — {len(actifs)} agenda(s) à jour.")
    return 0


if __name__ == "__main__":
    if "--lister" in sys.argv:
        for agenda in agendas_actifs():
            print(f"\n═══ {google_agenda.RENDUS[agenda][0]} ═══")
            resultat = moodle.recuperer_tout(sources=AGENDAS[agenda]["sources"])
            if resultat is None:
                sys.exit(1)
            liste, bilan = resultat
            for source, nombre in bilan:
                print(f"   {source} : {nombre} événement(s)")
            print(f"📚 {len(liste)} événement(s) :")
            for e in liste:
                quand = e['start'] if e['start'] else "journée"
                print(f"   {e['date']}  {quand:>8}  {e['titre'][:56]:<56}  {e['provenance']}")
        sys.exit(0)
    sys.exit(principale())

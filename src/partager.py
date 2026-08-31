"""
Partage des agendas avec les étudiants qui en font la demande.

Donner accès à un agenda Google, c'est poser une règle dans sa liste de
contrôle d'accès (ACL). Ce script le fait en lot, à partir d'un fichier de
demandes, plutôt qu'à la main dans l'interface — quatre agendas de cours,
quatre d'examens et une trentaine de personnes, cela fait vite deux cents clics.

    python src/partager.py --lister              qui a accès à quoi
    python src/partager.py --appliquer           applique le fichier de demandes
    python src/partager.py --ajouter a@b.c M1G2  une personne, tout de suite
    python src/partager.py --retirer a@b.c M1G2
    python src/partager.py --relancer a@b.c M1G2   renvoie l'invitation
    python src/partager.py --relancer-tous        à tous les abonnés

Le fichier de demandes est `donnees/inscriptions.txt`, une ligne par personne :

    # commentaire
    alice@gmail.com   M1G2
    bob@gmail.com     IRTL3

C'est le format que produit la page d'inscription, pour être collé tel quel.

Choisir une promotion partage DEUX agendas : les cours et les examens. Ils sont
séparés parce que c'est le seul moyen de leur donner des couleurs distinctes
chez les personnes abonnées, mais personne ne veut de l'un sans l'autre.

L'agenda des rendus Moodle figure parmi les choix. Il ne l'a pas toujours été :
il vient d'un export qui pouvait contenir les événements personnels de son
propriétaire. Deux garde-fous, dans `moodle.py`, ont réglé la question — le
paramètre `preset_what` est imposé par le code, et tout événement sans cours
rattaché est écarté. Sans eux, le partager exposerait ce que celui-ci noterait
un jour dans son propre calendrier.
"""

import re
import sys
from pathlib import Path

import chemins
import google_agenda
from telechargement import PROMOS, variable_env

for _flux in (sys.stdout, sys.stderr):
    try:
        _flux.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

FICHIER_DEMANDES = variable_env("EDT_INSCRIPTIONS",
                                str(chemins.donnee("inscriptions.txt")))

# Rôle donné : lecture seule. `reader` laisse voir les détails d'un cours ;
# `freeBusyReader` ne montrerait que « occupé », inutile pour un emploi du
# temps. Aucun rôle d'écriture n'est proposé : personne ne doit pouvoir
# modifier un agenda que le bot réécrit à chaque heure.
ROLE = "reader"

# Google prévient par courriel la personne avec qui on partage — « … a partagé
# un agenda avec vous », avec le bouton « Ajouter cet agenda ». C'est SUR CE
# COURRIEL que repose tout le tutoriel, et sans lui l'agenda n'apparaît nulle
# part : la personne attend un accès qu'elle a pourtant reçu.
#
# Ce paramètre était à False, par excès de discrétion. Personne ne recevait
# rien. Le mettre à 0 n'a de sens que pour se partager un agenda à soi-même.
NOTIFIER = variable_env("EDT_NOTIFIER", "1") != "0"

# Une adresse plausible suffit : Google refusera de toute façon ce qui n'est
# pas un compte valide. Le but est d'attraper les fautes de frappe évidentes
# avant d'envoyer huit requêtes pour rien.
REGEX_COURRIEL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def catalogue():
    """Les choix proposés : clé -> (intitulé, [(marqueur, nom d'agenda), ...]).

    Chaque choix porte l'agenda des cours ET celui des examens. La clé est ce
    que les gens écrivent dans leur demande ; elle est dérivée du suffixe de
    fichier, déjà unique par combinaison.
    """
    choix = {}
    for promo, config in PROMOS.items():
        for moitie, nom in config["agendas"].items():
            cle_courte = config["suffixes"][moitie].lstrip("_").upper()
            marque = config["cles"][moitie]
            choix[cle_courte] = (nom, [
                (marque, nom),
                (f"{marque}-EXAMENS", f"{nom} — Examens"),
            ])

    # Les rendus n'ont pas d'agenda d'examens jumeau : c'est un agenda unique,
    # et il ne dépend d'aucune promotion.
    choix["RENDU"] = (google_agenda.NOM_RENDUS,
                      [(google_agenda.CLE_RENDUS, google_agenda.NOM_RENDUS)])
    return choix


CATALOGUE = catalogue()


def lire_demandes(chemin=None):
    """Lit le fichier de demandes. Rend [(courriel, clé)], ou None si invalide.

    Une ligne fautive arrête tout au lieu d'être ignorée : un partage passé
    sous silence, c'est quelqu'un qui attend son emploi du temps sans savoir
    pourquoi il ne vient pas.
    """
    chemin = Path(chemin or FICHIER_DEMANDES)
    if not chemin.exists():
        print(f"❌ {chemin} introuvable.")
        print("   Une ligne par personne : « adresse@exemple.com   CLE »")
        print(f"   Clés possibles : {', '.join(sorted(CATALOGUE))}")
        return None

    demandes, fautes = [], []
    for numero, brute in enumerate(chemin.read_text(encoding="utf-8").splitlines(), 1):
        ligne = brute.split("#")[0].strip()
        if not ligne:
            continue
        # Les lignes arrivent d'un salon Discord, recopiées à la main : elles
        # gardent souvent une mise en forme (`code`, « - » de liste, chevrons
        # ajoutés autour d'une adresse). La refuser pour ça n'aiderait
        # personne — on l'enlève et on lit ce qui reste.
        ligne = ligne.strip("`").lstrip("-*• ").strip().strip("`")
        morceaux = ligne.replace(",", " ").replace(";", " ").split()
        morceaux = [m.strip("`<>\"'") for m in morceaux]
        if len(morceaux) != 2:
            fautes.append(f"ligne {numero} : « {brute.strip()} » — deux champs attendus")
            continue
        courriel, cle = morceaux[0].strip().lower(), morceaux[1].strip().upper()
        if not REGEX_COURRIEL.match(courriel):
            fautes.append(f"ligne {numero} : « {courriel} » n'est pas une adresse")
        elif cle not in CATALOGUE:
            fautes.append(f"ligne {numero} : clé « {cle} » inconnue "
                          f"({', '.join(sorted(CATALOGUE))})")
        else:
            demandes.append((courriel, cle))

    if fautes:
        print(f"❌ {len(fautes)} ligne(s) illisible(s) dans {chemin.name} :")
        for faute in fautes:
            print(f"   {faute}")
        return None
    return demandes


def _regles(service, agenda_id):
    """Les règles d'accès d'un agenda, indexées par adresse.

    Google inscrit l'agenda lui-même comme un « utilisateur », avec son propre
    identifiant en guise d'adresse. Ce n'est pas une personne : le laisser
    passer le ferait apparaître dans la liste des abonnés, et un --retirer
    maladroit détruirait la règle qui fait de l'agenda ce qu'il est.
    """
    regles = {}
    for regle in service.acl().list(calendarId=agenda_id).execute().get("items", []):
        portee = regle.get("scope", {})
        adresse = portee.get("value", "").lower()
        if portee.get("type") != "user" or adresse.endswith("@group.calendar.google.com"):
            continue
        regles[adresse] = regle
    return regles


def _agendas_du_choix(service, cle):
    """Les identifiants des agendas d'un choix. Ceux qui n'existent pas encore
    sont signalés et sautés — un agenda d'examens n'apparaît qu'au premier
    examen publié."""
    trouves = []
    for marque, nom in CATALOGUE[cle][1]:
        identifiant = google_agenda.trouver_agenda(service, marque, nom)
        if identifiant is None:
            print(f"   ⚠️ Agenda « {nom} » introuvable, ignoré.")
            continue
        trouves.append((identifiant, nom))
    return trouves


def partager(service, courriel, cle):
    """Donne l'accès en lecture. Rend le nombre d'agendas effectivement ajoutés."""
    ajoutes = 0
    for identifiant, nom in _agendas_du_choix(service, cle):
        existante = _regles(service, identifiant).get(courriel)
        if existante and existante.get("role") == ROLE:
            continue  # déjà en place : ne rien faire, ne rien annoncer
        service.acl().insert(
            calendarId=identifiant,
            body={"scope": {"type": "user", "value": courriel}, "role": ROLE},
            sendNotifications=NOTIFIER,
        ).execute()
        print(f"   ✅ {courriel} → « {nom} »"
              + ("" if NOTIFIER else "  (sans courriel)"))
        ajoutes += 1
    return ajoutes


def relancer(service, courriel, cle):
    """Renvoie l'invitation à quelqu'un qui a déjà l'accès.

    Google n'expédie le courriel qu'à la CRÉATION de la règle : il faut donc la
    retirer et la reposer. L'accès n'est pas interrompu de façon perceptible —
    les deux appels s'enchaînent — et la personne reçoit une invitation neuve.

    Sert à rattraper les partages faits quand les notifications étaient
    désactivées : ces personnes ont l'accès sans l'avoir jamais su.
    """
    renvoyes = 0
    for identifiant, nom in _agendas_du_choix(service, cle):
        regle = _regles(service, identifiant).get(courriel)
        if regle is None:
            print(f"   ⚠️ {courriel} n'a pas accès à « {nom} », rien à relancer.")
            continue
        if regle.get("role") == "owner":
            continue
        service.acl().delete(calendarId=identifiant, ruleId=regle["id"]).execute()
        service.acl().insert(
            calendarId=identifiant,
            body={"scope": {"type": "user", "value": courriel}, "role": ROLE},
            sendNotifications=True,
        ).execute()
        print(f"   📧 Invitation renvoyée à {courriel} pour « {nom} »")
        renvoyes += 1
    return renvoyes


def retirer(service, courriel, cle):
    """Retire l'accès. Rend le nombre d'agendas effectivement retirés."""
    retires = 0
    for identifiant, nom in _agendas_du_choix(service, cle):
        regle = _regles(service, identifiant).get(courriel)
        if regle is None:
            continue
        if regle.get("role") == "owner":
            print(f"   ⛔ {courriel} est propriétaire de « {nom} » : non retiré.")
            continue
        service.acl().delete(calendarId=identifiant, ruleId=regle["id"]).execute()
        print(f"   🗑️ {courriel} retiré de « {nom} »")
        retires += 1
    return retires


def lister(service):
    """Affiche, pour chaque agenda, les personnes qui y ont accès."""
    for cle in sorted(CATALOGUE):
        intitule = CATALOGUE[cle][0]
        print(f"\n{cle}  —  {intitule}")
        for identifiant, nom in _agendas_du_choix(service, cle):
            regles = _regles(service, identifiant)
            lecteurs = sorted(a for a, r in regles.items() if r.get("role") == ROLE)
            proprietaires = sorted(a for a, r in regles.items() if r.get("role") == "owner")
            print(f"   {nom}  —  {len(lecteurs)} abonné(s)")
            for adresse in lecteurs:
                print(f"      {adresse}")
            if not lecteurs:
                print("      (personne)")
            for adresse in proprietaires:
                print(f"      {adresse}  (propriétaire)")


def _service():
    """Le client Calendar, ou None si l'autorisation manque."""
    creds = google_agenda.obtenir_identifiants()
    if creds is None:
        return None
    from googleapiclient.discovery import build
    return build('calendar', 'v3', credentials=creds)


def principale():
    """Applique la commande demandée. Rend 0 si tout a abouti."""
    if len(sys.argv) < 2:
        print(__doc__.strip())
        print(f"\nClés possibles : {', '.join(sorted(CATALOGUE))}")
        return 1

    service = _service()
    if service is None:
        return 1

    if "--lister" in sys.argv:
        lister(service)
        return 0

    if "--relancer-tous" in sys.argv:
        total = 0
        for cle in sorted(CATALOGUE):
            for identifiant, _ in _agendas_du_choix(service, cle):
                for adresse, regle in _regles(service, identifiant).items():
                    if regle.get("role") == ROLE:
                        total += relancer(service, adresse, cle)
                break  # les deux agendas d'un choix ont les mêmes abonnés
        print(f"\n📧 {total} invitation(s) renvoyée(s).")
        return 0

    for action, fonction in (("--ajouter", partager), ("--retirer", retirer),
                             ("--relancer", relancer)):
        if action in sys.argv:
            i = sys.argv.index(action)
            if i + 2 >= len(sys.argv):
                print(f"❌ Usage : {action} adresse@exemple.com CLE")
                return 1
            courriel, cle = sys.argv[i + 1].lower(), sys.argv[i + 2].upper()
            if cle not in CATALOGUE:
                print(f"❌ Clé « {cle} » inconnue. "
                      f"Valeurs : {', '.join(sorted(CATALOGUE))}")
                return 1
            fonction(service, courriel, cle)
            return 0

    if "--appliquer" not in sys.argv:
        print("❌ Commande inconnue. Voir --lister, --appliquer, --ajouter, --retirer.")
        return 1

    demandes = lire_demandes()
    if demandes is None:
        return 1
    if not demandes:
        print("Aucune demande dans le fichier.")
        return 0

    print(f"📋 {len(demandes)} demande(s).")
    total = 0
    for courriel, cle in demandes:
        total += partager(service, courriel, cle)
    if total:
        print(f"\n✅ {total} accès ajouté(s).")
    else:
        print("\n✅ Rien à faire : tous les accès demandés existaient déjà.")
    return 0


if __name__ == "__main__":
    sys.exit(principale())

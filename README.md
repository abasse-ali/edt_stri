# EDT STRI — synchronisation automatique des emplois du temps

Bot qui lit les emplois du temps publiés en PDF par le département STRI
(Université Toulouse III) et les recopie dans **Google Agenda**, toutes les
heures, sans intervention.

Chaque étudiant s'abonne une fois à l'agenda qui le concerne : les cours
apparaissent, se déplacent et disparaissent tout seuls quand le PDF change,
sur ordinateur comme sur téléphone (Google Agenda, Apple Calendrier, Outlook).

> Tu es étudiant et tu veux juste ton emploi du temps sur ton téléphone ?
> Va directement dans **[TUTO.txt](TUTO.txt)**, écrit pour ça. Ce README
> s'adresse à qui veut comprendre ou modifier le code.

---

## Ce que fait le bot

```
   PDF publié par le STRI
            │
            │  telechargement.py     Playwright, pour passer le pare-feu du site
            ▼
   edt_l3.pdf / edt_m1.pdf
            │
            │  lecture_pdf.py        couche vectorielle : bordures, fonds colorés,
            │                        libellés d'heure, titres, salles, professeurs
            ▼
   cellules (une case = un cours)
            │
            │  edt_stri.py           filtrage par demi-promo, horaires, comparaison
            ▼                        avec la passe précédente
   edt_data_*.json + edt_*.ics
            │
            │  google_agenda.py      API Calendar v3 : ajouts, modifications, retraits
            ▼
   4 agendas Google  ─────────────►  notification Discord des changements
```

Il n'y a **aucune IA** dans la chaîne : tout est lu dans la géométrie du PDF.
Une version antérieure interrogeait un modèle de langage ; elle produisait des
horaires inventés et a été retirée (voir [HISTORIQUE.md](HISTORIQUE.md)).

---

## Deux promotions, quatre agendas

Un même PDF sert **deux demi-promotions** : les cours communs occupent toute la
hauteur d'un créneau, les cours propres à un groupe n'en occupent que la moitié
(haute ou basse). Le bot est donc lancé quatre fois, une par combinaison.

| `EDT_PROMO` | `EDT_MOITIE` | Agenda Google | Fichiers produits |
|---|---|---|---|
| `M1` | `BAS`  | STRI M1 G2    | `edt_data_m1g2.json`, `edt_m1g2.ics` |
| `M1` | `HAUT` | STRI Ingé2 G1 | `edt_data_inge2g1.json`, `edt_inge2g1.ics` |
| `L3` | `BAS`  | IRT L3        | `edt_data_irtl3.json`, `edt_irtl3.ics` |
| `L3` | `HAUT` | STRI Ingé1    | `edt_data_inge1.json`, `edt_inge1.ics` |

La table complète (URL du PDF, noms d'agenda, couleurs) est dans `PROMOS`, en
tête de [telechargement.py](telechargement.py) : c'est le seul endroit à
modifier pour ajouter une promotion.

### Le code couleur du PDF

Les couleurs de fond ne sont pas décoratives, elles portent l'information :

| Couleur | Signification | Effet |
|---|---|---|
| 🟩 vert vif | case contenant le nom d'une salle | le texte devient le lieu, pas le titre |
| 🟨 jaune | examen | titre préfixé `[EXAMEN]`, événement rouge |
| 🟧 orange | cours réservé aux **Ingé** | retenu uniquement en moitié `HAUT` |
| 🫒 vert olive | cours réservé aux **IRT L3** | retenu uniquement en moitié `BAS` |
| ⬜ blanc | cours ordinaire | position dans la case (haut / bas / pleine hauteur) |
| ⬛ gris | journée en entreprise (alternants) | ignoré |

L'orange a longtemps été pris pour « cours annulé » : 22 cours manquaient dans
les agendas Ingé. C'est le genre de règle qu'on ne devine pas — d'où
`verif_edt.py`, qui les revérifie toutes à chaque exécution.

---

## Les fichiers

### Le code

| Fichier | Rôle |
|---|---|
| [edt_stri.py](edt_stri.py) | Chaîne complète pour **une** combinaison promo × demi-promo |
| [lecture_pdf.py](lecture_pdf.py) | Lecture du PDF : cellules, couleurs, horaires, textes |
| [telechargement.py](telechargement.py) | Table `PROMOS` + téléchargement derrière le pare-feu |
| [google_agenda.py](google_agenda.py) | Écriture dans Google Agenda (API Calendar v3) |
| [test_local.py](test_local.py) | Lanceur local : reproduit les 4 passes de la CI |
| [verif_edt.py](verif_edt.py) | Vérifie le résultat **réel** du jour (102 contrôles) |
| [test_edt.py](test_edt.py) | Vérifie la **logique** du code (47 tests, sans réseau) |
| [alerte_ci.py](alerte_ci.py) | Prévient sur Discord quand la CI échoue |

### Les données

| Fichier | Rôle |
|---|---|
| `professeurs.txt` | Initiales → nom complet des enseignants |
| `edt_m1.pdf`, `edt_l3.pdf` | Derniers PDF publiés, commités pour détecter les changements |
| `edt_data_*.json` | État précédent de chaque agenda, base de la comparaison |
| `edt_*.ics` | Export standard, pour qui préfère un abonnement à un fichier |
| `journal.csv` | Une ligne par exécution : nombre de cours, état |
| `credentials.json`, `token.json` | Identifiants Google (**jamais commités**) |

### Le reste

| Fichier | Rôle |
|---|---|
| [TUTO.txt](TUTO.txt) | Mode d'emploi pour les étudiants qui s'abonnent |
| [HISTORIQUE.md](HISTORIQUE.md) | Journal des bugs rencontrés et de leurs corrections |
| `.github/workflows/edt_sync.yml` | Exécution horaire sur GitHub Actions |
| `export_cours/<promo>/` | Images de debug (générées si `EDT_DEBUG=1`) |

---

## Installation

```bash
git clone <ce dépôt> && cd edt_stri

python -m venv venv
.\venv\Scripts\Activate.ps1          # Windows
# source venv/bin/activate           # Linux / macOS

pip install -r requirements.txt
playwright install chromium          # le site du STRI filtre les robots
```

**Poppler** n'est nécessaire que pour les images de debug. Il est détecté
automatiquement s'il est dans le `PATH` ou dans `./poppler` ; sinon, indiquer
son chemin dans `POPPLER_PATH`.

**Accès Google.** Créer un projet sur Google Cloud, activer l'API Calendar,
télécharger les identifiants OAuth sous le nom `credentials.json`, puis lancer
une première fois en local : le navigateur s'ouvre et `token.json` est écrit.
C'est ce fichier, encodé en base64, qui alimente le secret `GDRIVE_TOKEN` de la
CI. Le jeton doit couvrir le scope `https://www.googleapis.com/auth/calendar`.

---

## Utilisation

```bash
# Tout faire, comme la CI : 2 téléchargements puis 4 passes
python test_local.py

# Variantes utiles
python test_local.py --no-download        # retraiter les PDF déjà présents
python test_local.py --promo L3           # une seule promotion
python test_local.py --moitie HAUT        # une seule demi-promo
python test_local.py --no-debug           # sans les images de debug

# Une passe précise, sans le lanceur
EDT_PROMO=L3 EDT_MOITIE=HAUT python edt_stri.py

# Télécharger seulement
python telechargement.py --promo L3

# Contrôler ce qui a été produit
python verif_edt.py                       # tout, agendas Google compris
python verif_edt.py --hors-ligne          # sans réseau (83 contrôles)
python verif_edt.py --promo M1
python test_edt.py                        # tests de logique, instantané
```

⚠️ `python edt_stri.py` seul ne traite **que le M1 en moitié basse**. La
configuration est lue au chargement du module, donc une exécution ne couvre
qu'une combinaison : c'est pour cela que la CI lance quatre processus.

⚠️ `--no-download` republie les PDF déjà présents. Si le STRI a publié une
nouvelle version entre-temps, cela écrase les agendas avec des données
périmées. À réserver au débogage.

---

## Variables d'environnement

Toutes facultatives ; elles se placent dans un fichier `.env` en local (chargé
automatiquement) ou dans les secrets et variables du dépôt en CI.

| Variable | Défaut | Rôle |
|---|---|---|
| `EDT_PROMO` | `M1` | Promotion traitée : `M1` ou `L3` |
| `EDT_MOITIE` | `BAS` | Demi-promo traitée : `BAS` ou `HAUT` |
| `DISCORD_WEBHOOK_URL` | — | Sans elle, aucune notification n'est envoyée |
| `GOOGLE_CALENDAR_ID` | — | Force un agenda précis au lieu de le chercher par son nom |
| `EDT_PDF` | selon la promo | Autre fichier PDF en entrée |
| `EDT_PDF_URL` | selon la promo | Autre URL de téléchargement |
| `EDT_DEBUG` | `0` | `1` : exporte une image annotée par journée |
| `POPPLER_PATH` | détecté | Dossier `bin` de Poppler |
| `EDT_COULEUR` | selon la promo | Couleur de fond de l'agenda |
| `EDT_COULEUR_COURS` | selon la promo | Couleur des événements (visible par les abonnés) |
| `EDT_COULEUR_EXAMENS` | `tomate` | Couleur des examens |
| `EDT_CHUTE_MAX` | `40` | % de cours perdus au-delà duquel on refuse de publier |
| `EDT_FORCER` | `0` | `1` : publie malgré le garde-fou ci-dessus |
| `EDT_JOURNAL` | `journal.csv` | Fichier de journal |
| `EDT_AGENDA_PUBLIC` | `0` | `1` : rend l'agenda accessible par lien |
| `EDT_AUTORISER` | `0` | `1` : autorise l'ouverture d'un navigateur pour l'OAuth |

Les valeurs vides sont traitées comme absentes : dans un workflow GitHub, une
variable non définie est quand même transmise comme chaîne vide, et sans cette
règle elle écraserait le défaut.

---

## Les garde-fous

Le bot écrit dans des agendas que d'autres personnes consultent : mieux vaut
qu'il ne publie rien plutôt qu'une bêtise. Quatre barrières, chacune signalée
sur Discord :

1. **Extraction incomplète** — des mots du PDF ne sont rattachés à aucune
   cellule : la lecture a raté quelque chose, on ne publie pas.
2. **Effondrement** — le nombre de cours chute de plus de `EDT_CHUTE_MAX` %
   par rapport à la passe précédente : on garde l'ancien état.
3. **Synchronisation impossible** — jeton expiré, quota, réseau : `edt_stri.py`
   rend le code 1. La CI ne commite alors pas les PDF, donc la comparaison de
   l'heure suivante détecte à nouveau le changement et **réessaie**. Sans cela,
   un échec unique figeait les agendas jusqu'au PDF suivant.
4. **`verif_edt.py`** relit après coup tout ce qui a été produit. La CI ne
   sauvegarde les fichiers que si ce contrôle passe.

Ces vérifications portent sur les données du jour. Les règles elles-mêmes
(routage des couleurs, horaires, identifiants, format ICS) sont couvertes par
`test_edt.py`, qui tourne **avant** la synchronisation en CI et ne demande ni
réseau ni PDF.

---

## Fonctionnement en production

Le workflow [edt_sync.yml](.github/workflows/edt_sync.yml) tourne **toutes les
heures** :

1. télécharge les deux PDF ;
2. les compare aux versions commitées — s'ils sont identiques, tout s'arrête là ;
3. lance `test_edt.py` ;
4. reconstruit le jeton Google depuis le secret `GDRIVE_TOKEN` ;
5. exécute les quatre passes ;
6. lance `verif_edt.py --sans-fraicheur` ;
7. efface le jeton, puis commite PDF, JSON, ICS et journal sur `main`.

Toute étape en échec déclenche `alerte_ci.py` et un message Discord.

**Secrets à définir** dans le dépôt : `GDRIVE_TOKEN` (le `token.json` encodé en
base64) et `DISCORD_WEBHOOK_URL`.

⚠️ GitHub désactive les workflows planifiés après **60 jours sans activité** sur
le dépôt. Le bot commitant à chaque changement de PDF, le cas ne se présente
qu'en période creuse — à surveiller à la rentrée.

---

## Partager un agenda

Depuis Google Agenda, « Partager avec des personnes en particulier », puis
l'adresse Gmail de l'étudiant, en accès **lecture seule**. L'agenda apparaît
alors dans son compte, et sur son iPhone après activation sur
[calendar.google.com/calendar/syncselect](https://calendar.google.com/calendar/syncselect).
La marche à suivre côté étudiant est détaillée dans [TUTO.txt](TUTO.txt).

À noter : la **couleur de fond** d'un agenda appartient à chaque abonné et ne
peut pas lui être imposée. C'est pourquoi le bot pose aussi une couleur sur
chaque événement — celle-là, stockée sur l'événement, est la même pour tout le
monde.

---

## Pour aller plus loin

Les décisions techniques non évidentes sont expliquées en commentaire à
l'endroit où elles s'appliquent : pourquoi les bordures verticales ne peuvent
pas servir à délimiter une journée, pourquoi une case de salle se teste sur son
centre et non sur ses bords, pourquoi les identifiants d'événement sont des MD5,
pourquoi l'ICS est écrit avec `newline=''`.

[HISTORIQUE.md](HISTORIQUE.md) retrace tout ce qui a cassé, et comment.

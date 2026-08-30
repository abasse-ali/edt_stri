# EDT STRI — synchronisation automatique des emplois du temps

Bot qui recopie dans **Google Agenda**, toutes les heures et sans
intervention, les deux calendriers du département STRI (Université Toulouse III) :

- les **cours**, publiés en PDF sur [stri.fr](https://stri.fr/) ;
- les **rendus**, saisis dans le calendrier Moodle du site eFormation — devoirs
  à déposer, dates limites, examens déclarés dans un cours.

Chaque étudiant s'abonne une fois à l'agenda qui le concerne : les cours
apparaissent, se déplacent et disparaissent tout seuls quand le PDF change,
sur ordinateur comme sur téléphone (Google Agenda, Apple Calendrier, Outlook).

> Tu es étudiant et tu veux juste ton emploi du temps sur ton téléphone ?
> Va directement dans **[docs/TUTO.txt](docs/TUTO.txt)**, écrit pour ça. Ce README
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


   Calendriers Moodle (STRI + inetdoc, export iCalendar)
            │
            │  moodle.py             dépliage, UTC -> Paris, échéances vs séances
            ▼
   échéances
            │
            │  rendus.py             comparaison avec la passe précédente
            ▼
   rendus_data.json
            │
            │  google_agenda.py      le même rapprochement que pour les cours
            ▼
   agenda « Rendu M1 »  ───────────►  notification Discord des changements
```

Les deux chaînes sont **séparées** : le PDF et Moodle changent à des moments
différents, et une panne de l'un ne doit pas empêcher l'autre de se mettre à
jour.

Il n'y a **aucune IA** dans la chaîne : tout est lu dans la géométrie du PDF.
Une version antérieure interrogeait un modèle de langage ; elle produisait des
horaires inventés et a été retirée (voir [docs/HISTORIQUE.md](docs/HISTORIQUE.md)).

---

## Structure du dépôt

```
edt_stri/
├── src/          le code, et les données qu'il embarque
│   ├── chemins.py          où vivent les fichiers — la seule source de vérité
│   ├── telechargement.py   table PROMOS + téléchargement anti-bot
│   ├── lecture_pdf.py      lecture de la couche vectorielle du PDF
│   ├── edt_stri.py         chaîne des cours, une combinaison à la fois
│   ├── moodle.py           lecture du calendrier Moodle
│   ├── rendus.py           chaîne des rendus
│   ├── google_agenda.py    écriture dans Google Agenda
│   ├── alerte_ci.py        alerte Discord quand la CI casse
│   └── professeurs.txt     initiales → nom complet
│
├── tests/
│   ├── test_edt.py         la LOGIQUE, sans réseau ni PDF
│   └── verif_edt.py        les DONNÉES du jour, PDF et agendas compris
│
├── donnees/      tout ce qui est régénéré, jamais écrit à la main
│   ├── edt_m1.pdf  edt_l3.pdf
│   ├── edt_data_*.json  rendus_data.json
│   ├── edt_*.ics           (non versionnés)
│   ├── journal.csv
│   └── export_cours/       images de debug (non versionnées)
│
├── docs/         TUTO.txt, HISTORIQUE.md
├── test_local.py           le lanceur, à la racine car c'est le point d'entrée
├── requirements.txt
└── .github/workflows/
```

Aucun module ne construit un chemin lui-même : ils passent tous par
[src/chemins.py](src/chemins.py). C'est ce qui permet de lancer `python
tests/verif_edt.py` depuis n'importe quel répertoire et de trouver quand même
les bons fichiers — avant, tout se résolvait par rapport au répertoire courant
et ne marchait que depuis la racine.

`token.json` et `credentials.json` restent à la racine : ils ne sont pas
versionnés, et la CI écrit le jeton avant de connaître la disposition du dépôt.

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
tête de [src/telechargement.py](src/telechargement.py) : c'est le seul endroit à
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

## Les rendus Moodle

Le PDF ne dit rien des devoirs à rendre, des validations de TP ni des quiz :
ceux-là vivent dans les calendriers Moodle. Il y en a **deux**, décrits dans
`SOURCES` en tête de [src/moodle.py](src/moodle.py) :

| Source | Variable | Périmètre imposé | Ce qui est retenu |
|---|---|---|---|
| eFormation STRI | `MOODLE_ICS_URL` | `all` | tout ce qui relève d'un cours |
| Moodle inetdoc | `MOODLE_INETDOC_ICS_URL` | `courses` | les échéances seulement |

Le STRI reste sur `all` faute de mieux : mesuré, `courses` y rend **zéro**
événement — le devoir n'y est pas rattaché à un cours où l'on est inscrit.

Le calendrier d'inetdoc mélange 20 échéances et 49 **séances** — TP, cours,
examens. Ces séances sont déjà dans les agendas de l'emploi du temps, elles y
feraient doublon, et la moitié concerne l'autre demi-promo (« - G1 »). Seuls
les événements **sans durée** — une date limite, l'ouverture ou la fermeture
d'un quiz — sont donc retenus de cette source.

Tout atterrit dans un **seul** agenda : un endroit unique où regarder ce qu'il
reste à rendre. La contrepartie est que la lecture est **tout ou rien** — une
source illisible interrompt la publication, faute de quoi le rapprochement
effacerait les échéances de l'autre.

Le paramètre `preset_what` est **imposé par le code**, quelle que soit la case
cochée le jour où l'adresse a été copiée : c'est un réglage de confidentialité,
il n'a pas à dépendre d'un clic. Et tout événement **sans cours rattaché** est
écarté — Moodle range ainsi les rendez-vous privés. Ces deux garde-fous sont ce
qui rend l'agenda « Rendu M1 » partageable sans risque.

Chaque adresse d'export se récupère **une seule fois** :

1. ouvrir <https://www.stri.fr/eformation/calendar/view.php>
   (ou <https://moodle.inetdoc.net/calendar/view.php>) ;
2. **Exporter le calendrier** ;
3. Événements à exporter : **Tous les événements** —
   Durée : **Intervalle personnalisé** (la fenêtre la plus large, ~1 an) ;
4. bouton **URL du calendrier**, et non « Exporter » qui télécharge un fichier
   figé ;
5. coller l'adresse obtenue dans la variable de la source (`.env` en local,
   secret du dépôt en CI).

⚠️ Cette adresse contient `authtoken=…`, qui donne accès au calendrier
personnel **sans mot de passe**. Elle se traite comme un mot de passe : jamais
dans le code, jamais dans un commit. Le bot ne l'affiche jamais en entier, même
dans un message d'erreur.

Pour voir ce que contient l'export avant de publier quoi que ce soit :

```bash
python src/rendus.py --lister
```

La sortie liste les cours Moodle et le nombre d'événements de chacun. Si le
calendrier en mélange plusieurs et qu'un seul intéresse, `MOODLE_FILTRE` est une
expression régulière testée sur l'intitulé, le cours et la description :

```bash
MOODLE_FILTRE='Rendu M1' python src/rendus.py
```

Chaque rendu porte un **rappel 5 h avant l'échéance** : une notification
poussée par le téléphone, comme n'importe quel rappel d'agenda. C'est Google
qui la déclenche, pas le bot — elle part même si la CI est en panne, et il n'y
a rien à retenir d'une exécution à l'autre. Le délai se règle avec
`MOODLE_RAPPEL_MINUTES` (`0` pour n'en poser aucun).

Un rappel appartient au propriétaire de l'agenda : Google le range dans la part
privée de l'événement. Les personnes avec qui l'agenda serait partagé gardent
donc les leurs — comme pour la couleur de fond, aucune API ne permet de leur en
imposer un. Les quatre agendas de cours n'en posent aucun, et ne sont pas
touchés.

Trois détails que Moodle impose et que le bot corrige au passage :

- les heures sont exportées en **UTC** — une échéance à 22h00 s'écrit
  `200000Z`, la lire telle quelle la daterait de deux heures trop tôt ;
- les titres gardent parfois des **entités HTML** : inetdoc publie
  `Hub &amp\; Spoke`, où le `\;` est en plus un échappement iCalendar ;
- une date limite a une **durée nulle**. Contrairement à ce qu'on pourrait
  croire, l'API Google l'accepte — vérifié — et c'est la représentation juste :
  le rappel s'ancre alors sur l'échéance elle-même. `MOODLE_DUREE_ECHEANCE`
  permet de lui donner une épaisseur visible dans la grille, auquel cas
  l'événement se **termine** à l'heure limite.

---

## Les fichiers

### Le code

| Fichier | Rôle |
|---|---|
| [src/edt_stri.py](src/edt_stri.py) | Chaîne complète pour **une** combinaison promo × demi-promo |
| [src/lecture_pdf.py](src/lecture_pdf.py) | Lecture du PDF : cellules, couleurs, horaires, textes |
| [src/chemins.py](src/chemins.py) | Emplacement de chaque fichier, calculé une seule fois |
| [src/telechargement.py](src/telechargement.py) | Table `PROMOS` + téléchargement derrière le pare-feu |
| [src/moodle.py](src/moodle.py) | Lecture du calendrier Moodle exporté en iCalendar |
| [src/rendus.py](src/rendus.py) | Chaîne complète des rendus : Moodle → agenda « Rendu M1 » |
| [src/partager.py](src/partager.py) | Donne accès aux agendas, en lot, depuis un fichier de demandes |
| [src/bot_discord.py](src/bot_discord.py) | Formulaire `/edt` dans Discord et validation des demandes |
| [src/google_agenda.py](src/google_agenda.py) | Écriture dans Google Agenda (API Calendar v3) |
| [test_local.py](test_local.py) | Lanceur local : reproduit les 4 passes de la CI |
| [tests/verif_edt.py](tests/verif_edt.py) | Vérifie le résultat **réel** du jour (une centaine de contrôles) |
| [tests/test_edt.py](tests/test_edt.py) | Vérifie la **logique** du code (104 tests, sans réseau) |
| [src/alerte_ci.py](src/alerte_ci.py) | Prévient sur Discord quand la CI échoue |

### Les données

| Fichier | Rôle |
|---|---|
| `src/professeurs.txt` | Initiales → nom complet des enseignants |
| `donnees/edt_m1.pdf`, `donnees/edt_l3.pdf` | Derniers PDF publiés, commités pour détecter les changements |
| `donnees/edt_data_*.json` | État précédent de chaque agenda, base de la comparaison |
| `donnees/rendus_data.json` | État précédent des rendus Moodle, même rôle |
| `donnees/edt_*.ics` | Export standard, non versionné |
| `donnees/journal.csv` | Une ligne par exécution : nombre de cours, état |
| `donnees/inscriptions.txt` | Demandes de partage. **Non versionné** : adresses de tiers |
| `donnees/demandes_discord.json` | Demandes en attente de validation. **Non versionné** |
| `credentials.json`, `token.json` | Identifiants Google (**jamais commités**) |

### Le reste

| Fichier | Rôle |
|---|---|
| [docs/TUTO.txt](docs/TUTO.txt) | Mode d'emploi pour les étudiants qui s'abonnent |
| [docs/HISTORIQUE.md](docs/HISTORIQUE.md) | Journal des bugs rencontrés et de leurs corrections |
| `.github/workflows/edt_sync.yml` | Exécution horaire des emplois du temps |
| `.github/workflows/rendus_sync.yml` | Exécution horaire des rendus Moodle |
| [docs/DEPLOIEMENT.md](docs/DEPLOIEMENT.md) | Faire tourner le bot Discord en permanence |
| `Dockerfile`, `compose.yaml`, `deploiement/` | Déploiement du bot : Docker, systemd, tâche Windows |
| `donnees/export_cours/<promo>/` | Images de debug (générées si `EDT_DEBUG=1`) |

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
EDT_PROMO=L3 EDT_MOITIE=HAUT python src/edt_stri.py

# Télécharger seulement
python src/telechargement.py --promo L3

# Rendus Moodle
python src/rendus.py                      # récupérer, comparer, publier
python src/rendus.py --lister             # voir la source sans rien publier
python src/moodle.py                      # idem, sans passer par l'agenda

# Contrôler ce qui a été produit
python tests/verif_edt.py                 # tout, agendas Google compris
python tests/verif_edt.py --hors-ligne    # sans réseau ni agenda
python tests/verif_edt.py --promo M1
python tests/test_edt.py                  # tests de logique, instantané
```

Ces commandes marchent depuis n'importe quel répertoire, pas seulement depuis
la racine.

⚠️ `python src/edt_stri.py` seul ne traite **que le M1 en moitié basse**. La
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

Propres aux rendus Moodle :

| Variable | Défaut | Rôle |
|---|---|---|
| `MOODLE_ICS_URL` | — | Adresse d'export du Moodle du STRI. **Secret.** |
| `MOODLE_INETDOC_ICS_URL` | — | Adresse d'export du Moodle inetdoc. **Secret.** Sans aucune des deux, `rendus.py` ne fait rien |
| `MOODLE_FILTRE` | — | Expression régulière : ne garder que les événements correspondants |
| `MOODLE_AGENDA` | `Rendu M1` | Nom de l'agenda Google des rendus |
| `MOODLE_COULEUR` | `mangue` | Couleur de fond de cet agenda |
| `MOODLE_COULEUR_EVENEMENTS` | `mandarine` | Couleur de ses événements |
| `MOODLE_JSON` | `rendus_data.json` | État précédent des rendus |
| `MOODLE_CHUTE_MAX` | `50` | % de rendus perdus au-delà duquel on refuse de publier |
| `MOODLE_RAPPEL_MINUTES` | `300` | Notification poussée tant de minutes avant l'échéance ; `0` pour aucune |
| `MOODLE_DUREE_ECHEANCE` | `0` | Épaisseur donnée à une date limite, en minutes ; `0` la laisse ponctuelle |
| `EDT_INSCRIPTIONS` | `donnees/inscriptions.txt` | Fichier des demandes de partage |
| `DISCORD_DEMANDES` | `donnees/demandes_discord.json` | Demandes en attente de validation |

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
4. **`tests/verif_edt.py`** relit après coup tout ce qui a été produit. La CI ne
   sauvegarde les fichiers que si ce contrôle passe.

Un cinquième cas ne concerne que les rendus : l'export Moodle porte sur une
**fenêtre glissante**, choisie au moment où l'adresse a été créée. Si elle ne
remonte pas assez loin, une réconciliation naïve effacerait les échéances
passées à chaque exécution puis les annoncerait comme des annulations. Les
suppressions sont donc bornées à aujourd'hui : le passé n'est jamais touché.

Ces vérifications portent sur les données du jour. Les règles elles-mêmes
(routage des couleurs, horaires, identifiants, format ICS) sont couvertes par
`tests/test_edt.py`, qui tourne **avant** la synchronisation en CI et ne demande ni
réseau ni PDF.

---

## Fonctionnement en production

Deux workflows tournent **toutes les heures**, dans le même groupe de
concurrence pour ne jamais pousser deux commits en même temps.

[edt_sync.yml](.github/workflows/edt_sync.yml) — les cours :

1. télécharge les deux PDF ;
2. les compare aux versions commitées — s'ils sont identiques, tout s'arrête là ;
3. lance `tests/test_edt.py` ;
4. reconstruit le jeton Google depuis le secret `GDRIVE_TOKEN` ;
5. exécute les quatre passes ;
6. lance `tests/verif_edt.py --sans-fraicheur` ;
7. efface le jeton, puis commite `donnees/` (PDF, JSON, journal) sur `main`.

[rendus_sync.yml](.github/workflows/rendus_sync.yml) — les rendus :

1. s'arrête tout de suite si aucune adresse Moodle n'est définie ;
2. installe, teste, prépare le jeton comme ci-dessus ;
3. exécute `src/rendus.py` ;
4. efface le jeton, puis commite `donnees/rendus_data.json` et le journal.

Il ne dépend pas des PDF : le calendrier Moodle change quand il veut, et le
workflow de l'EDT s'arrête dès que les PDF sont inchangés — y greffer les rendus
les aurait figés la plupart du temps.

Toute étape en échec déclenche `alerte_ci.py` et un message Discord.

**Secrets à définir** dans le dépôt : `GDRIVE_TOKEN` (le `token.json` encodé en
base64), `DISCORD_WEBHOOK_URL`, et `MOODLE_ICS_URL` /
`MOODLE_INETDOC_ICS_URL` pour les rendus.

⚠️ GitHub désactive les workflows planifiés après **60 jours sans activité** sur
le dépôt. Le bot commitant à chaque changement de PDF, le cas ne se présente
qu'en période creuse — à surveiller à la rentrée.

---

## Partager un agenda

Chaque étudiant choisit sa promotion et donne son adresse Google sur une **page
d'inscription** publiée dans le salon Discord. Elle ne transmet rien : elle
fabrique une ligne à coller dans le salon, au format exact que lit
`partager.py` — `adresse@exemple.com   M1G2`.

### Le formulaire Discord

C'est la voie normale. Le salon porte un **panneau permanent** à deux boutons,
posé une fois par `/edt-panneau`. Un clic ouvre, **visible de la seule
personne qui a cliqué**, une liste où elle coche les agendas voulus — plusieurs
à la fois — puis une fenêtre pour son adresse. Rien n'est écrit dans le
salon : il ne s'encombre pas, et personne ne lit l'adresse d'un autre.

La demande t'arrive **en message privé**, sous forme de fiche : mention,
nom affiché, pseudo Discord, **rôles sur le serveur**, adresse et agendas
demandés. Les rôles disent souvent la promo — ils permettent de vérifier d'un
coup d'œil qu'une demande est cohérente. Tu peux y
**corriger les agendas demandés** — retirer celui auquel la personne n'a pas
droit, ajouter le bon — puis cliquer sur **Valider**. Le partage Google est
appliqué au clic, et la personne reçoit un message privé à son tour. La fiche
garde une ligne « Demandé à l'origine » quand tu as changé quelque chose.

Une fois tranchée, la fiche porte un bouton **Supprimer**. Discord n'autorise
personne à effacer le message d'un autre — même dans un message privé, seul
l'auteur le peut : c'est donc au bot de le faire, à ta demande. `/edt-menage`
les efface toutes d'un coup, en épargnant celles qui attendent encore une
décision.

Rien ne transite donc par un salon : ni l'adresse de la personne, ni ta
décision. Un salon de repli reste possible pour le cas où Discord refuserait le
message privé — beaucoup de comptes bloquent ceux venant d'un serveur. Le bot
vérifie au démarrage qu'une fiche pourra bien être remise, et le dit.

```bash
pip install -r requirements-bot.txt
python src/bot_discord.py
```

⚠️ Ce script est le seul du projet qui ne peut pas tourner par tâche planifiée.
Discord n'envoie une commande ou un clic qu'à un programme **déjà connecté** :
il faut une machine allumée en permanence. GitHub Actions ne convient pas — ni
techniquement (six heures maximum par job), ni contractuellement, ses
conditions d'utilisation interdisant tout usage étranger à la construction du
projet.

**[docs/DEPLOIEMENT.md](docs/DEPLOIEMENT.md)** détaille les voies possibles —
panneau gratuit type Katabump ou FridayDev, hébergeur en ligne, Raspberry Pi,
PC Windows — avec les fichiers prêts à l'emploi : `Dockerfile`, `compose.yaml`, un service systemd et un script de
tâche planifiée. Le bot n'installe que `requirements-bot.txt` : ni OpenCV, ni
NumPy, ni pdfplumber, puisqu'il ne lit aucun PDF.

| Variable | Rôle |
|---|---|
| `DISCORD_BOT_TOKEN` | Jeton du bot (Developer Portal → Bot → Reset Token) |
| `DISCORD_ADMINS` | Identifiants autorisés à valider. Le **premier** reçoit les fiches |
| `DISCORD_VALIDEUR` | Facultatif : envoyer les fiches à quelqu'un d'autre que le premier |
| `DISCORD_SALON_DEMANDES` | Facultatif : salon de repli si le message privé est refusé |
| `DISCORD_SERVEUR` | Facultatif : les commandes apparaissent aussitôt sur ce serveur |

| Bouton du panneau | Effet |
|---|---|
| 📅 Recevoir mon emploi du temps | La liste des agendas, puis l'adresse |
| 📖 Comment l'installer | Le tutoriel écran par écran, selon l'appareil |

Le tutoriel reprend [docs/TUTO.txt](docs/TUTO.txt) en six parcours — iPhone,
Android, ordinateur, « je n'ai pas d'adresse Google », dépannage, questions
fréquentes — avec des flèches Précédent/Suivant. Lui aussi est éphémère :
chacun avance à son rythme sans que le salon en garde trace.

| Commande | Qui | Effet |
|---|---|---|
| `/edt-panneau` | toi | Pose le panneau d'inscription dans le salon courant |
| `/edt` | tout le monde | Ouvre la même liste, pour qui ne retrouve pas le panneau |
| `/edt-liste` | toi | Les abonnés de chaque agenda, en message éphémère |
| `/edt-menage` | toi | Efface les fiches déjà traitées de ton message privé |

### En ligne de commande

Sans le bot, ou pour reprendre un lot d'anciennes demandes, les lignes vont
dans `donnees/inscriptions.txt`, puis :

```bash
python src/partager.py --lister              # qui a accès à quoi
python src/partager.py --appliquer           # tout le fichier d'un coup
python src/partager.py --ajouter a@b.c M1G2  # une personne, tout de suite
python src/partager.py --retirer a@b.c M1G2
```

« Rendu M1 » est un agenda unique, sans jumeau et indépendant des promotions.
Choisir une promotion partage en revanche **deux** agendas, les cours et les
examens :
ils sont séparés pour avoir des couleurs distinctes, mais personne ne veut de
l'un sans l'autre. Le rôle donné est toujours `reader` — un agenda que le bot
réécrit chaque heure ne doit être modifiable par personne.

Le fichier d'inscriptions n'est **pas versionné** : ce sont des adresses de
tiers. Et l'agenda des rendus Moodle n'est délibérément **pas** partageable —
il vient d'un export « Tous les événements » qui inclut les événements
personnels de son propriétaire.

L'agenda apparaît ensuite dans le compte de l'étudiant, et sur son iPhone après
activation sur
[calendar.google.com/calendar/syncselect](https://calendar.google.com/calendar/syncselect).
La marche à suivre côté étudiant est détaillée dans [docs/TUTO.txt](docs/TUTO.txt)
et sur la page d'inscription.

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

[docs/HISTORIQUE.md](docs/HISTORIQUE.md) retrace tout ce qui a cassé, et comment.

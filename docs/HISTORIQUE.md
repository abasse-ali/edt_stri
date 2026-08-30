# Historique des correctifs

Journal chronologique des problèmes rencontrés et de leur résolution, conservé pour comprendre *pourquoi* le code est écrit ainsi. Pour la présentation du projet, voir [README.md](README.md).

## Correctifs appliqués

1. **Mode secours cohérent** — les X de repli étaient dans le repère « grille recadrée » alors qu'ils servaient de X absolus (10/1863 au lieu de 337/2192) : décalage de 327 px, soit +2 h sur tous les cours, sans aucun message. Le repli restaure désormais les deux repères et se refuse si la page ne correspond pas. Un recadrage impossible lève une erreur au lieu d'être silencieux.
1. **Aucune destruction de données** — si une journée échoue ou si rien n'est extrait, l'ancien `edt_data.json` est conservé, aucun ICS vide n'est publié, une alerte Discord part et le script sort en code 1 (la CI ne commite pas).
1. **Boucles IA bornées** — `MAX_TENTATIVES_IA = 15` ; réponse vide et 503 en série ne peuvent plus boucler indéfiniment.
1. **Plus de `NameError`** — `model_name` est défini avant tout appel faillible.
2. **URL du PDF unique** — définie une seule fois (M1) et réutilisée par la CI via `--telecharger` ; le YAML ne duplique plus le script de téléchargement.
1. **Année déduite du PDF** — `ANNEE = 2026` en dur remplacé par `deviner_annee()` (+ passage décembre → janvier géré).
2. **Clé de comparaison** — inclut le titre : les créneaux SPLIT ne s'écrasent
   plus, leurs modifications sont bien notifiées.
3. **UID ICS déterministes** — les agendas abonnés mettent à jour les événements
   au lieu de les supprimer/recréer à chaque exécution.
4. **Timeouts réseau** (20 s) + `raise_for_status()` sur Discord et le PDF.
5.  **Poppler détecté automatiquement** — l'ancien chemin `D:\Mes Projets\...`
    (espace au lieu de `_`) n'existait pas.
6.  **Debug optionnel** (`EDT_DEBUG`) — plus de copies d'images inutiles en CI.
7.  **`telecharger_pdf()` réellement appelée** en local.
8.  **Divers** — `except:` nus supprimés, code mort retiré, entrée de
    `fusionner_rectangles` non mutée, sortie console forcée en UTF-8 (les emoji
    faisaient planter le script sur une console Windows cp1252), OAuth
    interactif refusé hors terminal (bloquait 6 h en CI), horaires illisibles ou
    incohérents (`fin <= début`) écartés explicitement.

## Correctifs supplémentaires (validés sur le PDF M1 réel)

14. **Horaires ancrés sur les libellés d'heure** — l'algorithme supposait
    « i-ème trait détecté = i-ème quart d'heure ». Un séparateur non détecté
    décalait donc toute la suite de la journée. Constaté sur le M1 : un trait
    manquant entre 10h et 11h datait **tous les cours à partir de 10h15 quinze
    minutes trop tôt**. Les positions sont désormais ancrées sur les libellés
    « 8h », « 9h »… lus par pdfplumber, les quarts d'heure étant interpolés
    uniquement là où un trait manque. Les rattrapages codés en dur (index 17/18
    et 21/22) ont disparu. Sur le L3, les 50 repères sont identiques au
    comportement d'origine ; le M1 passe de 42 repères faux à 46 repères justes.
15. **Cellules englobantes ignorées** — sur le M1, le reste vide d'une journée
    est dessiné comme une grande cellule pleine hauteur contenant la case d'un
    cours. L'IA y relisait le cours intérieur : le 26/08, « Adm. Windows »
    apparaissait à la fois en 16h15→18h15 (correct) et en 12h00→19h00 (doublon
    de 7 h). Les cellules qui en contiennent une autre sont écartées ; les cases
    de même largeur (SPLIT haut/bas) ne sont pas touchées.
16b. **Quarts d'heure manquants correctement replacés** — quand une heure ne
    fournit pas ses 3 séparateurs, l'interpolation linéaire des trois écrasait
    ceux qui étaient justes : sur le M1, `10h15` tombait 31 px à côté (près d'un
    quart d'heure). Une correspondance « au plus proche » ne marche pas non plus,
    car à midi un quart d'heure fait 18 px contre 38 ailleurs. On retient
    désormais l'affectation qui rend l'espacement le plus régulier. Résultat :
    écart médian de 1,2 px sur le M1, et la zone du midi du L3 retrouve
    exactement les valeurs validées en production (`13h30 = 841`).
16. **Repli refusé sur un autre emploi du temps** — les valeurs de secours
    décrivent la grille L3 (07h45→20h00). Le M1 s'arrête à 19h00 : le repli
    vérifie maintenant que la géométrie détectée correspond, sinon il abandonne.

## Enseignants

`professeurs.txt` contient 42 entrées (L3 + M1). Vérifié sur des créneaux
réels : `KPS` → Karen PINEL-SAUVAGNAT, `PL` → Philippe LATU, `CC` → Cédric
CHAMBAULT, `AA` → André AOUN, `TG` → Thierry GAYRAUD.

Deux ambiguïtés sont traitées explicitement dans le prompt :

- **`GB`** est à la fois les initiales de Guillaume BARANGER et le nom d'un
  groupe. Règle ajoutée : précédé d'un « / » c'est un groupe, à une place
  d'initiales de prof c'est l'enseignant.
- **`EG`** seul = Eric GONNEAU ; **`EG Sécurité`** = Etienne GÉRAIN.

Initiales encore non résolues sur le PDF M1 : **`MA`** (vu dans
« Tél. Spat. (MA & FM) », 1 occurrence entre parenthèses).

## Cours parallèles sans groupe (M1)

Le PDF M1 ne contient **aucun marqueur de groupe** (`/GA`, `/GB`, `/GC` :
zéro occurrence), alors qu'il empile deux cours parallèles sur certains
créneaux — 37 cases en moitié haute, 35 en moitié basse, 95 pleine hauteur.

Choix retenu : **on ne garde que le cours du bas**. C'est le comportement du
filtre existant (`TOP` n'est conservé que si le groupe correspond, donc jamais
sans marqueur) ; aucune modification n'a été nécessaire. Les cases pleine
hauteur, majoritaires, sont conservées normalement.

Conséquence à surveiller : si un cours qui te concerne est dessiné en moitié
haute, il n'apparaîtra pas dans l'agenda. Pour changer d'avis, la règle est
dans `traiter_journee()` :

```python
if pos_txt == 'TOP':
    keep = is_my_group      # -> True pour tout garder
```


## lecture_pdf.py — lecture sans IA (en cours, NON branché)

Module autonome qui reconstruit les cours depuis la couche texte du PDF.
**Il n'est pas encore appelé par `edt_stri.py`** : voir l'état ci-dessous.

Principe : les barres **horizontales** noires donnent les cellules. Chaque
segment de la barre médiane délimite exactement un cours, et son absence
signale une cellule pleine hauteur. Les traits verticaux sont inutilisables
(72 par journée sur le L3 : un par quart d'heure, indistinguables des vraies
bordures).

Validation contre `edt_data.json` (19 cours produits en production par l'IA) :

- **16/19 créneaux exacts**, avec salle, professeur et groupe corrects
- 3 écarts, tous sur l'heure de **fin** d'une cellule pleine hauteur
  (`10h00-11h30` au lieu de `10h00-12h00`) : le début est juste, la largeur
  de la cellule ne l'est pas encore.

Reste à faire avant de brancher :

1. Fixer la largeur des cellules pleine hauteur sans barre médiane.
2. Ajouter un test de complétude (mots non rattachés à une cellule) pour que le
   parseur puisse *décliner* une journée et laisser la main à l'IA.
3. Rejouer la validation sur les 30 journées du M1.


## Correctifs du 24/08 (agenda pollué)

L'agenda publié contenait des fragments : « Adm. Linux » 13h00-13h30 suivi de
« Linux (PL) » 13h30-14h00 pour un seul cours, des noms de salle devenus des
cours (« U3-307/308 », « G-307/308 »), des titres coupés (« D) »).

17. **Renforcement des séparateurs désactivé** (`RENFORCER_SEPARATEURS`) — cette
    étape repeignait en noir les traits de quart d'heure détectés dans la
    journée. Sur le M1 ces traits sont dessinés SOUS les cases de cours : les
    renforcer coupait les cours en morceaux. Mesuré sur les deux emplois du
    temps : **11 des 30 journées M1 réparées** (161 → 150 créneaux, les 11
    disparus étant des fragments), et **aucun changement sur les 20 journées
    L3** (41 créneaux strictement identiques). Réactivable par `EDT_RENFORT=1`.
18. **Imagette élargie à droite** — le libellé de salle déborde de la bordure de
    sa cellule ; la découpe donnait « U3-307/ » au lieu de « U3-307/308 ».
    L'imagette est étendue sans jamais mordre sur le cours suivant.
19. **Repli L3 supprimé** — les valeurs de secours codées en dur décrivaient la
    grille du L3 (07h45→20h00). Sans objet en M1, et dangereuses si appliquées.
    En cas d'échec d'extraction, le script abandonne et conserve les données
    précédentes plutôt que d'inventer des horaires.

### Pourquoi l'agenda semblait figé

Le fichier sur le Drive était bien à jour (93 événements M1). Google Agenda ne
recharge un calendrier externe (ICS) que toutes les 8 à 24 h : le délai vient de
là, pas de la génération. Pour forcer, il faut se désabonner puis se réabonner.


## Suppression de l'IA (24/08)

Gemini n'est plus utilisé. Les cours sont lus dans la **couche texte du PDF**.

Architecture retenue — tout vient du vectoriel du PDF :

| étape | outil | pourquoi |
|---|---|---|
| repères horaires | OpenCV + libellés `8h`, `9h`… | validé, écart médian 1,2 px |
| géométrie des cases | pdfplumber (`GrilleJour.cellules`) | bordures exactes, au dixième de point |
| titre, prof, salle, groupe, couleur | pdfplumber | exact, aucune OCR, aucun réseau |

### Résultats

| | avec IA | OpenCV | vectoriel PDF |
|---|---|---|---|
| M1 | 93 cours dont ~20 fragments | 91 cours, 4 journées polluées | **99 cours, 0 chevauchement** |
| durée | ~20 min | **7,2 s** |
| dépendances externes | 10 clés API, quotas, 403 | aucune |

`professeurs.txt` est conservé : il n'est plus envoyé à un modèle, mais reste
la source unique de la table des enseignants, relue par `charger_profs()`.

### Ce qui disparaît

`google-genai`, les 10 clés API, la rotation modèle/clé, le secret
`GEMINI_API_KEYS` dans la CI, les pauses de 60 s entre semaines, et toute la
gestion 429/403/503.


## Le projet ne cible plus que le M1

Le L3 est sorti du périmètre. Ce qui a été retiré :

- `MY_GROUPS = ["GB", "GC"]` et tout le filtrage par groupe. Le PDF M1 ne
  contient **aucun** marqueur `/GA`, `/GB`, `/GC` : cette logique n'y servait
  qu'à faire disparaître systématiquement les cours de la moitié haute.
- Les valeurs de secours codées en dur (grille L3 07h45→20h00).

À la place, un réglage explicite : `EDT_MOITIE` (voir plus bas).

Les mesures faites sur le L3 restent citées dans les commentaires : elles
documentent *pourquoi* un correctif existe (par exemple « 41 créneaux
identiques sur 20 journées » qui a validé la désactivation du renforcement).
Elles ne signifient pas que le L3 est encore pris en charge.

## Correctifs de cadrage et de découpe

20. **Colonne 19h15 rétablie** — le bord droit de la grille était détecté à
    x=2189 alors qu'un trait existe à x=2209 : la dernière colonne était rabotée.
    Les traits de l'en-tête sont désormais lus dans le PDF (exacts) plutôt que
    par morphologie, et le bord droit suit le dernier d'entre eux.
21. **Cadres de debug fidèles** — le rectangle rouge couvrait toute la hauteur
    même pour une demi-cellule. Il épouse maintenant la moitié réellement lue,
    avec un code couleur (rouge = pleine hauteur, bleu = haut, orange = bas) et
    l'horaire retenu.
22. **Cases englobantes réduites au lieu d'être supprimées** — sur le M1 les
    deux moitiés d'un créneau n'ont pas la même largeur (haut 16h15→18h15, bas
    15h45→18h45). Supprimer la grande case perdait le cours du bas
    (« Tel. Mobiles (YP) » absent de l'agenda). On n'en lit plus que la moitié
    laissée libre : **+6 cours**.
23. **Cases qui coupent un titre écartées** — une case dont le texte se
    poursuit juste au-delà de son bord n'est pas une cellule ; elle produisait
    des cours tronqués (« Tel. Mobiles » sans son professeur, sur un créneau
    faux).
24. **Déduplication** — même titre, même salle, créneaux qui se recouvrent :
    la plus courte est conservée (l'autre déborde sur la zone vide).


## Détection des cases par le vectoriel (24/08)

OpenCV cherchait les cellules par morphologie sur l'image rendue. Sur cette
image, les pointillés de la grille horaire et les bordures d'un cours sont tous
les deux des pixels sombres : la détection prenait donc des **zones vides pour
des cours**. Quatre journées en sortaient avec des créneaux fantômes qui
chevauchaient les vrais (« 12h00-19h15 » le 26/08 ; « Tél. Spat. » compté deux
fois le 01/10, le 10/09 et le 09/09).

Dans le PDF, la distinction est nette :

| élément | objet PDF | couleur |
|---|---|---|
| bordure de cellule | rectangle 1,7 pt | noir plein `(0, 0, 0)` |
| quart d'heure de la grille | rectangle 0,8 pt | motif pointillé `P67`, `P68`… |

`GrilleJour.cellules()` reconstruit donc chaque case depuis ses bordures noires,
et ne retient que les plages contenant du texte. Trois pièges rencontrés :

1. **Les bordures verticales ne sont pas confinées à une journée.** Le PDF
   dessine un seul rectangle traversant toutes les journées consécutives où la
   bordure existe (x = 246,5 court de y = 69,7 à 179,3, soit cinq bandes). Les
   filtrer sur `top`, comme les barres horizontales, les faisait toutes
   disparaître.
2. **Une case de salle est plus haute qu'une demi-bande** (10,8 pt contre
   10,5) : celle du lendemain commençait avant la fin de la journée courante et
   sa bordure passait pour une séparation TOP/BOTTOM. `dans_bande` se cale
   désormais sur le centre du rectangle.
3. **La moitié écartée l'est sans condition.** Une tentative de ne l'appliquer
   qu'aux cours réellement empilés a été retirée : elle rendait le résultat
   difficile à prévoir. Une cellule de la moitié opposée est écartée, point.
   Conséquence assumée : un cours seul dans son créneau, comme le TOEIC du
   02/09, n'apparaît que dans la version correspondant à sa position.

Résultat : 99 cours, aucun chevauchement, un seul cours sans professeur — et le
PDF n'en indique effectivement aucun pour celui-là.

## Abandon du Drive au profit de l'API Calendar (24/08)

Le bot publiait `edt.ics` sur Google Drive, et l'agenda s'y abonnait. Deux
impasses ont fait abandonner cette voie :

1. **Google Agenda relit une URL externe quand il le décide** — 8 à 24 h,
   parfois plus. Aucun en-tête HTTP ne force ce rythme. `X-PUBLISHED-TTL` et
   `REFRESH-INTERVAL` sont bien émis, mais Google les ignore (Apple Calendar et
   Outlook, eux, les respectent).
2. **iOS réécrit l'URL d'abonnement en `http://`.** Or Drive répond 403 au HTTP
   direct, et ne sert le fichier qu'en redirigeant vers `drive.usercontent.
   google.com` en HTTPS — une redirection inter-domaines qu'iOS refuse de
   suivre pour un calendrier. D'où l'« Échec de la validation » systématique.

Le bot écrit désormais **directement dans un agenda Google** (`google_agenda.py`).
Les cours apparaissent en quelques secondes, et sur l'iPhone via la
synchronisation normale du compte : aucun abonnement, aucune URL, aucun réglage
SSL.

| | abonnement ICS | API Calendar |
|---|---|---|
| délai de mise à jour | 8 à 24 h | quelques secondes |
| iOS | refusé | natif via le compte Google |
| périmètre OAuth | `drive.file` | `calendar` |
| partage du fichier | `anyone: writer` | aucun fichier exposé |

Chaque cours porte un identifiant déterministe (MD5 de date + horaires + titre,
dont l'alphabet hexadécimal est accepté tel quel par l'API) : un cours déplacé
est modifié sur place, un cours retiré du PDF disparaît de l'agenda. Une seconde
exécution rend « 0 ajout, 0 modification, 0 suppression ».

Les examens sont colorés en rouge (`colorId` 11, « Tomate »).

`edt.ics` continue d'être généré localement — export portable, ignoré par git —
mais n'est plus téléversé nulle part.


## Deux demi-promos, deux agendas (25/08)

Le PDF empile les deux demi-promos dans la même case : celle du haut et celle
du bas. Aucun marqueur de groupe ne les distingue (`/GB`, `/GC` : zéro
occurrence), **seule leur position compte**. Les cases pleine hauteur
concernent tout le monde.

| `EDT_MOITIE` | cellules retenues | couleur | fichiers |
|---|---|---|---|
| `BAS` (défaut) | `BOTTOM` + `FULL` | pistache `#7bd148` | `edt_data.json`, `edt.ics` |
| `HAUT` (Ingé) | `TOP` + `FULL` | raisin `#cd74e6` | `edt_data_inge.json`, `edt_inge.ics` |

Mesuré sur le PDF M1 : 86 cours en `BAS`, 85 en `HAUT`, dont **41 communs** —
les cellules pleine hauteur.

Chaque version écrit dans son propre agenda et son propre JSON de comparaison :
lancer l'une n'efface jamais les cours de l'autre. La CI fait les deux passes à
la suite, dans le même job — le téléchargement et l'installation des
dépendances ne sont payés qu'une fois.

    EDT_MOITIE=HAUT python edt_stri.py

Pour n'en garder qu'une, supprimer la ligne correspondante de l'étape
*Run Python script* du workflow.


## Retrouver son agenda après un renommage (25/08)

L'agenda était cherché par son nom. Renommer « EDT STRI M1 » en « STRI M1 G2 »
depuis l'interface Google — ce qui est parfaitement légitime — le rendait
introuvable : le script en créait un second et **dupliquait les 86 cours**.

La recherche se fait désormais sur un marqueur posé dans la description,
`[edt-stri:BAS]` ou `[edt-stri:HAUT]`, qui survit au renommage. Le repli par
nom sert aux agendas antérieurs au marqueur ; ils sont étiquetés au passage.
Le nom choisi par l'utilisateur n'est jamais réécrit.

`GOOGLE_CALENDAR_ID` reste prioritaire sur les deux.

### Couleurs

Google expose **deux palettes distinctes**, et le même numéro n'y désigne pas
la même teinte :

| palette | taille | usage ici |
|---|---|---|
| `event` | 11 couleurs | examens en tomate (`colorId` 11) |
| `calendar` | 24 couleurs | fond de l'agenda : pistache, raisin |

« Pistache » et « Raisin » n'existent que dans la seconde. La couleur est
appliquée sur l'entrée `calendarList`, pas sur l'agenda : elle appartient à
l'abonnement de l'utilisateur. Surchargeable par `EDT_COULEUR`.


## Imposer une couleur aux personnes abonnées (25/08)

Google distingue deux choses, et une seule est partageable :

| | où c'est stocké | qui le voit |
|---|---|---|
| couleur de fond de l'agenda | `calendarList`, l'abonnement de chaque personne | son propriétaire seul |
| couleur d'un événement | l'événement lui-même | **tout le monde** |

Régler la teinte d'un agenda **pour quelqu'un d'autre est impossible** : aucune
API n'expose la `calendarList` d'un tiers, et c'est délibéré. Chacun choisit la
sienne, Google en attribuant une au hasard à l'ajout.

Le script pose donc une couleur sur **chaque cours**, pas seulement sur les
examens. Elle est stockée sur l'événement, donc identique pour toute personne
avec qui l'agenda est partagé.

| version | couleur des cours | examens |
|---|---|---|
| `BAS` | basilic `#51b749` (colorId 10) | tomate `#dc2127` |
| `HAUT` | raisin `#dbadff` (colorId 3) | tomate `#dc2127` |

La palette des événements ne compte que onze teintes et **n'a pas de
pistache** : le vert le plus proche est le basilic. Surchargeable par
`EDT_COULEUR_COURS`.

Limite qui subsiste : l'app **Calendrier d'iOS** ignore les couleurs par
événement et peint tout à la couleur de l'agenda. Aucun réglage côté serveur
n'y change rien — seul le titre reste lisible partout (voir ci-dessous).


## Le titre plutôt que la couleur (25/08)

Une personne abonnée en `reader`, sur Google Agenda, voyait ses cours à la
teinte de son propre agenda — examens compris — alors que les événements
portaient bien `colorId` 10 et 11 depuis dix heures. Ni les droits ni les
données n'étaient en cause, et la raison n'a pas pu être établie depuis le
compte propriétaire : il faudrait s'authentifier comme l'abonné.

Constat retenu : **on ne peut pas compter sur la couleur** pour transmettre une
information à des personnes dont on ne maîtrise pas le client. Le titre, si :
c'est la seule chose que tous affichent à l'identique.

    avant : [EXAMEN] BD (Karen PINEL-SAUVAGNAT)
    après : 🔴 EXAMEN · BD (Karen PINEL-SAUVAGNAT)

Les couleurs restent en place — elles ne coûtent rien là où elles s'affichent.

Le marqueur vit dans `google_agenda.MARQUEUR_EXAMEN`, utilisé à la fois pour
construire le titre et pour reconnaître un examen à colorer : une seule source.
Le changer déplace les événements concernés, l'identifiant étant dérivé du
titre — cinq suppressions suivies de cinq créations, une seule fois.


## Les examens dans un agenda séparé (25/08)

Constat après trois tentatives : **aucune couleur ne franchit le partage.**

| réglage | où il vit | qui peut le fixer |
|---|---|---|
| couleur de fond d'un agenda | `calendarList` de chaque personne | elle seule |
| couleur d'un événement | vérifié : ne se propage pas non plus | elle seule |

Une personne abonnée en `reader` recevait bien les mises à jour — donc sur le
bon agenda, pas sur un vieil abonnement ICS — mais voyait tous ses cours à la
teinte que Google avait attribuée à son abonnement, examens compris, alors que
les événements portaient `colorId` 10 et 11 depuis dix heures.

Le seul mécanisme dont la distinction est garantie pour tout le monde est donc
la **séparation en deux agendas** : Google attribue une teinte différente à
chaque agenda ajouté, et toutes les applications colorent par agenda — y
compris celle d'Apple, qui ignore les couleurs par événement.

    STRI M1 G2                 84 cours
    STRI M1 G2 — Examens        2 examens
    STRI Ingé G1               82 cours
    STRI Ingé G1 — Examens      3 examens

Le nom de l'agenda d'examens suit celui de l'agenda principal, renommage
compris, et son marqueur est `[edt-stri:BAS-EXAMENS]`. Les couleurs continuent
d'être posées — elles ne coûtent rien là où elles s'affichent.

Contrepartie : les agendas d'examens doivent être partagés séparément avec les
mêmes personnes.


### Un examen n'est pas toujours en pleine hauteur

Mesuré sur le PDF M1 : six cellules d'examen, **deux `FULL`, deux `TOP` et deux
`BOTTOM`**. Un examen est donc dessiné en moitié haute ou basse exactement comme
un cours, et **cette position désigne le groupe concerné**.

Les examens suivent donc le même filtre que les cours : `FULL` pour tout le
monde, `TOP` pour les Ingés, `BOTTOM` pour le M1. Les publier dans les deux
versions mélangeait les promos.

| | M1 (`BAS`) | Ingé (`HAUT`) |
|---|---|---|
| 02/09 TOEIC (`TOP`) | | ✔ |
| 18/09 Adm. Linux 07h45 (`TOP`) | | ✔ |
| 18/09 Adm. Linux 10h00 (`BOTTOM`) | ✔ | |
| 18/09 Adm. Linux 13h30 (`BOTTOM`) | ✔ | |
| 25/09 BD (`FULL`) | ✔ | ✔ |
| 02/10 Interco (`FULL`) | ✔ | ✔ |


### Un fond d'examen ne couvre pas toujours toute la cellule

`GrilleJour.couleur()` exigeait que le fond coloré recouvre 60 % de la largeur
de la cellule, pour ne pas confondre une pastille orange sans rapport avec un
vrai fond d'examen.

Trop strict : le PDF est incohérent. Tantôt le fond jaune couvre toute la
cellule et la case verte de salle est dessinée par-dessus, tantôt il s'arrête
AVANT cette case — et ne couvre alors que 58 %. Les trois examens
d'« Adm. Linux » du 18/09 passaient donc pour des cours ordinaires.

Un fond qui **commence exactement au bord gauche** de la cellule lui appartient :
c'est ce qui le distingue d'une pastille posée au milieu. Le seuil descend à
35 % dans ce cas, reste à 60 % sinon.

Mesuré : 8 rectangles jaunes dans le PDF pour 6 cellules — les journées dont le
fond est dessiné en deux morceaux, un sous le titre et un sous le professeur.
3 examens détectés avant le correctif, 6 après. Les 4 fonds orange (Sport) sont
inchangés.

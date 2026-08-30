# Faire tourner le bot Discord en permanence

Le reste du projet n'a besoin de rien : GitHub Actions télécharge les PDF,
publie les agendas et s'arrête, toutes les heures, gratuitement.

Le bot Discord est le seul morceau qui ne rentre pas dans ce moule, et c'est
structurel : **Discord n'envoie une commande ou un clic de bouton qu'à un
programme déjà connecté**. Il ne suffit pas de se réveiller de temps en temps ;
il faut être là au moment exact où quelqu'un clique.

## Pourquoi pas GitHub Actions

C'est la première idée, et elle ne marche pas, pour deux raisons distinctes.

**Technique** : un job Actions est plafonné à six heures, et met une à deux
minutes à démarrer. Un bot qui redémarre toutes les six heures rate les clics
pendant chaque relance, et perd sa connexion entre-temps.

**Contractuelle**, et c'est la vraie : les conditions d'utilisation de GitHub
Actions interdisent de s'en servir pour autre chose que construire, tester et
publier un projet. Un bot en boucle est exactement ce qu'elles visent. Le
compte qui tourne là est celui qui synchronise les emplois du temps de toute la
promo — le mettre en jeu pour économiser quelques euros serait un mauvais
calcul.

## Les voies possibles

| | Vraiment 24/7 | Coût | Effort |
|---|---|---|---|
| Panneau gratuit (Katabump, FridayDev…) | à vérifier — beaucoup exigent un renouvellement | 0 | très faible |
| Hébergeur en ligne | oui | 0 à ~5 €/mois | moyen, une fois |
| Raspberry Pi ou vieux PC | oui, tant qu'il est allumé | ~5 €/an d'électricité | faible |
| Ton PC Windows | non | 0 | très faible |

Dans tous les cas, la machine a besoin de deux fichiers **qui ne sont pas dans
le dépôt** : `.env` (jeton Discord, adresses Moodle) et `token.json`
(autorisation Google). Copie-les à la main, jamais par un commit.

---

## 0. Panneau d'hébergement gratuit (Katabump, FridayDev…)

Ces services offrent un conteneur Python gratuit, pensé pour les bots Discord.
C'est la voie la plus rapide, et elle demande zéro machine à toi.

Ils reposent presque tous sur **Pterodactyl**, un panneau qui lance *un* fichier
et installe *un* fichier de dépendances. Deux réglages suffisent, dans les
variables du serveur :

| Variable du panneau | Valeur |
|---|---|
| Fichier à lancer (`PY_FILE`, « Main file »…) | `bot.py` |
| Fichier de dépendances (`REQUIREMENTS_FILE`) | `requirements-bot.txt` |

`bot.py`, à la racine du dépôt, existe uniquement pour eux : il ajoute `src/`
au chemin de recherche et appelle le vrai programme. Si le panneau te laisse
écrire la commande de démarrage toi-même, préfère :

```
pip install -r requirements-bot.txt && python -u src/bot_discord.py
```

⚠️ **Ne le laisse pas installer `requirements.txt`** — celui de la racine tire
OpenCV, NumPy, pdfplumber et Playwright, plusieurs centaines de mégaoctets qui
dépasseront le quota d'une offre gratuite, pour des bibliothèques dont le bot
ne se sert jamais.

Ensuite, dépose par le gestionnaire de fichiers ou en SFTP :

```
src/            tout le dossier
docs/
bot.py
requirements-bot.txt
.env            à créer, ou à remplir par les variables du panneau
token.json      l'autorisation Google
```

Le répertoire de travail n'a aucune importance : les chemins sont calculés à
partir de l'emplacement des modules. Vérifié en lançant `bot.py` depuis un
dossier sans rapport avec le dépôt.

### Ce que ce bot consomme

Mesuré, pour choisir une offre en connaissance de cause :

| | |
|---|---|
| Disque, avec `requirements-bot.txt` | **161 Mo** |
| Disque, avec `requirements.txt` | 483 Mo — trois fois plus, pour rien |
| Mémoire | quelques dizaines de mégaoctets ; aucune offre ne cale là-dessus |
| Port entrant | **aucun** |

Ce dernier point élimine toute une catégorie d'hébergeurs : Render, Koyeb,
Cloud Run et les « web services » gratuits en général exigent un programme qui
écoute sur un port, et mettent en veille ce qui ne reçoit pas de requête. Un
bot Discord n'écoute rien — il se connecte *sortant* et attend. Il faut donc un
hébergeur de *processus*, pas de site web.

### Ce qu'il faut vérifier avant de s'y installer

Je n'ai pas pu consulter leurs conditions — FridayDev refuse les requêtes
automatiques, Katabump ne rend pas de page lisible. Regarde donc toi-même :

- **le serveur se met-il en veille**, ou faut-il le **renouveler** tous les
  quelques jours ? Beaucoup d'offres gratuites le demandent, et un bot éteint
  ne reçoit aucun clic ;
- **le disque alloué** — quelques centaines de mégaoctets suffisent avec
  `requirements-bot.txt`, pas avec l'autre ;
- **le redémarrage automatique** après un plantage.

### Le risque à connaître

Tu déposes sur une machine tierce ton jeton Discord et ton `token.json` Google.
Le premier contrôle le bot ; le second donne accès en écriture à tes agendas —
rien d'autre, la portée est limitée au calendrier. Ces services sont tenus par
des particuliers.

Les deux se révoquent instantanément si besoin : Discord → Developer Portal →
Bot → *Reset Token*, et Google → compte → *Applications tierces* → retirer
l'accès. Rien n'est irréversible, mais autant le savoir avant.

### Un bon point

Les demandes en attente de validation sont écrites dans
`donnees/demandes_discord.json`. Un redémarrage du conteneur — une veille, une
maintenance de l'hébergeur — ne les perd donc pas : les fiches déjà envoyées
gardent des boutons vivants.

---

## 1. Oracle Cloud Always Free

Une vraie machine Linux, gratuite sans limite de durée. C'est la solution la
plus solide, et la seule gratuite qui ne dépende de personne.

### Créer l'instance

Console Oracle → **Compute → Instances → Create instance**.

| Réglage | Valeur | Pourquoi |
|---|---|---|
| Image | **Ubuntu 24.04** | `apt`, systemd, et un Python assez récent |
| Shape | **VM.Standard.E2.1.Micro** | 1 cœur AMD, 1 Go — largement assez pour 161 Mo |
| | *ou* VM.Standard.A1.Flex (ARM) | plus puissant, mais souvent « out of host capacity » |
| Clé SSH | ta clé publique | voir ci-dessous |

⚠️ **Prends bien 24.04, pas 20.04.** Ubuntu 20.04 livre Python 3.8, or le
projet exige 3.9 au minimum — `zoneinfo`, qui gère les fuseaux horaires, n'y
existe pas. Le script d'installation sait rattraper le coup en installant un
Python récent, mais autant partir sur un système encore supporté.

⚠️ **Vérifie l'étiquette « Always Free-eligible »** sur le shape choisi. Sans
elle, l'instance sera facturée — ou supprimée — à la fin des trente jours
d'essai. C'est l'erreur qui coûte cher.

Ne te bats pas pour l'ARM : le shape AMD `E2.1.Micro` est presque toujours
disponible, alors que l'A1 renvoie fréquemment « out of host capacity » dans
les régions demandées. Ce bot tient dans 161 Mo, quatre cœurs ARM seraient du
luxe.

Générer une clé SSH, depuis ton PC Windows :

```powershell
ssh-keygen -t ed25519 -C "oracle"
type $env:USERPROFILE\.ssh\id_ed25519.pub    # à coller dans la console
```

### Installer le bot

⚠️ **Le dépôt est privé.** GitHub répond 404 aux requêtes anonymes : ni
`curl … | bash`, ni `git clone` sans identifiants ne fonctionnent. Deux voies.

#### a. Envoyer une archive depuis ton PC — le plus simple

`git archive` exporte le contenu **du dépôt**, pas celui de ton disque. C'est
important : ta copie de travail Windows a des fins de ligne CRLF, et un script
shell en CRLF échoue sous Linux avec `bad interpreter: bash^M`. L'archive, elle,
sort en LF.

```powershell
git archive --format=tar.gz -o edt_stri.tgz HEAD
scp edt_stri.tgz .env token.json ubuntu@<adresse-ip>:~/
```

`.env` et `token.json` voyagent à part : ils ne sont pas dans le dépôt, et
n'ont rien à y faire.

```bash
ssh ubuntu@<adresse-ip>
mkdir -p edt_stri && tar xzf edt_stri.tgz -C edt_stri
mv .env token.json edt_stri/
bash edt_stri/deploiement/installer_serveur.sh
```

Pour mettre à jour plus tard, refais l'archive et relance le script : il est
idempotent.

#### b. Poser une clé de déploiement — pour un `git pull` sur le serveur

```bash
ssh-keygen -t ed25519 -f ~/.ssh/depot -N ""
cat ~/.ssh/depot.pub
```

Colle cette clé dans **GitHub → le dépôt → Settings → Deploy keys → Add**, en
lecture seule. Puis, sur le serveur :

```bash
printf 'Host github.com
  IdentityFile ~/.ssh/depot
' >> ~/.ssh/config
DEPOT=git@github.com:abasse-ali/edt_stri.git bash installer_serveur.sh
```

### Ce que fait le script

Il installe Python et ses dépendances système, met le code dans
`/opt/edt_stri`, crée l'environnement avec `requirements-bot.txt`, **adapte le
service systemd à cette machine** — utilisateur et chemins réels, au lieu du
modèle qui vise `pi` et `/opt/edt_stri` — puis l'active.

S'il manque `.env` ou `token.json`, il le dit et s'arrête avant de démarrer le
service, plutôt que de lancer un bot qui échouerait aussitôt.

```bash
sudo systemctl start edt-bot     # si le script ne l'a pas fait
journalctl -u edt-bot -f         # les journaux, en direct
```

### Le piège des instances inactives

Oracle **récupère les instances Always Free jugées inactives**. Un bot Discord
consomme très peu de processeur : le risque est réel, pas théorique.

La parade connue est de passer le compte en **Pay As You Go**. Les ressources
Always Free y restent gratuites, mais la récupération automatique ne s'applique
plus. En contrepartie, dépasser les quotas devient facturable — reste donc dans
les limites du gratuit.

### Avec Docker plutôt que systemd

```bash
git clone https://github.com/abasse-ali/edt_stri.git && cd edt_stri
# déposer .env et token.json ici
docker compose up -d && docker compose logs -f
```

`restart: unless-stopped` relance le conteneur après un plantage **et** après
un redémarrage de la machine.

---

## 2. Raspberry Pi ou vieux PC sous Linux

Vrai 24/7 chez toi, sans abonnement. Un Pi consomme environ 3 W, soit à peu
près cinq euros d'électricité par an.

```bash
sudo git clone <ce dépôt> /opt/edt_stri
cd /opt/edt_stri
python3 -m venv venv
./venv/bin/pip install -r requirements-bot.txt
# copier .env et token.json ici

sudo cp deploiement/edt-bot.service /etc/systemd/system/
sudo nano /etc/systemd/system/edt-bot.service   # adapter User= et les chemins
sudo systemctl daemon-reload
sudo systemctl enable --now edt-bot
```

Pour surveiller :

```bash
systemctl status edt-bot      # tourne-t-il ?
journalctl -u edt-bot -f      # les messages, en direct
journalctl -u edt-bot -n 50   # les cinquante derniers
```

`Restart=always` couvre le cas d'une panne réseau prolongée, qui finit par tuer
le processus même si `discord.py` sait se reconnecter tout seul.

---

## 3. Ton PC Windows

Gratuit et immédiat, mais **ce n'est pas du 24/7** : PC éteint ou en veille,
bot arrêté. Les demandes attendront ton retour — ce qui n'est pas dramatique,
personne n'attend son emploi du temps à trois heures du matin.

```powershell
powershell -ExecutionPolicy Bypass -File deploiement\installer_tache_windows.ps1
```

La tâche démarre à l'ouverture de session, se relance après un plantage, et
tourne sans fenêtre noire à l'écran (`pythonw.exe`). Pense à empêcher la mise
en veille, sinon le bot s'arrête avec l'écran :

```powershell
powercfg /change standby-timeout-ac 0
```

---

## Ce que le bot emporte avec lui

Il n'installe que `requirements-bot.txt` : `discord.py`, `requests`,
`python-dotenv` et les bibliothèques Google. **Ni OpenCV, ni NumPy, ni
pdfplumber** — le bot ne lit aucun PDF, rien ne justifierait trois cents
mégaoctets de dépendances sur un Raspberry Pi.

Ça n'a pas toujours été vrai : il les tirait par un seul import, celui de
`edt_stri` pour l'authentification Google. Cette fonction vit maintenant dans
`google_agenda.py`, et un test vérifie que la dépendance ne revient pas par
mégarde.

## Vérifier que tout va bien

Au démarrage, le bot dit où il en est :

```
✅ Connecté comme Alfred#6538.
   Valideurs : 794845504089227265
   0 demande(s) en attente.
   Les fiches partent en message privé à jpixfred1527 ✅
```

La dernière ligne est la plus importante : elle confirme qu'une demande pourra
t'être remise. Si elle manque ou signale un problème, une inscription
n'arriverait nulle part — et l'étudiant attendrait un accès que personne ne
verrait passer.

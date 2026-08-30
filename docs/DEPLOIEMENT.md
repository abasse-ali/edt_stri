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

## Les trois voies possibles

| | Vraiment 24/7 | Coût | Effort |
|---|---|---|---|
| Hébergeur en ligne | oui | 0 à ~5 €/mois | moyen, une fois |
| Raspberry Pi ou vieux PC | oui, tant qu'il est allumé | ~5 €/an d'électricité | faible |
| Ton PC Windows | non | 0 | très faible |

Dans les trois cas, la machine a besoin de deux fichiers **qui ne sont pas dans
le dépôt** : `.env` (jeton Discord, adresses Moodle) et `token.json`
(autorisation Google). Copie-les à la main, jamais par un commit.

---

## 1. Hébergeur en ligne

Le seul vrai 24/7 : indépendant de ton PC, de ta box et des coupures de
courant.

**Oracle Cloud Always Free** offre une machine ARM gratuite sans limite de
durée. L'inscription demande une carte bancaire pour vérification mais n'est
pas débitée ensuite, et elle est parfois capricieuse. **Fly.io**, **Railway**
et n'importe quel petit VPS font l'affaire pour quelques euros par mois. Les
offres gratuites changent souvent : vérifie avant de t'engager.

Une fois la machine obtenue, avec Docker :

```bash
git clone <ce dépôt> && cd edt_stri
# copier .env et token.json depuis ton PC (scp, ou un copier-coller prudent)
docker compose up -d
docker compose logs -f
```

`restart: unless-stopped` relance le conteneur après un plantage **et** après
un redémarrage de la machine.

Sans Docker, c'est le service systemd de la section suivante.

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

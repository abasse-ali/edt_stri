# EDT STRI

Lit les emplois du temps PDF de STRI (M1 et L3), les publie dans des agendas
Google et prévient sur Discord. Tout tourne dans GitHub Actions :
`edt_sync.yml` toutes les heures, `rendus_sync.yml` pour les rendus Moodle.

## Quand une alerte arrive

La plupart des pannes viennent d'un PDF que l'école a modifié : nouveau
logiciel, trait dessiné autrement, semaine retirée. Méthode :

1. Lire l'exécution en échec : `gh run view <id> --log-failed`. L'alerte
   contient son lien.
2. Trouver l'étape et le contrôle en défaut. Un PDF « a changé » d'après
   l'étape *Comparer les PDF* n'est pas commité tant que la CI échoue : celui
   du dépôt (`donnees/`) est donc l'ANCIEN.
3. Télécharger le PDF actuel hors du dépôt :
   `python src/telechargement.py --promo L3 /tmp/l3.pdf`, puis le relire avec
   `tests/verif_edt.py` (`relire_pdf`) et pdfplumber pour voir ce qui a changé.
4. Corriger la cause dans le code, pas le symptôme dans les données ni le
   garde-fou qui l'a détectée. Ajouter un test dans `tests/test_edt.py`, dans
   le style des voisins : la docstring raconte le cas vécu et sa date.
5. Vérifier que les PDF déjà commités donnent exactement les mêmes cellules
   qu'avant, puis lancer `python tests/test_edt.py`.
6. Proposer une pull request. Ne jamais pousser sur `main`, ne jamais
   modifier `donnees/` à la main : la CI s'en charge après la fusion.

## Conventions

- Tout est en français : code, commentaires, messages de commit, réponses.
- Les commentaires expliquent POURQUOI, avec le cas réel qui l'a motivé.
- `edt_stri.py` publie dans de vrais agendas : ne pas le lancer sans
  nécessité ; `verif_edt.py` relit sans rien publier.

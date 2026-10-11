# Démo jury — DeFi Risk Radar en deux minutes

Ouvrir `index.html` directement dans un navigateur, ou depuis la racine :

```bash
python3 -m http.server 8766 --bind 127.0.0.1 --directory docs/demo
```

Puis ouvrir <http://127.0.0.1:8766/radar/>. Aucun backend, compte ou réseau
nécessaire. Le navigateur affiche des calculs enregistrés des moteurs Core.

## Parcours et texte à dire

1. **0:00–0:25 — Dépendances.** « Notre question : une dépendance casse,
   combien perd-on et faut-il sortir ? Voici un fonds simulé de 2 millions
   de dollars sur un marché réel Morpho utilisant un PT Pendle. Il emprunte
   1,4 million : ses fonds propres sont 600 000 dollars. » Cliquer sur le PT
   ou un lien du graphe. « Chaque lien montre ses preuves et leur statut.
   Les dépendances non résolues restent visibles. » Fermer la preuve.
2. **0:25–0:50 — Sortie par taille.** « Une sortie dépend de la taille.
   Nous avons demandé des devis pour vendre tous les PT de trois fonds
   hypothétiques. À 1 et 2 millions, Pendle répond ; à 5 millions, cette route
   refuse le devis. Un prix spot ne suffit donc pas à estimer une sortie. »
   Cliquer sur la preuve du devis à 2 millions, puis la fermer.
3. **0:50–1:20 — Impact.** Conserver la décote de 20 %. « Nous supposons une
   baisse du prix économique du PT de 20 %. Le moteur calcule une perte de
   400 000 dollars : les fonds propres passent de 600 000 à 200 000 dollars.
   C'est quantité × prix sourcé × choc explicite. Le prix oracle reste
   distinct : ce scénario ne prouve pas une liquidation. »
4. **1:20–2:00 — Décision.** Garder d'abord la case décochée. « Le devis
   existe, mais la clôture n'est pas démontrée : le moteur refuse de forcer
   une décision. » Cocher **Explorer un plan de clôture supposé**.
   « Pour explorer le mécanisme, supposons une clôture possible, 300 dollars
   de gas et 1 000 dollars de gain net sur sept jours. À une probabilité
   supposée de 2 %, le résultat est STAY. » Choisir **10 %**.
   « Il devient EXIT. Le seuil est environ 2,23 % avec ces entrées fixes.
   Nous rendons les hypothèses et la décision auditables. » Télécharger
   le rapport du cas si le jury demande le détail.

Revenir à la première étape avant chaque présentation. **Recommencer**
revient au premier écran ; il ne réinitialise pas les sélections. Recharger
la page rétablit les valeurs initiales.

## Données et limites à expliquer

- Replay du **10 octobre 2026 à 19:22 UTC**, pas un devis actuel exécutable.
- Marchés et prix réels, fonds et scénarios simulés. Aucune transaction.
- Le refus à 5 millions concerne une route ; il ne prouve pas que toute
  sortie serait impossible.
- Probabilité, gain net et plan de clôture sont des hypothèses utilisateur.
  Le backing non résolu ne devient pas une perte chiffrée inventée.
- L'écran principal se concentre sur quatre fonctions. Marilou et Monte
  Carlo sont dans les compléments ; leurs limites de validation restent
  affichées. Leur sortie ne détermine pas le verdict de ce scénario.
- Les preuves embarquées contiennent des métadonnées et empreintes des
  réponses publiques. Les réponses brutes complètes restent dans les
  archives Data locales ; cette page ne prétend pas les embarquer.

## Reconstruction

L'archive publique minimale dans `source/` conserve les DTO nécessaires
et les preuves référencées, sans clés ni historiques inutilisés. Les montants
sont calculés en Decimal par Core ; JavaScript ne fait que les afficher.

```bash
.venv/bin/python backend/scripts/build_radar_demo.py
.venv/bin/python -m pytest -c backend/pytest.ini backend/app/tests/test_radar_demo.py -q
```

Pour utiliser une autre livraison Data, passer `--delivery chemin/du/bundle`.
Le générateur refuse un dataset qui ne permet pas le parcours documenté.

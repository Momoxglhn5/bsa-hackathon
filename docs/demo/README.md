# Marilou — démonstration et résultats

Démo hors ligne du prototype de prévision des prix USD de PT Pendle, préparée
pour le hackathon BSA. Snapshot du **10 octobre 2026**, modèle TimesFM 2.5,
statut **expérimental**.

## Ouvrir et présenter

Ouvrir [index.html](index.html) dans Safari ou Chrome. Le fichier embarque les
données : pas de serveur, de connexion internet ni de clé API nécessaire.

- [Pitch, parcours de présentation et questions du jury](marilou-pitch.md).
- [Résultats comparatifs](results/RESULTS.md).
- [Chiffres expliqués](CHIFFRES.md).
- [Données publiques de la démo](demo-data.json).
- [Vérifications de la démo](VERIFICATION.json), distinctes de la validation du modèle.
- [Aperçu](apercu.jpg).

La sélection d'un actif ou d'une vue affiche des résultats enregistrés ; elle
ne relance pas une inférence. Les vues rétrospectives affichent les sorties
archivées à J1, J3 et J7. Les courbes reliant ces trois points servent à la
lecture et ne constituent pas des prévisions quotidiennes supplémentaires.

## Rapports complets

Les trois modèles utilisent les mêmes quatre snapshots et les mêmes origines :

- [Chronos-2](results/chronos2.json).
- [TimesFM 2.5](results/timesfm.json).
- [Chronos-Bolt](results/chronos_bolt.json).
- [Comparaison agrégée](results/comparison.json).

Les rapports enregistrent les révisions des poids, les métriques, les
prévisions, les fenêtres et les références des snapshots. Les identifiants de
preuve renvoient aux exports de collecte conservés localement ; ces rapports
ne contiennent pas l'ensemble des preuves RPC et des réponses sources brutes.

Le run Chronos-Bolt applique une correction explicite des quantiles extérieurs
croisés, sans modifier la médiane : q10=min(q10,q50), q90=max(q90,q50). Le nombre
de lignes corrigées figure dans son rapport. TimesFM utilise son option
`fix_quantile_crossing`. La comparaison concerne les prévisions médianes.

## Limites

Le test a déjà été consulté pour comparer les modèles. Il comprend deux
semaines par actif sur les mêmes dates, avec des erreurs potentiellement
corrélées. Cette comparaison ne vaut pas validation indépendante.

Les quatre actifs ne valident pas le PT-siUSD initial. Les intervalles restent
insuffisamment calibrés. Les prix de marché ne sont pas l'oracle Morpho, et
aucune décision financière n'est déclenchée par ces prévisions.

Les exports Data bruts, les stores, les clés et les poids restent hors de Git.
Le générateur reproductible se trouve dans `ml/build_demo.py` ; les commandes
figurent dans le pitch.

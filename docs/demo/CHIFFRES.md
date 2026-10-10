# Comprendre les chiffres de Marilou

Tous les chiffres ci-dessous concernent le test rétrospectif réutilisé du
10 octobre 2026. Ils décrivent une comparaison exploratoire, pas un modèle
validé ou un rendement financier.

## L'erreur moyenne : MAE

La MAE est la moyenne des écarts absolus entre le prix prédit et le prix
réellement observé. Elle s'exprime en **USD par PT**. Plus elle est basse,
meilleure est la prévision centrale sur les observations testées.

Exemple illustratif : prédire 0,980 USD lorsque le prix observé est 0,983 USD
donne une erreur de 0,003 USD, soit 0,3 centime de dollar par PT. Cet exemple
explique le calcul ; il n'ajoute pas une observation au benchmark.

À J7, les erreurs moyennes mesurées sont :

| Prévision | MAE en USD / PT | En centimes de dollar / PT |
| --- | ---: | ---: |
| Prix inchangé | 0,0031375 | 0,31375 |
| Chronos-2 | 0,00344195 | 0,34420 |
| TimesFM 2.5 | 0,00298906 | 0,29891 |
| Chronos-Bolt | 0,00426773 | 0,42677 |

Ces moyennes ne donnent ni l'erreur maximale possible ni la précision garantie
d'une future prévision. Elles ne sont pas directement une erreur en pourcentage
du prix, car les calculs portent sur des écarts en USD.

## « 13,2 % d'erreur en moins que Chronos-2 »

On compare les MAE à J7 :

`100 × (1 − MAE TimesFM / MAE Chronos-2)`

TimesFM réduit donc l'erreur moyenne d'environ 13,2 % relativement à Chronos-2
sur ce test. Cela ne signifie pas « 86,8 % de précision » ni « 13,2 % de gain
financier ».

## « 4,7 % d'erreur en moins que le prix inchangé »

La baseline prévoit que le prix des prochains jours sera égal au dernier prix
observé. C'est une référence simple mais exigeante pour des prix qui bougent peu.

À J7, TimesFM est légèrement meilleur que cette baseline : sa MAE est inférieure
d'environ 4,7 %. Dans les tableaux de comparaison, le gain est positif quand
le modèle fait mieux, négatif quand il fait moins bien.

## Pourquoi J7 plutôt que J1 ?

| Horizon TimesFM | Gain face au prix inchangé | Lecture |
| --- | ---: | --- |
| J1 | −26,0 % | Erreur moyenne 26,0 % plus élevée |
| J3 | +1,4 % | Très petite amélioration |
| J7 | +4,7 % | Meilleure amélioration dans ce test |

Ces résultats justifient d'explorer TimesFM à J7. Ils ne prouvent pas qu'il
gagnera sur chaque actif ou chaque nouvelle semaine. Sur le développement,
son gain à J7 face à la baseline est de 14,1 %, contre 4,7 % sur le test réutilisé.

## La médiane et l'intervalle q10–q90

La **médiane q50** est la prévision centrale du modèle. q10 et q90 sont ses
quantiles inférieur et supérieur. L'intervalle q10–q90 est nominalement un
intervalle de 80 % si les quantiles sont correctement calibrés.

Nous mesurons sa couverture réelle en comptant combien de prix observés tombent
dans l'intervalle. À J7, TimesFM couvre **4 observations sur 8**, soit **50 %**,
au lieu des 80 % nominaux. Ce petit test indique une calibration insuffisante ;
il ne permet pas d'estimer précisément la couverture future.

On ne peut donc pas affirmer à l'utilisateur qu'il a 80 % de chances que son
prix futur reste dans l'intervalle affiché.

## Combien de données et de tests ?

- 4 PT distincts : reUSD, sUSDS, apyUSD et USD3.
- 123, 148, 147 et 144 clôtures quotidiennes respectivement : 562 points au total,
  répartis entre quatre séries. Ce ne sont pas 562 jours pour un seul PT.
- 45 fenêtres de développement au total, avec au moins 45 jours de contexte.
- 8 fenêtres sur le test réutilisé : deux semaines par actif, soit 8 erreurs
  évaluées pour chaque horizon J1, J3 et J7.
- Les semaines concernent les mêmes dates entre actifs : les 8 résultats ne
  sont pas nécessairement indépendants.

Les poids des modèles n'ont pas été entraînés sur ces données. Le développement
sert ici à mesurer les prévisions historiques, pas à mettre à jour les poids.

## Ce que prouvent les vérifications de la démo

Les 12 vues correspondent à 4 actifs × 3 modes d'affichage. Leur vérification
contrôle les interactions et l'affichage des résultats. L'absence d'erreur
JavaScript et les tests de code montrent que le logiciel fonctionne dans le
périmètre vérifié ; ils ne valident pas la qualité financière des prévisions.

Les résultats complets, avec leurs décimales, sont dans
[la comparaison JSON](results/comparison.json) et les trois rapports de modèle.

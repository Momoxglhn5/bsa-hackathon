# Comparaison exploratoire de modèles préentraînés

Même jeu de quatre PT, mêmes contextes, aucune mise à jour des poids. Le test a déjà été consulté : ces scores servent à explorer, pas à valider.

Gain = réduction de MAE par rapport à la persistance. Positif = meilleur.

| Modèle | Phase | J1 | J3 | J7 | Couverture J7 q10–q90 |
| --- | --- | ---: | ---: | ---: | ---: |
| amazon/chronos-2 | Développement (45 fenêtres) | -34.7% | +19.0% | +8.0% | 84.4% |
| amazon/chronos-2 | Test réutilisé (8 fenêtres) | +36.3% | -1.0% | -9.7% | 37.5% |
| google/timesfm-2.5-200m-pytorch | Développement (45 fenêtres) | -25.6% | +6.1% | +14.1% | 75.6% |
| google/timesfm-2.5-200m-pytorch | Test réutilisé (8 fenêtres) | -26.0% | +1.4% | +4.7% | 50.0% |
| amazon/chronos-bolt-base | Développement (45 fenêtres) | -36.6% | +3.1% | -1.5% | 84.4% |
| amazon/chronos-bolt-base | Test réutilisé (8 fenêtres) | -90.5% | -1.2% | -36.0% | 87.5% |

Une validation future doit fixer le modèle avant de collecter un nouveau test.

Les quatre actifs sont distincts du PT-siUSD initial. Aucune décision financière ni ForecastBundle VALIDATED ne provient de cette comparaison.

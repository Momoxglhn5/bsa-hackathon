# Marilou : validation prospective et décision

Le module `ml.predictive` fournit un parcours hors ligne : figer un protocole,
émettre les prévisions avant leurs cibles, mesurer les résultats observés et
exporter un snapshot utilisable par le moteur de décision. Aucune inférence
n'est exécutée dans l'API. Les anciens benchmarks exploratoires ne sont pas
promus au statut validé.

## Validation par actif et par horizon

Le protocole fige la stratégie, le marché Pendle, le modèle et sa révision.
Le CLI utilise l'heure UTC réelle, sans option pour antidater une prévision.
Les fichiers sont créés sans écraser les précédents. Les prévisions enregistrent
les quantiles quotidiens, le dernier prix, la moyenne des sept derniers prix,
les preuves et le hash du protocole. Un changement de modèle ou de protocole
invalide les anciennes prévisions pour ce test.

L'évaluation utilise seulement les cibles observées et rejette les origines
hebdomadaires qui se chevauchent. Les horizons J1, J3 et J7 sont évalués
séparément, sans agréger les actifs. Politique par défaut, fixée avant le test :

- Au moins 12 fenêtres prospectives hebdomadaires terminées par horizon.
- MAE inférieure d'au moins 5 % à la persistance, et inférieure à la moyenne mobile.
- Couverture empirique q10–q90 comprise entre 70 % et 90 %.
- Série réelle continue, prix positifs, preuves, stratégie vérifiée et échéance compatible.

Ces seuils constituent une politique d'acceptation du prototype, pas une preuve
statistique universelle. Douze fenêtres ne garantissent pas la généralisation
et peuvent partager un régime de marché. Une couverture observée acceptable
ne signifie pas une probabilité garantie sur la prochaine prévision. Le module
mesure la calibration et refuse les intervalles hors politique ; il ne les
élargit pas après avoir consulté le test. Une modification exige un nouveau
protocole et de nouvelles prévisions.

Un export historique récupéré aujourd'hui ne permet pas de recréer des
prévisions prospectives d'hier. Les fichiers locaux et les dates ne sont pas
des attestations signées : vérifier les archives des fournisseurs, les éventuelles
révisions et les versions du runtime reste nécessaire. Le chevauchement avec
les données de préentraînement n'est pas établi.

## Projection financière conditionnelle

Pour chaque horizon accepté, et pour une position précise :

```text
projection nette USD = quantité PT × (médiane future USD/PT − spot actuel USD/PT)
                    − dette actuelle USD × APR annuel supposé × jours / 365
```

Le prix USD du token emprunté, l'APR et les quantités sont supposés constants.
L'APR est une entrée explicite, pas une prévision du modèle. L'appréciation du PT
inclut déjà son évolution prédite : aucun rendement PT supplémentaire n'est ajouté.
Les frais de sortie restent calculés par le moteur de clôture.

L'export exige des montants et prix USD récents, des preuves réelles présentes,
et une prévision dont la date de départ est alignée avec la décision à cinq
minutes près. Collecter/inférer autour de la clôture quotidienne UTC ou fournir
une série quotidienne alignée sur l'heure de décision ; une prévision vieille
de plusieurs heures ne devient pas artificiellement une prévision depuis maintenant.
La durée du bundle est limitée à cinq minutes et un hash lie la projection aux
montants et prix exacts. Un changement d'entrée produit `FORECAST_INPUTS_CHANGED`.

Le moteur applique les décotes proportionnelles explicites de collateral à sa
valeur projetée. Les pertes sans transformation connue produisent
`FORECAST_LOSS_MAPPING_UNAVAILABLE` et un repli au mode normal. Les pertes oracle
inconnues restent inconnues. Aucune probabilité d'incident, aucun prix oracle,
aucune faisabilité de sortie ne sont déduits de la prévision ML.

## Utilisation

Depuis la racine avec l'environnement du projet, toutes les commandes utilisent
`PYTHONPATH=backend .venv/bin/python -m ml.predictive`.

```bash
PYTHONPATH=backend .venv/bin/python -m ml.predictive freeze \
  --strategy-id STRATEGY_ID --entity-id pendle:1:MARKET_ADDRESS \
  --model timesfm --revision MODEL_COMMIT_SHA --output /tmp/protocol.json

PYTHONPATH=backend .venv/bin/python -m ml.predictive issue \
  --protocol /tmp/protocol.json --snapshot /tmp/current-snapshot.json \
  --series /tmp/pt-daily-series.json --output /tmp/issued/week-01.json

PYTHONPATH=backend .venv/bin/python -m ml.predictive evaluate \
  --protocol /tmp/protocol.json --snapshot /tmp/current-snapshot.json \
  --series /tmp/pt-daily-series.json --forecasts /tmp/issued \
  --output /tmp/validation.json

PYTHONPATH=backend .venv/bin/python -m ml.predictive export \
  --protocol /tmp/protocol.json --snapshot /tmp/current-snapshot.json \
  --series /tmp/pt-daily-series.json --forecasts /tmp/issued \
  --position-id POSITION_ID --annual-borrow-apr 0.05 \
  --output /tmp/predictive-snapshot.json
```

`series` est un `TimeseriesDTO` quotidien du PT exact ; `snapshot` est un
`AnalyticsSnapshot` contenant la stratégie, la position, les prix et les preuves.
L'inférence exige 45 observations continues comme les adaptateurs existants.
Les dépendances TimesFM restent optionnelles dans `ml/requirements-timesfm.txt`.
Le dossier `issued` ne doit contenir que les prévisions de ce protocole.

L'export recalcule la validation à partir des prévisions et observations ; il
n'accepte pas un rapport portant manuellement la mention `VALIDATED`. Il conserve
les quotes, relations, séries et autres prévisions du snapshot, ajoute la preuve
du rapport et remplace seulement les bundles de la position ciblée. Les horizons
qui échouent ne sont pas publiés. Le rapport est écrit à côté du snapshot dans
un fichier `.validation.json`.

Pour utiliser le résultat dans l'API :

```bash
ANALYTICS_SNAPSHOT=/tmp/predictive-snapshot.json \
  .venv/bin/python -m uvicorn app.main:app --app-dir backend --port 8000
```

Appeler ensuite les simulations puis `/api/v1/decisions` avec `mode=predictive`,
les mêmes identifiants et le même horizon. Sans bundle accepté, le mode normal
reste actif avec sa raison de repli. Sans plan de clôture ou pertes chiffrables,
un bundle accepté ne force aucun verdict STAY/EXIT.

## État de livraison

L'infrastructure est testée sur des journaux synthétiques, avec raccordement
au vrai moteur de décision, recalcul des décotes, péremption et invalidation
sur changement d'entrées. Cela ne valide pas TimesFM sur PT-siUSD aujourd'hui.
Il reste à collecter les prévisions prospectives et leurs observations futures.
Le test par défaut nécessite environ 12 semaines avec des origines hebdomadaires ;
un simple nouveau run CPU sur les anciens benchmarks ne remplace pas cette étape.

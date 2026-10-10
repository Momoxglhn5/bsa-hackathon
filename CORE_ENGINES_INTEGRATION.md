# Core Engines — intégration V1.1

Les quatre moteurs sont intégrés à l'application FastAPI Data existante. Ils lisent les DTO partagés, SQLite/Parquet ou un snapshot JSON, sans requête fournisseur ni transaction. Toutes les modifications Core restent locales tant qu'elles ne sont pas publiées explicitement.

## Lancement

Depuis la racine, Python 3.13 :

```bash
python3.13 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements-analytics.txt
backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Par défaut, Data et Core lisent `backend/data/domino.sqlite` et `backend/data/timeseries` (ou `DOMINO_DATA_DIR`). Les routes Data sont conservées. Le processus de refresh Data s'exécute séparément. Un magasin vide produit HTTP 503 pour l'analyse d'un marché ; le catalogue de scénarios reste disponible.

Pour la démo entièrement synthétique avec quote et carry hypothétiques :

```bash
ANALYTICS_SNAPSHOT=backend/app/fixtures/analytics_demo.json ANALYTICS_REPLAY=true \
  backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Dans un second terminal :

```bash
backend/.venv/bin/python backend/scripts/demo_analytics.py
backend/.venv/bin/python -m pytest -c backend/pytest.ini backend/app/tests -q
```

La documentation est disponible sur <http://127.0.0.1:8000/docs>. L'application accepte aussi directement le JSON Data `ui_mock_data_v1.json`, sans quote inventée. Aucun fichier `.env` n'est chargé automatiquement.

## Configuration

| Variable | Comportement |
| --- | --- |
| `DOMINO_DATA_DIR` | Répertoire Data existant ; défaut `backend/data` |
| `ANALYTICS_SNAPSHOT` | Snapshot JSON explicite ; absent → lecture des stores Data |
| `ANALYTICS_REPLAY` | Défaut true pour un snapshot explicite, false pour les stores ; évalue les dates au temps du snapshot en replay |
| `ANALYTICS_INPUTS` | Fichier JSON facultatif `AnalyticsInputs` pour les stores : quotes, observations Risk et bundles Forecast fournis par leurs producteurs |

En replay, les réponses portent `FROZEN_REPLAY`. Une donnée historique n'est jamais présentée comme quote live. Dans les stores, `data_mode=REAL` est le défaut des routes Core ; demander explicitement `?data_mode=DEMO` pour leurs fixtures. Un snapshot injecté sélectionne explicitement son propre dataset et son mode.

## Contrats et intégration

`AnalyticsSnapshot` conserve `MarketDTO`, `PositionDTO`, `StrategyDTO`, `EvidenceDTO`, `RelationDTO` et `TimeseriesDTO`. Les contrats Data restent la référence ; le conteneur ajoute uniquement les entrées analytiques :

| Entrée | Responsabilité |
| --- | --- |
| `quotes: QuoteContext[]` | Quote `ExitPriceDTO` de taille exacte, conversion USD, gas/frais, disponibilité et plan de remboursement |
| `risk_observations: RiskObservation[]` | Risque 0–1 pour un facteur défini, marché, date, méthode et preuves |
| `forecasts: ForecastBundle[]` | Projection de carry sur la position et l'horizon exacts, validation et référence de backtest |
| `additional_nodes` / `additional_edges` | Dépendances supplémentaires explicitement sourcées ou hypothétiques |

`analytics/provider.py` exporte les stores en snapshot immuable. Il lit les tables SQLite dans une transaction cohérente, sépare REAL/DEMO et calcule un identifiant à partir du contenu. Chaque changement crée un service distinct : une simulation calculée sur l'ancien dataset ne peut pas servir silencieusement à une décision sur le nouveau.

`ANALYTICS_INPUTS` est relu à chaque requête Core et peut être remplacé atomiquement par son producteur. Ses preuves doivent exister dans le magasin du même mode Data. Exemple de fichier sans données supplémentaires disponibles :

```json
{"quotes": [], "risk_observations": [], "forecasts": []}
```

Exporter les données disponibles sans modifier la base :

```bash
backend/.venv/bin/python backend/scripts/export_analytics_snapshot.py \
  --data-mode REAL --output /tmp/radar-real.json
```

Ajouter au snapshot une position fictive en **unités de tokens**, avec quantités choisies explicitement :

```bash
backend/.venv/bin/python backend/scripts/export_analytics_snapshot.py \
  --data-mode REAL --strategy-id strat_e76bee1f14994d77f99f42a7 \
  --collateral-amount 2000000 --borrow-amount 1200000 \
  --output /tmp/radar-real-with-simulated-fund.json
```

Ces quantités ne signifient pas automatiquement 2 M$ / 1,2 M$. Les valeurs USD exigent les vrais prix. `--inputs PATH` ajoute un fichier `AnalyticsInputs`; `--quotes PATH` accepte aussi un tableau de `QuoteContext`. L'export ne fabrique ni prix, ni preuve, ni profondeur de marché, ni quote.

## Graph Engine

Une arête `source → target` signifie « source dépend de target ». La propagation parcourt les prédécesseurs. Deux états de visite par nœud distinguent confirmé et potentiel ; un seul chemin représentatif par position évite l'énumération exponentielle.

Les relations Data sont traduites par `graph/resolver.py`. Les identifiants `contract:` des oracles/IRM rejoignent les nœuds analytiques correspondants. Les preuves RPC doivent correspondre à la chaîne, au bloc et au contrat lu. Un marché sans bloc ne peut pas confirmer une relation générée. Les relations Data explicites remplacent les relations générées équivalentes, y compris lorsqu'elles sont non résolues.

La jointure PT/Pendle utilise adresse et chaîne. `SY_YIELD_TOKEN` devient `USES_YIELD_TOKEN`, une dépendance de contrat ; `SY_ACCOUNTING_ASSET` devient `ACCOUNTED_IN`, informative et non obligatoire. `assetInfo` ne crée jamais une preuve `BACKED_BY`. `USES_YT` est également informatif. Les inconnues de résolution profonde restent affichées et le graphe reste `PARTIAL`.

Une position `SIMULATED` reste hypothétique même sur un marché vérifié. Les expositions confirmées et potentielles sont disjointes ; chaque position contribue une seule fois. Assiette capital : equity positive. Gross : collatéral + dette. Une equity négative reste visible dans la valorisation.

Un fallback sain ne bloque la propagation que si sa preuve RPC existe et date de moins de cinq minutes au temps évalué. Sa panne indirecte relance la propagation des arêtes précédemment bloquées. Un état inconnu conserve l'exposition potentielle. Le producteur est responsable de la justification métier de l'état du fallback ; le moteur ne simule pas le contrat du fallback.

## Current Risk Engine

Dix facteurs de poids fixe 0,1, dans cinq dimensions : utilisation / drawdown TVL ; source vérifiée / audit ; contrôle / timelock ; âge déploiement / version ; concentration / absence de fallback vérifié.

Le score est la moyenne des risques connus × 100. La complétude tient compte des dix facteurs. Publication seulement si couverture ≥ 50 % et au moins trois dimensions couvertes à 50 %. Les facteurs absents restent inconnus. L'utilisation Morpho, les deux indicateurs de dépendances et le drawdown de liquidité avec historique quotidien complet sont calculés automatiquement lorsque leurs entrées sont disponibles.

Les six facteurs sécurité/gouvernance/historique opérationnel consomment des `RiskObservation` sourcées ; le moteur ne prétend pas découvrir des audits ou administrateurs absents de Data. La concentration décrit le portefeuille représenté ; la santé LTV/HF de chaque position est affichée séparément.

Confiance heuristique : RPC 1, API officielle 0,8, dérivé 0,5, utilisateur 0,3, fixture 0. Qualité minimale des preuves et fraîcheur décroissant sur 24 h ; une preuve explicitement stale est plafonnée à 0,25. Ce nombre ne mesure pas la probabilité de sécurité.

Le drawdown exige 31 points couvrant 30 jours, sans intervalle > 36 h, avec montants USD et preuves utilisables. Les observations Parquet actuelles ne conservent pas les IDs de preuve par point : un historique exporté sans ces références reste insuffisant pour publier ce facteur. Un `TimeseriesDTO` sourcé dans un snapshot peut les fournir.

## Stress Engine

| Scénario | Calcul et limite |
| --- | --- |
| `ORACLE_MANIPULATION` | Choc du prix oracle en unités de dette ; recalcul LTV/HF/liquidation. Spot et equity économique inchangés ; perte d'exécution inconnue |
| `ORACLE_OUTAGE` | Exposition et capital bloqué ; aucune liquidation/perte automatique |
| `UNAUTHORIZED_MINTING` | Perte = collatéral directement concerné × haircut explicite. Quantité émise ou impairment seul ne définit pas un prix |
| `CROSS_CHAIN_FAILURE` | Bridge absent → `NOT_APPLICABLE` ; graphe hypothétique séparé possible. Capital bloqué distinct de la perte |

Pour chiffrer un haircut cross-chain, fournir à la fois `assumed_token_haircut_ratio` et `haircut_applies_to_affected_collateral=true`. Ce second paramètre est une hypothèse explicite de transmission au collatéral affecté ; le moteur ne la déduit ni du backing ni de la durée de panne. Sans cette hypothèse, perte inconnue. Aucun haircut ne réécrit le prix oracle.

## Financial Decision Engine

```text
EquityNow = collateralUSD - debtUSD
ExpectedLoss = somme(probabilité_sur_H × perte_de_la_position)
StayValue = EquityNow + NetCarrySurH - ExpectedLoss
ExitValue = EquityNow - ExitCost
BreakEven(p_i) = (NetCarrySurH + ExitCost - autres_pertes_attendues) / perte_i
```

Les scénarios inclus doivent correspondre à la même stratégie, position et horizon. Plusieurs scénarios exigent une déclaration d'exclusivité et une somme de probabilités ≤ 1. Risque non chiffrable choisi → comparaison insuffisante, même avec probabilité 0. Une exclusion volontaire apparaît dans la réponse.

Normal fonctionne sans ML. Le carry est une hypothèse utilisateur en USD sur l'horizon, avec explication obligatoire. Le score Risk reste explicatif et ne devient jamais une probabilité. Les valeurs sont conditionnelles ; le moteur ne transforme pas un APY en gain garanti.

Une sortie exige quantité exacte, conversion USD, route, remboursement possible, gas et frais connus. Les prix de référence, oracle et conversion doivent avoir au plus cinq minutes, ne pas venir du futur et porter des preuves utilisables. La validité du quote est contrôlée séparément. Le taux Pendle marginal pour 1 PT ne devient pas une quote pour 1–5 M de PT. La dette et les frais sont comptés une fois. Une sortie vers un troisième token sans conversion couverte reste indisponible.

STAY et EXIT sont les seules actions. Comparaison impossible ou égalité → `verdict=null`. Seuil hors domaine → `NO_REVERSAL_IN_FEASIBLE_RANGE`, sans clamp silencieux. Le domaine multi-scénario est `0..1-somme(autres probabilités)`.

## Forecast Cache facultatif

`ForecastBundle` identifie stratégie, position, horizon, dates, validation, méthodologie, backtest et preuves. Seule la transformation explicite `NET_CARRY_USD_OVER_HORIZON` est acceptée. Un bundle valide remplace le carry dans la comparaison ; il ne change ni prix oracle, ni probabilités, ni pertes des simulations.

Bundle absent, périmé/futur ou non validé : repli Normal avec `FORECAST_UNAVAILABLE`, `STALE` ou `NOT_VALIDATED`. Les valeurs et le verdict restent identiques à Normal avec les mêmes entrées. Aucun modèle ML ni entraînement n'est inclus ; le producteur ML reste responsable de la validation réelle du bundle.

## API

| Méthode | Route | Résultat |
| --- | --- | --- |
| GET | `/api/v1/graph/{strategy_id}` | Dépendances, preuves et inconnues |
| GET | `/api/v1/risk/{strategy_id}` | Facteurs, score si publiable, couverture, confiance et santé des positions |
| GET | `/api/v1/scenarios` | Catalogue statique, disponible sans stockage initialisé |
| POST | `/api/v1/simulations` | Impacts, expositions, effets par position et hypothèses |
| GET | `/api/v1/incident-frequencies` | `INSUFFICIENT_DATA` tant qu'aucun historique calibré n'est fourni |
| POST | `/api/v1/decisions` | Valeurs conditionnelles, verdict et seuil de bascule |

Enveloppe Core : `schema_version`, `status`, `as_of`, `data`, `provenance`, `missing_fields`, `warnings`. Decimal → chaîne, inconnu → null. `missing_fields` énumère les champs directs nuls ; la complétude financière détaillée est portée par les résultats et leurs statuts, pas par cette liste seule.

HTTP 404 : identifiant/simulation absent. HTTP 422 : entrée incohérente. HTTP 503 : stockage/dataset indisponible. HTTP 200 avec statut métier explicite : quote absente, sortie impossible, données insuffisantes ou scénario non applicable.

Caches bornés : 256 simulations par snapshot, huit snapshots actifs par processus. Après refresh, éviction ou redémarrage, rejouer la simulation. Utiliser un seul worker ; aucune persistance de simulations multi-worker n'est revendiquée.

## Vérification et limites de livraison

Les tests couvrent Data et Core ensemble, les deux régressions bloc/fraîcheur, les contrats Data, les modes REAL/DEMO, les cycles et doubles comptes, le changement de snapshot, les scénarios, les quotes de taille différente, la décision et les états du Forecast Cache. Les fixtures des tests sont synthétiques, y compris celles qui imitent une preuve RPC.

Le manifeste livré sur `main` est désormais `VERIFIED_ACTIVE`, avec bloc et références de vérification. Les fichiers SQLite/Parquet réels ne sont pas versionnés ; ils doivent être collectés ou transférés séparément. Le rapport de validation Data signale encore des prix USD manquants. Le Core fonctionne avec ces inconnues, mais une décision financière réelle nécessite ces prix, un quote de taille complète et un plan de fermeture renseigné.

Le frontend, les collectes d'audits/gouvernance, les fréquences calibrées, l'entraînement ML et l'exécution de transactions restent hors de cette livraison Core. L'avertissement Starlette/HTTPX du client de test est connu et n'empêche pas les tests.

## Résultats de validation de cette livraison

- Suite complète Data + Core : **216 tests réussis**, un avertissement de dépréciation Starlette/HTTPX.
- Serveur Uvicorn réel sur loopback et stores temporaires synthétiques : routes Data, OpenAPI, Graph → Stress → Decision vérifiées ; STAY à 2 %, EXIT à 10 %, seuil 5,275 %.
- Export CLI d'un snapshot avec position simulée et quote fournie : exécuté et JSON revalidé ; serveur arrêté après vérification.
- Compilation Python, validation du workflow avec actionlint et lint Markdown : réussis.
- Scan Gitleaks : code de sortie 1, trois faux positifs dans les fichiers Data déjà livrés sur main (deux adresses publiques du manifeste et la signature d'une fonction de test). Aucun ajout Core signalé ; aucune règle désactivée.

Ces vérifications ne constituent pas un test live des fournisseurs ni une preuve de liquidité pour un montant réel.

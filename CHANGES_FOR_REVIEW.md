# Retour de correction — bug du provider analytics

Suite à ta demande : corriger **uniquement** le bug préexistant du provider qui faisait planter le snapshot réel. C'est fait, testé et poussé.

## Résumé

| Point | Détail |
| --- | --- |
| Branche | `feat/monte-carlo-pr1` (pas `main`, aucune pull request créée) |
| Commit | `380de5f` — *Skip stored strategies whose Morpho market is absent* |
| Fichiers | `backend/app/analytics/provider.py` (+5 / −1), `backend/app/tests/test_analytics_integration.py` (test + imports) |
| Tests | `python -m pytest app/tests -q` → **292 réussis**, 0 échec (1 avertissement Starlette déjà connu) |
| Vérification réelle | Store réel avec 44 stratégies : le snapshot se construit, la stratégie `VERIFIED_ACTIVE` est conservée, les 43 orphelines sont ignorées |

Le travail PR2 décrit dans le document de revue précédent est dans le commit précédent, `62387ec`. Il n'a pas été modifié par cette correction.

## Cause exacte

- `refresh_metadata` enregistre **toutes** les stratégies candidates trouvées (44).
- `refresh_current` n'enregistre que les marchés de la stratégie cible (2).
- `snapshot_from_store` reprenait les 44 stratégies telles quelles.
- Le validateur d'`AnalyticsSnapshot` exige que chaque stratégie ait son marché Morpho dans le snapshot. Il levait donc `strategy collateral must match the Morpho market` : toute l'API analytics (graphe, risque, Monte Carlo) échouait sur un store réel.

## Correction

Juste après le chargement, le provider ne garde que les stratégies dont la clé `morpho:<chain_id>:<morpho_market_id>` correspond à un `MarketDTO` Morpho chargé.

- Jointure par chaîne et identifiant de marché, jamais par symbole.
- Aucun marché créé, aucune fixture, aucun DTO public ni Monte Carlo modifiés.
- Les autres validations restent actives : une stratégie dont le marché est présent mais dont le PT ou le loan token ne correspond pas fait toujours échouer le snapshot.
- S'il ne reste aucune stratégie, le 503 existant est renvoyé.
- Le filtre s'applique à toute stratégie dont le marché est absent, quel que soit son statut, pas seulement aux `CANDIDATE`. Sur le store réel, seules des `CANDIDATE` étaient concernées. Dis-moi si tu préfères restreindre le filtre aux `CANDIDATE` et laisser échouer une stratégie vérifiée sans marché.

## Test ajouté

`test_store_snapshot_skips_strategies_without_stored_morpho_market` :

- enregistre un marché Morpho valide et sa stratégie (fixture synthétique) ;
- ajoute une stratégie `CANDIDATE` qui référence un marché absent ;
- vérifie que le store contient bien 2 stratégies, que le snapshot se construit, et qu'il ne garde que la stratégie valide.

## Diff complet

```diff
diff --git a/backend/app/analytics/provider.py b/backend/app/analytics/provider.py
index 503ae2c..e07df01 100644
--- a/backend/app/analytics/provider.py
+++ b/backend/app/analytics/provider.py
@@ -73,7 +73,11 @@ def _oracle_series(store, parquet, market, data_mode):
 def snapshot_from_store(store, parquet, data_mode=DataMode.REAL, *, inputs=None):
     inputs = inputs or AnalyticsInputs()
     markets = store.list_markets(data_mode=data_mode)
-    strategies = store.list_strategies(data_mode=data_mode)
+    # Metadata refreshes store every candidate, but only analysed markets are stored:
+    # keep strategies whose Morpho market is present (joined by chain and market id).
+    morpho_keys = {m.market_key for m in markets if m.morpho is not None}
+    strategies = [s for s in store.list_strategies(data_mode=data_mode)
+                  if f"morpho:{s.chain_id}:{s.morpho_market_id}" in morpho_keys]
     if not markets or not strategies:
         raise HTTPException(503, f"No {data_mode.value} analytics dataset stored; run the Data pipeline or select an explicit offline snapshot")
     positions = store.list_positions(data_mode)
diff --git a/backend/app/tests/test_analytics_integration.py b/backend/app/tests/test_analytics_integration.py
index 7453769..b79c6ce 100644
--- a/backend/app/tests/test_analytics_integration.py
+++ b/backend/app/tests/test_analytics_integration.py
@@ -14,7 +14,8 @@ from app.analytics.valuation import value_position
 from app.decision.forecast import ForecastBundle
 from app.graph.builder import build_graph
 from app.main import DEFAULT_SNAPSHOT, create_app
-from app.models.evidence import DataMode, RelationStatus, RelationType, make_relation
+from app.models.evidence import DataMode, DataStatus, RelationStatus, RelationType, make_relation
+from app.models.strategy import StrategyDTO, StrategyStatus, make_strategy_id
 from app.models.timeseries import TimeseriesDTO
 from app.risk.scoring import current_risk
 from app.storage.parquet_store import ParquetStore
@@ -40,6 +41,22 @@ def seed(snapshot, db):
         store.save_relations(snapshot.relations)
 
 
+def test_store_snapshot_skips_strategies_without_stored_morpho_market(snapshot, tmp_path):
+    # Regression: metadata refreshes store candidates whose Morpho market is never stored.
+    db = tmp_path / 'db.sqlite'
+    seed(snapshot, db)
+    kept = snapshot.strategies[0]
+    absent_market = '0x' + 'f0' * 32
+    orphan = kept.model_copy(update={
+        'morpho_market_id': absent_market, 'strategy_id': make_strategy_id(1, absent_market, kept.pt.address),
+        'status': StrategyStatus.CANDIDATE, 'data_status': DataStatus.UNVERIFIED, 'status_reason': 'synthetic orphan'})
+    with SqliteStore(db) as store:
+        store.save_strategies([StrategyDTO.model_validate(orphan.model_dump())])
+        assert len(store.list_strategies(data_mode=DataMode.DEMO)) == 2
+        exported = snapshot_from_store(store, ParquetStore(tmp_path / 'series'), DataMode.DEMO)
+    assert [s.strategy_id for s in exported.strategies] == [kept.strategy_id]
+
+
 def test_missing_market_block_never_confirms(snapshot):
     data = real_shaped_market(snapshot)
     data['markets'][0]['block_number'] = None
```

## Questions

1. Le filtre devrait-il être limité aux stratégies `CANDIDATE` ?
2. Faut-il signaler les stratégies ignorées dans le snapshot (compteur ou avertissement) plutôt que de les exclure silencieusement ?

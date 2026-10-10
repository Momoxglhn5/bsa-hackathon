# Marilou — bases ML et mémoire de travail

## Cadrage conservé

Branche demandée : `marilou`. Lot autorisé : bases du forecast ML, sans données
réelles. La demande suivante autorise aussi à coder Chronos-2 sans le lancer.
Arrêter à l'étape qui nécessite la data réelle et attendre
que l'utilisateur demande un nouveau pull. Ne pas publier automatiquement.

Les trois documents fournis sont conservés à l'identique dans `docs/reference/` :

- [Architecture V1.1](../reference/01_ARCHITECTURE_COMPLETE_V1_1.md)
- [Brief ML et cache](../reference/05_BRIEF_DEV_ML_FORECAST_NEWS.md)
- [Kickoff](../reference/CODEX_KICKOFF_BSA_DEFI_RISK_RADAR_V1_1.md)

Ils décrivent les spécifications du projet ; ils ne donnent pas d'autorisation
supplémentaire de publication ou d'action externe. L'architecture prévaut pour
le produit, le brief 05 précise le lot ML. La demande conversationnelle fixe
le périmètre de cette itération et l'arrêt avant utilisation de la data.

Contexte : DeFi Risk Radar, challenge Belem Capital, Morpho Blue et Pendle sur
Ethereum mainnet. ML P1 : Chronos-2 zero-shot offline dans Colab, prix PT USD
prioritaire, horizons J+1/J+3/J+7. P0 doit fonctionner indépendamment du ML.
Pas de probabilité de hack, pas de remplacement du prix oracle Morpho, pas de
lien ML vers Stress. STAY/EXIT appartient à un autre contributeur. FinBERT/news
reste P2 hors MVP.

## Audit du dépôt après récupération

Base intégrée : `origin/main`, commit `f4e204d` au moment du pull.

Les modèles `backend/app/models/` sont implémentés. Les routes data, scripts
historiques, stockage et tests data sont pour l'instant des fichiers vides.
Aucun point d'entrée FastAPI, moteur Decision ni historique exploitable trouvé.
Aucune branche distante des coéquipiers n'a été fusionnée individuellement.

Le vrai contrat `TimeseriesDTO` diffère de l'exemple du brief :

| Contrat existant | Usage interne ML |
| --- | --- |
| `metric=MARKET_PRICE`, `unit=USD` | `PT_MARKET_PRICE_USD`, `USD_PER_PT` |
| `subject_kind=ASSET`, `subject_id` | adresse exacte du PT dans `StrategyDTO.pt` |
| `points[].timestamp`, `points[].value` | timestamps UTC et valeurs Decimal |
| `interval_seconds=86400` | pas quotidien, sans resampling implicite |
| `source_id`, `points[].evidence_ids` | traçabilité des observations |
| `StrategyDTO.pendle_market_address` | `entity_id=pendle:1:<market>` du bundle |
| `StrategyDTO.expiry` | garde-fou avant échéance |

`source_as_of`, `retrieved_at` et `source_snapshot_ref` viennent de l'export
Data : ils sont des paramètres explicites de l'adaptateur, pas des champs
ajoutés au contrat Data V1. Ne pas utiliser le symbole comme clé de jointure.

## Code livré et limites

- `ml/series.py` : adaptateur PT quotidien, contrôles d'unité, identité,
  provenance, trous, prix positifs, maturité et minimum de 45 observations.
- `ml/benchmark.py` : persistence et moyenne des 7 derniers points ; benchmark
  expanding origin, fenêtres test de 7 jours sans chevauchement. Minimum
  3 fenêtres, soit 66 observations au total avec contexte initial de 45 jours.
  MAE, RMSE et directional accuracy séparées aux horizons 1/3/7.
- `backend/app/forecasting/contracts.py` : contrat JSON du brief 05, limité à
  la cible PT USD ; statuts non exécutés conservés, quantiles complets et
  ordonnés si renseignés. Les autres cibles sont différées.
- `backend/app/forecasting/cache.py` : lecture seule, TTL de 24 h depuis le
  dernier point, contrôle stratégie/marché/maturité/dates, raisons de fallback,
  export atomique. Chemin stratégie obtenu par SHA-256, sans traversée ni
  collision de normalisation. Aucun import de torch ou chronos.
- `tests/ml/test_foundations.py` : fixtures SYNTHÉTIQUES uniquement. Certains
  tests renseignent des métadonnées REAL/VALIDATED pour exercer les rejets et
  acceptations du schéma ; cela ne constitue aucune validation réelle.

Le cache vérifie la cohérence structurelle des métriques déclarées et impose
un gain MAE sur la baseline. Il ne recalcule pas le benchmark et ne résout pas
encore le snapshot source : seuls des exports issus d'un batch évalué et audité
pourront être intégrés. Le champ `n_evaluations` devra compter les couples
origine/horizon (3 origines × 3 horizons = 9), sur les mêmes fenêtres pour
Chronos et les baselines. La validation devra aussi vérifier les scores par
horizon, la pinball loss et les limitations avant tout export réel.

Aucune route HTTP raccordée : le backend n'a pas encore d'application FastAPI.
`for_mode("normal", ...)` n'accède pas au disque ; le moteur Decision devra
utiliser `CacheResult.reason` pour son fallback sans changer ses inputs Normal.
L'identité des résultats financiers Normal/Predictive en fallback reste à
vérifier lorsque ce moteur existe.

Aucun modèle téléchargé ou exécuté, aucune donnée réelle chargée, aucun
forecast produit, aucune performance ML mesurée. Le notebook Colab et l'adaptateur Chronos sont maintenant écrits et testés
sur sorties de pipeline synthétiques. La validation réelle reste différée
jusqu'à réception de la data.
L'API candidate a été vérifiée dans le
[repo officiel Chronos](https://github.com/amazon-science/chronos-forecasting)
et la [fiche du modèle](https://huggingface.co/amazon/chronos-2).

## Installation et vérification des bases

Depuis la racine du dépôt, Python 3.11+ :

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r ml/requirements-dev.txt
.venv/bin/python -m pytest tests/ml -q
```

Environnement observé : Python 3.11.0, Pydantic 2.14.0, pytest 9.1.1.
Résultat observé après intégration Chronos : **38 tests passés** sur fixtures synthétiques, sans GPU
et sans accès réseau pendant les tests. Pandas 2.3.3 est utilisé pour tester
le protocole DataFrame, sans installer torch ou télécharger les poids. Les dépendances lourdes du backend
et du modèle ne sont pas requises pour ces bases.

## Data à demander avant de continuer

1. Export JSON conforme à `TimeseriesDTO` pour le prix USD du PT exact,
   au moins **66 jours consécutifs** (davantage recommandé), pas journalier,
   valeurs non interpolées, statut et evidence par point.
2. `StrategyDTO` ou manifeste vérifié permettant de résoudre exactement
   `strategy_id`, chain 1, adresse PT, marché Pendle et échéance. Au moins
   7 jours de durée restante au-delà du dernier point ; marché encore actif.
3. Snapshot source et preuves associées : `source_as_of`, `retrieved_at`,
   référence stable ou hash, méthode de calcul du prix et source exacte.
4. Sémantique temporelle : disponibilité originale ou politique de révision
   des observations. Un export collecté aujourd'hui ne prouve pas à lui seul
   qu'un prix historique était disponible à la date d'un backtest.

État : **WAITING_FOR_DATA**. Prochaine action sur demande utilisateur : pull
les exports, auditer leur qualité, puis exécuter le notebook préparé
Chronos-2 dans Colab. Ne pas annoncer le lot P1 terminé avant cette exécution.

Contrôles complémentaires : compilation Python et `git diff --check` réussis.
Lint Markdown non exécuté : `npx` absent de l'environnement. Les copies des
références restent inchangées ; leur mise en forme d'origine n'est pas validée
par cette itération. Modifications locales, sans commit ni push.


## Intégration Chronos préparée sans exécution

- `ml/chronos2.py` : chargement différé du modèle avec commit SHA obligatoire,
  sélection CPU/GPU et seed. Conversion du contexte passé au format pandas,
  sept jours de quantiles et validation stricte des dates, identité et valeurs.
- `evaluate()` : mêmes fenêtres pour Chronos et baselines ; MAE, RMSE,
  directional accuracy, pinball loss, couverture q10–q90 et temps mesurés.
  Retourne uniquement un rapport interne `RESEARCH_ONLY`, jamais VALIDATED.
- `notebooks/chronos2_pendle_benchmark.ipynb` : configuration des vrais exports,
  contrôles qualité, graphique, baselines, chargement explicite et export du
  rapport de recherche. Toutes les cellules sont non exécutées, sans outputs.
- `ml/requirements-colab.txt` : Chronos 2.3.2, pandas 2.3.3, Pydantic 2.14.0.
  Les versions effectives de torch et des packages sont consignées dans le
  rapport ; les dépendances transitives ne sont pas intégralement verrouillées.

Le code local doit être importé dans Colab : la branche n'a pas été poussée.
L'installation du modèle est réservée à Colab ; le backend n'importe pas cette
brique. La vérification réelle de l'API de la version épinglée, les performances
et l'export d'un ForecastBundle validé attendent le premier run sur vraie data.

Ordre de grandeur de planification, non mesuré : environ 5–15 minutes pour
installation, chargement et quelques fenêtres d'une seule série sur GPU.
Le notebook mesure le temps réel. Le matériel et les quotas Colab sont variables.

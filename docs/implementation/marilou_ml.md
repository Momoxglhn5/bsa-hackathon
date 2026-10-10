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

## Collecte réelle du 10 octobre 2026

Sur demande utilisateur, exécution réelle de :

```bash
cd backend
../.venv/bin/python scripts/backfill_histories.py --days 90 --time-frame day
cd ..
.venv/bin/python -m ml.collect_pt_history --days 90
PYTHONPATH=backend .venv/bin/python -m pytest tests/ml backend/app/tests/test_parquet_store.py backend/app/tests/test_backfill.py -q
```

Résultats : **63 tests passés**. Dataset collecté en lecture seule depuis les
API officielles et sauvegardé dans `backend/data/` (ignoré par Git) :

- Pendle `PT_IMPLIED_APY` et `UNDERLYING_APY` : 32 points par série,
  du 9 septembre au 10 octobre 2026, sans trous signalés.
- Morpho `TOTAL_SUPPLY_ASSETS`, `TOTAL_BORROW_ASSETS`, `UTILIZATION`,
  `SUPPLY_APY`, `BORROW_APY` : 10 points par série, du 1er au 10 octobre 2026.
- Prix PT USD : 31 clôtures complètes, aucune journée manquante ; bougies
  du 9 septembre au 9 octobre, disponibles en fin de journée entre le
  10 septembre et le 10 octobre UTC. Une bougie incomplète exclue.

Collecteur ML ajouté : `ml/collect_pt_history.py`. Endpoint officiel
`/v4/1/prices/{pt_address}/ohlcv`, `currency=USD`, `timeFrame=day`.
Référence : [documentation API Pendle](https://api-v2.pendle.finance/core/docs).
Le timestamp d'observation représente la disponibilité de la clôture (début
bougie + 24 h), afin de ne pas utiliser la clôture avant la fin du jour.
Les prix ont la précision fournie par l'API, conservée comme texte Decimal.
Aucune interpolation ajoutée ; les prix de jours sans volume déclaré restent
à auditer pour déterminer la méthode du fournisseur.

Export prix et preuves :
`backend/data/ml/pt_usd/20261010T165506Z/` contient `raw_ohlcv.json`,
`series.json`, `evidence.json`, `strategy_manifest.json`, `collection_report.json`.
La série prix est également enregistrée sous
`backend/data/timeseries/real/MARKET_PRICE/asset_1_0x76543ac61ba84c6feeee76c6a918127bf0ca5fd1.parquet`.
Les preuves des requêtes sont enregistrées dans le SQLite existant.

Le manifeste de stratégie reprend les vérifications RPC poussées par l'équipe ;
il n'a pas été revérifié par ce collecteur. La durée de 90 jours demandée ne
crée pas de mesures avant les premières observations retournées par la source.

État actuel : **INSUFFICIENT_HISTORY** (31 clôtures, minimum technique 45,
minimum de notre benchmark 66). Aucun run Chronos, aucun forecast validé.
Avant de continuer : trouver une source plus longue pour ce PT exact ou faire
valider un marché distinct possédant un historique suffisant ; ne pas mélanger
les historiques de PT ou d'échéances différents.

## Recherche des jours antérieurs — 10 octobre 2026

Demande : trouver les jours manquants pour le **même PT**. Résultat : aucun
nouveau point quotidien accepté. Conserver les 31 clôtures déjà collectées.

Vérifications réellement effectuées :

- Pendle OHLCV quotidien sur un an, puis requête ciblée du 5 janvier au
  8 septembre inclus : **0 point** sur cette période antérieure.
- Pendle OHLCV hebdomadaire : 5 bougies ; la première étiquette commence au
  7 septembre, mais cela n'est pas une preuve de prix quotidiens avant le
  9 septembre. Une bougie hebdomadaire n'est pas découpée artificiellement.
- DeFiLlama : aucun prix retourné aux dates 1er août, 20 août, 1er septembre
  et 8 septembre. Son endpoint `prices/first` situe le premier prix enregistré
  le **9 septembre 2026**. La requête chart ne fournit pas les jours manquants.
- Blockscout associe le PT à une transaction de création du 5 janvier 2026.
  Le RPC public confirme le reçu et les logs PT de cette transaction. Cela
  prouve l'ancienneté du contrat, sans fournir des prix USD pour les jours
  manquants. Ne pas conclure que le marché a forcément été créé en septembre.
- La lecture d'état historique via Publicnode est refusée sans token archive.
  L'accès aux pages Etherscan a échoué. Aucun accès payant activé.

Les réponses brutes, URLs, heures de collecte et hashes sont conservés dans
`backend/data/ml/history_search_20261010/`, avec `search_report.json`.
Les données DeFiLlama restent à part : leur méthode et leurs timestamps
irréguliers ne sont pas interchangeables avec les clôtures quotidiennes Pendle.

Le reste des jours n'a donc pas été trouvé dans les sources publiques testées.
Un accès à une autre archive de prix ou une reconstruction historique avec
prix USD sous-jacent sourcé pourrait être étudié ; cela constituerait une
nouvelle méthode à valider, pas des clôtures Pendle déjà observées.
Aucune baisse des seuils 45/66, aucun modèle exécuté, aucun changement de marché.

## Recherche Morpho Blue — 10 octobre 2026

Vérification supplémentaire demandée par l'utilisateur, via la documentation
Morpho et des requêtes réellement exécutées sur `https://api.morpho.org/graphql`.

- `assetByAddress` sur le PT exact, `historicalPriceUsd` quotidien demandé du
  5 janvier au 10 octobre : **17 observations**, du **24 septembre au 10 octobre**.
- Ce sont des prix USD de l'actif, pas des prix oracle Morpho en unités de dette.
  Les observations restent séparées des clôtures Pendle et n'ajoutent aucune
  date antérieure à l'historique Pendle déjà collecté.
- REST `/v0/blue/markets/1:<id>/state/history?lookback=inception` : 11 points,
  dont le point courant ; la collecte GraphQL normalisée conserve 10 points
  quotidiens. REST APY inception : liste vide. Les taux GraphQL collectés
  précédemment restent disponibles.
- `marketById.creationTimestamp` situe la création du marché Morpho actuel au
  **30 septembre 2026** (bloc 26090437). La création du PT est distincte de
  la création de ce marché d'emprunt.

Brut, requêtes, timestamps de collecte et hashes enregistrés dans
`backend/data/ml/morpho_history_search_20261010/`. Un export JSON de recherche
séparé contient les 17 prix ; liaison des preuves par point et audit de la
méthode du fournisseur restent nécessaires avant consommation par Marilou.

Résultat : **aucun jour supplémentaire antérieur trouvé**. Historique PT
quotidien retenu toujours 31 clôtures Pendle ; aucun run de modèle.

## RPC Alchemy archive — 10 octobre 2026

À la demande de l'utilisateur, endpoint Ethereum Mainnet de son application
Alchemy existante récupéré dans Safari et enregistré dans `backend/.env`,
ignoré par Git, permissions `600`. Aucun changement nécessaire à la pipeline :
`EthereumRpcClient` charge déjà cette variable. Aucun accès payant activé.
La clé et l'URL complète ne figurent pas dans les preuves ni dans ce document.

Vérifications réelles : chain ID `1`, mode configuration `env`, lectures du
PT `decimals() = 18` réussies au bloc `24167000` (5 janvier) et `25700000`
(7 août). L'accès archive fonctionne. Le marché sélectionné `0xd44f…c408`
renvoie des données non décodables à ces blocs ; ces échecs ne sont pas
transformés en observations manquantes à zéro.

Le reçu de création du PT permet d'identifier un autre pool :
`0x4fada915e1deb7418077a90dd7a5c225f6eb4841`. `readTokens()` confirme le même
PT, SY, YT et la même échéance au bloc de création `24166901`. Les lectures
`readState(address(0))` du 5 janvier, 7 août et 10 octobre ont toutes réussi.
Réserves PT/SY/LP et `lastLnImpliedRate` sont exactement identiques aux trois
instantanés. Cela ne prouve pas l'absence de toute transaction intermédiaire,
mais ne fournit pas un historique de prix USD ni une preuve de liquidité utile.
L'API Pendle refuse l'historique de cet ancien pool : HTTP `404`, message
`market is unsafe`. La cause de cette classification n'est pas établie.

Les preuves RPC, reçu et instantanés restent des données de recherche séparées
sous `backend/data/ml/alchemy_archive_20261010/`. Aucun remplacement du marché
actuel, aucun ajout à la série ML, aucun run Chronos. L'accès archive débloqué
ne résout donc pas à lui seul l'insuffisance de l'historique de prix USD.

Méthodes contractuelles vérifiées dans la
[documentation officielle Pendle](https://docs.pendle.finance/pendle-v2-dev/Contracts/PendleMarket).

## Recherche pour atteindre 66 jours — 10 octobre 2026

Nouvel audit avec le RPC archive Alchemy configuré, sans exécution de modèle.
Pour 66 clôtures consécutives au 10 octobre, il manque les 35 bougies du
5 août au 8 septembre inclus, précédant les 31 bougies Pendle disponibles.

- Recherche binaire du premier bloc contenant le bytecode du marché sélectionné :
  bloc `25940362`, **9 septembre 2026 à 14:18:35 UTC**. Le bloc précédent
  `25940361` ne contient aucun bytecode pour cette adresse. Le marché actuel
  n'existait donc pas durant la période manquante.
- Audit Alchemy `alchemy_getAssetTransfers` du bloc de création du PT au
  `25936089` (8 septembre à 23:59:59 UTC), pagination terminée : seulement
  **4 transferts**, tous le **5 janvier**, lors de deux transactions d'amorçage.
  Aucun transfert indexé pendant les 35 jours manquants. Cela ne constitue pas
  une preuve indépendante de l'absence de tout éventuel échange OTC.
- Offre totale vérifiée par `totalSupply()` au 7 août et au 8 septembre :
  seulement **0.114951698010407242 PT**. Elle dépasse 6.7 millions le 10 octobre.
- Deux pools anciens utilisent le même PT/SY/YT/échéance. Le premier avait déjà
  été refusé par Pendle (`market is unsafe`) ; le second est
  `0xd436e299f64ccd7f8d0aa54a93070d22241278af`, également vérifié sur chaîne
  et refusé par Pendle : HTTP `404`, `market is unsafe`.
- Alchemy Historical Token Prices, adresse exacte sur Ethereum, quotidien
  du 5 août au 10 octobre : HTTP `400`, **Token not found**.
- GeckoTerminal, pools du token exact sur Ethereum : HTTP `200`, **0 pool**.

Résultat : **aucun prix USD quotidien supplémentaire trouvé**, 31 clôtures
retenues et 35 manquantes. Un état initial de pool, un taux implicite figé ou
une valorisation théorique ne sont pas des clôtures USD observées. Ne pas les
utiliser pour compléter artificiellement le benchmark. Le seuil 66 décrit le
protocole de benchmark retenu, pas une exigence intrinsèque de Chronos-2.

Rapport, requêtes, réponses brutes, hashes, preuves RPC et dates manquantes :
`backend/data/ml/history_completion_20261010/`. Les fichiers sont ignorés par
Git et contrôlés pour ne pas contenir la clé RPC.

Documentation des nouvelles sources :
[Alchemy Transfers](https://www.alchemy.com/docs/data/transfers-api/transfers-endpoints/alchemy-get-asset-transfers),
[Alchemy Historical Prices](https://www.alchemy.com/docs/data/prices-api/prices-api-endpoints/prices-api-endpoints/get-historical-token-prices),
[GeckoTerminal](https://api.geckoterminal.com/docs/index.html).

## Premier run Chronos-2 réel — 10 octobre 2026

Exécution locale CPU terminée à la demande de l'utilisateur, sur les **31
clôtures Pendle** collectées. Poids préentraînés utilisés sans entraînement ni
fine-tuning (`weights_updated=false`), conformément à l'approche zero-shot.

Mode exploratoire explicite ajouté à la préparation, à l'inférence et au
benchmark. Il autorise au minimum 14 observations sans désactiver les contrôles
sur l'identité, les preuves, les unités, les gaps ou l'échéance. Le mode standard
conserve 45 jours de contexte et trois fenêtres indépendantes. Le run court
utilise deux fenêtres de sept jours avec 17 puis 24 jours de contexte ; les
baselines voient exactement les mêmes préfixes, sans observations futures.
Prévision finale à partir des 31 observations.

Modèle : `amazon/chronos-2`, révision figée
`29ec3766d36d6f73f0696f85560a422f50e8498c`. Runtime exact conservé avec les
résultats. Identité PT/Morpho/Pendle/échéance revérifiée par RPC au bloc
`26163477` avant le run. Empreinte du brut vérifiée avant préparation.

| Horizon | MAE Chronos-2 USD/PT | MAE prix inchangé USD/PT |
| --- | --- | --- |
| 1 jour | 0.000268 | 0.000100 |
| 3 jours | 0.001264 | 0.001200 |
| 7 jours | 0.003189 | 0.002400 |

Chronos-2 ne bat pas la baseline prix inchangé aux trois horizons dans ce petit
échantillon. Deux fenêtres ne permettent pas une validation ni une conclusion
générale sur ses performances. Les quantiles ne sont pas des intervalles dont
la couverture serait validée. Le rapport reste **RESEARCH_ONLY** et aucun
ForecastBundle validé n'est créé. Aucune modification du cache applicatif.

Durée totale mesurée : 55.53 s, dont 55.38 s de chargement/téléchargement des
poids. Dernière inférence : 0.036 s sur CPU. Ce n'est pas une mesure Colab.

Exports : `backend/data/ml/chronos2_exploratory_20261010/` contient
`report.json`, `RESULTS.md`, `strategy.json`, `strategy_evidence.json`,
`requirements-runtime.txt`. La normalisation et les révisions historiques
restent à auditer avant toute validation.

Commande reproductible, depuis la racine du dépôt :

```bash
.venv/bin/python -m ml.run_chronos2 \
  --export backend/data/ml/pt_usd/20261010T165506Z \
  --strategy backend/data/ml/chronos2_exploratory_20261010/strategy.json \
  --revision 29ec3766d36d6f73f0696f85560a422f50e8498c \
  --output backend/data/ml/chronos2_exploratory_20261010/report.json \
  --device cpu --exploratory
```

Vérification : **46 tests ML passent**, dont protection du mode standard,
fenêtres courtes sans fuite temporelle et contrôle des preuves conservé.
`pip check` ne détecte aucune dépendance cassée. Notebook Colab toujours non
exécuté ; le run réel a été effectué sur la machine locale.

## Autres historiques récupérés — 10 octobre 2026

L'utilisateur a demandé de trouver d'autres données historiques pour permettre
un benchmark plus long. Quatre PT Ethereum distincts ont été sélectionnés avant
évaluation : marchés actifs créés avant août, sous-jacents liés au dollar,
classés par TVL dans le catalogue officiel. Leurs PT et échéances ont été
revérifiés par RPC au bloc `26163750`. Aucune relation Morpho pour ces nouveaux
PT n'est revendiquée ; ils servent de benchmarks séparés.

| Actif Pendle | Clôtures quotidiennes USD | Première observation UTC |
| --- | --- | --- |
| reUSD | 123 | 2026-06-10 |
| sUSDS | 148 | 2026-05-16 |
| apyUSD | 147 | 2026-05-17 |
| USD3 | 144 | 2026-05-20 |

Toutes les séries terminent le 10 octobre UTC, soit la clôture de la bougie du
9 octobre : **562 observations réparties entre quatre actifs**, sans gaps ni
interpolation. Ne jamais concaténer ces actifs ou les ajouter aux 31 jours du
PT actuel. Les volumes sont manquants pour respectivement 2, 14, 2 et 13 jours ;
ils restent vides. La méthode et les révisions des prix fournisseur restent à
auditer ; la longueur seule ne valide pas un modèle.

Collecteur reproductible : `ml/collect_benchmark_histories.py`. Chaque cible
possède une réponse OHLCV brute avec hash, CSV, TimeseriesDTO, preuves API/RPC,
rapport et Parquet ; historiques bruts de taux/APY/TVL collectés séparément avec
preuves. L'adaptateur existant gère leurs requêtes d'historiques de rendement.

Exports : `backend/data/ml/comparable_histories_20261010/`, inventaire lisible
`DATASETS.md`, détail `collection_summary.json`. `dataset_splits.json` réserve
les 14 dernières observations de chaque actif, du 27 septembre au 10 octobre,
pour un futur test final. Aucun run Chronos sur ces quatre séries à cette étape.

Vérifications : hashes, schéma DTO, valeurs positives, fréquence quotidienne,
cohérence JSON/CSV/Parquet, absence de clé RPC dans les exports ; 46 tests ML
passent. Les fichiers de données sont ignorés par Git.

## Run des quatre historiques longs — 10 octobre 2026

À la demande de l'utilisateur, Chronos-2 exécuté sur reUSD, sUSDS, apyUSD et
USD3, en CPU. Même révision et seed que le premier run, sans modification des
poids. Protocole enregistré avant évaluation dans `protocol.json` : minimum
45 jours de contexte ; 14 jours finaux réservés ; deux origines hebdomadaires
successives, sans ajustement après lecture des scores.

45 fenêtres de développement réparties entre quatre actifs (9, 12, 12, 12),
avec cutoff au 26 septembre. Test final : 27 septembre–10 octobre ; deux
fenêtres par actif. À la seconde origine, les observations de la première
semaine sont devenues disponibles et peuvent entrer dans le contexte. Même
planning pour Chronos, dernier prix et moyenne glissante.

| Horizon | MAE Chronos USD/PT | MAE dernier prix USD/PT | Gain agrégé |
| --- | --- | --- | --- |
| 1 jour | 0.000462 | 0.000725 | +36.3 % |
| 3 jours | 0.003941 | 0.003900 | -1.0 % |
| 7 jours | 0.003442 | 0.003138 | -9.7 % |

sUSDS bat le dernier prix aux trois horizons sur le test final. Cela n'autorise
pas une sélection après coup puis une validation sur le même test. Couverture
agrégée q10–q90 : 100 %, 62.5 %, 37.5 % aux horizons 1/3/7 jours. Incertitude
insuffisamment calibrée à 7 jours ; seulement deux semaines et erreurs
potentiellement corrélées entre actifs. Statut conservé **RESEARCH_ONLY**.
Aucun résultat ne valide le PT original ni une décision STAY/EXIT.

Durée mesurée, chargement inclus et poids en cache : **5.84 secondes**.
Exports : `backend/data/ml/chronos2_comparable_run_20261010/` : `summary.json`,
rapports par PT, prévisions sur sept jours, `RESULTS.md`, `protocol.json`,
`dataset_splits_used.json`. Le découpage original utilisé est conservé avec son
hash ; le manifeste des datasets marque désormais le test final comme évalué.
Tout ajustement futur devra annoncer que ce test est déjà consommé.

Lanceur reproductible : `ml/run_comparable_benchmark.py`. Il vérifie les hashes,
l'identité RPC du PT et son échéance, les valeurs normalisées contre le brut et
le découpage. Aucun StrategyDTO Morpho artificiel : ces données sont des
benchmarks d'actifs séparés. 48 tests ML passent, dont le contrôle des préfixes
strictement passés pour les deux semaines de test.

## Comparaison des modèles préentraînés — 10 octobre 2026

Deux alternatives ont été exécutées sur CPU, sans mise à jour des poids :
`google/timesfm-2.5-200m-pytorch` révision
`1d952420fba87f3c6dee4f240de0f1a0fbc790e3` (timesfm 2.0.2), et
`amazon/chronos-bolt-base` révision `5d9f166d69f47aef3401367a7b842e78fe97b121`.
Même quatre snapshots, contexte minimal de 45 jours, 45 fenêtres de développement,
et 8 fenêtres sur le test déjà consulté. Cette sélection est exploratoire et ne
constitue pas une validation indépendante.

| Modèle | Gain MAE J7 développement / persistance | Gain MAE J7 test réutilisé / persistance | Couverture J7 test q10–q90 |
| --- | ---: | ---: | ---: |
| Chronos-2 | +8,0 % | −9,7 % | 37,5 % |
| TimesFM 2.5 | +14,1 % | +4,7 % | 50,0 % |
| Chronos-Bolt | −1,5 % | −36,0 % | 87,5 % |

TimesFM réduit la MAE J7 d'environ 13,2 % face à Chronos-2 sur le test réutilisé.
À J3 son gain contre la persistance est seulement 1,4 %, et à J1 il est moins
bon que Chronos-2 et la persistance. Candidat proposé pour J7, sans remplacement
du modèle en production : intervalles encore insuffisamment calibrés et besoin
de nouvelles observations avec protocole figé. Les quatre actifs ne valident
pas le PT-siUSD initial.

Le premier run Bolt brut a été arrêté par le garde-fou des quantiles croisés.
Le second run applique explicitement q10=min(q10,q50), q90=max(q90,q50), sans
changer q50 ; le protocole et le nombre de lignes corrigées sont conservés.
TimesFM utilise les options de l'exemple officiel, dont fix_quantile_crossing,
avec max_context=256, max_horizon=128 et torch_compile=False sur CPU.
Aucun réglage des prédictions centrales à partir des scores n'a été effectué.

Rapports locaux ignorés par Git :
`backend/data/ml/pretrained_comparison_20261010/{comparison.json,RESULTS.md}`.
Les adaptations se trouvent dans `ml/pretrained_candidates.py` ; le runner
`ml/run_comparable_benchmark.py` accepte `--model chronos2|bolt|timesfm`.
Installer les dépendances optionnelles avec
`.venv/bin/pip install -r ml/requirements-timesfm.txt`.
Comparer les trois summary.json avec `python -m ml.compare_pretrained_reports`,
en passant `--output` vers un nouveau dossier. Les versions, révisions et
paramètres sont enregistrés dans chaque rapport. Aucun ForecastBundle VALIDATED
ni cache de décision n'a été écrit.

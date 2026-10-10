# Monte Carlo PR2 — raccordement Data et calibration oracle

Statut : raccordement implémenté et testé uniquement sur des fixtures synthétiques. Aucune série oracle réelle n'a encore été collectée ni calibrée. **PR2 réel non validé.**

## Chaîne

```text
SQLite (DTO + preuves) + Parquet (ORACLE_PRICE, source ethereum_rpc)
  → analytics/provider.py : snapshot, série oracle réduite aux candidats d'échantillonnage,
    preuves RPC reliées par (oracle, bloc)
  → stress/monte_carlo/adapter.py : contrôles d'identité, fraîcheur et preuves
  → stress/monte_carlo/calibration.py : OracleCalibration vérifiée, sinon UNAVAILABLE
  → MonteCarloRequest → run_monte_carlo → MonteCarloResult (provenance.oracle_calibration)
```

Route : `POST /api/v1/monte-carlo`. Le client ne fournit que les réglages : `strategy_id`, `position_id`, `horizon_days` (1, 3 ou 7), `num_paths`, `seed`, `volatility_source`, `debt_model`, `borrow_apr`, et `annual_volatility` / `annual_drift` uniquement en `USER_ASSUMPTION`. Les montants, le prix oracle, la LLTV et l'expiry viennent de Data.

## Mapping

| Champ Monte Carlo | Source Data |
| --- | --- |
| `collateral_quantity_pt` | `PositionDTO.collateral_amount` |
| `debt_loan_units` | `PositionDTO.borrow_amount` (jamais les totaux du marché) |
| `lltv` | `MarketDTO.morpho.lltv` |
| `initial_oracle_price_loan_per_pt` | `MarketDTO.morpho.oracle_price.value` |
| `pt_expiry` | `StrategyDTO.expiry` |

Les contrôles sont les suivants : chaîne 1 partout, jointure par adresse et identifiant (jamais par symbole), même mode Data, marché, stratégie, PT et loan token identiques, et prix oracle `MORPHO_ORACLE` coté en loan token. La fraîcheur est de 300 s pour la position, le marché et l'oracle, et de 24 h pour la stratégie, sans date future. En REAL, il faut une preuve RPC de l'oracle au bloc du marché ; une position `ONCHAIN` doit aussi avoir une preuve RPC Morpho au même bloc. Une incohérence d'identité renvoie HTTP 422. Une donnée absente ou non prouvée renvoie HTTP 200 avec le statut `UNAVAILABLE` et des `missing_fields` précis, sans simulation et sans recours à la fixture.

`input_scope=REAL_DATA` exige un marché REAL, une position `ONCHAIN` et une calibration observée vérifiée. Une position `SIMULATED`, des données DEMO ou une volatilité supposée restent `HYPOTHETICAL`, même sur un marché réel.

## Calibration `ORACLE_DAILY_LOG_RETURNS_30D_V1`

- Série : `ORACLE_PRICE`, sujet = ce marché Morpho, unité TOKEN en loan token, méthode `MORPHO_ORACLE`, source `ethereum_rpc`, mode REAL. Un historique spot PT, USD ou d'une autre source est refusé.
- Grille : 31 minuits UTC ; le dernier est le minuit le plus récent ≤ `as_of`. Fenêtre de 30 jours.
- Échantillon : pour chaque minuit, le dernier point valide observé dans `[minuit − 1 h, minuit]`. Aucun point hors fenêtre, aucune interpolation, aucun zéro. Un minuit sans point valide rend la calibration `UNAVAILABLE` (`oracle_series.sample:<date>`).
- Point valide : valeur > 0, statut OK, bloc connu, preuve RPC `eth_call price()` de l'oracle du marché, au même bloc, avec hash de bloc, récupérée au plus tard à `as_of`. La valeur brute de la preuve doit donner exactement la valeur stockée (`raw / 10^(36 + déc. loan − déc. PT)`). Les candidats invalides ignorés sont comptés.
- Doublons : Parquet déduplique par (horodatage, source). Les blocs des 31 échantillons doivent être strictement croissants.
- Changement de source : une seule source (`ethereum_rpc`) et l'oracle du marché. L'oracle d'un marché Morpho est immuable, car il fait partie de l'identifiant du marché.
- Rendements : `ln(P_k / P_{k−1})`, 30 valeurs, écart-type d'échantillon (n − 1), annualisé par √365, calculé en Decimal (60 chiffres, résultat arrondi à 1e-18). Ce sont des rendements logarithmiques de prix, pas un APY ni un APR.
- Drift : fixé à zéro par politique, jamais estimé. La moyenne journalière est seulement rapportée.
- Futur : les points postérieurs à `as_of` sont ignorés et comptés ; une preuve récupérée après `as_of` invalide son point.
- Une volatilité annualisée > 5 sort du domaine de simulation → `UNAVAILABLE`.

La provenance (`provenance.oracle_calibration`) conserve la méthode, la période, les 31 points (cible, horodatage, bloc, prix, preuve), les paramètres et les compteurs.

Limites : la calibration décrit la variabilité passée de l'oracle, pas une prévision ; la fréquence de franchissement reste une fréquence de modèle GBM, pas une probabilité objective de liquidation. Un oracle PT déterministe (décote linéaire, par exemple) donne une volatilité quasi nulle : l'avertissement `LOW_OBSERVED_ORACLE_VOLATILITY_MAY_REFLECT_DETERMINISTIC_ORACLE` le signale. Le rendement de grille utilise un intervalle nominal d'un jour, alors que l'écart réel est d'un jour ± 1 h.

## Collecte

Deux voies réutilisent l'adaptateur RPC, SQLite et Parquet :

1. Prospective : `python -m app.ingestion.refresh --task refresh_current` écrit un point oracle prouvé à chaque exécution. Seules les exécutions tombées dans l'heure précédant minuit UTC servent d'échantillons ; il faut donc 31 jours d'exécutions régulières.
2. Rétrospective : `scripts/backfill_oracle_history.py` lit `price()` au dernier bloc ≤ chaque minuit (31 par défaut, 60 au maximum) par `eth_call` historique, du plus récent au plus ancien. Il ne fait ni transaction ni signature. Il s'arrête après trois échecs consécutifs tout en conservant les points récents déjà obtenus ; les observations sont stockées dans l'ordre chronologique. Les lectures à des blocs anciens exigent en général un RPC **archive** configuré dans `ETHEREUM_RPC_URL` (environnement ou `backend/.env`, jamais commité) ; le RPC public par défaut peut refuser ces blocs.

`scripts/check_oracle_calibration.py` vérifie hors ligne si les stores permettent une calibration et liste sinon les champs manquants. Il n'écrit rien et ne crée pas de base vide.

## Corrections issues de la revue

Le prix oracle courant exige désormais une preuve RPC `eth_call price()` de la source `ethereum_rpc`, sur la chaîne, l'oracle et le bloc attendus, avec statut OK, hash de bloc et fraîcheur de cinq minutes. La valeur brute `out0` doit correspondre exactement à `raw_value` et à la valeur normalisée du DTO. Une divergence donne `UNAVAILABLE`, sans simulation.

Une position sans dette, y compris une position vide, donne HTTP 200 avec `NOT_APPLICABLE`, `data: null` et `NO_DEBT`. Les contrôles d'identité, de fraîcheur et de preuve restent appliqués, mais aucune calibration ni simulation n'est requise. Une position obsolète reste `UNAVAILABLE`.

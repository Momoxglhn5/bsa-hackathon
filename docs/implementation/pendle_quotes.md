# Prix de sortie PT Pendle (`pendle_quotes.py`)

Fichier : `backend/app/adapters/pendle_quotes.py` (nouveau). Aucun autre fichier source n'est modifié ; le module n'importe pas `pendle_api.py`.

## Pourquoi

Le brief (C4) impose trois prix séparés : prix de marché du PT, prix oracle Morpho, prix d'exécution de sortie. La todolist nomme la métrique `PT_EXIT_EXECUTION_PRICE` (étapes 21 et 26) mais aucune étape ne la collecte. Ce module la collecte.

## Ce qui est implémenté

Classe `PendleQuoteClient`, méthode `get_pt_exit_quote(market_address, chain_id=1) -> PtExitQuote`.

- Endpoint : `GET /v1/sdk/{chainId}/markets/{market}/swapping-prices` (OpenAPI Pendle : « rates per 1 token », `null` si le swap est impossible, mis à jour à chaque bloc).
- Base : `PENDLE_API_BASE`, défaut `https://api-v2.pendle.finance/core`.
- Valeur retenue : `ptToUnderlyingTokenRate`, nombre de tokens sous-jacents reçus pour la vente d'un PT entier.

| Champ de `PtExitQuote` | Contenu |
| --- | --- |
| `metric` | `PT_EXIT_EXECUTION_PRICE` |
| `value` | `Decimal` (JSON lu avec `parse_float=Decimal`, sans arrondi float) ou `None` |
| `quote_asset_id` | `asset:1:<underlyingToken en minuscules>` |
| `unit` | `asset:1:<underlying> per PT` |
| `pricing_method` | `EXECUTION_QUOTE_SPOT` |
| `size_dependent` | `False` : taux spot, sans taille ni slippage |
| `market_id` | `pendle:1:<market en minuscules>` |
| `status` / `missing_reason` | `OK`, ou `UNAVAILABLE` avec la raison |
| `timestamp`, `source_id`, `source_url`, `http_status`, `raw`, `rate_limit` | provenance, payload brut, en-têtes `x-ratelimit-*` / `x-computing-unit` |

Ce n'est **pas** un prix USD : aucune conversion n'est faite.

## Erreurs

- 3 tentatives avec backoff exponentiel sur timeout, erreur réseau, 429 et 5xx ; 429 respecte `Retry-After`, sinon `x-ratelimit-reset`, plafonné à 60 s.
- 404 (marché inconnu **ou expiré** : l'API répond `Given market is expired`) → `UNAVAILABLE`, `value=None`, message de l'API dans `missing_reason`.
- Taux `null` ou de type inattendu → `UNAVAILABLE`, jamais 0.
- Autres 4xx, JSON invalide, ou tentatives épuisées → `PendleQuoteError(code, message, retryable, url, http_status, rate_limit)`.

## Vérification (2026-10-10)

`python backend/app/adapters/pendle_quotes.py` contre l'API réelle :

```text
pendle:1:0x03005432d0f02c46d48ccd859d677d261640e5e1 [OK] PT_EXIT_EXECUTION_PRICE=0.00070871939264979 asset:1:0xf252c5bd43907a6cab079e990845a37a7c5730d9 per PT
pendle:1:0x0bef762d2094ac80821c657dea6783fc43435292 [OK] PT_EXIT_EXECUTION_PRICE=0.9798008934049945 asset:1:0xa1fa7777974312f7d801a8880714a218f76233f8 per PT
pendle:1:0x0e201d27ded56cf4a0da8853d7604743de217435 [OK] PT_EXIT_EXECUTION_PRICE=0.844462239735554 asset:1:0xbd373c9d3d8976a4fecc504a93c768bbe8c3227c per PT
expired market [UNAVAILABLE] value=None reason='Given market is expired'
unknown market [UNAVAILABLE] reason='Market with id 1-0x0000000000000000000000000000000000000bad not found'
rate limit: {'x-ratelimit-remaining': '192', 'x-computing-unit': '1'}
```

Hors ligne (`httpx.MockTransport`) : 429 puis réponse avec taux `null` → `UNAVAILABLE` après 1 s d'attente ; 503 répété → `HTTP_503`, `retryable=True` ; `0.98765432109876543210` conservé à l'identique en `Decimal`.

## Limites

- Taux spot pour 1 PT : pas de prix d'exécution pour une taille donnée ni de slippage. `POST /v3/sdk/{chainId}/convert` le permettrait, mais c'est un constructeur de transaction qui exige une adresse `receiver` valide (rejet de `0x…01`) ; non retenu pour un outil read-only.
- Le sous-jacent est celui choisi par Pendle pour le swap, pas forcément l'actif de prêt Morpho (USDC). Exemple : `0.0007` pour NGI+ indique un sous-jacent de valeur unitaire élevée ; l'unité reste explicite, aucune conversion USD.
- Branchement non fait : snapshot (`collect`/`normalize`) et Parquet (`backfill`, `parquet_store`) appartiennent à la todolist du collègue ; à faire de concert, avec `pricing_method` « exécution » dans les modèles de prix.
- `httpx` doit figurer dans `backend/requirements.txt` (fichier commun, non modifié ici).

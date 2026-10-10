# Livraison Data — marché réel, fonds simulés

## Relancer la collecte

Depuis la racine, avec les dépendances backend installées dans `.venv` :

```bash
.venv/bin/python backend/scripts/finish_data.py --days 180
.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Le RPC Ethereum configuré dans `backend/.env` est utilisé en lecture seule. Aucune approbation de token, signature ou transaction n'est envoyée. Les magasins et les secrets locaux restent ignorés par Git.

`--days` indique la fenêtre demandée, pas un nombre de jours garanti. `--data-dir` permet une collecte isolée. Le script revérifie la stratégie sélectionnée, actualise les marchés et les prix, récupère les historiques, demande les devis et publie un dossier immuable sous `backend/data/delivery/`. Le pointeur `current.json` est remplacé atomiquement après la création du bundle. Un échec de vérification empêche sa publication ; les limites des autres sources restent explicites dans le rapport.

## Ce qui est livré

- Deux marchés réels Morpho/Pendle et leurs lectures Ethereum à un bloc fixé par refresh.
- Trois prix distincts : spots USD PT/USDC, oracle Morpho en USDC par PT, devis de vente pour une quantité exacte. Le spot USD de l'actif de référence est également stocké quand disponible.
- Historiques Parquet et export JSON des observations avec sources, dates et flags de qualité. Aucun point interpolé ni remplacement d'une absence par zéro.
- Positions **SIMULATED** de 1, 2 et 5 millions USD de collatéral, avec dette hypothétique égale à 70 % de sa valeur de marché. Ce ne sont ni des positions détenues ni une preuve de capacité d'emprunt.
- Devis PT→USDC sourcés, contrôlés sur le marché, la quantité, les tokens et le destinataire de simulation. Les transactions retournées par le fournisseur restent dans les archives brutes locales et ne sont pas exposées par l'API.
- Catalogue public d'incidents, avec source et empreinte du corps téléchargé. Les incidents apparentés ne sont pas attribués automatiquement au marché choisi.
- Snapshot Core JSON, entrées de quotes, copie SQLite, réponses API brutes obtenues durant la collecte et checksums SHA-256. Les anciennes observations peuvent ne disposer que de métadonnées de preuve ; leur réponse brute originale n'est pas recréée.

## Résultat vérifié le 10 octobre 2026

| Élément | Résultat |
| --- | --- |
| Stratégie sélectionnée | `strat_e76bee1f14994d77f99f42a7`, `VERIFIED_ACTIVE` |
| PT Pendle | 32 jours d'APY observés, du 9 septembre au 10 octobre |
| Marché Morpho sélectionné | 10 jours de métriques quotidiennes, du 1er au 10 octobre |
| Historique USD du PT déjà collecté | 31 jours, du 10 septembre au 10 octobre |
| Fonds hypothétique 1 M USD | Devis obtenu, clôture non vérifiée |
| Fonds hypothétique 2 M USD | Devis obtenu, clôture non vérifiée |
| Fonds hypothétique 5 M USD | Fournisseur : `MarketProportionTooHigh`, devis indisponible |
| Catalogue d'incidents | 1 299 événements rapportés à la date de collecte |
| Graph → Stress → Decision | Réponses HTTP 200 ; décision `INSUFFICIENT_DATA`, verdict absent |

Ces comptes concernent le marché sélectionné, pas les marchés comparables de Marilou. Les sources peuvent évoluer : `report.json` est la référence de chaque nouvelle collecte. Les montants des devis sont indicatifs et expirent selon une politique locale de cache de cinq minutes ; ce délai n'est pas une garantie d'exécution du fournisseur.

## API pour le frontend

| Route | Usage |
| --- | --- |
| `GET /api/v1/data-delivery` | Rapport de collecte et couverture réelle |
| `POST /api/v1/exit-quotes` | Quote mise en cache pour le `strategy_id` et l'`input_amount` exact, en unités entières de PT |
| `GET /api/v1/incident-catalog` | Incidents sourcés ; aucune probabilité calibrée |
| Routes existantes marchés, stratégies, séries, preuves | SQLite/Parquet, sans appel fournisseur dans les handlers |

Le Core découvre les entrées de quotes du bundle publié automatiquement en mode REAL. `ANALYTICS_INPUTS` reste prioritaire pour fournir un plan de clôture externe documenté. Pour un replay figé, utiliser `ANALYTICS_SNAPSHOT` avec le chemin du `analytics_snapshot.json` voulu et `ANALYTICS_REPLAY=true`. Le replay est signalé ; il ne rend pas le devis utilisable aujourd'hui.

Une quantité non mise en cache donne `MISSING` / `UNAVAILABLE`. Une quote expirée donne `STALE`. Une quote obtenue reste `PARTIAL` tant que le remboursement et le coût total de clôture ne sont pas vérifiés. Le devis refusé de 5 M dispose également d'une preuve d'erreur fournisseur.

## Limites qui restent réelles

Le devis de vente seul ne prouve pas qu'il est possible de rembourser Morpho avant de retirer le collatéral. La liquidité externe de remboursement, le plan de clôture, son gaz total et les frais supplémentaires restent inconnus. Le gaz estimé par Pendle et ses frais rapportés sont conservés à titre informatif, sans inventer un coût complet ni déduire deux fois des frais.

Le catalogue d'incidents ne fournit ni population exposée ni durée d'exposition permettant de calibrer une probabilité. Les fréquences restent indisponibles. Le Graph conserve ses liens non vérifiés et Risk conserve ses facteurs manquants ; aucune note complète ou recommandation STAY/EXIT n'est forcée.

Sources : [documentation et OpenAPI Pendle](https://api-v2.pendle.finance/core/docs), [SDK hébergé Pendle](https://docs.pendle.finance/pendle-v2/Developers/Backend/HostedSdk), [catalogue d'incidents DefiLlama](https://defillama.com/hacks).

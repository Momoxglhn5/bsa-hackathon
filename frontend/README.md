# DeFiNe — frontend React

Interface React du handoff design v11, branchée sur le backend FastAPI (`/api/v1`).
Elle reproduit l'intro 3D, le dashboard Explorer / Simuler / Décider, la page Graphe
et les drawers. Toutes les valeurs affichées viennent du backend. Une valeur absente
reste « Indisponible », jamais zéro. Aucun bouton n'exécute de transaction.

## Lancer la démo

Prérequis : Node.js 22 ou supérieur, et le virtualenv backend décrit dans le
[README racine](../README.md#développement).

```bash
scripts/radar-dev.sh
```

Le script démarre trois processus :

| Processus | Port | Données |
| --- | --- | --- |
| Backend « Live market » | 8000 | Replay du marché réel enregistré (`docs/demo/radar/source/analytics_snapshot.json`), fonds simulés de 1, 2 et 5 M$ |
| Backend « Demo » | 8001 | Jeu complet régénéré à chaque lancement par `backend/scripts/build_demo_dataset.py` dans `backend/data/demo` : 5 marchés, 7 positions |
| Frontend Vite | 5173 | <http://localhost:5173> ; `#dashboard` ouvre directement le dashboard |

L'interface est en anglais. Le jeu « Demo » est chargé par défaut. La pastille **DEMO / LIVE MARKET** de la barre
du haut ouvre le drawer Données, qui permet de changer de jeu.
Déroulé de présentation : [parcours jury](../docs/demo/PARCOURS_JURY.md).

Sans le script, lancer chaque backend comme indiqué dans le README racine, puis :

```bash
npm --prefix frontend ci
npm --prefix frontend run dev
```

Le serveur Vite relaie `/api/` vers le port 8000 et `/api-demo/` vers le port 8001.
Le navigateur n'appelle donc aucun fournisseur et aucune modification CORS n'est nécessaire.
Les cibles se changent dans `frontend/.env` (voir [.env.example](.env.example)).

## Contrôles

```bash
npm --prefix frontend test
npm --prefix frontend run build
```

`build` exécute aussi la vérification de types (`tsc --noEmit`).
Ces deux commandes tournent dans le job CI « Frontend ».

## Organisation

| Dossier | Rôle |
| --- | --- |
| `src/api` | Client HTTP unique (timeouts, annulation, erreurs 4xx/5xx), types des contrats et une fonction par route |
| `src/state` | Jeu de données, sélection, navigation, simulations de session et invalidation par snapshot |
| `src/lib` | Formatage d'affichage (en-US), libellés anglais, noms de nœuds et atteignabilité du graphe |
| `src/intro` | Scène three.js (océan, plongée) chargée à la demande et contrôleur de défilement |
| `src/explore`, `src/simulate`, `src/decide` | Les trois onglets |
| `src/graph` | Page Graphe : moteur canvas force-directed et panneau de nœud |
| `src/drawers` | Données, Méthodologie, Incidents, Preuves |

## Écrans et routes backend

| Écran | Routes | Ajouts par rapport à la maquette |
| --- | --- | --- |
| Chargement | `GET /catalog`, `/graph/{id}`, `/risk/{id}`, `/scenarios` | Positions et devis issus du jeu servi |
| Explorer | `/risk` (valorisation, score, couverture, confiance), `/graph`, `/series` | Dimensions et dix facteurs du risque, marchés Pendle et Morpho, historique de 90 jours |
| Simuler · choc | `POST /simulations` | Avant/après, pertes par position, chemin de propagation |
| Simuler · Monte Carlo | `POST /monte-carlo` | Onglet ajouté : franchissement de la LLTV, quantiles de LTV, VaR et ES |
| Décider | `POST /carry-estimates`, `/closing-plans`, `/decisions` | Carry et plan de clôture calculés à l'ouverture, seuil de bascule, sensibilité, mode Prédictif |
| Devis de sortie | contexte Core (`/catalog`) | Devis à la taille exacte de la position, validité évaluée à l'heure du snapshot |
| Graphe | `/graph`, `GET /graph/{id}/exposure` | Positions exposées à une dépendance, preuves des liens, choc pré-rempli |
| Drawers | `/health`, `/strategies`, `/data-status`, `/data-delivery`, `/incident-catalog`, `/evidence/{id}` | Snapshot, fraîcheur, incidents DefiLlama filtrables, preuves |

## Choix et limites

- **Route ajoutée au backend** : `GET /api/v1/catalog`, en lecture seule et testée. Avec un snapshot injecté, aucune autre route ne fournissait la liste des positions ni les `exit_quote_id` valides, ce qui bloquait STAY/EXIT.
- **Jeu Demo** : marchés, prix, adresses et positions synthétiques (`data_mode` DEMO). Le backend garde donc tous les liens du graphe au statut « hypothèse » et plafonne la confiance des preuves. Seul le catalogue d'incidents DefiLlama est réel.
- **Jeu Live market** : les routes Data (`/strategies`, `/series`, `/evidence`…) lisent SQLite et Parquet, pas le snapshot injecté ; elles répondent 404 ou 503 et les panneaux concernés restent vides. Les preuves viennent alors de l'enveloppe Core.
- **Couleurs du graphe** : estimation visuelle à partir du type, de la profondeur, du partage et du niveau de preuve des liens. Ce n'est pas le score de risque du backend.
- **Sensibilité STAY/EXIT** : projection linéaire des valeurs renvoyées par `/decisions`. Elle n'est tracée que si elle retrouve exactement le seuil de bascule du backend.
- **Alarme de sortie** : aucune route n'existe. Seul un drapeau `liquidatable` renvoyé par le backend produit une bannière, et uniquement sur Explorer.
- **Non exposé par le backend** : historique persistant des simulations et des décisions, listing des prévisions, explication LLM. L'interface ne les simule pas.
- **Logo** : logo DeFiNe fourni par l'équipe (source dans `docs/brand/`), décliné dans `frontend/public/` en emblème transparent, logo complet, favicon et icône iOS.

# BSA Hackathon

Dépôt de travail pour notre participation au hackathon BSA.

## Hackathon

- [Tracks et informations sur DoraHacks](https://dorahacks.io/hackathon/bsa-the-hackathon/tracks)
- Track : à choisir.
- Projet : DeFi Risk Radar, analyse des dépendances et du risque systémique d’une stratégie PT Pendle utilisée comme collatéral sur Morpho Blue.
- Stack technique : FastAPI (Python) pour l’API de données, React + TypeScript + React Flow pour l’interface.

## Prochaines étapes

- [ ] Lire les règles, les critères de jugement et les exigences de soumission.
- [ ] Choisir un track et définir le problème à résoudre.
- [ ] Définir le MVP et la stack technique.
- [ ] Construire et tester le prototype.
- [ ] Ajouter les instructions d’installation et de lancement.
- [ ] Préparer la démonstration et la soumission.

## Développement

Ne jamais committer de clés API, de clés privées de wallet ou de fichiers `.env`.

Le projet comporte deux parties :

- `backend/` : API FastAPI en lecture seule (`/api/v1`) au-dessus d’un stockage SQLite + Parquet. Les données sont mises à jour séparément par l’ingestion.
- `frontend/` : application **DeFi Risk Radar** (React 19, TypeScript, Tailwind CSS 4, React Flow `@xyflow/react`, `d3-force`), qui ne lit que cette API.

### Lancer la démo locale (données DEMO)

Prérequis : Python 3 (testé avec 3.13) et Node.js 22 ou supérieur avec npm.

```bash
# 1. Backend : dépendances, données DEMO, API sur le port 8000
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python scripts/load_demo_fixture.py
uvicorn app.main:app --port 8000

# 2. Frontend, dans un autre terminal : http://localhost:5173
cd frontend
cp .env.example .env.local
npm ci
npm run dev
```

`load_demo_fixture.py` écrit dans `backend/data/` (ignoré par Git), ou dans `$DOMINO_DATA_DIR` si la variable est définie. Toutes les valeurs DEMO sont simulées : l’interface l’indique dans la barre de contexte.

Variables du frontend (`frontend/.env.example`) :

| Variable | Rôle |
| --- | --- |
| `VITE_API_BASE_URL` | URL de l’API, par défaut `http://localhost:8000`. |
| `VITE_DATA_MODE` | `DEMO` ou `REAL`, transmis tel quel à l’API. |
| `VITE_POSITION_ID` | Identifiant de la position affichée : l’API n’a pas de route pour lister les positions. |

### Contrôles du frontend

```bash
cd frontend
npm run typecheck
npm test
npm run build
```

Tests du backend : `cd backend && python -m pytest -q app/tests`.

### Ce que fait l’interface

| Écran | Données réelles de l’API | Calculé dans le navigateur |
| --- | --- | --- |
| 01 Dependency Graph | Relations de `GET /strategies/{id}`, marchés, position, statut de fraîcheur, preuves, séries | Mise en page `d3-force`, lien position → marché (`UI_JOIN`) |
| 02 Stress Simulator | Position, LLTV, prix de marché du collatéral, chemin de propagation tiré des relations | Impact du choc (formule Morpho : incitation de liquidation `min(1.15, 1 / (0.3·LLTV + 0.7))`) |
| 03 STAY / EXIT | Position, valeur du collatéral | Verdict à partir d’hypothèses saisies : fréquences d’incident, pertes, coût de sortie |

Les routes `/scenarios`, `/simulations`, `/exit-quotes`, `/incident-frequencies`, `/decisions` et `/forecasts` n’existent pas dans l’API. Les écrans 02 et 03 l’affichent et marquent leurs résultats comme estimations. Sans coût de sortie saisi, le verdict reste `INSUFFICIENT_DATA`.

## CI Checks

Le workflow `.github/workflows/ci.yml` s’exécute à chaque push, sur les pull requests et à la demande depuis GitHub Actions.

| Check | Vérification |
| --- | --- |
| Markdown | Structure et cohérence des fichiers Markdown avec markdownlint-cli2. |
| GitHub Actions validation | Syntaxe, expressions et configuration des workflows avec actionlint. |
| Secret scan | Recherche de secrets dans tout l’historique Git avec Gitleaks ; valeurs masquées dans les logs. |
| Frontend | `npm ci`, vérification des types, tests unitaires Vitest et build Vite dans `frontend/`. |

Les actions GitHub sont figées par leur SHA et les outils de vérification par leur version. Le workflow dispose uniquement d’un accès en lecture au dépôt et ne nécessite aucun secret configuré. Dependabot propose chaque semaine les mises à jour des actions GitHub ; les versions des outils CLI sont à maintenir séparément.

### Exécuter les contrôles localement

Prérequis pour ces outils CI : Node.js 22 ou supérieur avec npm, et Go 1.26.1. Go sert uniquement aux outils de vérification et ne définit pas la stack de l’application.

```bash
npx --yes markdownlint-cli2@0.23.3
go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.12 -color
go run github.com/zricethezav/gitleaks/v8@v8.30.1 git --redact --no-banner --log-opts='--all'
```

Le scan Git couvre les commits, pas les fichiers non commités. Pour vérifier également le dossier de travail avant un commit :

```bash
go run github.com/zricethezav/gitleaks/v8@v8.30.1 dir . --redact --no-banner
```

Le job Frontend vérifie les types, les tests unitaires et le build de l’application. Les tests du backend ne sont pas encore exécutés en CI. Les contrôles ne bloquent une fusion que si une règle de protection de branche les exige ; cette configuration est distincte du workflow.

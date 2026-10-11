# DeFiNe — BSA Hackathon

Anciennement « DeFi Risk Radar » : les documents de référence dans `docs/` gardent ce nom.

Analyse en lecture seule de positions d'emprunt Morpho utilisant des PT Pendle : découvrir les dépendances, simuler une défaillance et comparer STAY/EXIT sous hypothèses explicites.

## Hackathon

- [Tracks et informations sur DoraHacks](https://dorahacks.io/hackathon/bsa-the-hackathon/tracks)
- Track visé dans les briefs projet : Belem Capital DeFi Risk Challenge ; règles de soumission à revérifier sur la source officielle.
- Backend : Python 3.13, FastAPI, Pydantic v2, NetworkX, Decimal, snapshots JSON.
- Interface jury : application React sur un jeu de démo complet ; version statique hors ligne en secours, alimentée par un replay de données réelles.

## Application web

Le frontend React (`frontend/`) reprend le design du handoff v11 et branche les routes du backend : intro 3D, Explorer, Simuler (choc et Monte Carlo), Décider (carry, clôture, STAY/EXIT), graphe des dépendances, preuves et état des données.

```bash
scripts/radar-dev.sh
```

Ouvrir <http://localhost:5173>. Le script régénère le jeu « Demo » (5 marchés PT Pendle sur Morpho Blue, 7 positions de 4 fonds simulés, 90 jours d'historique, devis de sortie, prévisions validées, incidents DefiLlama réels) et le sert sur le port 8001. Il lance aussi le replay du marché réel (port 8000) et le serveur Vite. Le jeu Demo est synthétique ; la pastille DEMO de la barre du haut le signale et permet de basculer sur le jeu « Live market ». L'interface est en anglais.

- Déroulé de présentation et chiffres attendus : [parcours jury](docs/demo/PARCOURS_JURY.md).
- Matrice écran → API et limites : [frontend/README.md](frontend/README.md).

## Disponible localement

- Application web React connectée aux routes Data, Delivery et Core, sans valeur inventée ni transaction.
- Graphe de dépendances et propagation inverse, sans double compte des positions.
- Stress oracle, minting et cross-chain avec exposition, santé de position et pertes uniquement chiffrables.
- Décision conditionnelle STAY/EXIT, coût de sortie validé, seuil de bascule et repli Predictive → Normal.
- Mode Normal : estimation déterministe du carry, prévisualisation remboursement/retrait/vente, financement externe et frais déclarés explicitement ([contrats et limites](docs/implementation/normal_backend.md)).
- Risque observable avec couverture, confiance et score masqué si données insuffisantes.
- Monte Carlo du prix oracle Morpho raccordé à Data, avec calibration de volatilité sur la série oracle prouvée par RPC ; contrôlé sur un snapshot de marché réel avec 11 observations RPC ; les runs sous hypothèse passent, la calibration sur 31 points reste indisponible ([détails](docs/implementation/monte_carlo_pr2.md)).
- APIs Data et Analytics sous `/api/v1`, documentation OpenAPI et tests automatisés.

**Le script de démo analytics et le jeu « Demo » de l'application utilisent des données synthétiques.** Le jeu « Live market » et la [version statique](docs/demo/radar/README.md) utilisent un marché et des prix réels enregistrés, des fonds simulés et des hypothèses explicites. Les moteurs sont branchés aux stores SQLite/Parquet Data et aux relations vérifiées. Le manifeste livré est `VERIFIED_ACTIVE` ; les stores réels, ignorés par Git, doivent être collectés ou transférés séparément. Les prix USD et quotes absents restent `null` ; aucun verdict réel n'est forcé.

## Version statique hors ligne

En secours, sans backend : ouvrir [la page autonome](docs/demo/radar/index.html), ou lancer :

```bash
python3 -m http.server 8766 --bind 127.0.0.1 --directory docs/demo
```

Ouvrir <http://127.0.0.1:8766/radar/> : dépendances et preuves → sorties 1/2/5 M$ → impact du choc → STAY/EXIT conditionnel, sur le replay du marché réel. Le [texte associé](docs/demo/radar/README.md) précise le parcours et les limites.

## Développement

Depuis la racine, avec Python 3.13 :

```bash
python3.13 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements-analytics.txt
backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Ouvrir <http://127.0.0.1:8000/docs>. Dans un second terminal :

```bash
backend/.venv/bin/python backend/scripts/demo_analytics.py
backend/.venv/bin/python -m pytest -c backend/pytest.ini backend/app/tests -q
```

Par défaut, l'application conserve les routes Data et lit ses stores (`DOMINO_DATA_DIR`, défaut `backend/data`). Les routes Core utilisent REAL ; ajouter `?data_mode=DEMO` pour les données de démonstration stockées. Un magasin vide produit un statut d'indisponibilité explicite.

Pour utiliser le script de démo ci-dessus, lancer le serveur avec un snapshot explicitement synthétique :

```bash
ANALYTICS_SNAPSHOT=backend/app/fixtures/analytics_demo.json ANALYTICS_REPLAY=true \
  backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Aucun secret nécessaire pour cette démo. Le replay évalue la fraîcheur à la date du snapshot et est signalé dans les réponses. `ANALYTICS_INPUTS` permet de fournir aux stores des quotes de taille exacte, observations Risk et bundles Forecast. Le mode Predictive utilise un bundle validé ou revient au mode Normal sans changer les valeurs de ce dernier.

Exporter Data vers un snapshot analytique reproductible, sans requête réseau ni modification de la base :

```bash
backend/.venv/bin/python backend/scripts/export_analytics_snapshot.py --data-mode REAL --output /tmp/radar-real.json
```

Voir [CORE_ENGINES_INTEGRATION.md](CORE_ENGINES_INTEGRATION.md) pour les contrats, exemples de requêtes, calculs, limites et entrées attendues du développeur Data. Ne jamais committer de clés API, de clés privées de wallet ou de fichiers `.env`.

## Collecte Data réelle

La collecte complète se lance avec `.venv/bin/python backend/scripts/finish_data.py --days 180`. Elle produit des prix sourcés, des historiques, des devis pour les fonds simulés de 1/2/5 M USD et un snapshot rejouable. Les quotes disponibles n'attestent pas de la faisabilité de clôture. Voir [la livraison Data](docs/data/DATA_DELIVERY.md) pour les API, le résultat vérifié et les limites.

## CI Checks

Le workflow `.github/workflows/ci.yml` s’exécute à chaque push, sur les pull requests et à la demande depuis GitHub Actions.

| Check | Vérification |
| --- | --- |
| Markdown | Structure et cohérence des fichiers Markdown avec markdownlint-cli2. |
| GitHub Actions validation | Syntaxe, expressions et configuration des workflows avec actionlint. |
| Secret scan | Recherche de secrets dans tout l’historique Git avec Gitleaks ; valeurs masquées dans les logs. |
| Core analytics tests | Tests pytest des moteurs, de leurs APIs et de l'intégration au contrat Data, sans accès réseau. |
| Frontend | `npm ci`, tests Vitest, vérification de types et build Vite de `frontend/`. |

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

Les tests analytiques utilisent `backend/requirements-analytics.txt` sous Python 3.13. Les tests Data et Core s’exécutent ensemble ; leurs doubles de fournisseurs ne valident pas une collecte réseau réelle. Les contrôles ne bloquent une fusion que si une règle de protection de branche les exige ; cette configuration est distincte du workflow.

## Lot ML — Marilou

Les bases du forecast P1 sont disponibles sur la branche `marilou` : adaptation
des séries Data V1, benchmark chronologique, contrat JSON et garde-fous du cache.
Un premier run Chronos-2 a été exécuté localement sur 31 clôtures Pendle réelles.
Ce run exploratoire utilise les poids préentraînés sans les modifier. Sur deux
fenêtres de test, il ne bat pas la prévision prix inchangé à 1, 3 et 7 jours ;
aucune prédiction n'est validée ni publiée dans le cache applicatif.

TimesFM 2.5 et Chronos-Bolt sont également intégrés pour la recherche. Sur quatre
historiques PT distincts, TimesFM réduit la MAE à J7 de 13,2 % face à Chronos-2
sur le test réutilisé. La comparaison reste exploratoire ; les intervalles
ne sont pas suffisamment calibrés et le PT-siUSD initial n'est pas validé.

Voir [le contexte conservé, les tests et les données attendues](docs/implementation/marilou_ml.md).

Le parcours de validation prospective et l'export vers le moteur de décision
sont implémentés dans `ml.predictive` : [commandes et politique d'acceptation](docs/implementation/marilou_predictive.md).
Les résultats exploratoires existants restent non validés ; aucun verdict
financier n'est forcé par cette intégration.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r ml/requirements-dev.txt
.venv/bin/python -m pytest tests/ml -q
```

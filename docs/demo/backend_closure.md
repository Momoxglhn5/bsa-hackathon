# Fermeture backend — correctifs et vérification

Les moteurs existants sont conservés. Aucun contrat Data V1 ni dépendance ajouté.
Les seuls nouveaux champs publics concernent les montants de sortie demandés,
dans `ClosingPlanResult`. La provenance de mode des simulations reste privée au
processus ; elle n'est ni acceptée du client ni ajoutée au JSON public.

## Résultats par étape

Suite exécutée après chaque point depuis la racine :

```bash
backend/.venv/bin/python -m pytest -c backend/pytest.ini backend/app/tests tests/ml -q
```

| Ordre | Correctif | Résultat final de l'étape |
| --- | --- | --- |
| FIX 5 | Parcours Graph → Stress → Decision Normal, STAY puis EXIT | 421 tests passent |
| FIX 1 | Modes simulation/position/valorisation compatibles, preuve forecast du même mode | 425 tests passent |
| FIX 4 | Sortie complète, coûts comptés une fois, montant net et fonds propres renvoyés | 430 tests passent |
| FIX 2 | Expiration stricte du forecast et relecture à chaque décision | 431 tests passent |
| FIX 3 | Maturité contrôlée avec carry automatique, manuel ou forecast | 436 tests passent |

La première exécution du FIX 1 a eu quatre échecs dans les nouveaux jeux de test :
leurs preuves `FIXTURE` ne pouvaient pas porter le mode REAL. Les jeux ont été
corrigés sans assouplir les DTO ; les 421 tests antérieurs passaient déjà.
La suite finale garde un avertissement tiers Starlette/HTTPX connu.
Markdown, actionlint et `git diff --check` passent. Les scans de secrets du
dossier et de l'historique utilisent des exclusions exactes pour les faux
positifs examinés, dont six adresses publiques de tokens dans l'archive réelle
récemment intégrée ; aucune règle globale de détection n'est désactivée.

## Parcours HTTP vérifié

Le [parcours jury exact](hero_path.md) est testé automatiquement. Un serveur
Uvicorn local a également été lancé et le script `demo_analytics.py` exécuté
contre lui. Toutes les requêtes du script ont répondu HTTP 200 ; le serveur
a ensuite été arrêté.

- Probabilité supposée 2 % : STAY, valeur 793 000 USD.
- Probabilité supposée 10 % : EXIT.
- Valeur de sortie : 779 900 USD ; seuil de bascule : 5,275 %.
- Aperçu de clôture avec financement/frais explicitement supposés :
  1 200 000 unités disponibles pour rembourser, vente à 1 980 000 USD,
  coûts d'opérations de 150 USD, montant net de 1 979 850 USD,
  coût économique de sortie de 20 150 USD et fonds propres de 779 850 USD.

Les deux comparaisons utilisent des hypothèses de frais différentes : le fixture
initial pour le verdict manuel, puis 100 + 20 + 30 USD pour l'aperçu explicite.
Aucun montant de financement principal n'est déduit deux fois.

Les tests REAL de clôture emploient des données synthétiques portant ce mode pour
tester les gardes. Ils ne prouvent pas une disponibilité réelle de fonds ni une
exécution on-chain. Aucune transaction ou nouvelle collecte live n'a été faite.

## Endpoints sur l'archive réelle

Contrôle par FastAPI TestClient sur
`docs/validation/monte_carlo_20261011/snapshot.json`, en replay à sa date,
avec la position simulée `pos_mc_validation`. Chaque appel ci-dessous répond
HTTP 200 ; son statut métier indique ce qui est réellement calculable.

| Endpoint | Statut observé | Interprétation |
| --- | --- | --- |
| `GET /api/v1/graph/{strategy_id}` | `PARTIAL` | Graphe disponible, backing et autres dépendances encore inconnus |
| `GET /api/v1/scenarios` | `OK` | Catalogue disponible |
| `POST /api/v1/simulations` | `CALCULATED` | Minting sur collatéral avec haircut explicitement supposé de 20 % |
| `POST /api/v1/carry-estimates` | `CONDITIONAL` | Carry à sept jours calculable avec taux actuels constants par hypothèse |
| `GET /api/v1/risk/{strategy_id}` | `INSUFFICIENT_DATA` | Couverture 30 %, score global masqué |
| `GET /api/v1/incident-frequencies` | `INSUFFICIENT_DATA` | Pas de probabilité d'incident calibrée |
| `POST /api/v1/closing-plans` | `QUOTE_UNAVAILABLE` | Aucune quote d'exécution dans cette archive |
| `POST /api/v1/decisions` Normal | `QUOTE_UNAVAILABLE` | Pas de verdict forcé sans devis/plan de sortie |
| `POST /api/v1/decisions` Predictive | `QUOTE_UNAVAILABLE` | Repli Normal avec `FORECAST_UNAVAILABLE` |
| `POST /api/v1/monte-carlo`, volatilité supposée | `OK` | Simulation hypothétique sur données du marché réel |
| `POST /api/v1/monte-carlo`, volatilité observée | `UNAVAILABLE` | 11 observations sur les 31 requises, 20 manquantes |

Les routes Data et de livraison restent disponibles dans l'application, mais
le checkout ne contient pas les stores live SQLite/Parquet ni le bundle de
livraison. Leur contenu réel actuel n'a donc pas été revérifié. Le snapshot
analytique injecté ne remplit pas automatiquement ces magasins.

La clôture avec devis frais et financement suffisant est opérationnelle sous
hypothèses déclarées. Une sortie réellement prouvée demande toujours ses sources
de financement et coûts ; la démonstration synthétique ne remplace pas cela.
Le ML validé sur le PT cible, l'historique oracle complet et la résolution des
dépendances économiques ne sont pas créés artificiellement par ces correctifs.

Après intégration du main `1809658` et de sa démo jury : **440 tests passent**.

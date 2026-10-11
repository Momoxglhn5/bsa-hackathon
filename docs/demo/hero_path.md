# Démo jury — graphe, choc et choix garder/sortir

Toutes les données de cette démo sont synthétiques. Les montants, la baisse de
prix et la probabilité sont des hypothèses, pas une prédiction de crise.
Le replay utilise l'heure du snapshot : il n'affiche pas un devis actuel.

## Lancer le serveur

Depuis la racine, avec un seul processus :

```bash
ANALYTICS_SNAPSHOT=backend/app/fixtures/analytics_demo.json ANALYTICS_REPLAY=true \
  backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

## 1. Afficher les dépendances

```bash
curl --fail-with-body http://127.0.0.1:8000/api/v1/graph/strat_d589cd964a2da9b34f7a5ab5
```

Position : `pos_demo_fund`. Collatéral : 2 000 000 PT synthétiques ; dette :
1 200 000 unités du token emprunté. Les fonds propres actuels valent 800 000 USD.

## 2. Simuler une baisse de valeur de 20 %

Le scénario minting représente ici une émission non garantie avec une baisse de
prix explicitement supposée sur le collatéral. Le moteur ne déduit pas cette
baisse d'une quantité de tokens émis. Le prix oracle reste inchangé.

```bash
curl --fail-with-body -X POST http://127.0.0.1:8000/api/v1/simulations \
  -H 'Content-Type: application/json' -d '{
    "strategy_id": "strat_d589cd964a2da9b34f7a5ab5",
    "position_id": "pos_demo_fund",
    "scenario_type": "UNAUTHORIZED_MINTING",
    "scenario_scope": "HYPOTHETICAL",
    "target_node_id": "asset:1:0x1111111111111111111111111111111111111111",
    "horizon_days": 7,
    "parameters": {"assumed_token_haircut_ratio": "0.2"}
  }'
```

La réponse donne une perte conditionnelle de 400 000 USD. Copier exactement
`data.simulation_id` dans `loss_scenario_ref` ci-dessous. Cet identifiant dépend
du contenu du snapshot ; il ne faut pas réutiliser celui d'un autre dataset.
Avec le fixture actuel et la requête ci-dessus, il vaut
`stress:05e841a0bc5251995bca34c1`.

## 3. Comparer garder et sortir

Remplacer `SIMULATION_ID_RETOURNE` par l'identifiant reçu à l'étape 2 :

```bash
curl --fail-with-body -X POST http://127.0.0.1:8000/api/v1/decisions \
  -H 'Content-Type: application/json' -d '{
    "strategy_id": "strat_d589cd964a2da9b34f7a5ab5",
    "position_id": "pos_demo_fund",
    "mode": "normal",
    "horizon_days": 7,
    "scenario_assumptions": [{
      "loss_scenario_ref": "SIMULATION_ID_RETOURNE",
      "probability_over_horizon": "0.02",
      "basis": "USER_ASSUMPTION"
    }],
    "exit_quote_id": "quote:synthetic-full-position",
    "estimated_net_carry_usd": "1000",
    "carry_explanation": "Synthetic seven-day net carry assumption for the jury demo"
  }'
```

Résultats attendus : `CONDITIONAL`, `STAY`, valeur garder 793 000 USD,
valeur sortir 779 900 USD, coût de sortie 20 100 USD et seuil de bascule
`0.05275`, soit 5,275 %. Les explications et avertissements restent visibles.
Le devis de vente et le plan de financement du fixture sont des hypothèses.

Répéter avec `probability_over_horizon="0.10"` : le verdict devient `EXIT`.
Cela signifie « avec une probabilité supposée de 10 % », pas « le programme
prédit une crise à 10 % ». Demander `mode="predictive"` sans prévision revient
au même calcul Normal avec `fallback_reason="FORECAST_UNAVAILABLE"`.

## Vérifier automatiquement le parcours

Le script récupère lui-même l'identifiant de simulation, teste STAY puis EXIT,
et vérifie aussi le choc oracle ainsi que les aperçus rendement/clôture :

```bash
backend/.venv/bin/python backend/scripts/demo_analytics.py
backend/.venv/bin/python -m pytest -c backend/pytest.ini backend/app/tests tests/ml -q
```

Un choc oracle seul recalcule la santé de la position mais n'invente pas de
perte de vente : il ne suffit pas à produire ce verdict financier. Le scénario
de baisse explicite ci-dessus est celui à utiliser pour cette démonstration.

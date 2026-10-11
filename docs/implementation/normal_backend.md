# Backend Normal — carry et plan de clôture

Le parcours Graph → Stress → Decision fonctionne sans ML ni Monte Carlo.
Cette livraison ajoute deux previews et les raccorde à la décision existante.
Les champs Data V1 sont conservés ; les nouveaux paramètres sont optionnels.

## Carry déterministe

`POST /api/v1/carry-estimates` accepte :

```json
{
  "strategy_id": "STRATEGY_ID",
  "position_id": "POSITION_ID",
  "horizon_days": 7,
  "assumptions": {
    "pt_annual_return": "0.10",
    "borrow_apy": "0.05",
    "holding_fees_usd": "0",
    "explanation": "Illustrative annual effective rates and holding fees"
  }
}
```

Les identifiants sont à remplacer par ceux des données chargées. Les taux sont
des ratios annuels effectifs : `0.10` signifie 10 %, pas 0,10 %. Le calcul utilise
Decimal à 60 chiffres :

```text
gain collatéral = valeur actuelle USD × ((1 + taux PT)^(H/365) − 1)
coût dette     = dette actuelle USD × ((1 + APY emprunt)^(H/365) − 1)
carry net      = gain collatéral − coût dette − frais de détention
```

Sans `assumptions`, le moteur utilise l'APY implicite du marché Pendle exact
et l'APY d'emprunt Morpho, avec dates et preuves utilisables de moins de cinq
minutes. Les taux sont maintenus constants par hypothèse et les frais de
détention supplémentaires sont supposés nuls, avec avertissements explicites.
Les prix USD doivent être récents et prouvés ; l'horizon doit finir avant
l'échéance du PT. Sinon, le résultat est `INSUFFICIENT_DATA` et le carry reste null.

L'APY implicite du PT n'est pas une garantie de rendement USD. La projection
suppose une valeur USD de référence constante et des quantités constantes,
sans modéliser de variation du taux, du sous-jacent ou du token de dette.
Elle n'ajoute pas de second rendement PT à l'appréciation supposée. Elle ne
modifie jamais le prix oracle. Les frais de clôture sont traités séparément.

## Plan de clôture en lecture seule

`POST /api/v1/closing-plans` accepte :

```json
{
  "strategy_id": "STRATEGY_ID",
  "position_id": "POSITION_ID",
  "exit_quote_id": "QUOTE_ID",
  "assumptions": {
    "available_repayment_loan_units": "1400000",
    "gas_cost_usd": "100",
    "extra_fees_usd": "20",
    "financing_fee_usd": "30",
    "basis": "USER_ASSUMPTION",
    "explanation": "Illustrative external repayment funds and complete costs"
  }
}
```

Les fonds sont exprimés en unités entières du token de dette, pas en USD ni en
unités brutes du contrat. Le plan suppose que ces fonds sont disponibles avant
le remboursement : remboursement Morpho, retrait du collatéral, vente de la
quantité exacte de PT, récupération du principal de financement et déduction
des frais. Il ne modélise pas de flash loan ni de route atomique.

La réponse donne la dette à rembourser, les fonds déclarés, le déficit éventuel,
les étapes, les coûts et les fonds propres estimés après sortie :

- `sale_proceeds_usd` : montant du devis de vente converti en USD, déjà net des
  frais de venue et de l'impact de prix inclus par le fournisseur.
- `total_operation_cost_usd` : gaz + frais supplémentaires + frais de financement.
- `net_proceeds_usd` : vente moins ces coûts, avant remboursement de la dette.
- `exit_cost_usd` : écart entre la valeur actuelle du collatéral et ce montant net.
- `net_equity_after_exit_usd` : montant net moins la dette.

Le principal
de financement n'est pas compté une seconde fois comme une perte. Les frais
de venue déjà présents dans le montant net de la quote ne sont pas ajoutés à
nouveau ; `extra_fees_usd` exclut ces frais et les frais de financement séparés.

Une quote obtenue dans `/exit-quotes` contient son identifiant Core dans
`data.context.quote_id`. Elle doit correspondre à la position et à sa quantité.
Les deux previews ne collectent ni nouveau prix ni nouvelle quote.

Fonds insuffisants, remboursement externe non confirmé ou route connue comme
impossible : `EXIT_NOT_FEASIBLE`.
Quote absente, périmée ou incorrecte : `QUOTE_UNAVAILABLE`. Coûts, valorisation
ou faisabilité inconnus sans hypothèse complète : `INSUFFICIENT_DATA`.
Le devis doit avoir strictement moins de cinq minutes et ne pas avoir atteint
son `valid_until`. Une absence de financement est signalée même si d'autres
champs du plan restent inconnus.

Un plan accepté sous hypothèses reste `CONDITIONAL`, avec `USER_ASSUMPTION`.
Le moteur ne vérifie pas le solde d'un financeur et n'exécute aucune transaction.
Une route explicitement indisponible ou une clôture explicitement impossible
n'est pas rendue faisable par les hypothèses. Le gaz doit couvrir tout le plan,
pas seulement le swap Pendle. Les données peuvent changer avant exécution.

## Raccordement à Decision

Dans `POST /api/v1/decisions`, les mêmes objets se nomment `carry_assumptions`
et `closing_assumptions`. Ils accompagnent les identifiants et scénarios habituels.
Sans carry monétaire fourni, Decision appelle automatiquement l'estimation de
carry. Un montant `estimated_net_carry_usd` explicite reste accepté avec son
explication ; le combiner avec `carry_assumptions` renvoie HTTP 422.

Les réponses incluent `carry_calculation` et `closing_plan`. L'interface peut
donc exposer les paramètres, la provenance, les coûts et les raisons d'échec.
Une décision ne devient complète que si les autres entrées sont exploitables :
quote, pertes conditionnelles et probabilités utilisateur. Aucun verdict n'est
forcé à partir d'un incident non chiffrable ou de scénarios qui se chevauchent.
La maturité est contrôlée même avec carry manuel ou forecast : un horizon qui
atteint ou dépasse la maturité renvoie `INSUFFICIENT_DATA` et
`HORIZON_BEYOND_PT_EXPIRY`, sans carry ni valeur STAY chiffrés. Le remboursement
à maturité n'est pas modélisé ; aucun rendement post-maturité n'est extrapolé.

Stress conserve en interne le mode Data de son calcul, sans nouveau champ public.
Une décision rejette une simulation de mode différent ou de provenance interne
inconnue. Les quantités, marchés et preuves de valorisation doivent aussi partager
le même mode. Le mélange donne `MIXED_DATA_MODE_NOT_COMPARABLE`, aucun verdict
et aucune comparaison financière chiffrée. Le lecteur de forecast vérifie le
mode de ses preuves et refuse `valid_until <= now` à chaque décision.

## Preuves, stockage et qualité

Parquet conserve désormais `evidence_ids` et `interval_seconds` des DTO séries.
Les fichiers anciens restent lisibles ; leurs preuves et fréquence absentes
restent inconnues. Aucun identifiant n'est inventé pour les anciennes lignes.
Les nouveaux snapshots de refresh conservent leurs preuves. Le provider inclut
les preuves des séries dans le snapshot ; l'API séries les expose également.
Le Risk Engine refuse une observation dont les preuves appartiennent à un
autre mode Data ou à une autre chaîne.

Les trois alertes Gitleaks historiques examinées concernent deux adresses
publiques de tokens et un argument de helper de test. `.gitleaksignore` ne
contient que leurs empreintes exactes ; il ne désactive aucune règle globale.
Après intégration de l'archive Monte Carlo réelle, six autres correspondances
examinées sont également des adresses publiques de tokens ; seules leurs
empreintes exactes dans l'archive et son commit sont exclues.

## Vérifier et démontrer

```bash
backend/.venv/bin/python -m pytest -c backend/pytest.ini backend/app/tests tests/ml -q
npx --yes markdownlint-cli2@0.23.3
```

Le script `backend/scripts/demo_analytics.py` démontre aussi les deux previews
et une décision Normal avec carry calculé, sur le serveur lancé avec le snapshot
DEMO décrit dans le README. Les montants, taux et financements y sont synthétiques.

La livraison ne complète pas artificiellement les audits, timelocks, contrôles
administrateur ou dépendances économiques encore non documentés. Ces éléments
restent inconnus tant que leurs sources et méthodes ne sont pas fournies.
Les modules prédictif ML et Monte Carlo ne sont pas modifiés par cette livraison.

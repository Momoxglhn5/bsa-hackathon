# Parcours jury — DeFiNe

Démonstration de l'application React en trois minutes environ. L'interface est en
anglais ; les libellés en gras ci-dessous sont ceux affichés à l'écran. Les chiffres
ont été relevés dans l'interface sur le jeu « Demo ». Les montants de position sont
fixes ; le carry et les dates varient légèrement selon l'heure de génération du jeu.

## Avant de présenter

```bash
scripts/radar-dev.sh
```

1. Attendre la ligne `Local: http://localhost:5173/` de Vite.
2. Ouvrir <http://localhost:5173>. En secours, <http://localhost:5173/#dashboard>
   saute l'intro 3D.
3. Vérifier la pastille **DEMO** à gauche du sélecteur de position.

Le script régénère le jeu de démo à chaque lancement : les prix restent dans la
fenêtre de fraîcheur de 5 minutes exigée par les moteurs. Relancer le script
juste avant le passage devant le jury.

## Jeu de données « Demo »

Cinq marchés Pendle PT utilisés comme collatéral sur Morpho Blue, sept positions
réparties entre quatre fonds fictifs, 90 jours d'historique, un devis de sortie
par position et le catalogue réel d'incidents DefiLlama.

| Fonds | Marché | Collatéral | LTV / LLTV | Health factor | Risk index |
| --- | --- | ---: | --- | ---: | ---: |
| Atlas Fund | PT-sUSDe → USDC | 4,000,000 USD | 80.0% / 91.5% | 1.144 | 47 |
| Atlas Fund | PT-USDe → USDC | 2,500,000 USD | 86.0% / 91.5% | 1.064 | 50 |
| Nova Treasury | PT-sUSDe → USDC | 1,500,000 USD | 62.0% / 91.5% | 1.476 | 47 |
| Nova Treasury | PT-USD0++ → USDC | 2,000,000 USD | 78.0% / 86.0% | 1.103 | 62 |
| Orion Reserve | PT-wstETH → WETH | 3,000,000 USD | 60.0% / 86.0% | 1.433 | 35 |
| Orion Reserve | PT-weETH → WETH | 1,200,000 USD | 72.0% / 86.0% | 1.194 | 44 |
| Helix Fund | PT-wstETH → WETH | 900,000 USD | 74.0% / 86.0% | 1.162 | 35 |

Marchés, prix, adresses et positions sont synthétiques : les scores ne
constituent pas une évaluation d'Ethena, d'Usual, de Lido ou d'ether.fi.
La pastille DEMO le rappelle à l'écran.

## Déroulé

### 1. Intro — 15 s

Faire défiler, puis cliquer sur **Open the dashboard**.

> "A fund borrows USDC against Pendle PTs. Its position depends on an oracle,
> a Morpho market, an SY, a stablecoin… DeFiNe makes those dependencies visible
> and prices what happens when one of them breaks."

### 2. Explore — 40 s

Position **Atlas Fund · PT-sUSDe → USDC** (sélectionnée par défaut).

- Résumé : 4,000,000 USD de collatéral, 3,201,621 USDC de dette,
  798,699 USD d'equity, LTV 80% pour un seuil de 91.5%, health factor 1.144.
- **Risk index** 47 / 100, **Data coverage** 100%. Dans **Risk dimensions**,
  **Dependencies 100 / 100** domine.
- **Watchlist** : marge de 11.5 points de LTV avant liquidation, échéance du PT,
  lien de backing USDe non vérifié.
- **History** : 90 jours de prix oracle, APY implicite, utilisation.

### 3. Graph — 30 s

Bouton **Graph** en haut.

- Cliquer sur l'**Oracle** : 2 étapes jusqu'à la position, criticité Critical,
  transmission par valuation.
- **Exposed positions** : Atlas Fund et Nova Treasury, 5,500,000 USD de
  collatéral exposé à ce seul oracle.
- Premier sélecteur → **All positions** : les sept positions et leurs
  dépendances partagées (USDC, USDe, WETH, rate model Morpho).

### 4. Simulate — 50 s

**← Back to Dashboard**, onglet **Simulate**. Les valeurs sont pré-remplies.

1. **Oracle manipulation**, −15%, **Run simulation** : LTV 80% → 94.1%,
   health factor 1.144 → 0.972, position **Liquidatable**. Deux positions touchées.
2. **Unauthorized mint** sur PT-sUSDe, haircut 20%, **Run simulation** :
   perte de **800,000 USD** pour Atlas Fund, equity à −1,301 USD ;
   Nova Treasury perd 300,000 USD.
3. Cliquer sur **Use in STAY/EXIT →**.

Option : **Monte Carlo**, volatilité 25%, 10,000 paths sur 7 jours :
0.02% de franchissement de la LLTV, VaR 95% ≈ 224,000 USDC.

### 5. Decide — 45 s

Onglet **Decide** ; le mint est coché.

- **Net carry** calculé automatiquement : ≈ +2,959 USD sur 7 jours.
- Devis **Sell PT-sUSDe → USDC** : 4,052,274 PT-sUSDe → 3,970,669 USDC,
  price impact −0.74%.
- **Closing plan** FEASIBLE : total exit cost 30,199 USD.
- Probabilité **2** → **Compare STAY / EXIT** : **Stay in the position**
  (785,658 USD contre 768,501 USD).
- Probabilité **10** → **Compare STAY / EXIT** : **Exit the position**
  (721,421 USD contre 768,501 USD).
- Break-even : **4.14%** sur 7 jours. Le graphique **Probability sensitivity**
  montre le changement de verdict.
- Option : mode **Predictive** ; le carry vient d'une prévision validée
  (≈ +2,722 USD) et le seuil passe à 4.12%.

> "Below a 4% weekly risk of an unauthorized mint, the fund stays;
> above it, it exits — with the real exit cost already computed."

## Questions probables

| Question | Réponse |
| --- | --- |
| Is the data real? | The Demo set is synthetic so every screen is populated. The **Live market** set (DEMO chip → Data) replays a Pendle/Morpho market recorded on-chain, with simulated 1, 2 and 5 M$ funds. DefiLlama incidents are real. |
| Where do positions come from? | Simulated funds. The real collector (`backend/scripts/finish_data.py`) reads markets, prices, history and quotes; it does not track wallets yet. |
| Is the risk index a probability? | No, it is an ordinal 0–100 index. STAY/EXIT probabilities are user inputs, and the break-even shows when they change the decision. |
| Does the tool execute transactions? | No. Everything is read-only, closing plan included. |

## En cas de problème

| Symptôme | Action |
| --- | --- |
| « Data not loaded » | Vérifier que le script tourne, puis **Retry**. |
| Devis « EXPIRED » ou décision indisponible | Relancer `scripts/radar-dev.sh` pour régénérer le jeu. |
| Intro 3D lente | Ouvrir directement `http://localhost:5173/#dashboard`. |
| Pas d'Internet | Tout fonctionne ; seules les polices Google passent en police système. Ouvrir l'application une fois connecté avant la présentation pour les mettre en cache. |

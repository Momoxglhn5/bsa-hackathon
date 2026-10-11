# Monte Carlo — contrôle sur marché réel du 11 octobre 2026

Statut : **validation fonctionnelle effectuée sur des entrées de marché réelles ;
calibration observée sur 30 jours indisponible**. Ce dossier ne valide pas une
probabilité réelle de liquidation ni la pertinence prédictive du GBM.

## Données et résultats

- Marché Morpho : `0x0c93c623fd2a78a2ffa36b019f74c400c3f3d81fcb22851c88e0c96e510f9689`.
- Oracle : `0xf9c1d6954eb6c59731cd277bc27bbc5239c67570`.
- Historique RPC : **11 minuits UTC**, du 1er au 11 octobre, avec blocs, hashes et valeurs brutes.
- Fenêtre obligatoire : 31 points, donc **20 échantillons manquants**.
- Fonds de contrôle : 1 000 000 PT et dette de 580 000 USDC, position **SIMULATED**, pas un wallet réel.
- Runs fonctionnels : 10 000 trajectoires, seed 42, horizons 1/3/7 jours, volatilité **supposée** 25 % par an et drift zéro.
- Les trois runs sous hypothèse renvoient `OK` avec `HYPOTHETICAL_INPUT_SCOPE`.
- Les trois demandes de calibration observée renvoient `UNAVAILABLE`, sans trajectoires.
- Les répétitions à seed identique donnent les mêmes résultats ; les prix sans preuve correspondante et les entrées périmées sont refusés.

La volatilité de 25 % est un paramètre de contrôle du calcul, choisi explicitement
pour cette vérification. Elle n'est ni une mesure des onze jours ni une estimation
du risque de ce marché. Les éventuelles fréquences nulles ne prouvent pas
l'absence de risque. Les VaR/ES sont des pertes de valorisation oracle en unités
du token emprunté ; le champ de perte de liquidation réalisée reste vide.

## Pourquoi les jours précédents sont indisponibles

Une collecte de 31 jours a réussi les onze lectures récentes. Les lectures des
30, 29 et 28 septembre ont renvoyé `0x`, donc aucun prix décodable. Deux lectures
historiques indépendantes `eth_getCode` confirment :

| Minuit cible UTC | Dernier bloc précédent | Taille du bytecode oracle |
| --- | --- | --- |
| 30 septembre | 26086569 | 0 octet |
| 1er octobre | 26093737 | 2598 octets |

Les blocs et leurs hashes sont enregistrés dans `contract_checks.json`.
Le contrat était absent au premier bloc et présent au second ; ces contrôles
bornent son apparition sans prétendre identifier son bloc de déploiement exact.
Il est impossible d'obtenir le prix de ce contrat avant qu'il existe. Une clé
RPC différente ne crée pas ces données.

Avec la grille actuelle, la première fenêtre complète peut se terminer au
**31 octobre 2026 UTC**, si chaque minuit depuis le 1er octobre reste accessible.
Une position réelle vérifiée est également nécessaire pour le périmètre
`REAL_DATA` ; les fonds simulés restent hypothétiques après calibration.
L'oracle paraît évoluer de manière régulière sur ces onze observations : sa
faible variabilité ne doit pas être interprétée comme une couverture des risques
de liquidité, de décrochage du PT, de backing ou d'incident de protocole.

## Rejouer sans réseau

Depuis la racine du dépôt, avec l'environnement backend :

```bash
.venv/bin/python backend/scripts/validate_monte_carlo_real.py \
  --snapshot docs/validation/monte_carlo_20261011/snapshot.json \
  --strategy-id strat_e76bee1f14994d77f99f42a7 \
  --position-id pos_mc_validation --assumed-volatility 0.25 \
  --output /tmp/monte-carlo-replay.json
```

Choisir un nouveau fichier de sortie s'il existe déjà. Le runner ne contacte
aucun fournisseur et ne modifie pas les stores. Il passe par la route FastAPI
réelle `/api/v1/monte-carlo` en replay à la date du snapshot. Retirer
`--assumed-volatility` pour vérifier uniquement la calibration observée.

Le code retour zéro indique que les contrôles fonctionnels ont réussi. Consulter
le champ `status` : `PARTIAL_VALIDATION` signifie que la calibration observée
reste impossible. Un futur `OBSERVED_PIPELINE_CHECKS_PASSED` attestera seulement
l'exécution de cette chaîne avec la fenêtre requise, pas une validation économique
ou statistique de ses fréquences de franchissement.

Pour renouveler les données et retenter la calibration :

```bash
.venv/bin/python backend/scripts/backfill_oracle_history.py --days 31
PYTHONPATH=backend .venv/bin/python -m app.ingestion.refresh --task refresh_current
.venv/bin/python backend/scripts/check_oracle_calibration.py
```

Exporter ensuite un nouveau snapshot avec la position voulue et rejouer le runner.
Le script d'export permet de déclarer un fonds simulé ; il ne transforme pas cette
hypothèse en position on-chain. Les RPC sont configurés dans les fichiers locaux
ignorés par Git ; aucune clé ou URL contenant une clé n'est incluse ici.

## Fichiers

`snapshot.json` conserve les DTO et preuves nécessaires au replay, avec une
seule position simulée. `report.json` conserve les six réponses API, les contrôles,
les points historiques et le hash du snapshot. `contract_checks.json` conserve
les deux observations de bytecode. Ces archives sont datées ; elles ne constituent
pas des données de marché courantes.

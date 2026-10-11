# Revue backend — 11 octobre 2026

Suivi : les trois défauts ci-dessous sont corrigés localement par le lot de
fermeture. Voir [les résultats et limites actuels](../demo/backend_closure.md).
Ce document conserve les observations de la revue avant correctifs.

## Périmètre et résultat

Revue du code courant, y compris les modifications Normal locales non publiées,
sur `main` au commit `1c25f7f`. Un fetch confirme zéro commit d'écart avec
`origin/main` au moment de la revue. Les modifications locales ne sont pas
présentes sur le distant. Aucun code applicatif modifié pendant cette revue.

Références produit : architecture V1.1, brief Core Analytics fourni et grille
transmise en capture. Les poids de cette grille sont ceux fournis par le projet ;
la revue ne constitue pas une nouvelle vérification des règles du hackathon.

Le parcours est cohérent avec l'intention du MVP, mais il n'est pas entièrement
validé sur données réelles. Le plan de clôture conditionnel est implémenté ;
la disponibilité réelle du financement et le coût d'exécution complet restent
à prouver. Trois défauts reproductibles sont décrits ci-dessous.

## Défauts reproductibles

### P2 — Forecast REAL accepté avec preuve DEMO

Fichier : `backend/app/decision/forecast.py`, lignes 61–64.

Le lecteur vérifie la présence, le statut et la date des preuves, sans comparer
leur `data_mode` à celui de la position et du marché. Reproduction avec le
journal synthétique de `test_marilou_predictive.study` : produire les bundles
par `project`, conserver toutes les données REAL, puis changer uniquement la
preuve dérivée de validation en DEMO. Le snapshot revalidé par Pydantic est
accepté et la décision renvoie `CONDITIONAL`, `effective_mode=predictive`,
`fallback_reason=null`, `verdict=STAY`.

Il faut rejeter les preuves de mode incompatible au lecteur, même si le
producteur officiel les contrôle déjà. Ce défaut concerne des entrées analytiques
fournies au serveur ; aucun endpoint public d'import n'a été identifié.
Une chaîne non supportée est déjà refusée par les DTO et n'est pas nécessaire
pour reproduire ce défaut.

### P2 — Carry manuel contourne la maturité

Fichier : `backend/app/decision/engine.py`, lignes 46–53.

La vérification `horizon_before_pt_expiry` est dans `estimate_carry`, appelé
seulement lorsqu'aucun montant de carry n'est fourni. Une requête avec carry
manuel et horizon de 365 jours est donc comparée avec le modèle courant malgré
la maturité du PT pendant cet horizon. Reproduction sur `analytics_demo.json` :
snapshot du 10 octobre 2026, maturité du 8 janvier 2027, stress minting à 365 jours,
carry explicite de 1 000 USD, probabilité de 2 %. Résultat : `CONDITIONAL`, `STAY`.

Il faut vérifier la maturité indépendamment du mode et de la source du carry,
ou implémenter explicitement un modèle post-maturité. Aucun modèle de ce type
n'est présent dans cette comparaison.

### P2 — Forecast encore accepté à son échéance exacte

Fichier : `backend/app/decision/forecast.py`, ligne 54.

`valid_until < now` accepte `now == valid_until`. Un appel direct à
`forecast_carry` à l'échéance retourne encore le montant et l'identifiant du
bundle. La quote de clôture rejette déjà cette même frontière avec `<=`.
Aligner le contrôle du forecast sur une validité exclusive à l'échéance.

## État des blocs

| Bloc | Disponible | Limites restantes |
| --- | --- | --- |
| Data | Collecte, normalisation, SQLite/Parquet, preuves, historiques, quotes de taille exacte, replay | Stores live absents dans ce checkout ; collecte réelle non relancée pendant cette revue ; sortie de 5 M historiquement refusée par le fournisseur |
| Graphe | Orientation dépendant → dépendance, jointure par adresse/chaîne, propagation inverse, cycles, déduplication, preuves | Backing économique non résolu ; implémentation oracle, source de rendement et gouvernance encore inconnues |
| Risk | Facteurs explicables, couverture, confiance, santé LTV/HF séparée | Sur l'archive réelle : couverture 30 %, score global masqué ; audits, contrôles, timelocks, âges/version et historique de liquidité insuffisants |
| Stress déterministe | Oracle manipulation/outage, minting, cross-chain conditionnel ou non applicable | Choc oracle : santé calculée, perte d'exécution inconnue ; minting sous-jacent : pas de transmission automatique ; bridge non prouvé : pas de scénario réel forcé |
| Normal STAY/EXIT | Comparaison, seuil de bascule, carry net déterministe, financement/frais explicites et plan de clôture | Clôture sous hypothèses uniquement ; garde de maturité à compléter pour le carry manuel ; démo complète réelle avec quote et plan encore à archiver |
| Prédictif ML | Adaptateurs Chronos-2/TimesFM/Bolt, émission prospective, provenance brute vérifiée, validation par actif/horizon, projection financière et repli Normal | Aucun modèle validé sur le PT cible ni bundle réel accepté livré ; politique de 12 fenêtres hebdomadaires ; deux défauts du lecteur Core ci-dessus |
| Monte Carlo | Simulation oracle, HF/LTV, fréquence de franchissement, Wilson, VaR/ES, seed reproductible, calibration RPC et endpoint | 11 observations réelles sur 31 ; 20 manquantes ; fonds simulé ; runs fonctionnels à volatilité supposée, calibration observée indisponible |

Les pertes Monte Carlo sont en unités du token emprunté, pas des pertes USD
réalisées. La fréquence de franchissement simulée ne devient ni probabilité
objective de liquidation, ni probabilité d'incident dans Decision.

Les deux anciennes anomalies ML sont corrigées dans le producteur : vérification
des prix contre les réponses Pendle brutes et horodatage après inférence avec
contrôle des échéances. Les régressions correspondantes passent. Cela ne valide
pas la performance économique du modèle.

## Vérification de la capture

- Dépendances, 30 % : partiellement résolu. L'archive réelle produit 11 nœuds
  et 13 arêtes, dont `BACKED_BY=UNRESOLVED`. Le graphe reste `PARTIAL`.
- Impact défendable, 30 % : moteurs opérationnels sous hypothèses explicites.
  Monte Carlo réel reste `PARTIAL_VALIDATION`, sans calibration observée.
- STAY/EXIT et pourquoi, 25 % : calcul et explication de clôture conditionnelle
  désormais présents. Aucun contrôle de solde de financeur ni estimation prouvée
  du gaz total n'a été ajouté. Le blocage réel n'est donc pas entièrement levé.
- Démo réelle, 15 % : replay réel Monte Carlo vérifié ; parcours utilisateur
  complet non vérifié. Aucun frontend applicatif trouvé dans le dépôt.

Sur `docs/validation/monte_carlo_20261011/snapshot.json`, le carry à sept jours
est calculable et conditionnel, environ 1 347,09 USD. Le stress minting avec
haircut explicite de 20 % chiffre environ 195 464,20 USD de perte. Le snapshot
ne contient aucune quote ni forecast : la décision reste `QUOTE_UNAVAILABLE`,
verdict absent, avec repli `FORECAST_UNAVAILABLE` si Predictive est demandé.
Ces chiffres sont un replay daté sur fonds simulé, pas des valeurs courantes.

## Intégration et vérifications

- Suite Data + Core + ML exécutée : **420 tests passent**, un avertissement
  tiers Starlette/HTTPX.
- Runner Monte Carlo réel rejoué sur l'archive, 10 000 trajectoires, seed 42,
  volatilité supposée 25 %, horizons 1/3/7 : tous les contrôles fonctionnels
  passent ; résultat `PARTIAL_VALIDATION`, 11 points disponibles, 20 manquants.
- Probes supplémentaires : les trois défauts ci-dessus reproduits. Ils ne sont
  pas couverts par les 420 tests existants.
- Aucun téléchargement de poids ni nouvelle inférence réelle effectué.
- Aucun appel live aux fournisseurs, aucune transaction ni publication.

L'API expose les previews carry/clôture, mais pas de route pour créer/recalculer
une position simulée à un montant libre. La livraison Data prépare trois tailles
fixes ; le curseur de taille libre du brief exige encore un raccordement backend.
Les quantiles et métriques détaillés ML restent dans les fichiers hors ligne ;
la route de consultation forecast prévue au brief n'est pas exposée.

La CI exécute `backend/app/tests` mais pas `tests/ml` ; la suite ML complète
est donc vérifiée localement sans être entièrement imposée par le workflow.
`CORE_ENGINES_INTEGRATION.md` contient encore des passages historiques devenus
faux, notamment sur les preuves Parquet, le carry manuel obligatoire et le
nombre de tests. Les documents Normal et Predictive récents sont plus à jour.

Priorités : corriger les trois gardes, archiver un parcours réel complet avec
quote exacte et hypothèses de financement visibles, résoudre le backing
économique documentable, puis raccorder l'interface et ses tailles de position.

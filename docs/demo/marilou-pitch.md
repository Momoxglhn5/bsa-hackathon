# Marilou — démo BSA prête à présenter

## Pitch de 60 à 90 secondes

« Dans DeFi Risk Radar, nous analysons des stratégies Morpho qui utilisent des
PT Pendle. Marilou explore une question complémentaire : quelle trajectoire le
prix de marché d’un PT pourrait-il suivre dans les sept prochains jours ?

Nous avons collecté quatre historiques réels de PT Ethereum, de 123 à 148
clôtures quotidiennes par actif, en conservant les sources et les preuves
permettant de vérifier les contrats. Nous ne remplaçons pas les données
manquantes par des prix inventés.

Voici l’historique, la médiane prédite par TimesFM et son intervalle
expérimental. La ligne orange est notre référence simple : le prix ne change
pas. On peut aussi revenir sur deux semaines de test et comparer la prévision
aux prix réellement observés.

Nous avons exécuté trois modèles préentraînés sur les mêmes données. Dans
cette comparaison, TimesFM réduit l’erreur moyenne à sept jours de 13,2 % face
à Chronos-2 et de 4,7 % face à la référence prix inchangé.

Nous montrons aussi la limite : l’intervalle nominal de 80 % ne couvre que
50 % des résultats à J7 sur ce petit test. Le prototype fonctionne, mais il
reste expérimental. La prochaine étape est de figer le modèle et de le tester
sur de nouvelles observations, puis d’évaluer le PT précis de notre stratégie.
Les prévisions ne déclenchent aucune décision financière. »

## Parcours de démonstration — environ deux minutes

1. Ouvrir `index.html`. Montrer le statut expérimental et la date du snapshot.
2. Garder PT-reUSD, premier actif de la liste. Montrer l’historique, la médiane,
   la baseline et l’intervalle. Le graphe affiche les 30 dernières clôtures
   et les sept prévisions futures enregistrées le 10 octobre 2026.
3. Choisir « Test rétrospectif · semaine 1 ». Les prix réellement observés
   après la coupure deviennent visibles. Les prévisions archivées sont
   uniquement celles de J1, J3 et J7 ; les segments entre ces points sont visuels.
4. Montrer la semaine 2, puis changer d’actif pour montrer que les quatre
   historiques sont distincts. Ne pas choisir uniquement le meilleur résultat.
5. Montrer la comparaison à J7, puis à J1 : TimesFM ne gagne pas à chaque horizon.
6. Terminer sur la couverture de 50 % et le besoin d’un nouveau test indépendant.
7. Si le jury demande les preuves, ouvrir « Source & traçabilité de l’actif ».

## Réponses courtes aux questions du jury

**Avez-vous entraîné un modèle ?**

Nous avons utilisé des modèles préentraînés sans mise à jour de leurs poids.
Nous avons implémenté la collecte, les adaptations de format, les contrôles,
le benchmark et la démonstration.

**Que signifie le gain de 13,2 % ?**

C’est une réduction de l’erreur absolue moyenne du prix en USD à J7 par rapport
à Chronos-2, sur huit fenêtres de test réparties entre quatre PT. Ce n’est
ni un rendement financier ni un taux de réussite de 86,8 %.

**Le modèle est-il validé ?**

Non. Nous avons réutilisé un test déjà consulté pour comparer les modèles.
Les actifs ont deux semaines de test chacun, sur les mêmes dates, et les
erreurs peuvent être corrélées. Il faut un nouveau test indépendant.

**Pourquoi montrer un intervalle qui n’est pas fiable à 80 % ?**

Parce qu’il décrit la sortie probabiliste du modèle, et nous affichons sa
couverture mesurée. Il ne représente pas une garantie de confiance calibrée.

**Cela prédit-il une liquidation ou un hack ?**

Non. Cela prédit le prix de marché du PT en USD. Le prix de marché n’est pas
l’oracle Morpho, et le modèle ne produit pas de probabilité de hack.

**Les résultats concernent-ils votre stratégie réelle ?**

Les quatre PT servent à un benchmark distinct. Ils ne valident pas le PT-siUSD
initial. Le modèle ne remplace pas les décisions analytiques existantes.

**Est-ce en direct ?**

Cette démo est un snapshot hors ligne du 10 octobre 2026. Les données et les
prévisions sont réelles et enregistrées ; changer la sélection ne lance pas
une nouvelle inférence. La collecte et l’inférence ont leurs scripts séparés.

## Ce qu’on peut affirmer

Prototype fonctionnel sur données réelles ; prévision jusqu’à J7 ; comparaison
chronologique avec des baselines ; gain exploratoire ciblé ; limites mesurées.

Éviter : « modèle validé », « 80 % de chances que le prix reste dans cet
intervalle », « prédit les liquidations », « garantit une bonne décision » ou
« gagne sur tous les actifs ». Les révisions et la disponibilité historique
originale des prix du fournisseur n’ont pas été auditées.

## Relancer la démo depuis le dépôt

Les exports réels restent locaux et ignorés par Git. Depuis la racine du dépôt,
avec l’environnement Python utilisé pour les benchmarks :

```bash
PYTHONPATH=backend .venv/bin/python -m ml.build_demo \
  --datasets backend/data/ml/comparable_histories_20261010 \
  --report backend/data/ml/timesfm_comparable_run_20261010/summary.json \
  --comparison backend/data/ml/pretrained_comparison_20261010/comparison.json \
  --output backend/data/ml/hackathon_demo_20261010
```

Ouvrir `backend/data/ml/hackathon_demo_20261010/index.html` dans un navigateur.
Le fichier embarque les données nécessaires : pas de serveur, de clé API,
de téléchargement de poids ou de connexion internet nécessaire pour la démo.

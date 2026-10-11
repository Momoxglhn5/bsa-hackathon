# DeFi Risk Radar — brief webapp pour le designer

Ce brief accompagne la codebase. Il décrit le parcours à concevoir, les actions
utiles et les limites du backend actuel. Les placements proposés sont un point
de départ : tu es libre de proposer une meilleure composition et une identité
visuelle forte, tant que le parcours et la signification des données restent clairs.

État de référence : `main`, commit `2b75272`, consulté le 11 octobre 2026.
Il s'agit d'une cible UX à concevoir, pas d'une description d'un frontend livré.

Cette révision intègre la livraison Data : prix USD sourcés, positions simulées
de 1/2/5 M USD, cotations de vente pour des quantités exactes et catalogue
d'incidents. Leur disponibilité dépend du bundle collecté localement ; les
données réelles ne sont pas livrées automatiquement par un clone Git.

## 1. Le produit en une phrase

Comprendre de quoi dépend une position DeFi, simuler la défaillance d'une
dépendance et comparer le fait de conserver ou de sortir de la position sous
des hypothèses explicites.

Le cas central est une position d'emprunt Morpho avec un PT Pendle en collatéral,
sur Ethereum. L'utilisateur visé connaît la DeFi, mais doit pouvoir comprendre
rapidement où se situe son exposition et pourquoi un résultat change.

La webapp est un outil d'analyse en lecture seule. STAY/EXIT est une comparaison
conditionnelle ; aucun bouton ne vend des tokens, rembourse une dette ou signe
une transaction. Une connexion wallet n'est pas nécessaire au parcours MVP.

## 2. Architecture de navigation proposée

Conserver trois écrans principaux, cohérents avec le périmètre produit du repo :

1. **Explorer** : accueil/dashboard et graphe des dépendances.
2. **Simuler** : paramétrage d'un choc et lecture de ses impacts.
3. **Décider** : comparaison STAY/EXIT et sensibilité aux hypothèses.

Les preuves, la méthodologie et l'état des données s'ouvrent en panneaux
contextuels, sans imposer de nouvelles pages.

Navigation persistante proposée : logo, Explorer / Simuler / Décider, stratégie
sélectionnée, position sélectionnée et état des données. Sur petit écran,
adapter cette structure sans perdre le contexte de l'analyse.

La stratégie et la position doivent rester les mêmes entre les trois écrans.
Changer de contexte invalide les résultats qui ne lui correspondent plus.
Le mode de données Réel / Démo, s'il est proposé, est distinct du mode d'analyse
Normal / Prédictif. Ces deux contrôles ne doivent jamais être confondus.
Une position simulée peut utiliser des marchés réels : afficher alors
**Marchés réels · Position simulée**, plutôt qu'un simple badge « Réel ».

Une landing marketing séparée est facultative et secondaire. Pour la démo,
l'ouverture de l'app doit donner accès rapidement à une stratégie et à sa valeur.

## 3. Écran 1 — accueil, dashboard et graphe

### Première impression

L'accueil répond immédiatement à trois questions : quelle position regarde-t-on,
quel est son état, et quelle dépendance mérite d'être examinée ?

Proposition de composition :

- En-tête : nom lisible de la stratégie, Morpho + Pendle, réseau, échéance du PT.
- Sélecteurs de stratégie et de position ; badge position on-chain ou simulée.
- Pour la livraison Data : presets **1 M$ / 2 M$ / 5 M$ de collatéral**, reliés
  aux positions effectivement disponibles. La dette initiale supposée vaut
  70 % de la valeur de marché du collatéral ; ce n'est pas un objectif de LTV
  oracle, ni une preuve de capacité d'emprunt.
- Résumé financier : collatéral, dette, fonds propres, LTV, seuil LLTV et health
  factor, seulement lorsque calculables. Les unités doivent être explicites.
- Trois indicateurs distincts : indice de risque, couverture des données et
  confiance dans les preuves. Ne pas les fusionner en un score de sécurité.
- Zone centrale avec contrôle **Synthèse / Graphe**, placé au-dessus du contenu.
- Action principale **Simuler un choc**, accessible depuis les deux vues.

La Synthèse présente les facteurs de risque connus, les informations manquantes
et les dépendances importantes. Le Graphe montre les relations entre position,
marché Morpho, PT, marché Pendle, SY, oracle et autres composants effectivement
présents dans la réponse API.

Le designer peut donner davantage de place au graphe, voire l'ouvrir par défaut,
si le résumé de position et les actions restent faciles à trouver.

### Où se trouve le bouton Graph View ?

Il s'agit du segment **Graphe** dans **Synthèse / Graphe**, visible dans l'en-tête
de la zone principale. Un clic change la représentation de la même stratégie.
Ce n'est ni un réglage caché ni l'activation d'un calcul prédictif.

### Interactions et boutons

| Contrôle | Effet attendu |
| --- | --- |
| Sélecteur stratégie / position | Change le contexte de toute l'analyse. |
| 1 M$ / 2 M$ / 5 M$ de collatéral | Sélectionne une position simulée existante ; ne crée pas de position ni de quote. |
| Synthèse / Graphe | Change la représentation sans perdre le contexte. |
| Rechercher dans le graphe | Retrouve un nœud par nom ou adresse. |
| Zoom + / −, Recentrer | Rend le canvas navigable ; prévoir une alternative clavier. |
| Clic sur un nœud | Ouvre son inspecteur : rôle, adresse, dépendances connues. |
| Clic sur une relation | Affiche type de relation, vérification, date et preuves. |
| Voir les preuves | Ouvre le panneau de provenance du composant sélectionné. |
| Simuler ce composant | Préremplit une cible compatible dans l'écran Simuler. |
| Simuler un choc | Ouvre le simulateur avec la stratégie et la position courantes. |
| État des données | Ouvre le panneau fraîcheur, couverture et éléments manquants. |
| Historique d'incidents | Ouvre un panneau documentaire sourcé, sans modifier les probabilités. |
| Recharger les données | Relit les données disponibles ; ne promet pas une collecte live. |

Les prix de marché USD du PT et de l'actif de dette peuvent désormais être
alimentés par le pipeline. Montrer leur date et leur source. Les historiques
réellement disponibles peuvent alimenter des mini-graphiques ; afficher la
période couverte, les trous et l'absence de série, sans inventer 180 jours
parce que la collecte a demandé cette fenêtre.

Différencier visuellement les relations confirmées, observées, hypothétiques
et non résolues. Utiliser une légende, des styles de traits et des libellés,
pas seulement des couleurs. Une dépendance inconnue ne doit pas apparaître sûre.
Les flèches expriment le sens de la relation ; la propagation d'un incident
doit être expliquée séparément, pas déduite d'une animation ambiguë.

Si aucun composant n'est sélectionné, l'inspecteur propose une courte aide.
Si le graphe est partiel, montrer les classes de dépendances non documentées.

## 4. Écran 2 — simulateur de stress

Objectif : comprendre ce qui serait touché si un composant échouait, puis ce
qui peut réellement être chiffré.

Proposition : formulaire à gauche, impacts à droite, graphe compact en dessous
ou au centre. L'organisation peut changer ; conserver une lecture avant/après.

### Formulaire

| Élément | Contenu |
| --- | --- |
| Type de scénario | Manipulation d'oracle, panne d'oracle, mint non autorisé, défaillance cross-chain. |
| Cible | Composant du graphe compatible avec le scénario. |
| Horizon | Durée explicite, reprise ensuite dans la décision. |
| Qualification de la cible | Vérifiée, observée ou hypothétique, selon les données et le scénario. |
| Hypothèses | Champs spécifiques au scénario, expliqués dans un langage simple. |
| Lancer la simulation | Calcule le résultat ; affiche un état d'attente honnête. |
| Réinitialiser | Efface les hypothèses de ce formulaire. |

Champs spécifiques actuellement acceptés :

- Manipulation d'oracle : variation du prix oracle en pourcentage.
- Panne d'oracle : cible ; pas de curseur de durée dans le contrat actuel.
- Mint non autorisé : quantité émise, dégradation du backing et/ou décote de prix
  supposée. Aucune équivalence automatique entre quantité émise et perte de prix.
- Cross-chain : durée, service indisponible (redemption ou transfert bridge),
  éventuellement décote supposée et confirmation de son application au collatéral.

Ne proposer que les champs pertinents. Un scénario cross-chain peut être non
applicable faute de dépendance représentée ; permettre ce résultat honnête.

### Résultat

Afficher séparément capital exposé confirmé, exposition potentielle, capital
opérationnellement bloqué et perte estimée. Une exposition n'est pas une perte.

Présenter les positions touchées, les chemins de propagation et les métriques
avant/après disponibles : fonds propres, health factor et risque de liquidation.
Les montants agrégés et le détail de la position sélectionnée doivent être
identifiables pour éviter les doubles interprétations.

Actions : **Modifier les hypothèses**, **Voir le chemin d'impact**, **Voir les
preuves**, **Utiliser dans STAY/EXIT**. Conserver l'identifiant de simulation et
son horizon pour la décision. Plusieurs scénarios peuvent être sélectionnés
ensuite ; un historique persistant de simulations serait une extension.

Une modification du formulaire rend l'ancien résultat explicitement périmé
jusqu'au prochain calcul. Ne pas laisser croire qu'il reflète déjà les nouveaux champs.

## 5. Écran 3 — décision STAY / EXIT

Objectif : comparer les deux options sur une même position, en USD et sur le
même horizon, en montrant ce qui fait basculer le résultat.

### Où se trouve le mode prédictif ?

Placer **Normal / Prédictif** en haut de cet écran, près du sélecteur d'horizon.
Ce contrôle est visible avant le calcul et reste visible dans le résultat.
Les horizons prédictifs prévus sont J1, J3 et J7.

Le mode Normal doit suffire au parcours complet. Si Prédictif est demandé
mais indisponible, afficher **Mode Normal utilisé — prévision indisponible**,
avec la raison réelle. Ne pas conserver une décoration laissant croire que
le résultat repose sur l'IA.

Le ML actuellement livré est exploratoire. Le cache ML et le contrat utilisé
par Decision ne sont pas encore raccordés. Prévoir le design de ces états,
mais ne pas présenter la connexion comme opérationnelle. Le frontend ne doit
pas convertir lui-même une prévision du prix PT en carry ou en prix oracle.

### Entrées et actions

| Contrôle | Effet attendu |
| --- | --- |
| Horizon | Aligne hypothèses, simulations et comparaison. |
| Normal / Prédictif | Demande un mode ; le résultat affiche aussi le mode réellement utilisé. |
| Sélection des simulations | Ajoute ou retire les scénarios de la comparaison. |
| Probabilité par scénario | Saisie + curseur, libellés « Hypothèse utilisateur sur H jours ». |
| Hypothèse d'exclusivité | Explique et déclare si les scénarios peuvent survenir ensemble. |
| Rendement net sur l'horizon | Saisie USD et explication obligatoire si fourni par l'utilisateur. |
| Consulter la cotation de vente | Lit la quote mise en cache pour la quantité exacte de PT de la position. |
| Recharger la cotation enregistrée | Relit le cache ; ne lance pas une nouvelle demande au fournisseur. |
| Comparer STAY / EXIT | Lance le calcul avec les hypothèses affichées. |
| Voir les hypothèses / preuves | Ouvre le détail du résultat. |
| Modifier le scénario | Retourne au simulateur en conservant le contexte. |

L'API `POST /api/v1/exit-quotes` est désormais disponible. Elle consulte une
quote déjà collectée pour `strategy_id` et `input_amount`, exprimé en unités
de PT, pas en dollars ni en unités brutes du contrat. Utiliser la quantité
exacte de la position ; ne pas la recalculer depuis un montant USD arrondi.

Le formulaire ne déclenche pas une cotation live. Une quantité non mise en
cache produit « Cotation indisponible pour cette quantité ». Il n'existe
toujours pas d'API publique de création de position simulée : un éditeur libre
de montant nécessite un travail backend supplémentaire. Les presets sélectionnent
les positions du rapport de livraison, puis leur détail via l'API Positions.

### Bloc cotation de vente et faisabilité de clôture

Séparer deux informations : **Vente PT → USDC** et **Clôture de la position Morpho**.
Le premier bloc présente quantité PT, montant USDC estimé, prix d'exécution,
impact de prix si disponible, source, date et expiration. Le second présente
ce qui reste à vérifier : liquidité de remboursement, plan de clôture, gaz total
et frais supplémentaires. Ne pas utiliser le seul devis de vente comme coût
total de sortie, ni le gaz Pendle comme gaz total de clôture.

Prévoir ces états : quote disponible mais partielle, expirée, quantité absente
du cache, refus du fournisseur et livraison Data absente (503). Un refus lié
à la taille doit être expliqué en langage simple avec accès à la preuve.
La politique locale d'expiration est de cinq minutes ; utiliser `valid_until`
retourné par l'API, sans laisser entendre que le fournisseur garantit ce délai.

### Résultat et sensibilité

Deux cartes comparables : **Conserver** et **Sortir**, accompagnées de la valeur
estimée, du rendement net, de la perte attendue et du coût de sortie disponibles.
Le verdict se lit comme **Conserver sous ces hypothèses**, par exemple.
En cas d'égalité, l'expliquer sans forcer un gagnant.

Afficher le seuil de probabilité qui ferait basculer la comparaison lorsqu'il
est calculable. Un graphique de sensibilité est souhaitable : choisir le
scénario étudié, varier son hypothèse et voir l'effet calculé. Ce graphique
nécessite des recalculs via le moteur ; ne pas dessiner une courbe inventée.

États à concevoir : résultat conditionnel, données insuffisantes, cotation
absente/périmée, sortie non faisable et repli prédictif vers Normal. Montrer la
raison et l'action utile. Des scénarios qui se chevauchent ne doivent pas être
additionnés comme s'ils étaient indépendants ou exclusifs.

Le bouton **Sortir** ne doit jamais ressembler à une action d'exécution.
Le produit compare une option ; il ne clôture pas la position.

## 6. Panneaux et états communs

- **Preuves** : source, date, bloc si disponible, données observées et référence
  consultable. Copier l'adresse ou ouvrir la source lorsque cela est pertinent.
- **Méthodologie** : définition de l'indice, hypothèses de calcul et limites.
- **État des données** : réel/démo, replay éventuel, fraîcheur, valeurs absentes
  et conflits de vérification ; rapport de livraison, positions disponibles et
  couverture historique via `/data-delivery`. Si aucun bundle n'est publié,
  afficher « Données non chargées » plutôt qu'un dashboard rempli de zéros.
- **Historique d'incidents** : panneau avec nom, date, classification, technique,
  perte rapportée et lien source lorsqu'ils sont disponibles. Recherche et filtres
  peuvent être réalisés dans l'interface sur les événements reçus. Distinguer
  « Écosystème apparenté » de « Marché sélectionné » : l'API ne confirme pas
  l'attribution au marché. Ne pas préremplir les curseurs de probabilité depuis
  ces événements. Prévoir catalogue absent et métadonnées manquantes.
- **Chargement / erreur** : message contextualisé et réessayer ; préserver les saisies.
- **Aucune stratégie / position** : expliquer ce qui manque, proposer les données
  de démonstration si elles sont effectivement configurées.
- **Valeur inconnue** : « Indisponible » ou tiret expliqué, jamais zéro par défaut.
- **Démo / position simulée** : badge persistant ; un résultat ne devient pas réel
  parce que les calculs sont fonctionnels.

L'indice de risque n'est pas une probabilité de hack. La confiance décrit la
qualité des preuves, pas une garantie de sécurité. Le prix de marché PT et le
prix oracle utilisé pour la santé Morpho sont deux informations distinctes.

## 7. Lecture de la codebase et correspondance API

Commencer par le README, `CORE_ENGINES_INTEGRATION.md` et
`docs/data/DATA_DELIVERY.md`. Les documents
d'architecture expriment aussi des intentions ; les routes et schémas Python
déterminent les fonctionnalités réellement raccordables aujourd'hui.

| Besoin UI | Source actuelle |
| --- | --- |
| Stratégies et marchés | `GET /api/v1/strategies`, `/markets` et leurs détails. |
| Position connue | `GET /api/v1/positions/{position_id}`. |
| Graphe et inspecteur | `GET /api/v1/graph/{strategy_id}` ; `backend/app/graph/types.py`. |
| Indice, couverture, confiance | `GET /api/v1/risk/{strategy_id}` ; `backend/app/risk/scoring.py`. |
| Scénarios et impacts | `GET /api/v1/scenarios`, `POST /api/v1/simulations` ; `backend/app/stress/schemas.py`. |
| Comparaison et repli prédictif | `POST /api/v1/decisions` ; `backend/app/decision/schemas.py`. |
| Séries, preuves, statut | `GET /api/v1/series`, `/evidence/{evidence_id}`, `/data-status`. |
| Livraison, presets et couverture historique | `GET /api/v1/data-delivery` ; `data.positions`, `quote_results`, `history`. |
| Cotation de vente exacte en cache | `POST /api/v1/exit-quotes` ; `backend/app/api/routes_delivery.py`. |
| Catalogue d'incidents sourcés | `GET /api/v1/incident-catalog` ; `backend/app/ingestion/incidents.py`. |
| Recherche ML et démo séparée | `ml/`, `backend/app/forecasting/`, `docs/demo/index.html`. |

Les enveloppes Data et Analytics diffèrent : lire leurs schémas, plutôt que
supposer un format identique. Les nombres Decimal sont sérialisés en chaînes.
Le frontend doit exploiter les statuts, dates, avertissements et champs manquants.

Les routes news et forecast public citées dans certains briefs ne sont pas
exposées actuellement. Le catalogue d'incidents est un historique documentaire,
pas un feed de news ni une estimation de fréquence calibrée.
Pas de feed news, d'alertes push, de compte
utilisateur ou de portefeuille multi-wallet à ajouter au parcours MVP sans
nouveau cadrage. La démo ML statique est une référence pour les résultats de
recherche, pas le dashboard produit à recopier.

### Cas concrets à reprendre dans les maquettes

Le rapport Data documente une collecte du 10 octobre 2026 avec des quotes
obtenues pour 1 et 2 M USD de collatéral, une quote refusée pour 5 M USD
(`MarketProportionTooHigh`) et 1 299 incidents rapportés. Ce sont des exemples
datés, pas des constantes à coder ni des quotes encore fraîches aujourd'hui.
Le rapport publié courant est la référence de l'interface.

Cette collecte obtient une décision `INSUFFICIENT_DATA`, sans verdict, malgré
les devis de vente : la clôture complète reste non vérifiée. Maquetter ce cas
comme un résultat utile, avec les éléments manquants. Un exemple STAY/EXIT
complet doit être explicitement synthétique ou disposer d'un plan de clôture
vérifié fourni séparément. Ne pas forcer un verdict pour embellir la démo.

Les nouvelles quotes sont découvertes automatiquement par le Core en mode
REAL ; `ANALYTICS_INPUTS` peut fournir des entrées prioritaires. Le designer
n'a pas à exposer ces paramètres techniques dans le parcours utilisateur.

## 8. Liberté créative et livrables souhaités

Tu es libre sur l'identité, les couleurs, la typographie, le thème clair/sombre,
les layouts, le dessin du graphe et les micro-interactions. L'ancien brief
évoque un univers fintech sombre ; c'est une piste, pas une obligation de ce brief.
Tu peux améliorer les libellés et l'ordre des panneaux si le parcours devient
plus compréhensible. Aucun choix de bibliothèque graphique n'est imposé ici.

Privilégier une interface lisible et précise : hiérarchie claire, unités
visibles, états crédibles et interactions explicables. Éviter les effets visuels
qui font passer une hypothèse pour une certitude.

Livrables attendus : les trois écrans desktop, leurs adaptations mobile,
les panneaux communs, les composants réutilisables et un prototype cliquable.
Inclure au minimum un résultat complet, un cas de données partielles et un
cas Prédictif indisponible. Ajouter les variations quote partielle / expirée /
refusée pour 5 M$, et un panneau d'incidents. Identifier les résultats complets
synthétiques. Annoter ce qui exige un nouveau travail backend.

Le prototype doit raconter ce parcours : ouvrir la stratégie → regarder le
graphe → sélectionner une dépendance → simuler un choc → lire les impacts →
comparer STAY/EXIT → modifier une hypothèse → comprendre le seuil de bascule.

Sur mobile, permettre aussi une lecture en liste des dépendances ; un canvas
seul ne doit pas bloquer l'accès aux preuves et aux actions. Prévoir contrastes,
navigation clavier, focus visible et réduction des animations.

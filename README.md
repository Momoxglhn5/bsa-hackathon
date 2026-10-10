# BSA Hackathon

Dépôt de travail pour notre participation au hackathon BSA.

## Hackathon

- [Tracks et informations sur DoraHacks](https://dorahacks.io/hackathon/bsa-the-hackathon/tracks)
- Track : à choisir.
- Projet : à définir.
- Stack technique : à choisir selon le projet.

## Prochaines étapes

- [ ] Lire les règles, les critères de jugement et les exigences de soumission.
- [ ] Choisir un track et définir le problème à résoudre.
- [ ] Définir le MVP et la stack technique.
- [ ] Construire et tester le prototype.
- [ ] Ajouter les instructions d’installation et de lancement.
- [ ] Préparer la démonstration et la soumission.

## Développement

Le code et les instructions de lancement seront ajoutés après le choix du projet.
Ne jamais committer de clés API, de clés privées de wallet ou de fichiers `.env`.

## CI Checks

Le workflow `.github/workflows/ci.yml` s’exécute à chaque push, sur les pull requests et à la demande depuis GitHub Actions.

| Check | Vérification |
| --- | --- |
| Markdown | Structure et cohérence des fichiers Markdown avec markdownlint-cli2. |
| GitHub Actions validation | Syntaxe, expressions et configuration des workflows avec actionlint. |
| Secret scan | Recherche de secrets dans tout l’historique Git avec Gitleaks ; valeurs masquées dans les logs. |

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

Les checks de lint applicatif, de types, de tests et de build seront ajoutés dès que la stack sera choisie. Cette CI ne valide donc pas encore de comportement applicatif. Les contrôles ne bloquent une fusion que si une règle de protection de branche les exige ; cette configuration est distincte du workflow.

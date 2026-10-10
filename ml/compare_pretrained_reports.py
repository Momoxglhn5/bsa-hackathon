"""Compare completed research reports without loading weights or selecting a validated model."""
import argparse
from decimal import Decimal
import json
from pathlib import Path


def development_metrics(report):
    metrics = []
    for h in (1, 3, 7):
        source = [next(m for m in r['development']['horizon_metrics'] if m['horizon_days'] == h)
                  for r in report['results']]
        count = sum(m['n_evaluations'] for m in source)
        average = lambda key: sum(Decimal(m[key])*m['n_evaluations'] for m in source)/count
        mae, naive = average('mae_model'), average('mae_naive')
        metrics.append(dict(horizon_days=h, n_evaluations=count, mae_model=str(mae),
            mae_naive=str(naive), coverage_q10_q90=str(average('coverage_q10_q90')),
            gain_vs_naive_percent=str(100*(1-mae/naive)) if naive else None))
    return metrics


def compare(reports):
    identities = [{r['pt']:r['source_snapshot_ref'] for r in p['results']} for p in reports]
    if not identities or any(i != identities[0] for i in identities):
        raise ValueError('comparison requires identical asset snapshots')
    windows = [[[(w['cutoff_at'],w['horizon_days'],w['actual'])
                for w in r['final_test']['windows']] for r in p['results']] for p in reports]
    if any(w != windows[0] for w in windows):
        raise ValueError('comparison requires identical test targets and origins')
    return dict(status='RESEARCH_ONLY',comparison_status='EXPLORATORY_PREVIOUSLY_CONSULTED_TEST',
        models=[dict(model_id=p['protocol']['model_id'], revision=p['protocol']['revision'],
            total_seconds=p['total_seconds'],development=development_metrics(p),
            test=p['pooled_final_test_metrics']) for p in reports],
        limitations=['Model comparison reuses previously consulted test data',
            'Only two test weeks per asset; same calendar dates, correlated errors',
            'No independent validation, original PT-siUSD validation or runtime model replacement'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', nargs='+', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = compare([json.loads(p.read_text()) for p in args.reports])
    args.output.mkdir(exist_ok=False, parents=True)
    (args.output/'comparison.json').write_text(json.dumps(result,indent=2)+'\n')
    lines = ['# Comparaison exploratoire de modèles préentraînés', '',
        'Même jeu de quatre PT, mêmes contextes, aucune mise à jour des poids. '
        'Le test a déjà été consulté : ces scores servent à explorer, pas à valider.', '',
        'Gain = réduction de MAE par rapport à la persistance. Positif = meilleur.', '',
        '| Modèle | Phase | J1 | J3 | J7 | Couverture J7 q10–q90 |',
        '| --- | --- | ---: | ---: | ---: | ---: |']
    for model in result['models']:
        for phase,label in [('development','Développement (45 fenêtres)'),('test','Test réutilisé (8 fenêtres)')]:
            metrics = model[phase]
            gains = [f"{Decimal(m['gain_vs_naive_percent']):+.1f}%" for m in metrics]
            coverage = f"{100*Decimal(metrics[-1]['coverage_q10_q90']):.1f}%"
            lines.append('| '+' | '.join([model['model_id'],label,*gains,coverage])+' |')
    lines += ['', 'Une validation future doit fixer le modèle avant de collecter un nouveau test.', '',
        'Les quatre actifs sont distincts du PT-siUSD initial. Aucune décision financière '
        'ni ForecastBundle VALIDATED ne provient de cette comparaison.']
    (args.output/'RESULTS.md').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()

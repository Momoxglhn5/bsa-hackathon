import copy
from decimal import Decimal
import pytest
from ml.compare_pretrained_reports import compare


def report():
    metrics = [dict(horizon_days=h,n_evaluations=2,mae_model='0.01',mae_naive='0.02',
                    coverage_q10_q90='0.5') for h in (1,3,7)]
    return dict(protocol=dict(model_id='SYNTHETIC',revision='a'*40),total_seconds=1,
        pooled_final_test_metrics=metrics,
        results=[dict(pt='SYNTHETIC',source_snapshot_ref='sha256:SYNTHETIC',
            development=dict(horizon_metrics=metrics),
            final_test=dict(windows=[dict(cutoff_at='2026-01-01',horizon_days=1,actual='1')]))])


def test_comparison_keeps_research_status_and_weighted_gain():
    result = compare([report(),report()])
    assert result['comparison_status'] == 'EXPLORATORY_PREVIOUSLY_CONSULTED_TEST'
    assert Decimal(result['models'][0]['development'][0]['gain_vs_naive_percent']) == 50


@pytest.mark.parametrize('mutation',['snapshot','target'])
def test_comparison_rejects_different_data_or_test_targets(mutation):
    first = report()
    second = copy.deepcopy(first)
    if mutation == 'snapshot':
        second['results'][0]['source_snapshot_ref'] = 'other'
    else:
        second['results'][0]['final_test']['windows'][0]['actual'] = '2'
    with pytest.raises(ValueError):
        compare([first,second])

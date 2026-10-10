"""Exercise the running HTTP API with the checked-in synthetic snapshot."""
import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-mode', choices=['REAL','DEMO'], help='Select the Data store mode; omit for an explicit snapshot')
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    args = parser.parse_args()
    snapshot = json.loads((Path(__file__).resolve().parents[1] / 'app/fixtures/analytics_demo.json').read_text())
    position = snapshot['positions'][0]
    strategy_id = position['strategy_id']

    def call(path, data=None):
        body = json.dumps(data).encode() if data is not None else None
        suffix = '?data_mode=' + args.data_mode if args.data_mode else ''
        req = Request(args.base_url.rstrip('/') + '/api/v1/' + path + suffix, data=body,
                      headers={'Content-Type': 'application/json'})
        with urlopen(req, timeout=10) as response:
            return json.load(response)

    graph = call('graph/' + strategy_id)
    oracle = next(n['id'] for n in graph['data']['nodes'] if n['type'] == 'ORACLE')
    common = dict(strategy_id=strategy_id, position_id=position['position_id'], horizon_days=7, scenario_scope='HYPOTHETICAL')
    shock = call('simulations', dict(common, scenario_type='ORACLE_MANIPULATION', target_node_id=oracle,
                                     parameters={'oracle_price_change_ratio': '-0.4'}))
    minting = call('simulations', dict(common, scenario_type='UNAUTHORIZED_MINTING',
                                      target_node_id='asset:1:' + position['collateral_asset']['address'],
                                      parameters={'assumed_token_haircut_ratio': '0.2'}))
    decision = dict(strategy_id=strategy_id, position_id=position['position_id'], horizon_days=7,
                    scenario_assumptions=[dict(loss_scenario_ref=minting['data']['simulation_id'], probability_over_horizon='0.02')],
                    exit_quote_id=snapshot['quotes'][0]['quote_id'], estimated_net_carry_usd='1000',
                    carry_explanation='Synthetic seven-day net carry assumption, not guaranteed PT yield')
    stay = call('decisions', decision)
    decision['scenario_assumptions'][0]['probability_over_horizon'] = '0.1'
    exit_result = call('decisions', decision)
    assert stay['data']['verdict'] == 'STAY'
    assert exit_result['data']['verdict'] == 'EXIT'
    assert shock['data']['position_effects'][position['position_id']]['after']['liquidatable'] is True
    print(json.dumps(dict(dataset='SYNTHETIC / FROZEN REPLAY', graph_nodes=len(graph['data']['nodes']),
                          graph_edges=len(graph['data']['edges']), oracle_stress=shock['data'],
                          stay_at_2pct=stay['data'], exit_at_10pct=exit_result['data'],
                          risk=call('risk/' + strategy_id)['data']), indent=2))


if __name__ == '__main__':
    main()

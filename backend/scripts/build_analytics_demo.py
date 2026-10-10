"""Generate a fully synthetic snapshot; never collect or claim real chain data."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.analytics.dataset import AnalyticsSnapshot, QuoteContext
from app.models.evidence import EvidenceDTO
from app.models.market import (TokenRef, MarketPriceDTO, OraclePriceDTO, ExitPriceDTO,
                               MorphoMarketFields, PendleMarketFields, MarketDTO)
from app.models.position import PositionDTO
from app.models.strategy import StrategyDTO, make_strategy_id


def build_demo():
    now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
    address = lambda digit: '0x' + digit * 40
    ev = 'ev_' + 'a' * 24
    common = dict(chain_id=1, data_mode='DEMO', as_of=now,
                  source_ids=('fixture:analytics',), evidence_ids=(ev,))
    pt = TokenRef(chain_id=1, address=address('1'), symbol='DEMO-PT', decimals=18)
    usdc = TokenRef(chain_id=1, address=address('2'), symbol='DEMO-USD', decimals=6)
    sy = TokenRef(chain_id=1, address=address('3'), symbol='DEMO-SY', decimals=18)
    yt = TokenRef(chain_id=1, address=address('4'), symbol='DEMO-YT', decimals=18)
    oracle, irm, pendle_address = address('5'), address('6'), address('7')
    market_id = '0x' + 'a' * 64
    strategy_id = make_strategy_id(1, market_id, pt.address)
    pricing = dict(chain_id=1, observed_at=now, source_id='fixture:analytics',
                   evidence_ids=(ev,), status='UNVERIFIED')
    pt_price = MarketPriceDTO(**pricing, value='1', base_asset=pt, quote_unit='USD', pricing_method='API_SPOT')
    loan_price = MarketPriceDTO(**pricing, value='1', base_asset=usdc, quote_unit='USD', pricing_method='API_SPOT')
    op = OraclePriceDTO(**pricing, value='1', base_asset=pt, quote_unit='TOKEN', quote_asset=usdc,
                        pricing_method='MORPHO_ORACLE', oracle_address=oracle, block_number=123456,
                        raw_value=10**24, scale_exponent=24)
    morpho = MarketDTO(**common, market_id=market_id, protocol='MORPHO_BLUE', status='UNVERIFIED',
                       morpho=MorphoMarketFields(morpho_address=address('8'), loan_token=usdc,
                           collateral_token=pt, oracle_address=oracle, irm_address=irm,
                           lltv='0.915', lltv_raw=915000000000000000, utilization='0.6',
                           borrow_apy='0.05', collateral_price=pt_price, loan_price=loan_price, oracle_price=op))
    pendle = MarketDTO(**common, market_id=pendle_address, protocol='PENDLE_V2', status='UNVERIFIED',
                       pendle=PendleMarketFields(market_address=pendle_address, pt=pt, yt=yt, sy=sy,
                                                expiry=now+timedelta(days=90), pt_price=pt_price))
    strategy = StrategyDTO(**common, strategy_id=strategy_id, status='CANDIDATE', data_status='UNVERIFIED',
                           morpho_market_id=market_id, pendle_market_address=pendle_address, pt=pt,
                           loan_asset=usdc, expiry=now+timedelta(days=90))
    position = PositionDTO(**common, position_id='pos_demo_fund', kind='SIMULATED', market_id=market_id,
                           collateral_asset=pt, loan_asset=usdc, collateral_amount='2000000', borrow_amount='1200000',
                           strategy_id=strategy_id, label='Synthetic fund — NOT an on-chain holding', status='OK')
    quote = ExitPriceDTO(**pricing, value='0.99', base_asset=pt, quote_unit='TOKEN', quote_asset=usdc,
                         pricing_method='EXECUTION_QUOTE', input_amount='2000000', output_amount='1980000',
                         venue='SYNTHETIC_ONLY', price_impact='0.01', valid_until=now+timedelta(minutes=5))
    context = QuoteContext(quote_id='quote:synthetic-full-position', position_id=position.position_id, quote=quote,
                           output_usd_price=loan_price, gas_cost_usd='100', extra_fees_usd='0', route_available=True,
                           closing_feasible=True, requires_external_repayment_liquidity=True,
                           repayment_liquidity_confirmed=True, basis='USER_ASSUMPTION', evidence_ids=(ev,))
    evidence = EvidenceDTO(chain_id=1, data_mode='DEMO', evidence_id=ev, source_kind='FIXTURE',
                           source_id='fixture:analytics', method='synthetic test assumptions; no RPC call',
                           fetched_at=now, status='UNVERIFIED')
    return AnalyticsSnapshot(snapshot_id='synthetic-analytics-demo-v1', as_of=now,
                             description='ALL addresses, market observations, holdings and execution quotes are SYNTHETIC.',
                             markets=(morpho, pendle), positions=(position,), strategies=(strategy,), evidence=(evidence,), quotes=(context,))


if __name__ == '__main__':
    path = Path(__file__).resolve().parents[1] / 'app/fixtures/analytics_demo.json'
    path.write_text(build_demo().model_dump_json(indent=2) + '\n')
    print(path)

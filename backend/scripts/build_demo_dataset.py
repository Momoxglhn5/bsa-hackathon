"""Build the complete DEMO dataset served by the jury demo backend.

Five Pendle PT markets used as Morpho Blue collateral, seven simulated fund positions,
90 days of daily history, the six risk factors no collector provides yet, sized exit
quotes with declared closing plans, validated net-carry forecasts and the real
DefiLlama incident catalogue.

Everything except the incident catalogue is synthetic: data_mode DEMO, synthetic
addresses, FIXTURE / DERIVED / USER_INPUT evidence (the Data contract forbids RPC
evidence in DEMO). The backend therefore keeps every DEMO link a HYPOTHESIS and caps
evidence confidence. Timestamps are anchored at generation time so the replay clock
finds every price fresh.

Run from the repository root:
    backend/.venv/bin/python backend/scripts/build_demo_dataset.py [--out DIR] [--no-incidents]

Serve it with:
    DOMINO_DATA_DIR=DIR ANALYTICS_INPUTS=DIR/analytics_inputs.json ANALYTICS_REPLAY=true \\
      backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --port 8001
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal, localcontext
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.analytics.dataset import AnalyticsInputs, QuoteContext, RiskObservation  # noqa: E402
from app.decision.forecast import ForecastBundle  # noqa: E402
from app.ingestion.normalize import (  # noqa: E402
    MorphoApys,
    OnchainMarketParams,
    OnchainMarketState,
    OnchainPendleTokens,
    OracleRead,
    SourceRef,
    TokenMeta,
    _check,
    asset_id,
    build_market_price,
    contract_id,
    morpho_market_key,
    normalize_morpho_market,
    normalize_pendle_market,
    pendle_market_key,
)
from app.models.evidence import (  # noqa: E402
    DataMode,
    DataStatus,
    EvidenceDTO,
    RelationStatus,
    RelationType,
    SourceKind,
    make_evidence_id,
    make_relation,
)
from app.models.market import ExitPriceDTO, MarketDTO, PricingMethod, QuoteUnit  # noqa: E402
from app.models.position import PositionDTO, PositionKind  # noqa: E402
from app.models.strategy import StrategyDTO, StrategyStatus, make_strategy_id  # noqa: E402
from app.models.timeseries import SeriesUnit, SubjectKind, TimeseriesDTO, TimeseriesMetric, TimeseriesPointDTO  # noqa: E402
from app.storage.parquet_store import ParquetStore, observations_from_series  # noqa: E402
from app.storage.sqlite_store import DB_FILENAME, SqliteStore  # noqa: E402

DEMO = DataMode.DEMO
SOURCE_ID = "demo_fixture"
ANALYST_ID = "demo_analyst"
PARSER = "build_demo_dataset.v1"
MARKER = ".radar-demo-dataset"
DEFAULT_OUT = BACKEND_DIR / "data" / "demo"
INCIDENT_CACHE = BACKEND_DIR / "data" / "cache" / "incident_catalog.json"
HISTORY_DAYS = 90
BLOCK = 24_812_000
D = Decimal


def addr(name: str) -> str:
    """Deterministic synthetic address: never a real deployment."""
    return "0x" + hashlib.sha256(f"radar-demo:{name}".encode()).hexdigest()[:40]


def bytes32(name: str) -> str:
    return "0x" + hashlib.sha256(f"radar-demo-market:{name}".encode()).hexdigest()


TOKENS = {  # symbol -> decimals
    "USDC": 6, "WETH": 18, "sUSDe": 18, "USDe": 18, "wstETH": 18, "stETH": 18, "weETH": 18,
    "eETH": 18, "USD0++": 18, "USD0": 18,
}
SHARED_IRM = addr("irm:adaptive-curve")
MORPHO_SINGLETON = addr("morpho-blue")


@dataclass
class MarketSpec:
    key: str
    pt_symbol: str
    underlying: str
    accounting: str
    loan: str
    expiry: datetime
    lltv: str
    pt_usd: str
    loan_usd: str
    oracle: str
    implied: str
    underlying_apy: str
    liquidity_usd: str
    utilization: str
    supply_apy: str
    borrow_apy: str
    total_supply: str
    liquidity_trend: float  # relative change over the last 30 days
    risk: dict[str, str]
    derived: tuple[str, ...] = ("S_SOURCE_VERIFIED", "O_DEPLOYMENT_AGE", "O_VERSION_AGE")  # computed, not analyst input
    backing: str | None = None  # unresolved economic backing label
    inputs: tuple[str, ...] = ()
    forecast_bias: str = "0.9"

    @property
    def loan_decimals(self) -> int:
        return TOKENS[self.loan]


@dataclass
class PositionSpec:
    position_id: str
    label: str
    market: str
    size_usd: str
    ltv: str
    gas_usd: str
    extra_fees_usd: str


MARKETS = [
    MarketSpec("susde", "PT-sUSDe-26NOV2026", "sUSDe", "USDe", "USDC", datetime(2026, 11, 26, tzinfo=timezone.utc),
               "0.915", "0.98710", "0.99990", "0.98760", "0.0962", "0.0715", "48250000", "0.884", "0.0581", "0.0689",
               "182400000", -0.06,
               {"S_SOURCE_VERIFIED": "0.10", "S_AUDIT": "0.20", "G_CONTROL": "0.45", "G_TIMELOCK": "0.40",
                "O_DEPLOYMENT_AGE": "0.25", "O_VERSION_AGE": "0.30"},
               backing="Off-chain reserves", inputs=("USDe", "sUSDe"), forecast_bias="0.92"),
    MarketSpec("usde", "PT-USDe-24DEC2026", "USDe", "USDe", "USDC", datetime(2026, 12, 24, tzinfo=timezone.utc),
               "0.915", "0.97890", "0.99990", "0.97950", "0.1040", "0.0820", "21300000", "0.931", "0.0762", "0.0841",
               "96800000", -0.18,
               {"S_SOURCE_VERIFIED": "0.10", "S_AUDIT": "0.25", "G_CONTROL": "0.50", "G_TIMELOCK": "0.45",
                "O_DEPLOYMENT_AGE": "0.30", "O_VERSION_AGE": "0.35"},
               backing="Off-chain reserves", inputs=("USDe",), forecast_bias="0.78"),
    MarketSpec("wsteth", "PT-wstETH-25MAR2027", "wstETH", "stETH", "WETH", datetime(2027, 3, 25, tzinfo=timezone.utc),
               "0.86", "4045.90", "3380.00", "1.19850", "0.0310", "0.0280", "96400000", "0.712", "0.0168", "0.0262",
               "38500", -0.02,
               {"S_SOURCE_VERIFIED": "0.05", "S_AUDIT": "0.10", "G_CONTROL": "0.20", "G_TIMELOCK": "0.15",
                "O_DEPLOYMENT_AGE": "0.10", "O_VERSION_AGE": "0.15"},
               inputs=("stETH", "wstETH"), forecast_bias="1.05",
               derived=("S_SOURCE_VERIFIED", "S_AUDIT", "G_CONTROL", "G_TIMELOCK", "O_DEPLOYMENT_AGE", "O_VERSION_AGE")),
    MarketSpec("weeth", "PT-weETH-25JUN2027", "weETH", "eETH", "WETH", datetime(2027, 6, 25, tzinfo=timezone.utc),
               "0.86", "3529.40", "3380.00", "1.04500", "0.0360", "0.0310", "41800000", "0.794", "0.0204", "0.0291",
               "16200", -0.05,
               {"S_SOURCE_VERIFIED": "0.10", "S_AUDIT": "0.20", "G_CONTROL": "0.35", "G_TIMELOCK": "0.30",
                "O_DEPLOYMENT_AGE": "0.25", "O_VERSION_AGE": "0.25"},
               inputs=("eETH", "weETH"), forecast_bias="0.95",
               derived=("S_SOURCE_VERIFIED", "S_AUDIT", "O_DEPLOYMENT_AGE", "O_VERSION_AGE")),
    MarketSpec("usd0pp", "PT-USD0++-28JAN2027", "USD0++", "USD0", "USDC", datetime(2027, 1, 28, tzinfo=timezone.utc),
               "0.86", "0.96520", "0.99990", "0.97000", "0.1280", "0.0910", "9600000", "0.824", "0.0651", "0.0792",
               "54200000", -0.34,
               {"S_SOURCE_VERIFIED": "0.20", "S_AUDIT": "0.45", "G_CONTROL": "0.70", "G_TIMELOCK": "0.65",
                "O_DEPLOYMENT_AGE": "0.55", "O_VERSION_AGE": "0.60"},
               backing="RWA collateral", inputs=("USD0",), forecast_bias="0.70", derived=("S_SOURCE_VERIFIED",)),
]

POSITIONS = [
    PositionSpec("pos_atlas_susde", "Atlas Fund", "susde", "4000000", "0.80", "320", "150"),
    PositionSpec("pos_atlas_usde", "Atlas Fund", "usde", "2500000", "0.86", "300", "120"),
    PositionSpec("pos_nova_susde", "Nova Treasury", "susde", "1500000", "0.62", "260", "80"),
    PositionSpec("pos_nova_usd0pp", "Nova Treasury", "usd0pp", "2000000", "0.78", "340", "200"),
    PositionSpec("pos_orion_wsteth", "Orion Reserve", "wsteth", "3000000", "0.60", "410", "100"),
    PositionSpec("pos_orion_weeth", "Orion Reserve", "weeth", "1200000", "0.72", "380", "90"),
    PositionSpec("pos_helix_wsteth", "Helix Fund", "wsteth", "900000", "0.74", "360", "60"),
]

RISK_METHOD = {
    "S_SOURCE_VERIFIED": "Contract source code published and verified (demo assessment).",
    "S_AUDIT": "Security audit coverage and age (demo assessment).",
    "G_CONTROL": "Scope of admin powers over the contracts (demo assessment).",
    "G_TIMELOCK": "Delay imposed on governance changes (demo assessment).",
    "O_DEPLOYMENT_AGE": "Market deployment age (demo assessment).",
    "O_VERSION_AGE": "Deployed version age (demo assessment).",
}


def q(value: Decimal, places: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_DOWN)


@dataclass
class Dataset:
    as_of: datetime
    evidence: dict[str, EvidenceDTO] = field(default_factory=dict)
    markets: list[MarketDTO] = field(default_factory=list)
    strategies: list[StrategyDTO] = field(default_factory=list)
    positions: list[PositionDTO] = field(default_factory=list)
    relations: list = field(default_factory=list)
    series: list[tuple[str, TimeseriesDTO]] = field(default_factory=list)
    quotes: list[QuoteContext] = field(default_factory=list)
    risk: list[RiskObservation] = field(default_factory=list)
    forecasts: list[ForecastBundle] = field(default_factory=list)

    def ev(self, method: str, scope: str, *, kind=SourceKind.FIXTURE, source=SOURCE_ID, block=False,
           at: datetime | None = None, excerpt: dict[str, str] | None = None) -> EvidenceDTO:
        at = at or self.as_of
        params = {"dataset": "radar-demo", "scope": scope}
        e = EvidenceDTO(
            chain_id=1, data_mode=DEMO,
            evidence_id=make_evidence_id(source, method, params, block_number=BLOCK if block else None, fetched_at=at),
            source_kind=kind, source_id=source, method=method, request_params=params,
            block_number=BLOCK if block else None,
            block_hash=("0x" + hashlib.sha256(f"block:{BLOCK}".encode()).hexdigest()) if block else None,
            fetched_at=at, status=DataStatus.OK, decoded_excerpt=excerpt or {"note": "demo"}, parser_version=PARSER,
        )
        self.evidence.setdefault(e.evidence_id, e)
        return e

    def ref(self, e: EvidenceDTO) -> SourceRef:
        return SourceRef(e.source_id, e.evidence_id, e.fetched_at)


def token_meta(symbols: list[str], ref: SourceRef) -> dict[str, TokenMeta]:
    return {addr(f"token:{s}"): TokenMeta(addr(f"token:{s}"), TOKENS.get(s, 18), s, ref) for s in symbols}


def build_market(ds: Dataset, m: MarketSpec):
    T = ds.as_of
    market_id = bytes32(m.key)
    pt, yt, sy = addr(f"token:{m.pt_symbol}"), addr(f"token:YT-{m.pt_symbol[3:]}"), addr(f"token:SY-{m.underlying}")
    loan, underlying = addr(f"token:{m.loan}"), addr(f"token:{m.underlying}")
    oracle, pendle_addr = addr(f"oracle:{m.key}"), addr(f"pendle-market:{m.key}")
    e_item, e_params, e_state = ds.ev("fixture:morpho_market", m.key), ds.ev("fixture:morpho_params", m.key, block=True), ds.ev("fixture:morpho_state", m.key, block=True)
    e_oracle, e_apys, e_prices = ds.ev("fixture:oracle_price", m.key, block=True), ds.ev("fixture:morpho_apys", m.key), ds.ev("fixture:usd_prices", m.key)
    e_pendle, e_tokens, e_sy = ds.ev("fixture:pendle_market", m.key), ds.ev("fixture:pendle_tokens", m.key, block=True), ds.ev("fixture:sy_info", m.key, block=True)
    symbols = [m.loan, m.underlying, m.accounting, *m.inputs]
    meta = token_meta(symbols, ds.ref(e_params))
    meta[pt] = TokenMeta(pt, 18, m.pt_symbol, ds.ref(e_params))
    meta[yt] = TokenMeta(yt, 18, f"YT-{m.pt_symbol[3:]}", ds.ref(e_params))
    meta[sy] = TokenMeta(sy, 18, f"SY-{m.underlying}", ds.ref(e_params))
    loan_dec = m.loan_decimals
    supply_raw = int(D(m.total_supply) * D(10) ** loan_dec)
    borrow_raw = int(D(m.total_supply) * D(m.utilization) * D(10) ** loan_dec)
    lltv_raw = int(D(m.lltv) * D(10) ** 18)
    # Morpho oracle price is scaled by 10^(36 + loan decimals - collateral decimals).
    oracle_raw = int(D(m.oracle) * D(10) ** (36 + loan_dec - 18))
    from app.ingestion.normalize import token_ref  # local: same TokenRef construction as collection
    pt_token, loan_token = token_ref(pt, meta), token_ref(loan, meta)
    collateral_price = build_market_price(pt_token, m.pt_usd, quote_unit=QuoteUnit.USD, pricing_method=PricingMethod.API_SPOT, ref=ds.ref(e_prices))
    loan_price = build_market_price(loan_token, m.loan_usd, quote_unit=QuoteUnit.USD, pricing_method=PricingMethod.API_SPOT, ref=ds.ref(e_prices))
    morpho = normalize_morpho_market(
        {"market_id": market_id, "chain_id": 1, "loan_token": loan, "collateral_token": pt, "oracle_address": oracle,
         "irm_address": SHARED_IRM, "lltv_wad": str(lltv_raw)},
        ds.ref(e_item), data_mode=DEMO, tokens=meta,
        onchain_params=OnchainMarketParams(market_id, loan, pt, oracle, SHARED_IRM, lltv_raw, BLOCK, ds.ref(e_params)),
        onchain_state=OnchainMarketState(market_id, supply_raw, supply_raw * 10**6, borrow_raw, borrow_raw * 10**6,
                                         int(T.timestamp()) - 600, 0, BLOCK, ds.ref(e_state)),
        oracle=OracleRead(oracle, oracle_raw, BLOCK, ds.ref(e_oracle)),
        apys=MorphoApys(D(m.supply_apy), D(m.borrow_apy), ds.ref(e_apys)),
        collateral_price=collateral_price, loan_price=loan_price,
    ).dto
    dumped = morpho.model_dump()
    morpho = MarketDTO.model_validate({**dumped, "morpho": {**dumped["morpho"], "morpho_address": MORPHO_SINGLETON}})
    pendle = normalize_pendle_market(
        {"chainId": 1, "address": pendle_addr, "expiry": m.expiry.isoformat().replace("+00:00", "Z"),
         "pt": f"1-{pt}", "yt": f"1-{yt}", "sy": f"1-{sy}", "underlyingAsset": f"1-{underlying}",
         "details": {"impliedApy": m.implied, "underlyingApy": m.underlying_apy, "liquidity": m.liquidity_usd,
                     "totalPt": str(q(D(m.liquidity_usd) / D(m.pt_usd) * D("0.55"), 2)),
                     "totalSy": str(q(D(m.liquidity_usd) / D(m.pt_usd) * D("0.42"), 2))}},
        ds.ref(e_pendle), data_mode=DEMO, tokens=meta,
        onchain=OnchainPendleTokens(pendle_addr, sy, pt, yt, int(m.expiry.timestamp()), BLOCK, ds.ref(e_tokens)),
        pt_price=build_market_price(pt_token, m.pt_usd, quote_unit=QuoteUnit.USD, pricing_method=PricingMethod.API_SPOT, ref=ds.ref(e_prices)),
    ).dto
    checks = (*morpho.verification, *pendle.verification,
              _check("strategy.pt_is_morpho_collateral", pt, SOURCE_ID, pt, (e_params.evidence_id, e_tokens.evidence_id)))
    strategy = StrategyDTO(
        chain_id=1, data_mode=DEMO, strategy_id=make_strategy_id(1, market_id, pt), status=StrategyStatus.VERIFIED_ACTIVE,
        status_reason="DEMO: synthetic verification", data_status=DataStatus.OK, morpho_market_id=market_id,
        pendle_market_address=pendle_addr, pt=morpho.morpho.collateral_token, loan_asset=morpho.morpho.loan_token,
        underlying_asset=pendle.pendle.underlying_asset, expiry=m.expiry, as_of=T, block_number=BLOCK,
        verification=checks, source_ids=(SOURCE_ID,),
        evidence_ids=tuple(dict.fromkeys((*morpho.evidence_ids, *pendle.evidence_ids))),
    )
    ds.markets += [morpho, pendle]
    ds.strategies.append(strategy)

    # Dependencies declared by the (synthetic) contracts; DEMO links stay hypotheses in the graph.
    mk, pk = morpho_market_key(market_id), pendle_market_key(pendle_addr)
    rel = [
        (mk, RelationType.MARKET_LOAN_ASSET, asset_id(loan), e_params),
        (mk, RelationType.MARKET_COLLATERAL_ASSET, asset_id(pt), e_params),
        (mk, RelationType.MARKET_ORACLE, contract_id(oracle), e_params),
        (mk, RelationType.MARKET_IRM, contract_id(SHARED_IRM), e_params),
        (pk, RelationType.PENDLE_MARKET_PT, asset_id(pt), e_tokens),
        (pk, RelationType.PENDLE_MARKET_SY, asset_id(sy), e_tokens),
        (pk, RelationType.PENDLE_MARKET_YT, asset_id(yt), e_tokens),
        (asset_id(sy), RelationType.SY_YIELD_TOKEN, asset_id(underlying), e_sy),
        (asset_id(sy), RelationType.SY_ACCOUNTING_ASSET, asset_id(addr(f"token:{m.accounting}")), e_sy),
    ]
    rel += [(asset_id(sy), RelationType.SY_ACCEPTS_INPUT, asset_id(addr(f"token:{s}")), e_sy) for s in m.inputs if s != m.underlying]
    if m.accounting != m.underlying:
        rel.append((asset_id(underlying), RelationType.TOKEN_DECLARED_ASSET, asset_id(addr(f"token:{m.accounting}")), e_sy))
    for s, rt, t, e in rel:
        ds.relations.append(make_relation(source_node_id=s, target_node_id=t, relation_type=rt,
                                          verification_status=RelationStatus.CURATED, evidence=[e], observed_at=T,
                                          note="Dependency declared by the contract (demo)", data_mode=DEMO))
    if m.backing:
        ds.relations.append(make_relation(
            source_node_id=asset_id(addr(f"token:{m.accounting}")), target_node_id=asset_id(addr(f"backing:{m.backing}:{m.accounting}")),
            relation_type=RelationType.ECONOMIC_BACKING, verification_status=RelationStatus.UNRESOLVED, evidence=[e_sy],
            observed_at=T, note=f"{m.backing}: backing not verifiable on-chain", data_mode=DEMO))

    for factor, value in m.risk.items():
        kind = SourceKind.DERIVED if factor in m.derived else SourceKind.USER_INPUT
        e = ds.ev(f"demo:risk:{factor}", m.key, kind=kind, source=ANALYST_ID, at=T - timedelta(minutes=2))
        ds.risk.append(RiskObservation(market_key=mk, factor_id=factor, risk=D(value), observed_at=T - timedelta(minutes=2),
                                       evidence_ids=(e.evidence_id,), methodology=RISK_METHOD[factor]))
    build_history(ds, m, market_id, pendle_addr, pt, loan_token)
    return strategy, morpho


def walk(rng: random.Random, n: int, vol: float) -> list[float]:
    """Mean-reverting noise ending at 0 so the last point equals the current value."""
    x, out = 0.0, []
    for _ in range(n):
        x = 0.8 * x + rng.gauss(0, vol)
        out.append(x)
    return [v - out[-1] for v in out]


def build_history(ds: Dataset, m: MarketSpec, market_id: str, pendle_addr: str, pt: str, loan_token) -> None:
    T = ds.as_of
    rng = random.Random(f"radar-demo:{m.key}")
    days = [T - timedelta(days=HISTORY_DAYS - i) for i in range(HISTORY_DAYS + 1)]
    ago = [HISTORY_DAYS - i for i in range(HISTORY_DAYS + 1)]
    e = ds.ev("fixture:history", m.key)
    eth = m.loan == "WETH"
    eth_noise = walk(random.Random("radar-demo:eth"), len(days), 0.018) if eth else [0.0] * len(days)
    implied = float(m.implied)

    def series(metric, kind, subject, unit, values, *, method=None, quote=None, places=6):
        pts = tuple(TimeseriesPointDTO(timestamp=t, value=q(D(repr(v)), places), status=DataStatus.OK, evidence_ids=(e.evidence_id,))
                    for t, v in zip(days, values))
        dto = TimeseriesDTO(chain_id=1, data_mode=DEMO, series_id=f"ts_demo_{m.key}_{metric.value.lower()}",
                            metric=metric, subject_kind=kind, subject_id=subject, unit=unit, quote_asset=quote,
                            pricing_method=method, interval_seconds=86400, source_id=SOURCE_ID, status=DataStatus.OK, points=pts)
        entity = (f"asset:1:{subject}" if kind == SubjectKind.ASSET
                  else morpho_market_key(subject) if len(subject) == 66 else pendle_market_key(subject))
        ds.series.append((entity, dto))

    # PT accretes towards par as maturity approaches; ETH PTs also follow the ETH price in USD.
    pt_usd = [float(m.pt_usd) * (1 + implied) ** (-a / 365) * (1 + n) for a, n in zip(ago, eth_noise)]
    oracle = [float(m.oracle) * (1 + implied) ** (-a / 365) * (1 + 0.0004 * n) for a, n in zip(ago, walk(rng, len(days), 1.0))]
    implied_s = [max(0.005, implied + v) for v in walk(rng, len(days), 0.0018)]
    util = [min(0.985, max(0.4, float(m.utilization) + v)) for v in walk(rng, len(days), 0.012)]
    borrow = [max(0.001, float(m.borrow_apy) * (1 + 1.6 * (u - float(m.utilization)))) for u in util]
    liq_now = float(m.liquidity_usd)
    liq = [liq_now * (1 - m.liquidity_trend * min(a, 30) / 30) * (1 + v) for a, v in zip(ago, walk(rng, len(days), 0.015))]
    series(TimeseriesMetric.MARKET_PRICE, SubjectKind.ASSET, pt, SeriesUnit.USD, pt_usd, method=PricingMethod.API_SPOT, places=4 if eth else 6)
    series(TimeseriesMetric.ORACLE_PRICE, SubjectKind.MARKET, market_id, SeriesUnit.TOKEN, oracle, method=PricingMethod.MORPHO_ORACLE, quote=loan_token)
    series(TimeseriesMetric.PT_IMPLIED_APY, SubjectKind.MARKET, pendle_addr, SeriesUnit.RATIO, implied_s)
    series(TimeseriesMetric.UTILIZATION, SubjectKind.MARKET, market_id, SeriesUnit.RATIO, util)
    series(TimeseriesMetric.BORROW_APY, SubjectKind.MARKET, market_id, SeriesUnit.RATIO, borrow)
    series(TimeseriesMetric.LIQUIDITY_USD, SubjectKind.MARKET, market_id, SeriesUnit.USD, liq, places=2)


def build_position(ds: Dataset, p: PositionSpec, m: MarketSpec, strategy: StrategyDTO, morpho: MarketDTO) -> None:
    T = ds.as_of
    with localcontext() as ctx:
        ctx.prec = 60
        loan_dec = m.loan_decimals
        collateral = q(D(p.size_usd) / D(m.pt_usd), 4)
        debt = q(D(p.ltv) * collateral * D(m.oracle), min(loan_dec, 6))
        position = PositionDTO(
            chain_id=1, data_mode=DEMO, position_id=p.position_id, kind=PositionKind.SIMULATED, market_id=strategy.morpho_market_id,
            collateral_asset=strategy.pt, loan_asset=strategy.loan_asset, collateral_amount=collateral,
            collateral_amount_raw=int(collateral * D(10) ** 18), borrow_amount=debt, borrow_amount_raw=int(debt * D(10) ** loan_dec),
            strategy_id=strategy.strategy_id, label=p.label, as_of=T, status=DataStatus.OK, source_ids=(SOURCE_ID,),
        )
        ds.positions.append(position)
        # Sized exit quote: impact grows with the share of Pendle liquidity sold at once.
        impact = min(D("0.03"), D("0.0008") + D(p.size_usd) / D(m.liquidity_usd) * D("0.08"))
        price = q(D(m.pt_usd) / D(m.loan_usd) * (1 - impact), 8)
        output = price * collateral
        e_quote = ds.ev("fixture:exit_quote", p.position_id, at=T - timedelta(seconds=45))
        e_plan = ds.ev("demo:closing_plan", p.position_id, kind=SourceKind.USER_INPUT, source=ANALYST_ID, at=T - timedelta(seconds=45))
        quote = ExitPriceDTO(
            chain_id=1, value=price, base_asset=strategy.pt, quote_unit=QuoteUnit.TOKEN, quote_asset=strategy.loan_asset,
            pricing_method=PricingMethod.EXECUTION_QUOTE, observed_at=T - timedelta(seconds=45), source_id=SOURCE_ID,
            evidence_ids=(e_quote.evidence_id,), status=DataStatus.OK, input_amount=collateral, output_amount=output,
            venue="PENDLE_ROUTER", price_impact=q(impact, 6), valid_until=T + timedelta(minutes=4),
        )
        ds.quotes.append(QuoteContext(
            quote_id=f"quote:demo-{p.position_id.removeprefix('pos_')}", position_id=p.position_id, quote=quote,
            output_usd_price=morpho.morpho.loan_price, gas_cost_usd=D(p.gas_usd), extra_fees_usd=D(p.extra_fees_usd),
            route_available=True, closing_feasible=True, requires_external_repayment_liquidity=True,
            repayment_liquidity_confirmed=True, basis="USER_ASSUMPTION", evidence_ids=(e_plan.evidence_id,),
        ))
        # Validated net-carry projections for the predictive mode (synthetic backtest reference).
        cv, dv = collateral * D(m.pt_usd), debt * D(m.loan_usd)
        e_fc = ds.ev("demo:forecast", p.position_id, kind=SourceKind.DERIVED, source="demo_forecast", at=T - timedelta(minutes=5))
        for h in (1, 3, 7, 30):
            growth = (1 + float(m.implied)) ** (h / 365) - 1
            cost = (1 + float(m.borrow_apy)) ** (h / 365) - 1
            carry = (cv * D(repr(growth)) - dv * D(repr(cost))) * D(m.forecast_bias)
            ds.forecasts.append(ForecastBundle(
                bundle_id=f"fc_demo_{p.position_id.removeprefix('pos_')}_{h}d", strategy_id=strategy.strategy_id,
                position_id=p.position_id, horizon_days=h, generated_at=T - timedelta(minutes=5), valid_until=T + timedelta(hours=12),
                validation_status="VALIDATED", transformation="NET_CARRY_USD_OVER_HORIZON", estimated_net_carry_usd=q(carry, 2),
                methodology="Net carry projection over the horizon (demo dataset).",
                backtest_reference="demo-backtest-v1", evidence_ids=(e_fc.evidence_id,),
            ))


def incident_catalog(fetch: bool) -> dict | None:
    if INCIDENT_CACHE.exists():
        return json.loads(INCIDENT_CACHE.read_text())
    if not fetch:
        return None
    from app.ingestion.incidents import fetch_catalog
    try:
        catalog, _ = fetch_catalog()
    except Exception as exc:  # network is optional: the drawer then reports the catalogue as unavailable
        print(f"incident catalogue unavailable: {type(exc).__name__}", file=sys.stderr)
        return None
    INCIDENT_CACHE.parent.mkdir(parents=True, exist_ok=True)
    INCIDENT_CACHE.write_text(json.dumps(catalog, default=str, ensure_ascii=False))
    return catalog


def build(as_of: datetime) -> Dataset:
    ds = Dataset(as_of=as_of)
    built = {m.key: (m, *build_market(ds, m)) for m in MARKETS}
    for p in POSITIONS:
        m, strategy, morpho = built[p.market]
        build_position(ds, p, m, strategy, morpho)
    return ds


def write(ds: Dataset, out: Path, catalog: dict | None) -> None:
    if out.exists():
        if not (out / MARKER).exists():
            raise SystemExit(f"refusing to overwrite {out}: not a generated demo dataset")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / MARKER).write_text(ds.as_of.isoformat() + "\n")
    with SqliteStore(out / DB_FILENAME) as store:
        store.save_evidence(ds.evidence.values())
        store.save_markets(ds.markets)
        store.save_strategies(ds.strategies)
        store.save_relations({r.relation_id: r for r in ds.relations}.values())
        store.save_positions(ds.positions)
    parquet = ParquetStore(out / "timeseries")
    for entity, dto in ds.series:
        parquet.write_observations(observations_from_series(dto, entity, ds.as_of), DEMO, expected_interval_seconds=86400)
    inputs = AnalyticsInputs(quotes=tuple(ds.quotes), risk_observations=tuple(ds.risk), forecasts=tuple(ds.forecasts))
    (out / "analytics_inputs.json").write_text(inputs.model_dump_json(indent=2) + "\n")
    if catalog is not None:
        bundle = out / "delivery" / "demo"
        bundle.mkdir(parents=True)
        (bundle / "incident_catalog.json").write_text(json.dumps(catalog, default=str, ensure_ascii=False))
        (out / "delivery" / "current.json").write_text(json.dumps({"bundle": "demo"}))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"output data directory (default {DEFAULT_OUT})")
    parser.add_argument("--as-of", type=lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")), default=None,
                        help="anchor timestamp (default: now, UTC)")
    parser.add_argument("--no-incidents", action="store_true", help="skip the DefiLlama incident catalogue")
    args = parser.parse_args(argv)
    as_of = (args.as_of or datetime.now(timezone.utc)).replace(second=0, microsecond=0)
    ds = build(as_of)
    write(ds, args.out, None if args.no_incidents else incident_catalog(fetch=True))
    print(f"demo dataset: {len(ds.strategies)} markets, {len(ds.positions)} positions, "
          f"{sum(len(s.points) for _, s in ds.series)} history points, as_of {as_of.isoformat()} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

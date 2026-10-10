"""Build backend/app/fixtures/ui_mock_data_v1.json (DEMO data for the frontend).

Every object is built as its DTO (so all contract validators run) and written with
model_dump(mode="json"). Everything is simulated: data_mode DEMO, evidence FIXTURE,
fake repeating-pattern addresses, DEMO- symbols. "Verification" checks here compare
two fixture values; relations are CURATED (never ONCHAIN_VERIFIED: no RPC read exists).
Gaps are explicit: Morpho oracle/USD prices and Pendle liquidity are null, a series has
a MISSING point. No health factor, risk score or recommendation.

Deterministic (fixed timestamps): rerunning produces the same file.
Run from backend/:  python scripts/build_ui_fixture.py [--check]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.ingestion.normalize import (  # noqa: E402
    BACKING_UNRESOLVED_NOTE,
    MorphoApys,
    OnchainMarketParams,
    OnchainMarketState,
    OnchainPendleTokens,
    SourceRef,
    TokenMeta,
    _check,
    asset_id,
    contract_id,
    morpho_market_key,
    normalize_morpho_market,
    normalize_pendle_market,
    pendle_market_key,
)
from app.models.evidence import (  # noqa: E402
    SCHEMA_VERSION,
    DataMode,
    DataStatus,
    EvidenceDTO,
    RelationStatus,
    RelationType,
    SourceKind,
    make_evidence_id,
    make_relation,
)
from app.models.market import MarketDTO, PricingMethod  # noqa: E402
from app.models.position import PositionDTO, PositionKind  # noqa: E402
from app.models.strategy import StrategyDTO, StrategyStatus, make_strategy_id  # noqa: E402
from app.models.timeseries import (  # noqa: E402
    SeriesUnit,
    SubjectKind,
    TimeseriesDTO,
    TimeseriesMetric,
    TimeseriesPointDTO,
)

FIXTURE_PATH = BACKEND_DIR / "app" / "fixtures" / "ui_mock_data_v1.json"
NOTICE = "All values are simulated demo data. Not real markets, addresses or prices."
SOURCE_ID = "demo_fixture"
PARSER_VERSION = "build_ui_fixture.v1"
DEMO = DataMode.DEMO

GENERATED_AT = datetime(2026, 10, 1, tzinfo=timezone.utc)
EXPIRY = datetime(2027, 3, 25, tzinfo=timezone.utc)
FAKE_BLOCK = 12_345_678
FAKE_BLOCK_HASH = "0x" + "f" * 64


def _addr(c: str) -> str:
    return "0x" + c * 40


MORPHO_MARKET_ID = "0x" + "d" * 64
MORPHO_SINGLETON = _addr("c")
PENDLE_MARKET = _addr("e")
LOAN = _addr("1")        # DEMO-USD
PT = _addr("2")          # DEMO-PT
YT = _addr("3")
SY = _addr("4")
UNDERLYING = _addr("5")  # DEMO-yUSD (Pendle API "underlyingAsset" = SY yield token)
ORACLE = _addr("6")
IRM = _addr("7")
ACCOUNTING = _addr("8")  # DEMO-USD-BASE (SY accounting asset)

SYMBOLS = {LOAN: ("DEMO-USD", 6), PT: ("DEMO-PT-yUSD", 18), YT: ("DEMO-YT-yUSD", 18),
           SY: ("DEMO-SY-yUSD", 18), UNDERLYING: ("DEMO-yUSD", 18), ACCOUNTING: ("DEMO-USD-BASE", 18)}


def _evidence(method: str, *, block: bool = True) -> EvidenceDTO:
    params = {"fixture": "ui_mock_data_v1"}
    return EvidenceDTO(
        chain_id=1, data_mode=DEMO,
        evidence_id=make_evidence_id(SOURCE_ID, method, params, fetched_at=GENERATED_AT),
        source_kind=SourceKind.FIXTURE, source_id=SOURCE_ID, method=method, request_params=params,
        block_number=FAKE_BLOCK if block else None, block_hash=FAKE_BLOCK_HASH if block else None,
        fetched_at=GENERATED_AT, status=DataStatus.OK,
        decoded_excerpt={"note": "simulated"}, parser_version=PARSER_VERSION,
    )


def build_fixture() -> dict[str, Any]:
    ev = {name: _evidence(f"fixture:{name}", block=name.startswith("sim_"))
          for name in ("morpho_item", "morpho_apys", "pendle_item", "series",
                       "sim_morpho_params", "sim_morpho_state", "sim_pendle_tokens", "sim_sy_info")}

    def ref(name: str) -> SourceRef:
        return SourceRef(SOURCE_ID, ev[name].evidence_id, GENERATED_AT)

    tokens = {a: TokenMeta(a, d, s, ref("sim_morpho_params")) for a, (s, d) in SYMBOLS.items()}

    # --- Morpho market: simulated params + state, no oracle read, no USD prices -> PARTIAL
    morpho_item = {"market_id": MORPHO_MARKET_ID, "chain_id": 1, "loan_token": LOAN, "collateral_token": PT,
                   "oracle_address": ORACLE, "irm_address": IRM, "lltv_wad": "915000000000000000"}
    morpho = normalize_morpho_market(
        morpho_item, ref("morpho_item"), data_mode=DEMO, tokens=tokens,
        onchain_params=OnchainMarketParams(MORPHO_MARKET_ID, LOAN, PT, ORACLE, IRM, 915 * 10**15,
                                           FAKE_BLOCK, ref("sim_morpho_params")),
        onchain_state=OnchainMarketState(MORPHO_MARKET_ID, 2_500_000_000_000, 2_500_000_000_000_000_000,
                                         1_800_000_000_000, 1_790_000_000_000_000_000,
                                         int(GENERATED_AT.timestamp()), 0, FAKE_BLOCK, ref("sim_morpho_state")),
        apys=MorphoApys(Decimal("0.0412"), Decimal("0.0598"), ref("morpho_apys")),
    ).dto
    # The normaliser pins the real Morpho Blue singleton; the fixture must not carry real addresses.
    dumped = morpho.model_dump()
    morpho = MarketDTO.model_validate({**dumped, "morpho": {**dumped["morpho"], "morpho_address": MORPHO_SINGLETON}})

    # --- Pendle market: simulated readTokens, liquidity null in source -> PARTIAL
    pendle_item = {
        "chainId": 1, "address": PENDLE_MARKET, "expiry": EXPIRY.isoformat().replace("+00:00", "Z"),
        "pt": f"1-{PT}", "yt": f"1-{YT}", "sy": f"1-{SY}", "underlyingAsset": f"1-{UNDERLYING}",
        "details": {"impliedApy": "0.0874", "underlyingApy": "0.0621", "liquidity": None,
                    "totalPt": "1250000.5", "totalSy": "980000.25"},
    }
    pendle_tokens = OnchainPendleTokens(PENDLE_MARKET, SY, PT, YT, int(EXPIRY.timestamp()), FAKE_BLOCK,
                                        ref("sim_pendle_tokens"))
    pendle = normalize_pendle_market(pendle_item, ref("pendle_item"), data_mode=DEMO,
                                     onchain=pendle_tokens, tokens=tokens).dto

    # --- Strategy: VERIFIED_ACTIVE only because its (simulated) checks, evidence and block match.
    checks = (
        *morpho.verification,
        *pendle.verification,
        _check("strategy.pt_is_morpho_collateral", PT, SOURCE_ID, PT,
               (ev["sim_morpho_params"].evidence_id, ev["sim_pendle_tokens"].evidence_id)),
    )
    strategy = StrategyDTO(
        chain_id=1, data_mode=DEMO, strategy_id=make_strategy_id(1, MORPHO_MARKET_ID, PT),
        status=StrategyStatus.VERIFIED_ACTIVE, status_reason="DEMO: simulated verification",
        data_status=DataStatus.OK, morpho_market_id=MORPHO_MARKET_ID, pendle_market_address=PENDLE_MARKET,
        pt=morpho.morpho.collateral_token, loan_asset=morpho.morpho.loan_token,
        underlying_asset=pendle.pendle.underlying_asset, expiry=EXPIRY, as_of=GENERATED_AT,
        block_number=FAKE_BLOCK, verification=checks, source_ids=(SOURCE_ID,),
        evidence_ids=tuple(dict.fromkeys((*morpho.evidence_ids, *pendle.evidence_ids))),
    )

    # --- Position: simulated borrow, amounts consistent with raw values.
    position = PositionDTO(
        chain_id=1, data_mode=DEMO, position_id="pos_demo_ui_1", kind=PositionKind.SIMULATED,
        market_id=MORPHO_MARKET_ID, collateral_asset=strategy.pt, loan_asset=strategy.loan_asset,
        collateral_amount=Decimal("10000"), collateral_amount_raw=10_000 * 10**18,
        borrow_amount=Decimal("7500.5"), borrow_amount_raw=7_500_500_000,
        strategy_id=strategy.strategy_id, label="DEMO simulated borrow", as_of=GENERATED_AT,
        status=DataStatus.OK, source_ids=("user_input",),
    )

    # --- Relations: CURATED (simulated), backing UNRESOLVED.
    curated = "DEMO: simulated relation from fixture evidence, not read on-chain"
    m_key, p_key = morpho_market_key(MORPHO_MARKET_ID), pendle_market_key(PENDLE_MARKET)
    edges = [
        (m_key, RelationType.MARKET_LOAN_ASSET, asset_id(LOAN), "sim_morpho_params"),
        (m_key, RelationType.MARKET_COLLATERAL_ASSET, asset_id(PT), "sim_morpho_params"),
        (m_key, RelationType.MARKET_ORACLE, contract_id(ORACLE), "sim_morpho_params"),
        (m_key, RelationType.MARKET_IRM, contract_id(IRM), "sim_morpho_params"),
        (p_key, RelationType.PENDLE_MARKET_PT, asset_id(PT), "sim_pendle_tokens"),
        (p_key, RelationType.PENDLE_MARKET_SY, asset_id(SY), "sim_pendle_tokens"),
        (p_key, RelationType.PENDLE_MARKET_YT, asset_id(YT), "sim_pendle_tokens"),
        (asset_id(SY), RelationType.SY_YIELD_TOKEN, asset_id(UNDERLYING), "sim_sy_info"),
        (asset_id(SY), RelationType.SY_ACCOUNTING_ASSET, asset_id(ACCOUNTING), "sim_sy_info"),
    ]
    relations = [
        make_relation(source_node_id=s, target_node_id=t, relation_type=rt,
                      verification_status=RelationStatus.CURATED, evidence=[ev[e]],
                      observed_at=GENERATED_AT, note=curated, data_mode=DEMO)
        for s, rt, t, e in edges
    ]
    relations.append(make_relation(
        source_node_id=asset_id(UNDERLYING), target_node_id=asset_id(ACCOUNTING),
        relation_type=RelationType.ECONOMIC_BACKING, verification_status=RelationStatus.UNRESOLVED,
        evidence=[ev["sim_sy_info"]], observed_at=GENERATED_AT, note=BACKING_UNRESOLVED_NOTE, data_mode=DEMO,
    ))

    # --- Series: 7 daily points, one MISSING (never 0).
    days = [GENERATED_AT - timedelta(days=i) for i in range(6, -1, -1)]
    implied = ["0.0851", "0.0858", None, "0.0866", "0.0869", "0.0871", "0.0874"]
    pt_usd = ["0.9581", "0.9590", "0.9597", "0.9603", "0.9611", "0.9618", "0.9624"]
    series_ev = (ev["series"].evidence_id,)

    def points(values):
        return tuple(TimeseriesPointDTO(
            timestamp=d, value=Decimal(v) if v else None,
            status=DataStatus.OK if v else DataStatus.MISSING, evidence_ids=series_ev,
        ) for d, v in zip(days, values))

    series = [
        TimeseriesDTO(
            chain_id=1, data_mode=DEMO, series_id="ts_demo_pt_implied_apy_day",
            metric=TimeseriesMetric.PT_IMPLIED_APY, subject_kind=SubjectKind.MARKET, subject_id=PENDLE_MARKET,
            unit=SeriesUnit.RATIO, interval_seconds=86400, source_id=SOURCE_ID, status=DataStatus.PARTIAL,
            points=points(implied),
        ),
        TimeseriesDTO(
            chain_id=1, data_mode=DEMO, series_id="ts_demo_pt_market_price_usd_day",
            metric=TimeseriesMetric.MARKET_PRICE, subject_kind=SubjectKind.ASSET, subject_id=PT,
            unit=SeriesUnit.USD, pricing_method=PricingMethod.API_SPOT, interval_seconds=86400,
            source_id=SOURCE_ID, status=DataStatus.OK, points=points(pt_usd),
        ),
    ]

    def dump(items):
        return [i.model_dump(mode="json") for i in items]

    return {
        "schema_version": SCHEMA_VERSION,
        "data_mode": DEMO.value,
        "generated_at": GENERATED_AT.isoformat().replace("+00:00", "Z"),
        "notice": NOTICE,
        "markets": dump([morpho, pendle]),
        "strategies": dump([strategy]),
        "positions": dump([position]),
        "evidence": dump(ev.values()),
        "relations": dump(relations),
        "series": dump(series),
    }


def render() -> str:
    return json.dumps(build_fixture(), indent=2, sort_keys=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the DEMO UI fixture from validated DTOs")
    parser.add_argument("--check", action="store_true", help="exit 1 if the file is out of date")
    args = parser.parse_args(argv)
    text = render()
    if args.check:
        current = FIXTURE_PATH.read_text(encoding="utf-8") if FIXTURE_PATH.exists() else ""
        print("up to date" if current == text else f"{FIXTURE_PATH} is out of date")
        return 0 if current == text else 1
    FIXTURE_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {FIXTURE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

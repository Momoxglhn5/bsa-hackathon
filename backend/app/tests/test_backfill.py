"""backfill_histories.run_backfill with fake clients (no network)."""
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.adapters.morpho_api import MorphoEndpointUnavailable
from app.adapters.pendle_api import RawResponse
from app.ingestion.normalize import SourceRef, normalize_morpho_history
from app.models.market import TokenRef
from app.models.timeseries import TimeseriesMetric
from app.storage.parquet_store import FLAG_MISSING, ParquetStore
from app.storage.sqlite_store import SqliteStore

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from backfill_histories import run_backfill  # noqa: E402

NOW = datetime(2026, 10, 10, 15, tzinfo=timezone.utc)
MARKET_ID = "0x" + "ab" * 32
PENDLE = "0x" + "33" * 20
MANIFEST = {"chainId": 1, "morphoMarketId": MARKET_ID, "pendleMarketAddress": PENDLE,
            "loanTokenAddress": "0x" + "22" * 20, "status": "VERIFIED_ACTIVE"}
LOAN = TokenRef(chain_id=1, address="0x" + "22" * 20, decimals=6)


def pendle_raw(results) -> RawResponse:
    body = json.dumps({"results": results}).encode()
    return RawResponse(
        source_id="pendle_api", operation="rest:/v3/markets/historical-data", url="https://x/hist",
        request_params={"time_frame": "day"}, status_code=200, requested_at=NOW, retrieved_at=NOW,
        payload=json.loads(body, parse_float=Decimal), payload_sha256=hashlib.sha256(body).hexdigest(),
        attempts=1, rate_limit={}, raw_body=body,
    )


class FakePendle:
    def __init__(self):
        days = [NOW.replace(hour=0) - timedelta(days=i) for i in range(3, -1, -1)]
        self.results = [
            {"timestamp": d.isoformat().replace("+00:00", "Z"), "impliedApy": v, "underlyingApy": 0.04}
            for d, v in zip(days, [0.051, None, 0.053, 0.054])
        ]

    def fetch_market_history(self, address, time_frame, start, end):
        return pendle_raw(self.results)


class UnavailableMorpho:
    def fetch_market_history(self, *a, **k):
        raise MorphoEndpointUnavailable("market_history: no verified Morpho endpoint/query configured")

    fetch_apy_history = fetch_market_history


def backfill(tmp_path, **kw):
    with SqliteStore(tmp_path / "db.sqlite") as sqlite:
        return run_backfill(
            MANIFEST, pendle=FakePendle(), morpho=UnavailableMorpho(), parquet=ParquetStore(tmp_path / "ts"),
            sqlite=sqlite, loan_token=LOAN, now=NOW, days=30, time_frame="day", **kw,
        )


def test_pendle_only_still_written(tmp_path):
    result = backfill(tmp_path)
    status = {s.name: s.status for s in result.sources}
    assert status == {"pendle_history": "written", "morpho_market_history": "unavailable",
                      "morpho_apy_history": "unavailable"}
    assert result.wrote("pendle_history")
    assert {r.metric for r in result.series} == {"PT_IMPLIED_APY", "UNDERLYING_APY"}
    with SqliteStore(tmp_path / "db.sqlite") as sqlite:
        assert sqlite.counts()["evidence"] == 1


def test_rerun_no_duplicates(tmp_path):
    backfill(tmp_path)
    backfill(tmp_path)
    rows = ParquetStore(tmp_path / "ts").read_observations(f"pendle:1:{PENDLE}", "PT_IMPLIED_APY", "REAL")
    assert len(rows) == 4


def test_null_value_is_missing_row(tmp_path):
    backfill(tmp_path)
    rows = ParquetStore(tmp_path / "ts").read_observations(f"pendle:1:{PENDLE}", "PT_IMPLIED_APY", "REAL")
    assert rows[1].value_decimal is None and FLAG_MISSING in rows[1].quality_flags
    assert [r.value_decimal for r in rows if r.value_decimal is not None] == [
        Decimal("0.051"), Decimal("0.053"), Decimal("0.054")
    ]
    report = next(r for r in backfill(tmp_path).series if r.metric == "PT_IMPLIED_APY")
    assert report.missing == 1 and report.with_value == 3


def test_dry_run_writes_nothing(tmp_path):
    result = backfill(tmp_path, dry_run=True)
    assert result.wrote("pendle_history")
    assert not (tmp_path / "ts").exists()


def test_normalize_morpho_history_keeps_ratios():
    day = int(NOW.replace(hour=0).timestamp())
    payload = {"data": {"marketById": {"historicalState": {
        "supplyApy": [{"x": day, "y": Decimal("0.05")}, {"x": day - 86400, "y": None}],
        "supplyAssets": [{"x": day, "y": 501542984715}],
    }}}}
    out = normalize_morpho_history(payload, MARKET_ID, SourceRef("morpho_api", "ev_" + "a" * 24, NOW),
                                   time_frame="day", loan_token=LOAN)
    apy = out[TimeseriesMetric.SUPPLY_APY].dto
    assert [p.value for p in apy.points] == [None, Decimal("0.05")]
    assert apy.points[0].status.value == "MISSING"
    assert apy.unit.value == "RATIO"
    assets = out[TimeseriesMetric.TOTAL_SUPPLY_ASSETS].dto
    assert assets.points[0].value == Decimal("501542.984715")
    assert assets.quote_asset == LOAN
    assert TimeseriesMetric.BORROW_APY not in out


def test_normalize_morpho_history_requires_decimals_for_assets():
    payload = {"data": {"marketById": {"historicalState": {"supplyAssets": [{"x": 0, "y": 1}]}}}}
    with pytest.raises(ValueError):
        normalize_morpho_history(payload, MARKET_ID, SourceRef("morpho_api", "ev_" + "a" * 24, NOW),
                                 time_frame="day", loan_token=TokenRef(chain_id=1, address=LOAN.address))

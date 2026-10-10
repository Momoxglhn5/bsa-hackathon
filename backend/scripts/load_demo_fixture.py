"""Load the DEMO UI fixture into SQLite and Parquet (data_mode DEMO only).

Every object is validated against its DTO first; if ANY object is not DEMO (or does not
validate), nothing is written. Idempotent: evidence is insert-or-ignore, other rows are
upserts, Parquet rows are deduplicated.

Run from backend/:  python scripts/load_demo_fixture.py [--fixture PATH] [--db PATH] [--timeseries-root PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.ingestion.normalize import asset_id, morpho_market_key, pendle_market_key  # noqa: E402
from app.models.evidence import DataMode, EvidenceDTO, RelationDTO  # noqa: E402
from app.models.market import MarketDTO  # noqa: E402
from app.models.position import PositionDTO  # noqa: E402
from app.models.strategy import StrategyDTO  # noqa: E402
from app.models.timeseries import SubjectKind, TimeseriesDTO  # noqa: E402
from app.storage.parquet_store import ParquetStore, observations_from_series  # noqa: E402
from app.storage.sqlite_store import SqliteStore  # noqa: E402

FIXTURE_PATH = BACKEND_DIR / "app" / "fixtures" / "ui_mock_data_v1.json"
MODELS = {"markets": MarketDTO, "strategies": StrategyDTO, "positions": PositionDTO,
          "evidence": EvidenceDTO, "relations": RelationDTO, "series": TimeseriesDTO}


class NotDemoError(ValueError):
    """The fixture contains an object that is not DEMO data."""


def parse_fixture(data: dict[str, Any]) -> dict[str, list]:
    """Validate every object; raise before anything is written if one is not DEMO."""
    if data.get("data_mode") != DataMode.DEMO.value:
        raise NotDemoError(f"fixture data_mode is {data.get('data_mode')!r}, expected DEMO")
    parsed: dict[str, list] = {}
    for key, model in MODELS.items():
        parsed[key] = [model.model_validate(obj) for obj in data.get(key, [])]
        bad = [i for i, dto in enumerate(parsed[key]) if dto.data_mode != DataMode.DEMO]
        if bad:
            raise NotDemoError(f"{key}{bad}: not DEMO data; refusing to load")
    return parsed


def _entity_id(series: TimeseriesDTO) -> str:
    if series.subject_kind == SubjectKind.ASSET:
        return asset_id(series.subject_id)
    return morpho_market_key(series.subject_id) if len(series.subject_id) == 66 else pendle_market_key(series.subject_id)


def load_fixture(data: dict[str, Any], sqlite: SqliteStore, parquet: ParquetStore) -> dict[str, int]:
    """Returns rows written per kind (0 on a rerun with unchanged data, except upserts)."""
    p = parse_fixture(data)
    generated_at = datetime.fromisoformat(data["generated_at"].replace("Z", "+00:00"))
    written = {
        "evidence": sqlite.save_evidence(p["evidence"]),
        "markets": sqlite.save_markets(p["markets"]),
        "strategies": sqlite.save_strategies(p["strategies"]),
        "relations": sqlite.save_relations(p["relations"]),
        "positions": sqlite.save_positions(p["positions"]),
        "series_points": 0,
    }
    for s in p["series"]:
        rows = observations_from_series(s, _entity_id(s), generated_at)
        parquet.write_observations(rows, DataMode.DEMO, expected_interval_seconds=s.interval_seconds)
        written["series_points"] += len(rows)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load the DEMO UI fixture (DEMO data only)")
    parser.add_argument("--fixture", type=Path, default=FIXTURE_PATH)
    parser.add_argument("--db", type=Path, default=None, help="SQLite path (default: data dir)")
    parser.add_argument("--timeseries-root", type=Path, default=None)
    args = parser.parse_args(argv)
    data = json.loads(args.fixture.read_text(encoding="utf-8"))
    try:
        with SqliteStore(args.db) as sqlite:
            written = load_fixture(data, sqlite, ParquetStore(args.timeseries_root))
            print(f"db={sqlite.path} written={written}")
    except NotDemoError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Export Data storage to a reproducible analytics JSON, without network or DB writes.

A simulated fund can be added in token units. Prices, quotes and feasibility are
never fabricated. An optional quote-context JSON comes from the closing provider.
"""
import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.analytics.dataset import AnalyticsSnapshot, QuoteContext, AnalyticsInputs
from app.analytics.provider import snapshot_from_store
from app.models.evidence import DataMode
from app.models.position import PositionDTO
from app.storage.parquet_store import ParquetStore
from app.storage.sqlite_store import SqliteStore


def with_simulated_position(snapshot, strategy_id, collateral_amount, borrow_amount, position_id="pos_simulated_fund"):
    strategy = snapshot.strategy(strategy_id)
    market = next(m for m in snapshot.markets if m.market_key == f"morpho:{strategy.chain_id}:{strategy.morpho_market_id}")
    position = PositionDTO(chain_id=strategy.chain_id, data_mode=market.data_mode,
        position_id=position_id, kind="SIMULATED", market_id=market.market_id,
        collateral_asset=market.morpho.collateral_token, loan_asset=market.morpho.loan_token,
        collateral_amount=collateral_amount, borrow_amount=borrow_amount,
        strategy_id=strategy_id, as_of=snapshot.as_of, status="UNVERIFIED",
        source_ids=("analytics:user_assumption",), label="Simulated fund; capacity and execution unverified")
    data = snapshot.model_dump(mode="json")
    data["positions"] = [p for p in data["positions"] if p["position_id"] != position_id] + [position.model_dump(mode="json")]
    data["snapshot_id"] += ":simulated-fund"
    return AnalyticsSnapshot.model_validate(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--timeseries-root", type=Path)
    parser.add_argument("--data-mode", choices=["REAL", "DEMO"], default="REAL")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--strategy-id")
    parser.add_argument("--position-id", default="pos_simulated_fund")
    parser.add_argument("--collateral-amount", type=Decimal, help="Whole PT units, NOT USD")
    parser.add_argument("--borrow-amount", type=Decimal, help="Whole loan-token units, NOT USD")
    parser.add_argument("--inputs", type=Path, help="AnalyticsInputs JSON: quotes, risk observations, forecasts")
    parser.add_argument("--quotes", type=Path, help="JSON array of validated QuoteContext records")
    args = parser.parse_args()
    supplied = (args.strategy_id, args.collateral_amount, args.borrow_amount)
    if any(v is not None for v in supplied) and not all(v is not None for v in supplied):
        parser.error("strategy-id, collateral-amount and borrow-amount are required together")
    inputs = AnalyticsInputs.model_validate_json(args.inputs.read_text()) if args.inputs else AnalyticsInputs()
    if args.quotes:
        inputs = AnalyticsInputs.model_validate({**inputs.model_dump(), "quotes": json.loads(args.quotes.read_text())})
    with SqliteStore(args.db) as store:
        store._conn.execute("BEGIN")
        snapshot = snapshot_from_store(store, ParquetStore(args.timeseries_root), DataMode(args.data_mode), inputs=inputs)
    if args.strategy_id:
        snapshot = with_simulated_position(snapshot, *supplied, args.position_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(snapshot.model_dump_json(indent=2) + "\n")
    print(f"Exported {snapshot.snapshot_id}: {len(snapshot.positions)} positions; {len(snapshot.quotes)} quotes")


if __name__ == "__main__":
    main()

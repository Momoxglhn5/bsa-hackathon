"""Refresh the real demo strategy and publish an immutable, replayable Data bundle."""
import argparse
import hashlib
import json
import sqlite3
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, localcontext
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.adapters.morpho_api import MorphoApiClient
from app.adapters.pendle_api import PendleApiClient
from app.adapters.pendle_execution import PendleQuoteClient, QuoteUnavailable
from app.analytics.dataset import AnalyticsInputs, QuoteContext
from app.analytics.provider import snapshot_from_store
from app.ingestion.incidents import fetch_catalog
from app.ingestion.refresh import Refresher, Target
from app.models.position import PositionDTO
from app.models.evidence import DataMode, EvidenceDTO
from app.models.strategy import StrategyStatus
from app.models.timeseries import TimeseriesMetric
from app.storage.parquet_store import ParquetStore, FLAG_SNAPSHOT
from app.storage.sqlite_store import SqliteStore, default_data_dir


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, default=str, ensure_ascii=False) + "\n")


class RecordResponses:
    """Archive successful source bodies with their evidence id, not API secrets."""
    raw_dir = None

    def _build_raw(self, *args, **kwargs):
        raw = super()._build_raw(*args, **kwargs)
        if self.raw_dir:
            (self.raw_dir / f"{raw.evidence_id}.json").write_bytes(raw.raw_body)
        return raw


class RecordedPendle(RecordResponses, PendleApiClient):
    pass


class RecordedMorpho(RecordResponses, MorphoApiClient):
    pass


class SizingUnavailable(ValueError):
    """Sourced prerequisites for a simulated notional are unavailable."""


def position_for_size(market, strategy, size_usd, now, ltv=Decimal("0.70")):
    """Hypothetical notional, never an on-chain position or proof of capacity."""
    f = market.morpho
    if not f.collateral_price or not f.loan_price or f.collateral_price.value <= 0 or f.loan_price.value <= 0:
        raise SizingUnavailable("Positive sourced USD prices required for sized positions")
    if f.collateral_token.decimals is None or f.loan_token.decimals is None:
        raise SizingUnavailable("Verified token decimals required")
    if any(not 0 <= (now - price.observed_at).total_seconds() <= 300
           for price in (f.collateral_price, f.loan_price)):
        raise SizingUnavailable("Fresh USD spots required; cached prices are stale or in the future")
    with localcontext() as ctx:
        ctx.prec = 120
        collateral = (size_usd / f.collateral_price.value).quantize(Decimal(1).scaleb(-f.collateral_token.decimals), rounding=ROUND_DOWN)
        borrowed = (size_usd * ltv / f.loan_price.value).quantize(Decimal(1).scaleb(-f.loan_token.decimals), rounding=ROUND_DOWN)
    return PositionDTO(chain_id=1, data_mode="REAL", position_id=f"pos_simulated_{int(size_usd)}usd",
        kind="SIMULATED", market_id=market.market_id, strategy_id=strategy.strategy_id,
        collateral_asset=f.collateral_token, loan_asset=f.loan_token,
        collateral_amount=collateral, borrow_amount=borrowed, as_of=now, status="UNVERIFIED",
        source_ids=("analytics:user_assumption",), label=f"Hypothetical {size_usd} USD collateral; 70% market LTV; capacity unverified")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    args = parser.parse_args(argv)
    if not 1 <= args.days <= 730:
        parser.error("days must be within 1..730")
    started = datetime.now(timezone.utc)
    root = args.data_dir.resolve()
    delivery = root / "delivery"
    bundle = delivery / started.strftime("%Y%m%dT%H%M%S%fZ")
    raw_dir = bundle / "raw"
    raw_dir.mkdir(parents=True)
    report = {"schema_version": "data-delivery-v1", "data_mode": "REAL", "started_at": started,
              "bundle": str(bundle), "tasks": [], "warnings": [], "funds_are_simulated": True}
    target = Target.from_manifest()
    quotes, records = [], []
    with RecordedMorpho() as morpho, RecordedPendle() as pendle, SqliteStore(root / "domino.sqlite") as store, PendleQuoteClient() as execution:
        morpho.raw_dir = pendle.raw_dir = raw_dir
        parquet = ParquetStore(root / "timeseries")
        refresher = Refresher(morpho=morpho, pendle=pendle, sqlite=store, parquet=parquet, target=target)
        for name in ("verify_onchain", "refresh_current"):
            result = getattr(refresher, name)()
            report["tasks"].append(asdict(result))
        strategy = next((s for s in store.list_strategies() if s.morpho_market_id == target.morpho_market_id), None)
        if strategy is None or strategy.status != StrategyStatus.VERIFIED_ACTIVE:
            write_json(bundle / "report.json", report)
            raise SystemExit("No VERIFIED_ACTIVE selected strategy; see report, no current bundle published")
        report["strategy_id"] = strategy.strategy_id
        result = refresher.backfill_history(strategy, args.days)
        report["tasks"].append(asdict(result))
        # Refresh after history collection so spots and execution quotes share a short time window.
        report["tasks"].append(asdict(refresher.refresh_current(strategy)))
        market = store.get_market(f"morpho:1:{strategy.morpho_market_id}")
        if market is None:
            raise SystemExit("Selected Morpho market unavailable")
        for size in (Decimal("1000000"), Decimal("2000000"), Decimal("5000000")):
            record = {"strategy_id": strategy.strategy_id, "size_usd": str(size),
                      "status": "UNAVAILABLE", "quote": None, "context": None,
                      "missing_fields": ["closing_feasible", "gas_cost_usd", "extra_fees_usd", "repayment_liquidity_confirmed"]}
            try:
                position = position_for_size(market, strategy, size, datetime.now(timezone.utc))
                store.save_positions([position])
                record.update(position_id=position.position_id, input_amount=str(position.collateral_amount))
                quote, evidence, details, raw = execution.quote(market=strategy.pendle_market_address,
                    collateral=position.collateral_asset, loan=position.loan_asset, amount=position.collateral_amount)
                store.save_evidence([evidence])
                (raw_dir / f"{evidence.evidence_id}.json").write_bytes(raw)
                context = QuoteContext(quote_id=evidence.evidence_id, position_id=position.position_id,
                    quote=quote, output_usd_price=market.morpho.loan_price, route_available=True,
                    closing_feasible=None, basis="USER_ASSUMPTION", evidence_ids=(evidence.evidence_id,))
                quotes.append(context)
                record.update(status="PARTIAL", quote=quote.model_dump(mode="json"),
                              context=context.model_dump(mode="json"), details=details)
            except (SizingUnavailable, QuoteUnavailable, httpx.HTTPError) as exc:
                record["reason"] = str(exc)[:500]
                record["missing_fields"].append("execution_quote")
                if isinstance(exc, QuoteUnavailable) and exc.evidence is not None:
                    store.save_evidence([exc.evidence])
                    (raw_dir / f"{exc.evidence.evidence_id}.json").write_bytes(exc.raw_body)
                    record["evidence_ids"] = [exc.evidence.evidence_id]
                    record["observed_at"] = exc.evidence.fetched_at.isoformat()
            records.append(record)
        write_json(bundle / "exit_quotes.json", {"schema_version": "sized-exit-quotes-v1", "data_mode": "REAL", "records": records})
        try:
            catalog, raw = fetch_catalog()
            (raw_dir / "defillama_hacks.json").write_bytes(raw)
            write_json(bundle / "incident_catalog.json", catalog)
            report["incident_count"] = len(catalog["events"])
        except (httpx.HTTPError, ValueError) as exc:
            report["warnings"].append(f"Incident catalogue unavailable: {type(exc).__name__}")
            write_json(bundle / "incident_catalog.json", {"status": "UNAVAILABLE", "events": [], "probability": None})
        inputs = AnalyticsInputs(quotes=tuple(quotes))
        (bundle / "analytics_inputs.json").write_text(inputs.model_dump_json(indent=2) + "\n")
        snapshot = snapshot_from_store(store, parquet, inputs=inputs)
        # Include all available historical metrics in the JSON, retaining missing/gap flags.
        series = []
        observations = []
        all_evidence = tuple(EvidenceDTO.model_validate_json(r[0]) for r in store._conn.execute(
            "SELECT payload FROM evidence WHERE data_mode = ?", ("REAL",)))
        evidence_by_fetch = {}
        for evidence in all_evidence:
            evidence_by_fetch.setdefault((evidence.source_id, evidence.fetched_at), []).append(evidence.evidence_id)
        report["history"] = []
        asset_addresses = {market.morpho.collateral_token.address, market.morpho.loan_token.address}
        asset_addresses.update(m.pendle.underlying_asset.address for m in snapshot.markets
                               if m.pendle and m.pendle.underlying_asset)
        for entity in [m.market_key for m in snapshot.markets] + [f"asset:1:{a}" for a in sorted(asset_addresses)]:
            for metric in TimeseriesMetric:
                rows = parquet._load(parquet.path_for(entity, metric.value, DataMode.REAL))
                observations.extend(asdict(r) for r in rows)
                for source in sorted({r.source_id for r in rows}):
                    dto = parquet.read_series(entity, metric.value, DataMode.REAL, source_id=source,
                        quote_asset=market.morpho.loan_token if any(r.unit == "TOKEN" for r in rows if r.source_id == source) else None)
                    if dto:
                        by_time = {r.ts_utc: r for r in rows if r.source_id == source}
                        values = dto.model_dump()
                        for point in values["points"]:
                            row = by_time[point["timestamp"]]
                            point["evidence_ids"] = point["evidence_ids"] or tuple(evidence_by_fetch.get((source, row.retrieved_at), ()))
                        series.append(type(dto).model_validate(values))
                history_rows = [r for r in rows if FLAG_SNAPSHOT not in r.quality_flags]
                daily = [r for r in history_rows if r.value_decimal is not None]
                if history_rows:
                    report["history"].append({"entity_id": entity, "metric": metric.value,
                        "observations": len(history_rows), "with_value": len(daily), "missing": len(history_rows) - len(daily), "distinct_days": len({r.ts_utc.date() for r in daily}),
                        "first": min((r.ts_utc for r in daily), default=None), "last": max((r.ts_utc for r in daily), default=None)})
        snapshot = snapshot.model_copy(update={"series": tuple(series), "evidence": all_evidence, "as_of": max(snapshot.as_of, *(e.fetched_at for e in all_evidence)), "description": "Real market data; simulated 1/2/5M USD funds at 70% market LTV. Quotes indicative; repayment/closing unverified."})
        snapshot = type(snapshot).model_validate(snapshot.model_dump())
        snapshot = snapshot.model_copy(update={"snapshot_id": "delivery:REAL:" + hashlib.sha256(snapshot.model_dump_json().encode()).hexdigest()[:24]})
        write_json(bundle / "timeseries_observations.json", observations)
        report["warnings"].append("Closing feasibility, repayment liquidity and total closing gas/fees remain unverified; incident probabilities are uncalibrated.")
        (bundle / "analytics_snapshot.json").write_text(snapshot.model_dump_json(indent=2) + "\n")
        with sqlite3.connect(bundle / "domino.sqlite") as backup:
            store._conn.backup(backup)
        report["market_count"] = len(snapshot.markets)
        report["evidence_count"] = len(snapshot.evidence)
        report["quotes_available"] = len(quotes)
        report["positions"] = [p.model_dump(mode="json") for p in snapshot.positions]
        report["quote_results"] = [{k: v for k, v in r.items() if k not in ("quote", "context", "details")} for r in records]
    report["finished_at"] = datetime.now(timezone.utc)
    report["status"] = "PARTIAL" if report["warnings"] or any(not t["ok"] for t in report["tasks"]) or any(r["missing_fields"] for r in records) else "OK"
    write_json(bundle / "report.json", report)
    files = {str(p.relative_to(bundle)): hashlib.sha256(p.read_bytes()).hexdigest() for p in bundle.rglob("*") if p.is_file()}
    write_json(bundle / "checksums.json", files)
    # Publish only after a complete bundle exists. Readers use this one atomic pointer.
    pointer = delivery / f".{bundle.name}.tmp"
    write_json(pointer, {"bundle": bundle.name, "published_at": report["finished_at"]})
    pointer.replace(delivery / "current.json")
    print(json.dumps({"status": report["status"], "bundle": str(bundle), "quotes_available": len(quotes), "history": report["history"]}, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

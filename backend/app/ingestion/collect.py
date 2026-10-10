"""Raw data collection: call the Morpho / Pendle adapters and persist a replayable raw snapshot.

This module only fetches and stores. It does not normalise, score or build graphs.

Snapshot layout (written to `<root>/.<id>.partial/`, atomically renamed to `<root>/<id>/`):
    manifest.json          metadata, config, listing/discovery summary, records, errors
    bodies/0001_<src>_<op>.json   exact response bytes (sha256 recorded in the manifest)

Per-record metadata: source, operation, URL, request params/body, HTTP status,
requested_at, received_at, attempts, payload sha256, block (None for API calls),
evidence_id, rate-limit headers.

Replay: `load_raw_snapshot(path)` re-reads bodies, verifies every sha256 and evidence id,
and re-parses JSON with parse_float=Decimal exactly as the adapters do, so normalisation
can be re-run offline with identical inputs and provenance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import secrets
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

from app.adapters.morpho_api import MorphoAPIError, MorphoApiClient
from app.adapters.pendle_api import PendleAPIError, PendleApiClient, parse_pendle_id
from app.models.evidence import (
    ETHEREUM_MAINNET_CHAIN_ID,
    SCHEMA_VERSION,
    DataMode,
    make_evidence_id,
    normalize_address,
    normalize_bytes32,
)

logger = logging.getLogger(__name__)

RAW_SNAPSHOT_FORMAT = "raw-snapshot-v1"
CHAIN_ID = ETHEREUM_MAINNET_CHAIN_ID
DATA_DIR_ENV = "DOMINO_DATA_DIR"
MANIFEST_NAME = "manifest.json"
BODIES_DIR = "bodies"

PENDLE_MARKETS_OP = "rest:/v2/markets/all"
PENDLE_HISTORY_OP = "rest:/v3/markets/historical-data"
PENDLE_PRICES_OP = "rest:/v1/prices/assets"
MORPHO_MARKETS_OP = "rest:/v1/blue/markets"
MORPHO_DETAILS_OP = "graphql:marketById"


def default_raw_root() -> Path:
    """REAL raw snapshots only. Demo data lives in fixtures, never here."""
    return Path(os.environ.get(DATA_DIR_ENV, "data")) / "raw" / "real"


@dataclass(frozen=True)
class CollectConfig:
    pendle_active_only: bool = True
    pendle_page_limit: int = 100
    pendle_max_pages: int = 30
    morpho_page_limit: int = 100
    morpho_max_pages: int = 50
    fetch_history: bool = True
    history_time_frame: str = "day"
    history_days: int = 30
    fetch_prices: bool = True
    fetch_morpho_details: bool = True
    max_candidates: int = 50


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:60]


class RawSnapshotWriter:
    """Accumulates raw responses and errors; finalize() writes the manifest atomically."""

    def __init__(self, root: Path, config: CollectConfig, sources: dict[str, dict[str, Any]]) -> None:
        self.started_at = datetime.now(timezone.utc)
        self.snapshot_id = f"{self.started_at:%Y%m%dT%H%M%SZ}_{secrets.token_hex(3)}"
        self.root = root
        self.tmp_dir = root / f".{self.snapshot_id}.partial"
        self.final_dir = root / self.snapshot_id
        (self.tmp_dir / BODIES_DIR).mkdir(parents=True, exist_ok=False)
        self.config = config
        self.sources = sources
        self.records: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []
        self.listings: dict[str, dict[str, Any]] = {}
        self.discovery: dict[str, Any] = {}

    def add_response(self, raw: Any, *, ok: bool = True, block_number: int | None = None) -> int:
        """Persist one adapter RawResponse (body bytes + metadata). Returns record_id."""
        record_id = len(self.records) + 1
        body: bytes = raw.raw_body
        sha = hashlib.sha256(body).hexdigest()
        if sha != raw.payload_sha256:
            raise RuntimeError(f"body hash changed in memory for {raw.url}")
        body_file = f"{BODIES_DIR}/{record_id:04d}_{_slug(raw.source_id)}_{_slug(raw.operation)}.json"
        (self.tmp_dir / body_file).write_bytes(body)
        self.records.append({
            "record_id": record_id,
            "ok": ok,
            "source_id": raw.source_id,
            "operation": raw.operation,
            "http_method": getattr(raw, "http_method", "GET"),
            "url": raw.url,
            "request_params": raw.request_params,
            "request_body": getattr(raw, "request_body", None),
            "status_code": raw.status_code,
            "requested_at": _iso(raw.requested_at),
            "received_at": _iso(raw.retrieved_at),
            "attempts": raw.attempts,
            "block_number": block_number,
            "payload_sha256": sha,
            "body_file": body_file,
            "body_bytes": len(body),
            "evidence_id": raw.evidence_id,
            "rate_limit": dict(getattr(raw, "rate_limit", {}) or {}),
        })
        return record_id

    def add_error(self, source_id: str, operation: str, exc: BaseException, **context: Any) -> None:
        record_id = None
        response = getattr(exc, "response", None)
        if response is not None and getattr(response, "raw_body", None) is not None:
            record_id = self.add_response(response, ok=False)
        self.errors.append({
            "source_id": source_id,
            "operation": operation,
            "error_type": type(exc).__name__,
            "message": str(exc),
            "status_code": getattr(exc, "status_code", None),
            "url": getattr(exc, "url", None),
            "attempts": getattr(exc, "attempts", None),
            "record_id": record_id,
            "at": _iso(datetime.now(timezone.utc)),
            "context": {k: str(v) for k, v in context.items()},
        })

    def finalize(self, complete: bool) -> Path:
        manifest = {
            "format": RAW_SNAPSHOT_FORMAT,
            "schema_version": SCHEMA_VERSION,
            "snapshot_id": self.snapshot_id,
            "data_mode": DataMode.REAL.value,
            "chain_id": CHAIN_ID,
            "started_at": _iso(self.started_at),
            "finished_at": _iso(datetime.now(timezone.utc)),
            "complete": complete and not self.errors,
            "config": asdict(self.config),
            "sources": self.sources,
            "listings": self.listings,
            "discovery": self.discovery,
            "records": self.records,
            "errors": self.errors,
        }
        (self.tmp_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        os.replace(self.tmp_dir, self.final_dir)
        return self.final_dir


# ---------------------------------------------------------------------------
# Discovery (address matching only — no normalisation, no scoring)
# ---------------------------------------------------------------------------

def discover_pt_collateral(
    pendle_items: list[dict[str, Any]], morpho_items: list[dict[str, Any]]
) -> tuple[list[dict[str, str]], list[str]]:
    """Morpho markets whose collateral token is a Pendle PT, matched on chain_id + address."""
    anomalies: list[str] = []
    pt_index: dict[str, dict[str, Any]] = {}
    for item in pendle_items:
        try:
            chain_id, pt = parse_pendle_id(item.get("pt"))
            market = normalize_address(item.get("address"))
        except ValueError as exc:
            anomalies.append(f"pendle item {item.get('address')!r}: {exc}")
            continue
        if chain_id != CHAIN_ID:
            anomalies.append(f"pendle PT {pt} on chain {chain_id}")
            continue
        pt_index[pt] = {"pendle_market": market, "item": item}

    candidates: list[dict[str, str]] = []
    for item in morpho_items:
        try:
            market_id = normalize_bytes32(item.get("market_id"))
            collateral = normalize_address(item.get("collateral_token"))
        except ValueError as exc:
            anomalies.append(f"morpho item {item.get('market_id')!r}: {exc}")
            continue
        hit = pt_index.get(collateral)
        if hit is None:
            continue
        pendle_item = hit["item"]
        underlying = None
        if pendle_item.get("underlyingAsset") is not None:
            try:
                underlying = parse_pendle_id(pendle_item["underlyingAsset"])[1]
            except ValueError as exc:
                anomalies.append(f"pendle underlyingAsset {pendle_item['underlyingAsset']!r}: {exc}")
        candidates.append({
            "morpho_market_id": market_id,
            "pendle_market": hit["pendle_market"],
            "pt": collateral,
            "underlying": underlying,
        })
    return candidates, anomalies


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CollectResult:
    path: Path
    snapshot_id: str
    complete: bool
    record_count: int
    error_count: int
    candidate_count: int


def collect_raw_snapshot(
    config: CollectConfig = CollectConfig(),
    root: Path | None = None,
    morpho: MorphoApiClient | None = None,
    pendle: PendleApiClient | None = None,
) -> CollectResult:
    own_morpho, own_pendle = morpho is None, pendle is None
    morpho = morpho or MorphoApiClient()
    pendle = pendle or PendleApiClient()
    writer = RawSnapshotWriter(
        root or default_raw_root(),
        config,
        sources={
            "morpho_api": {"base_url": morpho.base_url},
            "pendle_api": {"base_url": pendle.base_url},
        },
    )
    complete = True
    try:
        complete = _collect(writer, config, morpho, pendle)
    except BaseException as exc:  # still persist what was fetched, then re-raise
        writer.add_error("collector", "fatal", exc)
        writer.finalize(complete=False)
        raise
    finally:
        if own_morpho:
            morpho.close()
        if own_pendle:
            pendle.close()
    path = writer.finalize(complete)
    return CollectResult(
        path=path,
        snapshot_id=writer.snapshot_id,
        complete=complete and not writer.errors,
        record_count=len(writer.records),
        error_count=len(writer.errors),
        candidate_count=len(writer.discovery.get("pt_collateral_candidates", [])),
    )


def _collect(writer: RawSnapshotWriter, cfg: CollectConfig, morpho: MorphoApiClient, pendle: PendleApiClient) -> bool:
    complete = True

    # 1. Pendle markets (chainId == 1 kept by the adapter; exclusions recorded)
    p_listing = pendle.list_markets(
        limit=cfg.pendle_page_limit, max_pages=cfg.pendle_max_pages,
        is_active=True if cfg.pendle_active_only else None,
    )
    for page in p_listing.pages:
        writer.add_response(page.response)
    for err in p_listing.errors:
        writer.add_error("pendle_api", PENDLE_MARKETS_OP, err)
    writer.listings["pendle_markets"] = {
        "complete": p_listing.complete,
        "stop_reason": p_listing.stop_reason,
        "items_chain_1": p_listing.item_count,
        "excluded": [e.reason for e in p_listing.excluded],
    }
    complete &= p_listing.complete
    pendle_items = [item for item, _ in p_listing.iter_items()]

    # 2. Morpho markets
    m_listing = morpho.list_markets(limit=cfg.morpho_page_limit, max_pages=cfg.morpho_max_pages)
    for page in m_listing.pages:
        writer.add_response(page.response)
    for err in m_listing.errors:
        writer.add_error("morpho_api", MORPHO_MARKETS_OP, err)
    writer.listings["morpho_markets"] = {
        "complete": m_listing.complete,
        "stop_reason": m_listing.stop_reason,
        "items_chain_1": m_listing.item_count,
    }
    complete &= m_listing.complete
    morpho_items = [item for item, _ in m_listing.iter_items()]

    # 3. Discovery: which Morpho markets use a Pendle PT as collateral
    candidates, anomalies = discover_pt_collateral(pendle_items, morpho_items)
    selected = candidates[: cfg.max_candidates]
    writer.discovery = {
        "pt_collateral_candidates": selected,
        "candidates_found": len(candidates),
        "candidates_truncated": max(0, len(candidates) - len(selected)),
        "anomalies": anomalies,
    }
    if len(selected) < len(candidates):
        complete = False

    # 4. Per-candidate fetches; each failure is recorded and does not stop the others
    if cfg.fetch_history:
        end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        start = end - timedelta(days=cfg.history_days)
        for market in dict.fromkeys(c["pendle_market"] for c in selected):
            try:
                raw = pendle.fetch_market_history(market, cfg.history_time_frame, start, end)
                writer.add_response(raw)
            except (PendleAPIError, ValueError) as exc:
                writer.add_error("pendle_api", PENDLE_HISTORY_OP, exc, pendle_market=market)

    if cfg.fetch_morpho_details:
        for market_id in dict.fromkeys(c["morpho_market_id"] for c in selected):
            try:
                writer.add_response(morpho.fetch_market_details(market_id))
            except (MorphoAPIError, ValueError) as exc:
                writer.add_error("morpho_api", MORPHO_DETAILS_OP, exc, morpho_market_id=market_id)

    if cfg.fetch_prices and selected:
        assets = [a for c in selected for a in (c["pt"], c["underlying"]) if a]
        result = pendle.fetch_asset_prices(list(dict.fromkeys(assets)))
        for raw in result.responses:
            writer.add_response(raw)
        for ids, exc in result.errors:
            writer.add_error("pendle_api", PENDLE_PRICES_OP, exc, ids=",".join(ids))
        writer.listings["pendle_prices"] = {"requested": len(result.requested_ids),
                                            "missing_ids": list(result.missing_ids)}

    return complete


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------

class SnapshotIntegrityError(RuntimeError):
    """A stored body or evidence id does not match the manifest."""


@dataclass(frozen=True)
class ReplayedResponse:
    """Same attributes the adapters' RawResponse exposes, rebuilt from disk."""

    record_id: int
    ok: bool
    source_id: str
    operation: str
    http_method: str
    url: str
    request_params: dict[str, str]
    request_body: dict[str, Any] | None
    status_code: int
    requested_at: datetime
    retrieved_at: datetime
    attempts: int
    block_number: int | None
    payload_sha256: str
    evidence_id: str
    rate_limit: dict[str, str]
    payload: Any
    raw_body: bytes = field(repr=False)


@dataclass(frozen=True)
class RawSnapshot:
    path: Path
    manifest: dict[str, Any]
    records: tuple[ReplayedResponse, ...]

    @property
    def errors(self) -> list[dict[str, Any]]:
        return self.manifest["errors"]

    @property
    def complete(self) -> bool:
        return bool(self.manifest["complete"])

    def responses(
        self, source_id: str | None = None, operation: str | None = None, ok: bool | None = True
    ) -> list[ReplayedResponse]:
        return [
            r for r in self.records
            if (source_id is None or r.source_id == source_id)
            and (operation is None or r.operation == operation)
            and (ok is None or r.ok == ok)
        ]

    def iter_pendle_market_items(self) -> Iterator[tuple[dict[str, Any], ReplayedResponse]]:
        """Pendle market items with chainId == 1 (same rule as the adapter)."""
        for r in self.responses("pendle_api", PENDLE_MARKETS_OP):
            for item in r.payload.get("results", []):
                if isinstance(item, dict) and item.get("chainId") == CHAIN_ID:
                    yield item, r

    def iter_morpho_market_items(self) -> Iterator[tuple[dict[str, Any], ReplayedResponse]]:
        for r in self.responses("morpho_api", MORPHO_MARKETS_OP):
            for item in r.payload.get("data", []):
                if isinstance(item, dict) and item.get("chain_id") == CHAIN_ID:
                    yield item, r


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_raw_snapshot(path: str | Path, verify: bool = True) -> RawSnapshot:
    path = Path(path)
    manifest = json.loads((path / MANIFEST_NAME).read_text(encoding="utf-8"))
    if manifest.get("format") != RAW_SNAPSHOT_FORMAT:
        raise SnapshotIntegrityError(f"unsupported format {manifest.get('format')!r}")
    if manifest.get("data_mode") != DataMode.REAL.value or manifest.get("chain_id") != CHAIN_ID:
        raise SnapshotIntegrityError("raw snapshots must be REAL data on chain_id 1")

    records: list[ReplayedResponse] = []
    for rec in manifest["records"]:
        body = (path / rec["body_file"]).read_bytes()
        received_at = _parse_iso(rec["received_at"])
        if verify:
            if hashlib.sha256(body).hexdigest() != rec["payload_sha256"]:
                raise SnapshotIntegrityError(f"sha256 mismatch for record {rec['record_id']}")
            expected_ev = make_evidence_id(
                rec["source_id"], rec["operation"], rec["request_params"], fetched_at=received_at
            )
            if expected_ev != rec["evidence_id"]:
                raise SnapshotIntegrityError(f"evidence_id mismatch for record {rec['record_id']}")
        records.append(ReplayedResponse(
            record_id=rec["record_id"],
            ok=rec["ok"],
            source_id=rec["source_id"],
            operation=rec["operation"],
            http_method=rec["http_method"],
            url=rec["url"],
            request_params=rec["request_params"],
            request_body=rec["request_body"],
            status_code=rec["status_code"],
            requested_at=_parse_iso(rec["requested_at"]),
            retrieved_at=received_at,
            attempts=rec["attempts"],
            block_number=rec["block_number"],
            payload_sha256=rec["payload_sha256"],
            evidence_id=rec["evidence_id"],
            rate_limit=rec["rate_limit"],
            payload=json.loads(body, parse_float=Decimal),
            raw_body=body,
        ))
    return RawSnapshot(path, manifest, tuple(records))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect or replay a raw Morpho/Pendle snapshot (chain_id=1)")
    parser.add_argument("--replay", metavar="PATH", help="verify and summarise an existing snapshot")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--all-pendle", action="store_true", help="include inactive Pendle markets")
    parser.add_argument("--history-days", type=int, default=30)
    parser.add_argument("--no-history", action="store_true")
    parser.add_argument("--no-prices", action="store_true")
    parser.add_argument("--no-morpho-details", action="store_true")
    parser.add_argument("--max-candidates", type=int, default=50)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    if args.replay:
        snap = load_raw_snapshot(args.replay)
        m = snap.manifest
        print(f"snapshot={m['snapshot_id']} complete={m['complete']} records={len(snap.records)} "
              f"errors={len(snap.errors)} (sha256 + evidence ids verified)")
        print(f"pendle items={sum(1 for _ in snap.iter_pendle_market_items())} "
              f"morpho items={sum(1 for _ in snap.iter_morpho_market_items())} "
              f"candidates={len(m['discovery'].get('pt_collateral_candidates', []))}")
        return 0

    cfg = CollectConfig(
        pendle_active_only=not args.all_pendle,
        fetch_history=not args.no_history,
        history_days=args.history_days,
        fetch_prices=not args.no_prices,
        fetch_morpho_details=not args.no_morpho_details,
        max_candidates=args.max_candidates,
    )
    result = collect_raw_snapshot(cfg, root=args.root)
    manifest = json.loads((result.path / MANIFEST_NAME).read_text(encoding="utf-8"))
    print(f"snapshot={result.snapshot_id} path={result.path}")
    print(f"complete={result.complete} records={result.record_count} errors={result.error_count} "
          f"candidates={result.candidate_count}")
    for name, info in manifest["listings"].items():
        print(f"  {name}: {info}")
    for c in manifest["discovery"]["pt_collateral_candidates"][:5]:
        print(f"  morpho {c['morpho_market_id'][:18]}… collateral PT {c['pt']} (pendle {c['pendle_market']})")
    for e in manifest["errors"][:5]:
        print(f"  ERROR {e['source_id']} {e['operation']}: {e['error_type']} {e['message'][:160]}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
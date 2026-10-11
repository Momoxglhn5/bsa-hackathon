"""Cached delivery endpoints: request handlers never call external providers."""
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from app.api.routes_data import Envelope, _envelope
from app.models.evidence import ContractModel, PositiveDecimal
from app.models.market import ExitPriceDTO

router = APIRouter(prefix="/api/v1")


class ExitQuoteRequest(ContractModel):
    strategy_id: str = Field(pattern=r"^strat_[0-9a-f]{16,64}$")
    input_amount: PositiveDecimal


def load_delivery(request, filename):
    root = Path(request.app.state.db_path).parent / "delivery"
    try:
        name = json.loads((root / "current.json").read_text())["bundle"]
        if not isinstance(name, str) or Path(name).name != name or name in (".", ".."):
            raise ValueError("Invalid bundle pointer")
        return json.loads((root / name / filename).read_text())
    except (OSError, ValueError, KeyError, TypeError):
        raise HTTPException(503, "No complete Data delivery cached; run backend/scripts/finish_data.py") from None


@router.get("/data-delivery", response_model=Envelope)
def delivery_status(request: Request):
    data = load_delivery(request, "report.json")
    return _envelope(status=data["status"], as_of=data["finished_at"], data_mode="REAL", data=data,
        source_ids=("morpho_api", "pendle_api", "ethereum_rpc"),
        warnings=["Real market data; fund positions are simulated; quotes do not prove repayment/closing feasibility"])


@router.post("/exit-quotes", response_model=Envelope)
def exit_quote(body: ExitQuoteRequest, request: Request):
    try:
        records = load_delivery(request, "exit_quotes.json")["records"]
        record = next((r for r in records if r["strategy_id"] == body.strategy_id
            and r.get("input_amount") is not None and Decimal(r["input_amount"]) == body.input_amount), None)
        if record is not None and record.get("quote") is not None:
            parsed = ExitPriceDTO.model_validate(record["quote"])
            if parsed.input_amount != body.input_amount or parsed.valid_until is None or "missing_fields" not in record:
                raise ValueError("Invalid cached quote")
    except (ValueError, KeyError, TypeError, InvalidOperation):
        raise HTTPException(503, "Invalid cached quote delivery; refresh the Data bundle") from None
    if record is None or record.get("quote") is None:
        return _envelope(status="MISSING", as_of=record.get("observed_at") if record else None, data_mode="REAL",
            data={"quote": None, "status": "UNAVAILABLE", "reason": record.get("reason") if record else "Exact amount not cached"},
            evidence_ids=record.get("evidence_ids", ()) if record else (),
            source_ids=("pendle_api",) if record else (), missing=("execution_quote",), warnings=["A spot price or differently sized quote cannot substitute for this request"])
    quote = record["quote"]
    now = request.app.state.clock()
    expired = now >= parsed.valid_until or not 0 <= (now-parsed.observed_at).total_seconds() <= 300
    return _envelope(status="STALE" if expired else "PARTIAL", as_of=quote["observed_at"], data_mode="REAL",
        data=record, source_ids=(quote["source_id"],), evidence_ids=quote["evidence_ids"],
        missing=record["missing_fields"], warnings=["Cached indicative quote; no verified closing plan"] + (["Quote expired; refresh required"] if expired else []))


@router.get("/incident-catalog", response_model=Envelope)
def incident_catalog(request: Request):
    catalog = load_delivery(request, "incident_catalog.json")
    return _envelope(status="PARTIAL" if catalog.get("retrieved_at") else "MISSING",
        as_of=catalog.get("retrieved_at"), data_mode="REAL", data=catalog,
        source_ids=("defillama_hacks",), missing=("calibrated_probability",),
        warnings=["Reported incidents do not establish a probability for the selected market"])

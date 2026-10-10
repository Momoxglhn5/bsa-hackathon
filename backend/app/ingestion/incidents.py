"""Sourced incident catalogue; event counts are NOT incident probabilities."""
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx

URL = "https://api.llama.fi/hacks"


def normalize_catalog(body, retrieved_at):
    items = json.loads(body, parse_float=Decimal)
    if not isinstance(items, list):
        raise ValueError("DefiLlama hacks response must be a list")
    events = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("date"), int):
            continue
        date = datetime.fromtimestamp(item["date"], timezone.utc)
        if date > retrieved_at:
            continue
        name = item.get("name", "")
        related = "morpho" in name.lower() or "pendle" in name.lower() or name.lower() == "penpie"
        events.append({"name": name, "date": date.isoformat(),
            "classification": item.get("classification"), "technique": item.get("technique"),
            "reported_loss_usd": item.get("amount"), "chains": item.get("chain"),
            "source_url": item.get("source") or None, "catalog_url": "https://defillama.com/hacks",
            "related_ecosystem": related, "matches_selected_market": None})
    return {"schema_version": "incident-catalog-v1", "data_mode": "REAL",
        "status": "PARTIAL", "retrieved_at": retrieved_at.isoformat(),
        "source_url": URL, "payload_sha256": hashlib.sha256(body).hexdigest(),
        "events": events, "related_events": [x for x in events if x["related_ecosystem"]],
        "probability": None, "methodology": "Source-reported incidents. No exposure denominator, calibrated frequency or confirmed selected-market attribution. Absence is not zero risk."}


def fetch_catalog():
    response = httpx.get(URL, timeout=30)
    response.raise_for_status()
    return normalize_catalog(response.content, datetime.now(timezone.utc)), response.content

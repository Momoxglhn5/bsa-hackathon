"""SYNTHETIC OHLCV protocol fixtures; never download market data in tests."""
from datetime import datetime, timedelta, timezone

import pytest

from ml.collect_pt_history import parse_daily_closes

END = datetime(2026, 10, 10, tzinfo=timezone.utc)
EVIDENCE = "ev_" + "a" * 24


def payload():
    ts = int((END-timedelta(days=1)).timestamp())
    return dict(currency="USD", timeFrame="day", total=2,
                results=f"time,open,high,low,close,volume\n{ts},1,1,1,0.970000000000000001,\n{ts+86400},1,1,1,0.98,10\n")


def test_close_available_at_day_end_and_partial_candle_excluded():
    points, excluded = parse_daily_closes(payload(), available_by=END, evidence_id=EVIDENCE)
    assert excluded == 1
    assert len(points) == 1
    assert points[0]["timestamp"] == END.isoformat()
    assert points[0]["value"] == "0.970000000000000001"
    assert points[0]["evidence_ids"] == [EVIDENCE]


@pytest.mark.parametrize("mutation", ["unit", "frequency", "count", "duplicate", "negative"])
def test_reject_ambiguous_or_invalid_response(mutation):
    data = payload()
    if mutation == "unit":
        data["currency"] = "ETH"
    elif mutation == "frequency":
        data["timeFrame"] = "hour"
    elif mutation == "count":
        data["total"] = 3
    elif mutation == "duplicate":
        lines = data["results"].splitlines()
        data["results"] = '\n'.join([lines[0], lines[1], lines[1]])
    elif mutation == "negative":
        data["results"] = data["results"].replace("0.970000000000000001", "-1")
    with pytest.raises(ValueError):
        parse_daily_closes(data, available_by=END, evidence_id=EVIDENCE)

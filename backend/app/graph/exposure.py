from decimal import Decimal

from app.analytics.valuation import value_position


def exposure(snapshot, position_ids, field="equity_usd", *, now=None):
    """Unique root positions; any missing contribution keeps aggregate unknown."""
    values = []
    for pid in sorted(set(position_ids)):
        p = next(p for p in snapshot.positions if p.position_id == pid)
        value = getattr(value_position(snapshot, p, now=now), field)
        if value is None:
            return None
        # Insolvency remains visible in Valuation. Capital at risk cannot be negative.
        values.append(max(Decimal(0), value) if field == "equity_usd" else value)
    return sum(values, Decimal(0))

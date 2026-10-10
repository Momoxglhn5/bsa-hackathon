"""USD spot prices from Pendle's documented /v1/prices/assets endpoint."""
from app.ingestion.normalize import SourceRef, price_from_pendle_prices
from app.models.market import MarketDTO, QuoteUnit


def enrich_market_prices(markets, client):
    """Keep market/RPC timestamps unchanged; each price carries its own timestamp."""
    assets = {}
    for market in markets:
        tokens = (market.morpho.collateral_token, market.morpho.loan_token) if market.morpho else tuple(
            t for t in (market.pendle.pt, market.pendle.underlying_asset) if t is not None)
        for token in tokens:
            if token.address not in assets or token.decimals is not None:
                assets[token.address] = token
    if not assets:
        return list(markets), [], []
    result = client.fetch_asset_prices(assets)
    prices = {}
    for raw in result.responses:
        for address, asset in assets.items():
            key = f"1-{address}"
            if key in raw.payload["prices"]:
                prices[address] = price_from_pendle_prices(
                    raw.payload["prices"], asset, quote_unit=QuoteUnit.USD,
                    ref=SourceRef.from_response(raw))
    enriched = []
    for market in markets:
        data = market.model_dump()
        if market.morpho:
            data["morpho"].update(collateral_price=prices.get(market.morpho.collateral_token.address),
                                  loan_price=prices.get(market.morpho.loan_token.address))
        else:
            data["pendle"]["pt_price"] = prices.get(market.pendle.pt.address)
        enriched.append(MarketDTO.model_validate(data))
    errors = [f"USD prices: {exc}" for _, exc in result.errors]
    errors += [f"USD price missing: {a}" for a in assets if prices.get(a) is None]
    return enriched, list(result.responses), errors

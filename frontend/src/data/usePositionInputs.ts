import { useState } from 'react'
import { num } from '../lib/format'
import type { PositionInputs } from '../lib/scenarios'
import { useRadar } from './useRadar'

export type PriceSource = 'oracle' | 'market' | 'manual'

export interface PositionModel {
  inputs: PositionInputs | null
  /** Why `inputs` is null, shown instead of numbers. */
  problem: string | null
  price: number | null
  priceSource: PriceSource | null
  setManualPrice: (value: number | null) => void
  loanSymbol: string
  collateralSymbol: string
}

/**
 * Position inputs for the estimates, from GET /positions/{id} and the Morpho market.
 * The collateral price is the oracle price when served, else the last stored market
 * price, else whatever the user types; the screens show which one is in use.
 */
export function usePositionInputs(): PositionModel {
  const { position, markets, collateralPrice } = useRadar()
  const [manual, setManual] = useState<number | null>(null)
  const morpho = markets.find((m) => m.data.morpho)?.data.morpho ?? null
  const p = position?.data ?? null
  const oracle = num(morpho?.oracle_price)
  const lastMarket = num(collateralPrice?.data.points.filter((pt) => pt.value !== null).at(-1)?.value)

  let price: number | null = manual
  let priceSource: PriceSource | null = manual !== null ? 'manual' : null
  if (price === null && oracle !== null) [price, priceSource] = [oracle, 'oracle']
  if (price === null && lastMarket !== null) [price, priceSource] = [lastMarket, 'market']

  const collateralAmount = num(p?.collateral_amount)
  const debt = num(p?.borrow_amount)
  const lltv = num(morpho?.lltv)
  let problem: string | null = null
  if (!p) problem = 'GET /positions/{id} found no position: set VITE_POSITION_ID.'
  else if (collateralAmount === null || debt === null) problem = 'The position has no collateral or borrow amount.'
  else if (lltv === null) problem = 'The Morpho market has no LLTV.'
  else if (price === null) problem = 'No collateral price: the API serves neither an oracle nor a market price. Enter one.'

  return {
    inputs: problem ? null : { collateralAmount: collateralAmount!, debt: debt!, collateralPrice: price!, lltv: lltv! },
    problem,
    price,
    priceSource,
    setManualPrice: setManual,
    loanSymbol: p?.loan_asset.symbol ?? morpho?.loan_token.symbol ?? 'loan',
    collateralSymbol: p?.collateral_asset.symbol ?? morpho?.collateral_token.symbol ?? 'collateral',
  }
}

export const PRICE_SOURCE_LABEL: Record<PriceSource, string> = {
  oracle: 'Morpho oracle price (API)',
  market: 'Last MARKET_PRICE point (API): oracle price not served',
  manual: 'Entered manually',
}

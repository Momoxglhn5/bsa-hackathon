// Client-side estimates for screens 02 and 03. The API serves no simulation, exit quote,
// incident frequency or decision route yet: every input here is either an API value or an
// assumption the user can see and edit, and every output is labelled as an estimate.

export interface PositionInputs {
  collateralAmount: number
  debt: number
  /** Collateral price in loan-asset units. */
  collateralPrice: number
  lltv: number
}

export type ScenarioId = 'oracle' | 'backing' | 'crosschain'

export interface StressInputs {
  scenario: ScenarioId
  /** Fractional price shock, 0.12 = −12 %. */
  shock: number
  /** Extra AMM exit haircut on top of the shock (backing scenario). */
  exitHaircut: number
  /** Whether the collateral oracle moves with the backing shock (oracle design is not served). */
  oracleTracksBacking: boolean
}

export interface StressResult {
  applicable: boolean
  oracleValueBefore: number
  oracleValueAfter: number
  ltvBefore: number
  ltvAfter: number
  hfBefore: number
  hfAfter: number
  exitValueBefore: number
  exitValueAfter: number
  liquidatable: boolean
  estimatedLoss: number
  /** Oracle drop that takes the health factor to 1. */
  liquidationDistance: number
}

/** Morpho Blue liquidation incentive factor: min(1.15, 1 / (0.3 * LLTV + 0.7)). */
export function liquidationIncentive(lltv: number): number {
  return Math.min(1.15, 1 / (0.3 * lltv + 0.7))
}

export function healthFactor(p: PositionInputs, oracleValue: number): number {
  return p.debt === 0 ? Infinity : (oracleValue * p.lltv) / p.debt
}

export function simulateStress(p: PositionInputs, s: StressInputs): StressResult {
  const value = p.collateralAmount * p.collateralPrice
  const ltvBefore = value === 0 ? Infinity : p.debt / value
  const base = {
    oracleValueBefore: value,
    ltvBefore,
    hfBefore: healthFactor(p, value),
    exitValueBefore: value,
    liquidationDistance: Math.max(0, 1 - ltvBefore / p.lltv),
  }
  if (s.scenario === 'crosschain') {
    return {
      ...base,
      applicable: false,
      oracleValueAfter: value,
      ltvAfter: ltvBefore,
      hfAfter: base.hfBefore,
      exitValueAfter: value,
      liquidatable: false,
      estimatedLoss: 0,
    }
  }
  const oracleMoves = s.scenario === 'oracle' || s.oracleTracksBacking
  const oracleValueAfter = oracleMoves ? value * (1 - s.shock) : value
  // An oracle-only fault leaves the tradable value intact; a backing failure does not.
  const exitValueAfter = s.scenario === 'oracle' ? value : value * (1 - s.shock) * (1 - s.exitHaircut)
  const hfAfter = healthFactor(p, oracleValueAfter)
  const liquidatable = hfAfter < 1
  let estimatedLoss: number
  if (liquidatable) {
    // Full liquidation: liquidators repay the debt and seize debt × LIF of collateral,
    // priced at the oracle. The loss is the seized collateral's tradable value minus the debt.
    const seizedShare = Math.min(1, (p.debt * liquidationIncentive(p.lltv)) / oracleValueAfter)
    estimatedLoss = seizedShare * exitValueAfter - p.debt + (value - exitValueAfter)
  } else {
    estimatedLoss = value - exitValueAfter
  }
  return {
    ...base,
    applicable: true,
    oracleValueAfter,
    ltvAfter: oracleValueAfter === 0 ? Infinity : p.debt / oracleValueAfter,
    hfAfter,
    exitValueAfter,
    liquidatable,
    estimatedLoss: Math.max(0, estimatedLoss),
  }
}

export interface DecisionInputs {
  /** Collateral value at risk, in loan-asset units. */
  exposure: number
  horizonDays: number
  /** Incident probability per day (assumption). */
  oracleRatePerDay: number
  backingRatePerDay: number
  /** Loss given incident, as a share of exposure (assumption). */
  oracleLgd: number
  backingLgd: number
  /** Exit cost as a share of exposure, or null when no exit price is known. */
  exitCostShare: number | null
  gasCost: number
}

export type Verdict = 'STAY' | 'EXIT' | 'INSUFFICIENT_DATA'

export interface DecisionResult {
  verdict: Verdict
  incidentProbability: number
  expectedLoss: number
  exitCost: number | null
  /** Days after which staying costs more than exiting now. */
  breakEvenDays: number | null
}

export function decide(d: DecisionInputs): DecisionResult {
  const pOracle = 1 - Math.exp(-d.oracleRatePerDay * d.horizonDays)
  const pBacking = 1 - Math.exp(-d.backingRatePerDay * d.horizonDays)
  const expectedLoss = d.exposure * (pOracle * d.oracleLgd + pBacking * d.backingLgd)
  const incidentProbability = 1 - (1 - pOracle) * (1 - pBacking)
  if (d.exitCostShare === null || !(d.exposure > 0)) {
    return { verdict: 'INSUFFICIENT_DATA', incidentProbability, expectedLoss, exitCost: null, breakEvenDays: null }
  }
  const exitCost = d.exposure * d.exitCostShare + d.gasCost
  const lossPerDay = d.exposure * (d.oracleRatePerDay * d.oracleLgd + d.backingRatePerDay * d.backingLgd)
  return {
    verdict: expectedLoss > exitCost ? 'EXIT' : 'STAY',
    incidentProbability,
    expectedLoss,
    exitCost,
    breakEvenDays: lossPerDay > 0 ? exitCost / lossPerDay : null,
  }
}

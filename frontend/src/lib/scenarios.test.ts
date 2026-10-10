import { describe, expect, it } from 'vitest'
import { decide, liquidationIncentive, simulateStress } from './scenarios'
import type { DecisionInputs, PositionInputs, StressInputs } from './scenarios'

const position: PositionInputs = { collateralAmount: 10_000, debt: 7_500, collateralPrice: 1, lltv: 0.915 }
const stress = (over: Partial<StressInputs>): StressInputs => ({
  scenario: 'oracle',
  shock: 0,
  exitHaircut: 0,
  oracleTracksBacking: false,
  ...over,
})

describe('liquidationIncentive', () => {
  it('matches the Morpho Blue formula and its 1.15 cap', () => {
    expect(liquidationIncentive(0.915)).toBeCloseTo(1 / (0.3 * 0.915 + 0.7), 10)
    expect(liquidationIncentive(0)).toBe(1.15)
  })
})

describe('simulateStress', () => {
  it('reports the oracle drop that reaches HF = 1', () => {
    const r = simulateStress(position, stress({}))
    expect(r.hfBefore).toBeCloseTo(1.22, 2)
    const atEdge = simulateStress(position, stress({ shock: r.liquidationDistance }))
    expect(atEdge.hfAfter).toBeCloseTo(1, 10)
  })

  it('stays healthy with no loss below the liquidation distance', () => {
    const r = simulateStress(position, stress({ shock: 0.1 }))
    expect(r.liquidatable).toBe(false)
    expect(r.estimatedLoss).toBe(0)
    expect(r.exitValueAfter).toBe(10_000)
  })

  it('charges the liquidation incentive once the oracle shock liquidates', () => {
    const r = simulateStress(position, stress({ shock: 0.25 }))
    expect(r.liquidatable).toBe(true)
    const seized = (7_500 * liquidationIncentive(0.915)) / 7_500
    expect(r.estimatedLoss).toBeCloseTo(Math.min(1, seized) * 10_000 - 7_500, 6)
  })

  it('moves the exit value but not the oracle on a backing failure by default', () => {
    const r = simulateStress(position, stress({ scenario: 'backing', shock: 0.1, exitHaircut: 0.02 }))
    expect(r.hfAfter).toBe(r.hfBefore)
    expect(r.exitValueAfter).toBeCloseTo(10_000 * 0.9 * 0.98, 6)
    expect(r.estimatedLoss).toBeCloseTo(10_000 - 8_820, 6)
  })

  it('can liquidate on a backing failure when the oracle tracks it', () => {
    const r = simulateStress(position, stress({ scenario: 'backing', shock: 0.3, oracleTracksBacking: true }))
    expect(r.liquidatable).toBe(true)
  })

  it('computes nothing for a scenario that does not apply', () => {
    const r = simulateStress(position, stress({ scenario: 'crosschain', shock: 0.4 }))
    expect(r.applicable).toBe(false)
    expect(r.estimatedLoss).toBe(0)
  })
})

describe('decide', () => {
  const inputs: DecisionInputs = {
    exposure: 1_000_000,
    horizonDays: 3,
    oracleRatePerDay: 0.004,
    backingRatePerDay: 0.003,
    oracleLgd: 0.18,
    backingLgd: 0.12,
    exitCostShare: 0.005,
    gasCost: 40,
  }

  it('withholds a verdict without an exit cost', () => {
    expect(decide({ ...inputs, exitCostShare: null }).verdict).toBe('INSUFFICIENT_DATA')
  })

  it('exits when the expected loss exceeds the exit cost', () => {
    const r = decide({ ...inputs, horizonDays: 7 })
    expect(r.exitCost).toBe(5_040)
    expect(r.expectedLoss).toBeGreaterThan(r.exitCost!)
    expect(r.verdict).toBe('EXIT')
  })

  it('stays over a short horizon and agrees with the break-even', () => {
    const r = decide(inputs)
    expect(r.verdict).toBe('STAY')
    // Loss rate 1 080/day against a 5 040 exit cost.
    expect(r.breakEvenDays).toBeCloseTo(5_040 / 1_080, 10)
  })
})

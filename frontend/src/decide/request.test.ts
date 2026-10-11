import { describe, expect, it } from 'vitest'
import { DEFAULT_DECISION_FORM, type SimRecord } from '../state/radar'
import { buildDecisionRequest, sensitivityBars } from './request'

const sim = (id: string): SimRecord => ({
  result: { simulation_id: id } as SimRecord['result'],
  request: {} as SimRecord['request'], snapshotId: 's', sourceId: 'demo', envelopeStatus: 'CALCULATED',
  envelopeWarnings: [], targetName: 'PT', ranAt: 0,
})

describe('buildDecisionRequest', () => {
  it('reproduces the documented hero path request', () => {
    const form = { ...DEFAULT_DECISION_FORM, picks: { 'stress:a': true }, prob: { 'stress:a': '2' }, carryMode: 'manual' as const, carryUsd: '1000', carryWhy: 'Synthetic seven-day net carry' }
    const { body, errors } = buildDecisionRequest('strat_x', 'pos_demo_fund', form, [sim('stress:a')], [{ quote_id: 'quote:synthetic-full-position' } as never])
    expect(errors).toEqual({})
    expect(body).toMatchObject({
      horizon_days: 7, mode: 'normal', exit_quote_id: 'quote:synthetic-full-position', estimated_net_carry_usd: '1000',
      scenarios_mutually_exclusive: true,
      scenario_assumptions: [{ loss_scenario_ref: 'stress:a', probability_over_horizon: '0.02', include_in_financial_comparison: true }],
    })
    expect(body?.carry_assumptions).toBeUndefined()
  })

  it('requires a scenario, an explanation and exclusive probabilities summing to at most 100 %', () => {
    expect(buildDecisionRequest('s', 'p', DEFAULT_DECISION_FORM, [], []).errors.scenarios).toBeDefined()
    const manual = { ...DEFAULT_DECISION_FORM, picks: { a: true }, carryMode: 'manual' as const, carryUsd: '10' }
    expect(buildDecisionRequest('s', 'p', manual, [sim('a')], []).errors.carryWhy).toBeDefined()
    const over = { ...DEFAULT_DECISION_FORM, picks: { a: true, b: true }, prob: { a: '60', b: '50' } }
    expect(buildDecisionRequest('s', 'p', over, [sim('a'), sim('b')], []).errors.scenarios).toMatch(/100/)
  })

  it('keeps an unknown probability unknown and an exclusion explicit', () => {
    const form = { ...DEFAULT_DECISION_FORM, picks: { a: true }, prob: { a: '' }, excludeUnquantified: { a: true } }
    const { body } = buildDecisionRequest('s', 'p', form, [sim('a')], [])
    expect(body?.scenario_assumptions[0]).toMatchObject({ probability_over_horizon: null, include_in_financial_comparison: false })
    expect(body?.exit_quote_id).toBeNull()
  })
})

describe('sensitivityBars', () => {
  it('is drawn only when it reproduces the backend break-even', () => {
    // Hero path at 2 %: STAY 793 000, EXIT 779 900, loss 400 000 → break-even 5.275 %.
    const bars = sensitivityBars(793000 - 779900, 0.02, 400000, 0.05275)
    expect(bars).toHaveLength(13)
    expect(bars![0].diff).toBeCloseTo(21100)
    // 1 % steps up to 12 %: the reversal sits inside the drawn domain.
    expect(bars![12].p).toBeCloseTo(0.12)
    expect(bars!.some((b) => b.diff > 0) && bars!.some((b) => b.diff < 0)).toBe(true)
    expect(sensitivityBars(13100, 0.02, 400000, 0.09)).toBeNull()
    expect(sensitivityBars(13100, 0.02, 0, 0.05)).toBeNull()
  })
})

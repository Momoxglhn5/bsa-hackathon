// Display labels for backend enums. Unknown codes are shown verbatim.
import type { LinkStatus, NodeType, ScenarioType } from '../api/types'

export const C = {
  gold: '#D4B47A',
  goldHi: '#E6CFA0',
  lend: '#8FB7EA',
  text: '#F2F5F9',
  textSoft: '#E7EDF5',
  textDim: '#C9D4E2',
  sec: '#A3B4CA',
  unk: '#8A9BB3',
  cau: '#F2B45B',
  dan: '#F17C82',
  pos: '#42C9A0',
} as const

export const NODE_TYPE_LABEL: Record<NodeType, string> = {
  POSITION: 'Position',
  MARKET: 'Market',
  ASSET: 'Asset',
  SY: 'Standardized Yield',
  ORACLE: 'Oracle',
  IRM: 'Rate model',
  BRIDGE: 'Bridge',
  CHAIN: 'Blockchain',
  PROTOCOL: 'Protocol',
  CONTRACT: 'Contract',
  GOVERNANCE: 'Governance',
  PRICE_FEED: 'Price feed',
}

export const LINK_STATUS: Record<LinkStatus, { label: string; short: string; color: string }> = {
  CONFIRMED: { label: 'Confirmed on-chain', short: 'Confirmed', color: C.pos },
  OBSERVED: { label: 'Observed via API', short: 'Observed', color: '#9DB4D6' },
  HYPOTHESIS: { label: 'Hypothesis', short: 'Hypothesis', color: C.sec },
  UNRESOLVED: { label: 'Unresolved', short: 'Unresolved', color: C.cau },
}

export const RELATION_LABEL: Record<string, string> = {
  BORROWS_FROM: 'borrows from',
  LENDS_TO: 'lends to',
  USES_LOAN_ASSET: 'loan asset',
  COLLATERALIZED_BY: 'collateral',
  USES_ORACLE: 'oracle',
  USES_IRM: 'rate model',
  USES_PT: 'PT',
  USES_SY: 'SY',
  BACKED_BY: 'backed by',
  REDEEMS_THROUGH: 'redeems through',
  QUOTED_BY: 'quoted by',
  RUNS_ON: 'runs on',
  BRIDGED_BY: 'bridged by',
  GOVERNED_BY: 'governed by',
  USES_YT: 'YT',
  USES_YIELD_TOKEN: 'yield token',
  ACCOUNTED_IN: 'unit of account',
  ACCEPTS_INPUT: 'accepts input',
  DECLARES_REDEMPTION_TOKEN: 'declared redemption token',
  DECLARES_ASSET: 'declared asset',
}

export const FAILURE_MODE_LABEL: Record<string, string> = {
  VALUATION: 'valuation',
  LIVENESS: 'liveness',
  ECONOMIC: 'economic value',
  CONTROL: 'control',
  EXECUTION: 'execution',
}

export const CRITICALITY_LABEL: Record<string, string> = {
  CRITICAL: 'Critical',
  MATERIAL: 'Material',
  SUPPORTING: 'Supporting',
}

export const SCENARIOS: Record<ScenarioType, { label: string; desc: string; targetTypes: NodeType[] }> = {
  ORACLE_MANIPULATION: { label: 'Oracle manipulation', desc: 'Oracle price deviates', targetTypes: ['ORACLE'] },
  ORACLE_OUTAGE: { label: 'Oracle outage', desc: 'Oracle stops responding', targetTypes: ['ORACLE'] },
  UNAUTHORIZED_MINTING: { label: 'Unauthorized mint', desc: 'Uncontrolled issuance', targetTypes: ['ASSET', 'SY', 'PROTOCOL', 'CONTRACT'] },
  CROSS_CHAIN_FAILURE: { label: 'Cross-chain failure', desc: 'Bridge or redemption down', targetTypes: ['BRIDGE'] },
}

export const DIMENSION_LABEL: Record<string, string> = {
  Liquidity: 'Liquidity',
  Security: 'Security',
  Governance: 'Governance',
  'Operational history': 'Operational history',
  Dependencies: 'Dependencies',
}

export const FACTOR_LABEL: Record<string, string> = {
  L_UTILIZATION: 'Morpho market utilization',
  L_TVL_DRAWDOWN: 'Liquidity drawdown (TVL)',
  S_SOURCE_VERIFIED: 'Verified source code',
  S_AUDIT: 'Security audit',
  G_CONTROL: 'Admin control',
  G_TIMELOCK: 'Governance timelock',
  O_DEPLOYMENT_AGE: 'Deployment age',
  O_VERSION_AGE: 'Version age',
  D_CONCENTRATION: 'Critical dependency concentration',
  D_NO_FALLBACK: 'No verified fallback',
}

export const DECISION_STATUS: Record<string, { kicker: string; verdict: string; color: string }> = {
  INSUFFICIENT_DATA: { kicker: 'INSUFFICIENT_DATA', verdict: 'Insufficient data — no verdict', color: C.cau },
  QUOTE_UNAVAILABLE: { kicker: 'QUOTE_UNAVAILABLE', verdict: 'Exact quote unavailable — no verdict', color: C.cau },
  EXIT_NOT_FEASIBLE: { kicker: 'EXIT_NOT_FEASIBLE', verdict: 'Exit not feasible with these inputs — no verdict', color: C.dan },
  CONDITIONAL: { kicker: 'CONDITIONAL', verdict: '', color: C.gold },
}

export const MISSING_FIELD_LABEL: Record<string, string> = {
  closing_feasible: 'Closing feasibility',
  gas_cost_usd: 'Total closing gas',
  extra_fees_usd: 'Extra fees',
  repayment_liquidity_confirmed: 'Repayment liquidity',
  execution_quote: 'Exact-size execution quote',
  oracle_series: 'RPC oracle series (31 daily points)',
  risk_index: 'Publishable risk index',
  scenario_estimated_loss_usd: 'Estimated loss',
  operationally_blocked_usd: 'Blocked capital',
  net_carry_usd: 'Net carry',
  exit_cost_usd: 'Exit cost',
  pt_expiry: 'PT maturity',
  calibrated_probability: 'Calibrated probability',
}

export const missingLabel = (f: string) => MISSING_FIELD_LABEL[f] ?? f

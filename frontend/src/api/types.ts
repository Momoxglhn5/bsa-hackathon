// Contracts of the FastAPI backend (/api/v1), written from its OpenAPI schema.
// Decimal values travel as strings and stay strings here: convert only for display.

export type Dec = string
export type DataMode = 'REAL' | 'DEMO'

// ---------------------------------------------------------------- envelopes

/** Data V1 routes (stores): health, markets, strategies, series, positions, evidence… */
export interface DataEnvelope<T> {
  schema_version: string
  status: string
  as_of: string | null
  snapshot: { data_mode: DataMode; block_number: number | null; block_hash: string | null }
  data: T
  provenance: { source_ids: string[]; evidence_ids: string[] }
  missing_fields: string[]
  warnings: string[]
}

/** Core routes: graph, risk, stress, carry, closing, decision, Monte Carlo, catalog. */
export interface CoreEnvelope<T> {
  schema_version: string
  status: string
  as_of: string
  data: T
  provenance: CoreProvenance
  missing_fields: string[]
  warnings: string[]
}

export interface CoreProvenance {
  snapshot_id: string
  data_modes: string[]
  position_kinds: string[]
  replay: boolean
  evaluated_at: string
  evidence: EvidenceDTO[]
}

// ---------------------------------------------------------------- Data V1 DTOs

export interface TokenRef {
  chain_id: number
  address: string
  symbol: string | null
  name: string | null
  decimals: number | null
}

export interface PriceDTO {
  chain_id: number
  value: Dec | null
  base_asset: TokenRef
  quote_unit: 'USD' | 'TOKEN'
  quote_asset: TokenRef | null
  pricing_method: string
  observed_at: string
  block_number: number | null
  source_id: string
  evidence_ids: string[]
  status: string
  kind?: string
}

export interface ExitPriceDTO extends PriceDTO {
  input_amount: Dec
  output_amount: Dec | null
  venue: string | null
  price_impact: Dec | null
  valid_until: string | null
}

export interface VerificationCheck {
  check: string
  status: string
  claimed_value?: string | null
  evidence_ids?: string[]
}

export interface MorphoFields {
  morpho_address: string
  loan_token: TokenRef
  collateral_token: TokenRef
  oracle_address: string
  irm_address: string
  lltv: Dec | null
  total_supply_assets: Dec | null
  total_borrow_assets: Dec | null
  utilization: Dec | null
  supply_apy: Dec | null
  borrow_apy: Dec | null
  fee: Dec | null
  last_update: string | null
  oracle_price: PriceDTO | null
  collateral_price: PriceDTO | null
  loan_price: PriceDTO | null
}

export interface PendleFields {
  market_address: string
  pt: TokenRef
  yt: TokenRef | null
  sy: TokenRef | null
  expiry: string | null
  underlying_asset: TokenRef | null
  is_expired: boolean | null
  implied_apy: Dec | null
  underlying_apy: Dec | null
  pt_price: PriceDTO | null
  liquidity_usd: Dec | null
  total_pt: Dec | null
  total_sy: Dec | null
}

export interface MarketDTO {
  chain_id: number
  data_mode: DataMode
  protocol: 'MORPHO_BLUE' | 'PENDLE_V2'
  market_id: string
  status: string
  as_of: string
  block_number: number | null
  morpho: MorphoFields | null
  pendle: PendleFields | null
  verification: VerificationCheck[]
  source_ids: string[]
  evidence_ids: string[]
}

export interface StrategyDTO {
  chain_id: number
  data_mode: DataMode
  strategy_id: string
  strategy_type: string
  status: 'CANDIDATE' | 'VERIFIED_ACTIVE' | 'EXPIRED' | 'UNSUPPORTED'
  status_reason: string | null
  data_status: string
  morpho_market_id: string
  pendle_market_address: string | null
  pt: TokenRef
  loan_asset: TokenRef
  underlying_asset: TokenRef | null
  expiry: string | null
  as_of: string
  block_number: number | null
  verification: VerificationCheck[]
  source_ids: string[]
  evidence_ids: string[]
}

export interface PositionDTO {
  chain_id: number
  data_mode: DataMode
  position_id: string
  kind: 'ONCHAIN' | 'SIMULATED'
  protocol: string
  market_id: string
  owner: string | null
  collateral_asset: TokenRef
  loan_asset: TokenRef
  collateral_amount: Dec | null
  borrow_amount: Dec | null
  strategy_id: string
  label: string | null
  as_of: string
  block_number: number | null
  status: string
  source_ids: string[]
  evidence_ids: string[]
}

export interface EvidenceDTO {
  chain_id: number
  data_mode: DataMode
  evidence_id: string
  source_kind: string
  source_id: string
  method: string
  target_address: string | null
  request_params: Record<string, unknown>
  block_number: number | null
  block_hash: string | null
  fetched_at: string
  payload_sha256: string | null
  status: string
  error_message: string | null
  source_uri: string | null
  decoded_excerpt: unknown
  parser_version: string | null
}

export interface TimeseriesPoint {
  timestamp: string
  value: Dec | null
  status: string
  block_number: number | null
  evidence_ids: string[]
}

export interface TimeseriesDTO {
  series_id: string
  metric: string
  subject_kind: string
  subject_id: string
  unit: string
  quote_asset: TokenRef | null
  source_id: string
  status: string
  interval_seconds?: number | null
  points: TimeseriesPoint[]
}

export interface HealthData {
  db: string
  counts: Record<string, number>
  latest_as_of: Record<string, string | null>
}

export interface DataStatusData {
  kind: 'strategy' | 'market'
  status: string
  freshness: { as_of: string; age_seconds: number; ttl_seconds: number; is_stale: boolean }
  missing_fields: string[]
  evidence_count: number
}

export interface IncidentEvent {
  name: string
  date: string
  classification: string | null
  technique: string | null
  reported_loss_usd: number | string | null
  chains: string[] | string | null
  source_url: string | null
  catalog_url: string
  related_ecosystem: boolean
  matches_selected_market: boolean | null
}

export interface IncidentCatalog {
  status: string
  retrieved_at?: string
  source_url?: string
  events: IncidentEvent[]
  related_events?: IncidentEvent[]
  probability: null
  methodology?: string
}

export interface ExitQuoteRecord {
  strategy_id: string
  status: string
  quote: ExitPriceDTO | null
  context?: QuoteContext | null
  position_id?: string
  input_amount?: Dec
  reason?: string
  missing_fields?: string[]
}

// ---------------------------------------------------------------- Core

export interface QuoteContext {
  quote_id: string
  position_id: string
  quote: ExitPriceDTO
  output_usd_price: PriceDTO | null
  gas_cost_usd: Dec | null
  extra_fees_usd: Dec | null
  route_available: boolean | null
  closing_feasible: boolean | null
  requires_external_repayment_liquidity: boolean
  repayment_liquidity_confirmed: boolean
  basis: 'VERIFIED_PLAN' | 'USER_ASSUMPTION'
  evidence_ids: string[]
}

export interface AnalyticsCatalog {
  description: string
  strategies: StrategyDTO[]
  markets: MarketDTO[]
  positions: PositionDTO[]
  quotes: QuoteContext[]
}

export type NodeType =
  | 'POSITION' | 'MARKET' | 'ASSET' | 'SY' | 'ORACLE' | 'IRM' | 'BRIDGE'
  | 'CHAIN' | 'PROTOCOL' | 'CONTRACT' | 'GOVERNANCE' | 'PRICE_FEED'

export type LinkStatus = 'CONFIRMED' | 'OBSERVED' | 'HYPOTHESIS' | 'UNRESOLVED'

export interface GraphNode {
  id: string
  type: NodeType
  label: string
  chain_id: number
  position_kind: string | null
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  relation_type: string
  verification_status: LinkStatus
  evidence_ids: string[]
  criticality: 'CRITICAL' | 'MATERIAL' | 'SUPPORTING'
  failure_modes: string[]
  required: boolean
  fallbacks: { node_id: string; state: string; evidence_ids: string[] }[]
  as_of: string
  block_number: number | null
  transmission_note: string | null
}

export interface GraphDTO {
  strategy_id: string
  graph_status: string
  snapshot_as_of: string
  nodes: GraphNode[]
  edges: GraphEdge[]
  unknown_dependency_classes: string[]
  warnings: string[]
}

export interface ExposedPosition {
  position_id: string
  kind: string
  path: string[]
  collateral_value_usd: Dec | null
  equity_usd: Dec | null
  link_statuses: LinkStatus[]
  evidence_ids: string[]
}

export interface DependencyExposure {
  dependency_id: string
  positions: ExposedPosition[]
  unique_position_count: number
  collateral_exposure_usd: Dec | null
  basis: 'STRUCTURAL_REACHABILITY_NOT_LOSS'
  warnings: string[]
}

export interface Valuation {
  collateral_value_usd: Dec | null
  debt_value_usd: Dec | null
  equity_usd: Dec | null
  gross_exposure_usd: Dec | null
  oracle_collateral_value_loan_units: Dec | null
  lltv_ratio: Dec | null
  ltv_ratio: Dec | null
  health_factor: Dec | null
  liquidatable: boolean | null
}

export interface FactorExplanation {
  factor_id: string
  dimension: string
  weight: Dec
  risk: Dec | null
  source_quality: Dec | null
  freshness: Dec | null
  evidence_ids: string[]
  explanation: string
}

export interface RiskResult {
  risk_index: Dec | null
  risk_status: string
  completeness_pct: Dec
  evidence_confidence_pct: Dec | null
  dimension_scores: Record<string, { score: Dec | null; coverage_pct: Dec }>
  factor_explanations: FactorExplanation[]
  unknown_factors: string[]
  position_risk: Record<string, Valuation>
  warnings: string[]
}

export interface ScenarioCatalog {
  scenario_types: ScenarioType[]
  probability_basis: string
}

export type ScenarioType = 'ORACLE_MANIPULATION' | 'ORACLE_OUTAGE' | 'UNAUTHORIZED_MINTING' | 'CROSS_CHAIN_FAILURE'
export type ScenarioScope = 'VERIFIED_TARGET' | 'OBSERVED_TARGET' | 'HYPOTHETICAL'

export interface ScenarioParameters {
  oracle_price_change_ratio?: Dec
  mint_amount?: Dec
  backing_impairment_ratio?: Dec
  assumed_token_haircut_ratio?: Dec
  haircut_applies_to_affected_collateral?: boolean
  outage_duration_hours?: Dec
  unavailable_service?: 'redemption' | 'bridge_transfer'
}

export interface SimulationRequest {
  strategy_id: string
  position_id: string
  scenario_type: ScenarioType
  target_node_id: string
  parameters: ScenarioParameters
  horizon_days: number
  scenario_scope: ScenarioScope
}

export interface PositionEffect {
  before: Valuation
  after: Valuation
  scenario_estimated_loss_usd: Dec | null
}

export interface SimulationResult {
  simulation_id: string
  strategy_id: string
  position_id: string
  horizon_days: number
  scenario_type: ScenarioType
  scenario_scope: string
  scenario_status: 'CALCULATED' | 'PARTIAL' | 'NOT_APPLICABLE'
  affected_node_ids: string[]
  affected_position_ids: string[]
  confirmed_exposed_capital_usd: Dec | null
  potentially_exposed_capital_usd: Dec | null
  gross_exposure_usd: Dec | null
  operationally_blocked_usd: Dec | null
  scenario_estimated_loss_usd: Dec | null
  position_effects: Record<string, PositionEffect>
  representative_paths: Record<string, string[]>
  assumptions: string[]
  evidence_ids: string[]
  warnings: string[]
}

export interface CarryAssumptions {
  pt_annual_return: Dec
  borrow_apy: Dec
  holding_fees_usd: Dec
  explanation: string
}

export interface CarryResult {
  status: string
  horizon_days: number
  net_carry_usd: Dec | null
  collateral_return_usd: Dec | null
  borrowing_cost_usd: Dec | null
  pt_annual_return: Dec | null
  borrow_apy: Dec | null
  holding_fees_usd: Dec | null
  basis: string
  methodology: string
  evidence_ids: string[]
  missing_fields: string[]
  warnings: string[]
}

export interface ClosingAssumptions {
  available_repayment_loan_units: Dec
  gas_cost_usd: Dec
  extra_fees_usd: Dec
  financing_fee_usd: Dec
  basis?: 'USER_ASSUMPTION'
  explanation: string
}

export interface ClosingPlanResult {
  status: string
  position_id: string
  quote_id: string | null
  basis: string
  required_repayment_loan_units: Dec | null
  available_repayment_loan_units: Dec | null
  repayment_shortfall_loan_units: Dec | null
  gas_cost_usd: Dec | null
  extra_fees_usd: Dec | null
  financing_fee_usd: Dec | null
  sale_proceeds_usd: Dec | null
  net_proceeds_usd: Dec | null
  total_operation_cost_usd: Dec | null
  exit_cost_usd: Dec | null
  net_equity_after_exit_usd: Dec | null
  steps: string[]
  missing_fields: string[]
  warnings: string[]
}

export interface ScenarioAssumption {
  loss_scenario_ref: string
  probability_over_horizon: Dec | null
  basis?: 'USER_ASSUMPTION'
  include_in_financial_comparison?: boolean
}

export interface DecisionRequest {
  strategy_id: string
  position_id: string
  mode: 'normal' | 'predictive'
  horizon_days: number
  scenario_assumptions: ScenarioAssumption[]
  scenarios_mutually_exclusive: boolean
  exit_quote_id: string | null
  estimated_net_carry_usd?: Dec | null
  carry_explanation?: string | null
  sensitivity_scenario_ref?: string | null
  carry_assumptions?: CarryAssumptions | null
  closing_assumptions?: ClosingAssumptions | null
}

export interface DecisionResult {
  horizon_days: number
  requested_mode: string
  effective_mode: 'normal' | 'predictive'
  forecast_bundle_id: string | null
  fallback_reason: string | null
  decision_status: 'CONDITIONAL' | 'INSUFFICIENT_DATA' | 'EXIT_NOT_FEASIBLE' | 'QUOTE_UNAVAILABLE'
  verdict: 'STAY' | 'EXIT' | null
  comparison: 'STAY_BETTER' | 'EXIT_BETTER' | 'EQUAL' | null
  equity_now_usd: Dec | null
  stay_value_usd: Dec | null
  exit_value_usd: Dec | null
  expected_loss_usd: Dec | null
  exit_cost_usd: Dec | null
  net_carry_usd: Dec | null
  break_even_probability: Dec | null
  break_even_status: string | null
  sensitivity_scenario_ref: string | null
  sensitivity_probability_max: Dec | null
  quote_status: string
  requires_external_repayment_liquidity: boolean | null
  included_scenario_ids: string[]
  excluded_scenario_ids: string[]
  excluded_unquantified_scenario_ids: string[]
  assumption_basis: string
  structural_risk_index: Dec | null
  risk_completeness_pct: Dec | null
  carry_calculation: CarryResult | null
  closing_plan: ClosingPlanResult | null
  explanation: string[]
  warnings: string[]
}

export interface MonteCarloRequest {
  strategy_id: string
  position_id: string
  horizon_days: 1 | 3 | 7
  num_paths: number
  seed: number
  volatility_source: 'USER_ASSUMPTION' | 'OBSERVED_ORACLE_SERIES'
  annual_volatility?: Dec | null
  annual_drift?: Dec | null
  debt_model: 'FROZEN_DEBT' | 'CONSTANT_APR'
  borrow_apr?: Dec | null
}

export interface MonteCarloData {
  strategy_id: string
  position_id: string
  model_version: string
  horizon_days: number
  initial_ltv: Dec | null
  initial_health_factor: Dec | null
  critical_oracle_drop_ratio: Dec | null
  breached_paths: number | null
  total_paths: number | null
  simulated_threshold_breach_frequency: Dec | null
  terminal_ltv_p50: Dec | null
  terminal_ltv_p95: Dec | null
  min_health_factor_p05: Dec | null
  mc_sampling_ci_95: { low: Dec; high: Dec } | null
  oracle_mark_to_model_var95_loan_units: Dec | null
  oracle_mark_to_model_es95_loan_units: Dec | null
  realized_liquidation_loss_usd: Dec | null
  assumptions: string[]
}

export interface OracleCalibration {
  method: string
  series_id: string
  window_start: string
  window_end: string
  observation_count: number
  return_count: number
  daily_log_return_mean: Dec
  daily_log_return_stdev: Dec
  annual_volatility: Dec
  annual_drift: Dec
  drift_policy: string
  skipped_candidate_points: number
  points: { target_time: string; observed_at: string; block_number: number; oracle_price_loan_per_pt: Dec; evidence_id: string }[]
  evidence_ids: string[]
}

/** Nested result: the Core envelope `data` is itself a Monte Carlo envelope. */
export interface MonteCarloResult {
  schema_version: string
  status: 'OK' | 'PARTIAL' | 'UNAVAILABLE' | 'NOT_APPLICABLE'
  as_of: string
  data: MonteCarloData | null
  provenance: { calibration: string; evidence_ids: string[]; oracle_calibration: OracleCalibration | null }
  missing_fields: string[]
  warnings: string[]
}

export interface IncidentFrequencies {
  estimates: string[]
  methodology: string
}

// Mirrors backend/app/models (data-contract-v1.0). Decimals arrive as strings.

export type DataMode = 'REAL' | 'DEMO'
export type DataStatus = 'OK' | 'PARTIAL' | 'MISSING' | 'STALE' | 'UNVERIFIED' | 'MISMATCH' | 'ERROR'
export type RelationStatus = 'ONCHAIN_VERIFIED' | 'API_OBSERVED' | 'CURATED' | 'UNRESOLVED'
export type RelationType =
  | 'MARKET_LOAN_ASSET'
  | 'MARKET_COLLATERAL_ASSET'
  | 'MARKET_ORACLE'
  | 'MARKET_IRM'
  | 'PENDLE_MARKET_PT'
  | 'PENDLE_MARKET_SY'
  | 'PENDLE_MARKET_YT'
  | 'SY_YIELD_TOKEN'
  | 'SY_ACCOUNTING_ASSET'
  | 'ECONOMIC_BACKING'

export interface Envelope<T> {
  schema_version: string
  status: DataStatus
  as_of: string | null
  snapshot: { data_mode: DataMode; block_number: number | null; block_hash: string | null }
  data: T
  provenance: { source_ids: string[]; evidence_ids: string[] }
  missing_fields: string[]
  warnings: string[]
}

export interface Token {
  chain_id: number
  address: string
  symbol: string | null
  name: string | null
  decimals: number | null
}

export interface MorphoMetrics {
  morpho_address: string
  loan_token: Token
  collateral_token: Token
  oracle_address: string
  irm_address: string
  lltv: string
  total_supply_assets: string | null
  total_borrow_assets: string | null
  utilization: string | null
  supply_apy: string | null
  borrow_apy: string | null
  oracle_price: string | null
  collateral_price: string | null
}

export interface PendleMetrics {
  market_address: string
  pt: Token
  yt: Token
  sy: Token
  expiry: string
  underlying_asset: Token | null
  is_expired: boolean
  implied_apy: string | null
  underlying_apy: string | null
  pt_price: string | null
  liquidity_usd: string | null
  total_pt: string | null
  total_sy: string | null
}

export interface Market {
  chain_id: number
  data_mode: DataMode
  protocol: 'MORPHO_BLUE' | 'PENDLE_V2'
  market_id: string
  status: DataStatus
  as_of: string
  block_number: number | null
  morpho: MorphoMetrics | null
  pendle: PendleMetrics | null
  source_ids: string[]
  evidence_ids: string[]
}

export interface Strategy {
  strategy_id: string
  strategy_type: string
  status: 'CANDIDATE' | 'VERIFIED_ACTIVE' | 'EXPIRED' | 'UNSUPPORTED'
  status_reason: string
  data_status: DataStatus
  morpho_market_id: string
  pendle_market_address: string | null
  pt: Token
  loan_asset: Token
  underlying_asset: Token | null
  expiry: string | null
  as_of: string
  block_number: number | null
  evidence_ids: string[]
}

export interface Relation {
  relation_id: string
  source_node_id: string
  target_node_id: string
  relation_type: RelationType
  verification_status: RelationStatus
  evidence_ids: string[]
  observed_at: string
  block_number: number | null
  note: string | null
}

export interface StrategyDetail {
  strategy: Strategy
  relations: Relation[]
}

export interface Position {
  position_id: string
  kind: 'ONCHAIN' | 'SIMULATED'
  protocol: string
  market_id: string
  owner: string | null
  collateral_asset: Token
  loan_asset: Token
  collateral_amount: string | null
  borrow_amount: string | null
  strategy_id: string | null
  label: string | null
  as_of: string
  block_number: number | null
}

export interface Evidence {
  evidence_id: string
  source_kind: string
  source_id: string
  method: string
  target_address: string | null
  block_number: number | null
  block_hash: string | null
  fetched_at: string
  payload_sha256: string | null
  status: DataStatus
  error_message: string | null
  source_uri: string | null
}

export interface SeriesPoint {
  timestamp: string
  value: string | null
}

export interface Series {
  series_id: string
  metric: string
  unit: 'RATIO' | 'USD' | 'TOKEN'
  quote_asset: Token | null
  status: DataStatus
  points: SeriesPoint[]
}

export interface DataStatusInfo {
  kind: 'market' | 'strategy'
  status: DataStatus
  freshness: { as_of: string; age_seconds: number; ttl_seconds: number; is_stale: boolean }
  missing_fields: string[]
  evidence_count: number
}

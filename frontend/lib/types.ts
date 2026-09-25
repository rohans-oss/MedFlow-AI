export type Role = "admin" | "procurement_manager" | "inventory_manager" | "department_manager" | "viewer";
export type StockStatus = "OUT_OF_STOCK" | "LOW" | "OK" | "OVERSTOCK";
export type MovementType = "RECEIPT" | "ISSUE" | "RETURN" | "WASTAGE" | "ADJUSTMENT";
export type AlertType = "OUT_OF_STOCK" | "LOW_STOCK" | "EXPIRED" | "EXPIRING_SOON";
export type Severity = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
export type AlertStatus = "OPEN" | "ACKNOWLEDGED" | "RESOLVED";

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface Hospital {
  id: number;
  name: string;
  code: string;
  city: string | null;
  state: string | null;
  bed_count: number | null;
  expiry_warning_days: number;
  is_demo: boolean;
  organization_id?: number | null;
  status?: TenantStatus;
}

export interface Department {
  id: number;
  name: string;
  code: string;
  description: string | null;
  is_active: boolean;
}

export interface User {
  id: number;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  department_id: number | null;
  department: Department | null;
  last_login_at: string | null;
  created_at: string;
}

export interface Me extends Omit<User, "role"> {
  role: Role | null; // V8: null when no hospital is selected
  hospital: Hospital | null; // V8: the ACTIVE hospital
  permissions: string[];
  organization: OrgBrief | null;
  memberships: MembershipBrief[];
  admin_organizations: OrgBrief[];
  is_platform_admin: boolean;
}

export interface Category {
  id: number;
  name: string;
  description: string | null;
}

export interface Consumable {
  id: number;
  sku: string;
  name: string;
  description: string | null;
  unit: string;
  unit_cost: number;
  reorder_level: number;
  max_level: number | null;
  is_active: boolean;
  category: Category | null;
}

export interface Ref {
  id: number;
  code: string;
  name: string;
}
export interface ConsumableRef {
  id: number;
  sku: string;
  name: string;
  unit: string;
}

export interface Supplier {
  id: number;
  code: string;
  name: string;
  contact_person: string | null;
  email: string | null;
  phone: string | null;
  address: string | null;
  city: string | null;
  gstin: string | null;
  default_lead_time_days: number;
  notes: string | null;
  is_active: boolean;
}

export interface SupplierListItem extends Supplier {
  product_count: number;
  receipts_90d: number;
  last_receipt_at: string | null;
}

export interface SupplierProduct {
  id: number;
  supplier_sku: string | null;
  unit_price: number;
  lead_time_days: number;
  moq: number;
  is_preferred: boolean;
  supplier: Ref;
  consumable: ConsumableRef;
}

export interface Batch {
  id: number;
  lot_number: string;
  expiry_date: string | null;
  quantity: number;
  initial_quantity: number;
  unit_cost: number;
  received_at: string;
  supplier: Ref | null;
  is_expired: boolean;
  days_to_expiry: number | null;
}

export interface Movement {
  id: number;
  movement_type: MovementType;
  quantity: number;
  balance_after: number;
  reference: string | null;
  reason: string | null;
  created_at: string;
  consumable: ConsumableRef;
  batch: { id: number; lot_number: string; expiry_date: string | null } | null;
  department: { id: number; name: string; code: string } | null;
  supplier: Ref | null;
  performed_by: { id: number; full_name: string } | null;
}

export interface InventoryRow {
  consumable_id: number;
  sku: string;
  name: string;
  unit: string;
  category: string | null;
  category_id: number | null;
  reorder_level: number;
  max_level: number | null;
  unit_cost: number;
  is_active: boolean;
  usable_stock: number;
  expired_stock: number;
  stock_value: number;
  status: StockStatus;
  batch_count: number;
  next_expiry: string | null;
  last_movement_at: string | null;
  open_alerts: number;
}

export interface DailyPoint {
  date: string;
  issued: number;
  received: number;
  issued_value: number;
}

export interface InventoryDetail {
  item: Consumable;
  stock: InventoryRow;
  batches: Batch[];
  suppliers: SupplierProduct[];
  recent_movements: Movement[];
  daily: DailyPoint[];
  avg_daily_issue_30d: number;
  days_of_cover: number | null;
}

export interface MovementResult {
  movements: Movement[];
  usable_stock: number;
  status: StockStatus;
}

export interface Alert {
  id: number;
  alert_type: AlertType;
  severity: Severity;
  status: AlertStatus;
  title: string;
  message: string;
  created_at: string;
  updated_at: string;
  acknowledged_at: string | null;
  resolved_at: string | null;
  consumable: ConsumableRef | null;
  batch: { id: number; lot_number: string; expiry_date: string | null } | null;
}

export interface AuditEntry {
  id: number;
  action: string;
  entity_type: string;
  entity_id: number | null;
  details: Record<string, unknown> | null;
  ip_address: string | null;
  created_at: string;
  user: { id: number; full_name: string } | null;
}

export interface NamedCount {
  name: string;
  value: number;
}

export interface DashboardSummary {
  items_monitored: number;
  status_counts: Record<StockStatus, number>;
  stock_value: number;
  expired_value: number;
  expiring_soon_value: number;
  open_alerts: Partial<Record<AlertType, number>>;
  open_alerts_by_severity: Partial<Record<Severity, number>>;
  daily: DailyPoint[];
  consumption_by_department: NamedCount[];
  top_consumed: NamedCount[];
  recent_movements: Movement[];
  critical_items: InventoryRow[];
}

/* ---------------- Version 2 — forecasting ---------------- */

export interface ModelMetrics {
  mae: number | null;
  rmse: number | null;
  wape: number | null;
  bias: number | null;
  n_points: number;
  actual_total?: number | null;
  predicted_total?: number | null;
}

export interface ModelVersion {
  id: number;
  run_id: string;
  name: string;
  model_type: "xgboost" | "moving_average_7" | "historical_average" | "xgboost_procedure";
  trained_at: string;
  data_start: string;
  data_end: string;
  test_start: string;
  test_end: string;
  horizon_days: number;
  n_series: number;
  n_train_rows: number;
  dataset_hash: string;
  features: string[] | null;
  params: Record<string, unknown> | null;
  metrics: ModelMetrics;
  feature_importance: Record<string, number> | null;
  is_active: boolean;
  notes: string | null;
}

export interface ItemMetric {
  consumable_id: number;
  name: string;
  sku: string;
  unit: string;
  actual_total: number;
  predicted_total: number;
  mae: number;
  rmse: number;
  wape: number | null;
  bias: number | null;
  n_points: number;
}

export interface ModelDetail {
  model: ModelVersion;
  run_candidates: ModelVersion[];
  items: ItemMetric[];
}

export interface HorizonTotal {
  days: number;
  predicted: number;
  lower: number;
  upper: number;
}

export interface ItemForecast {
  item: string;
  item_id: number;
  sku: string;
  unit: string;
  horizon_days: number;
  predicted_demand: number;
  lower: number;
  upper: number;
  interval: string;
  model_version: string;
  model_type: string;
  trained_at: string;
  data_through: string;
  is_stale: boolean;
  horizons: HorizonTotal[];
  daily: { date: string; predicted: number; lower: number; upper: number }[];
  history: { date: string; actual: number; censored: boolean }[];
  drivers_horizon: number | null;
  base_level: number | null;
  drivers: { feature: string; label: string; units: number }[];
  explanation: string[];
  backtest: {
    test_start: string;
    test_end: string;
    actual_total: number;
    predicted_total: number;
    mae: number;
    rmse: number;
    wape: number | null;
    bias: number | null;
    daily: { date: string; actual: number | null; predicted: number }[];
  } | null;
  usable_stock: number;
  avg_daily_last_30: number;
  days_of_cover: number | null;
  // V2B
  forecast_source: ForecastSource;
  scheduled_procedures: number;
  procedure_driven_demand: number;
  procedure_impact: ProcedureImpact | null;
  model_comparison: ModelComparison | null;
  comparison_daily: { date: string; predicted: number; lower: number; upper: number }[];
  expected_shortage: number;
  stock_covers_horizon: boolean;
  days_of_stock_remaining: number | null;
}

export interface ForecastListItem {
  consumable_id: number;
  name: string;
  sku: string;
  unit: string;
  category: string | null;
  predicted_demand: number;
  lower: number;
  upper: number;
  avg_daily_last_30: number;
  change_vs_recent: number | null;
  usable_stock: number;
  days_of_cover: number | null;
  backtest_wape: number | null;
  scheduled_procedures: number;
  procedure_driven_demand: number;
  expected_shortage: number;
}

export interface ForecastOverview {
  model: ModelVersion | null;
  horizon_days: number;
  is_stale: boolean;
  total_value: number;
  items: ForecastListItem[];
  comparison: ModelComparison | null;
}

export interface TrainResult {
  run_id: string;
  active_model: string;
  model_type: string;
  data_start: string;
  data_end: string;
  n_series: number;
  n_items: number;
  candidates: Record<string, ModelMetrics>;
  procedure_model: {
    trained: boolean;
    consistent_improvement?: boolean;
    skipped_reason?: string | null;
    v2a_wape?: number | null;
    v2b_wape?: number | null;
    improved?: boolean;
    schedule_through?: string | null;
  } | null;
  seconds: number;
}

/* ---------------- Version 2B — procedure-aware forecasting ---------------- */

export type ForecastSource = "procedure_aware" | "consumption" | "baseline";
export type ProcedureStatus = "SCHEDULED" | "COMPLETED" | "CANCELLED";

export interface ModelComparison {
  v2a_model: string | null;
  v2a_wape: number | null;
  v2b_model: string | null;
  v2b_wape: number | null;
  v2b_trained: boolean;
  improved: boolean;
  active_source: ForecastSource;
  summary: string;
  schedule_through: string | null;
  falls_back_to_v2a_from: string | null;
  schedule_changed_since_training: boolean;
}

export interface ProcedureImpact {
  horizon_days: number;
  start: string;
  scheduled_procedures: number;
  expected_quantity: number;
  types: {
    procedure_type_id: number;
    code: string;
    name: string;
    department: string;
    count: number;
    quantity_per_procedure: number;
    expected_quantity: number;
  }[];
  departments: string[];
  daily: Record<string, number>;
  v2a_forecast: number | null;
  v2b_forecast: number | null;
  procedure_effect: number | null;
  model_procedure_contribution: number | null;
  share_of_forecast: number | null;
  note: string;
}

export interface ProcedureTypeRow {
  id: number;
  code: string;
  name: string;
  description: string | null;
  avg_duration_minutes: number | null;
  is_active: boolean;
  is_synthetic: boolean;
  department: { id: number; name: string; code: string };
  mapping_count: number;
  upcoming_count: number;
}

export interface ScheduleRow {
  id: number;
  scheduled_date: string;
  count: number;
  status: ProcedureStatus;
  notes: string | null;
  is_synthetic: boolean;
  created_at: string;
  procedure_type: { id: number; code: string; name: string };
  department: { id: number; name: string; code: string };
}

export interface MappingRow {
  id: number;
  quantity_per_procedure: number;
  notes: string | null;
  is_active: boolean;
  is_synthetic: boolean;
  procedure_type: { id: number; code: string; name: string };
  consumable: ConsumableRef;
}

export interface ProcedureSummary {
  days: number;
  total_scheduled: number;
  by_type: { procedure_type_id: number; code: string; name: string; department: string; count: number }[];
  schedule_through: string | null;
  synthetic_rows: number;
}

/* ---------------- Version 3 — stockout risk ---------------- */

export type RiskLevel = "HIGH" | "MEDIUM" | "LOW";

export interface RiskMetrics {
  tp: number;
  fp: number;
  fn: number;
  tn: number;
  precision: number | null;
  recall: number | null;
  f1: number | null;
  f2: number | null;
  pr_auc: number | null;
  roc_auc: number | null;
  brier?: number | null;
  n_rows: number;
  n_positive: number;
  positive_rate: number | null;
  n_events: number;
  event_recall: number | null;
  lead_time_recall: number | null;
  median_warning_days: number | null;
}

export interface RiskEvent {
  consumable_id: number;
  date: string;
  lead_time: number | null;
  first_warning: string | null;
  warning_days: number | null;
  caught: boolean;
  caught_in_time: boolean;
}

export interface RiskModel {
  id: number;
  run_id: string;
  name: string;
  model_type: "xgboost_classifier" | "cover_rule" | "reorder_rule";
  trained_at: string;
  horizon_days: number;
  data_start: string;
  data_end: string;
  eval_start: string | null;
  eval_end: string | null;
  n_train_rows: number;
  n_positive_train: number;
  dataset_hash: string;
  features: string[] | null;
  params: Record<string, unknown> | null;
  metrics: { backtest: RiskMetrics; validation: RiskMetrics; backtest_with_floor?: RiskMetrics };
  evaluation: {
    events: RiskEvent[];
    events_with_floor?: RiskEvent[];
    pr_curve: { threshold: number; precision: number | null; recall: number | null }[];
    calibration: { bin: string; n: number; mean_predicted: number; observed_rate: number }[];
    selection: string;
  } | null;
  feature_importance: Record<string, number> | null;
  warn_threshold: number;
  high_threshold: number;
  is_active: boolean;
  notes: string | null;
}

export interface RiskModelDetail {
  model: RiskModel;
  run_candidates: RiskModel[];
  item_names: Record<string, string>;
}

export interface RiskItem {
  consumable_id: number;
  name: string;
  sku: string;
  unit: string;
  category: string | null;
  usable_stock: number;
  reorder_level: number;
  forecast_7: number;
  forecast_14: number;
  forecast_30: number;
  days_of_stock_remaining: number | null;
  expected_stockout_date: string | null;
  shortage_quantity: number;
  expiring_quantity: number;
  lead_time_days: number | null;
  order_by_date: string | null;
  order_overdue: boolean;
  can_replenish_in_time: boolean | null;
  probability: number;
  risk_level: RiskLevel;
  out_of_stock: boolean;
  probability_source: string;
  main_reason: string;
  as_of: string;
  updated_at: string;
}

export interface RiskDetail extends RiskItem {
  horizon_days: number;
  reasons: string[];
  drivers: { feature: string; label: string; value: number | null; contribution: number }[];
  projection: { date: string; demand: number; stock_end: number; unmet: number; expired: number }[];
  history: { at: string; probability: number; risk_level: RiskLevel }[];
  risk_model: string | null;
  forecast_model: string | null;
  warn_threshold: number | null;
  high_threshold: number | null;
}

export interface RiskOverview {
  model: RiskModel | null;
  forecast_model: string | null;
  as_of: string | null;
  horizon_days: number;
  counts: { high: number; medium: number; low: number; out_of_stock: number; total: number };
  items: RiskItem[];
  message: string | null;
}

export interface RiskTrainResult {
  run_id: string;
  active_model: string;
  model_type: string;
  selection: string;
  n_events: number;
  seconds: number;
}

/* ---------------- Version 4 — supplier intelligence ---------------- */

export interface SupplierMetrics {
  orders: number;
  open: number;
  overdue: number;
  received: number;
  cancelled: number;
  decided: number;
  otif_successes: number;
  otif_rate: number | null;
  otif_ci_low: number | null;
  otif_ci_high: number | null;
  prior: number | null;
  reliability_score: number | null;
  grade: string | null;
  limited_evidence: boolean;
  on_time_rate: number | null;
  late_rate: number | null;
  avg_days_late: number | null;
  lead_time_median: number | null;
  lead_time_mean: number | null;
  lead_time_p90: number | null;
  lead_time_std: number | null;
  quoted_lead_time: number | null;
  cancellation_rate: number | null;
  fill_rate: number | null;
  in_full_rate: number | null;
  price_pairs: number;
  price_changes: number;
  price_stability: number | null;
  price_change_pct: number | null;
}

export interface ScoreMethod {
  formula: string;
  otif: string;
  decided: string;
  prior: string;
  interval: string;
  grades: string;
}

export interface ScorecardRow {
  supplier_id: number;
  code: string;
  name: string;
  city: string | null;
  is_active: boolean;
  default_lead_time_days: number;
  has_catalogue: boolean;
  metrics: SupplierMetrics | null;
}

export interface Scorecards {
  window_days: number;
  window_start: string;
  window_end: string;
  prior: number;
  hospital: SupplierMetrics;
  suppliers: ScorecardRow[];
  method: ScoreMethod;
}

export type SupplierOrderStatus = "OPEN" | "PARTIAL" | "RECEIVED" | "CANCELLED";

export interface SupplierOrderRow {
  id: number;
  reference: string;
  supplier_id: number;
  supplier: string;
  consumable_id: number;
  item: string;
  sku: string;
  unit: string;
  ordered_date: string;
  expected_date: string;
  quoted_lead_time_days: number;
  quantity_ordered: number;
  quantity_received: number;
  unit_price: number;
  status: SupplierOrderStatus;
  first_delivery_date: string | null;
  completed_date: string | null;
  lead_time_days: number | null;
  days_late: number | null;
  overdue_days: number;
  close_reason: string | null;
  recommendation_id?: number | null;
  is_synthetic: boolean;
}

export interface OpenSupplierOrder extends SupplierOrderRow {
  days_in_transit: number;
  predicted_arrival: string;
  p_late: number | null;
  p_before_stockout: number | null;
  before_stockout_k: number | null;
  before_stockout_n: number | null;
  item_risk_level?: RiskLevel | null;
  item_stockout_date?: string | null;
}

export interface SupplierDetailPerf {
  supplier: { id: number; code: string; name: string; city: string | null; default_lead_time_days: number };
  window_days: number;
  window_start: string;
  window_end: string;
  prior: number;
  metrics: SupplierMetrics;
  monthly: { month: string; orders: number; decided: number; otif_rate: number | null; on_time_rate: number | null; avg_price: number }[];
  lead_time_histogram: { days: number; orders: number }[];
  items: {
    consumable_id: number;
    name: string;
    sku: string;
    unit: string;
    catalogue_price: number | null;
    quoted_lead_time_days: number | null;
    moq: number | null;
    is_preferred: boolean;
    metrics: SupplierMetrics | null;
    price_history: { date: string; price: number }[];
  }[];
  recent_orders: SupplierOrderRow[];
  method: ScoreMethod;
}

export type SupplierVerdict = "likely" | "uncertain" | "unlikely" | "no_evidence" | "no_deadline";

export interface ItemSupplierOption {
  supplier_id: number;
  supplier: string;
  code: string;
  is_active: boolean;
  is_preferred: boolean;
  unit_price: number;
  price_vs_cheapest: number | null;
  moq: number;
  quoted_lead_time_days: number;
  typical_lead_time_days: number;
  p90_lead_time_days: number;
  lead_time_basis: "item" | "supplier" | "quoted";
  evidence_basis: "item" | "supplier" | "none";
  evidence_orders: number;
  item_orders: number;
  on_time_rate: number | null;
  otif_rate: number | null;
  reliability_score: number | null;
  grade: string | null;
  cancellation_rate: number | null;
  fill_rate: number | null;
  price_stability: number | null;
  in_time_k: number | null;
  in_time_n: number | null;
  p_in_time: number | null;
  verdict: SupplierVerdict;
  arrives_typically: string;
  arrives_worst_case: string;
}

export interface ItemRiskLink {
  risk_level: RiskLevel;
  probability: number;
  usable_stock: number;
  expected_stockout_date: string | null;
  days_of_stock_remaining: number | null;
  shortage_quantity: number;
  forecast_14: number;
  as_of: string;
  out_of_stock: boolean;
}

export interface ItemSupplierOptions {
  item: { id: number; name: string; sku: string; unit: string };
  risk: ItemRiskLink | null;
  deadline_days: number | null;
  options: ItemSupplierOption[];
  open_orders: OpenSupplierOrder[];
  summary: string[];
}

export interface SupplierAtRiskRow {
  item: { id: number; name: string; sku: string; unit: string };
  risk: ItemRiskLink;
  deadline_days: number | null;
  n_suppliers: number;
  n_likely: number;
  best: ItemSupplierOption | null;
  open_orders: number;
  overdue_orders: number;
  headline: string;
}

export interface LeadTimeEval { mae: number; bias: number; within_1_day: number; p90_coverage?: number }
export interface DelayEval { brier: number; roc_auc: number | null; log_loss: number; mean_predicted: number; observed_rate: number }

export interface SupplierEvaluation {
  n_test: number;
  test_start: string;
  test_end: string;
  late_rate_test: number | null;
  lead_time: Record<"quoted" | "supplier_history" | "supplier_item_history", LeadTimeEval> | null;
  delay: Record<"hospital_rate" | "supplier_rate" | "supplier_item_rate", DelayEval> | null;
  notes: string[];
}

/* ------------------------------------------------------------------ V5 procurement intelligence */

export interface CostSettingsValues {
  service_level: number;
  review_period_days: number;
  horizon_days: number;
  stockout_cost_multiplier: number;
  holding_cost_rate: number;
  disposal_cost_pct: number;
  order_cost: number;
  allow_split: boolean;
  budget_limit: number | null;
  simulations: number;
}

export interface CostSettings {
  values: CostSettingsValues;
  defaults: CostSettingsValues;
  explain: Record<keyof CostSettingsValues, string>;
  formula: string[];
  is_default: boolean;
  updated_at: string | null;
  updated_by: string | null;
}

export interface ItemRef { id: number; name: string; sku: string; unit: string }

export interface AttentionRow {
  item: ItemRef;
  risk_level: RiskLevel | null;
  risk_probability: number | null;
  v3_stockout_date: string | null;
  usable_stock: number;
  avg_daily_demand: number;
  in_transit_orders: number;
  in_transit_quantity: number;
  expected_in_transit: number;
  overdue_orders: number;
  need_by_date: string | null;
  order_by_date: string | null;
  horizon_days: number;
  p_stockout_no_order: number;
  p_stockout_14_no_order: number;
  expected_shortage_no_order: number;
  required_quantity: number;
  moq_adjusted_quantity: number;
  reference_supplier: string;
  safety_stock: number;
  reasons: string[];
  pending_recommendation_id: number | null;
  pending_summary: string | null;
}

export interface Attention {
  as_of: string;
  max_horizon_days: number;
  items: AttentionRow[];
  skipped: { item_id: number; reason: string }[];
  settings: CostSettingsValues;
}

export interface ScenarioLine {
  supplier_id: number;
  supplier: string;
  code: string;
  quantity: number;
  unit_price: number;
  moq: number;
  line_value?: number;
  quoted_lead_time_days?: number;
  arrival_basis?: string;
  arrival_evidence?: number;
  typical_arrival_date: string | null;
  p90_arrival_date?: string | null;
  p_arrive_by_need: number;
  p_never_in_horizon?: number;
  window_k: number;
  window_n: number;
  reliability_score?: number | null;
  grade?: string | null;
}

export interface ScenarioCosts {
  purchase: number;
  purchase_goods: number;
  order_fixed: number;
  stockout: number;
  holding: number;
  expiry: number;
  carried_forward: number;
  total: number;
}

export interface ScenarioMetrics {
  p_stockout: number;
  p_stockout_14: number;
  expected_shortage: number;
  shortage_p90: number;
  median_stockout_date: string | null;
  expected_expired_units: number;
  expected_used_in_horizon: number;
  expected_carried_units: number;
  arrival_date: string | null;
  last_arrival_date: string | null;
  p_arrive_by_need: number | null;
}

export type ScenarioTag = "recommended" | "cheapest" | "most_reliable" | "fastest" | "no_order" | "split";

export interface Scenario {
  key: string;
  kind: "none" | "single" | "split" | "custom";
  label: string;
  note: string;
  tags: ScenarioTag[];
  rank: number;
  lines: ScenarioLine[];
  quantity: number;
  purchase_value: number;
  costs: ScenarioCosts;
  metrics: ScenarioMetrics;
}

export interface ReplenishmentCalc {
  lead_time_days: number;
  lead_time_sd: number;
  review_period_days: number;
  cover_days: number;
  avg_daily_demand: number;
  demand_over_cover: number;
  sigma_daily: number;
  safety_stock: number;
  usable_stock: number;
  expiring_before_use: number;
  expected_incoming: number;
  reorder_point: number;
  order_by_date: string | null;
  need_by_date: string | null;
  required_quantity: number;
  moq: number;
  moq_adjusted_quantity: number;
  expiry_cap: number | null;
  storage_cap: number | null;
  warnings: string[];
  projection: { date: string; without_deliveries: number; with_in_transit: number }[];
}

export interface TransitRow {
  order_id: number;
  reference: string;
  supplier_id: number;
  supplier: string;
  outstanding: number;
  expected_date: string;
  overdue_days: number;
  arrival_basis: string;
  p_arrive_by_need: number;
  window_k: number;
  window_n: number;
  expected_quantity: number;
  typical_arrival_date: string | null;
}

export interface ProcurementPlan {
  item: ItemRef;
  as_of: string;
  horizon_days: number;
  risk: { risk_level: RiskLevel; probability: number; expected_stockout_date: string | null; usable_stock: number } | null;
  demand: { forecast_30: number; sigma_daily: number; sigma_basis: string; shelf_life_days: number | null; reference_price: number };
  reference_supplier: string;
  replenishment: ReplenishmentCalc;
  replenishment_by_supplier: Record<string, { required_quantity: number; moq_adjusted_quantity: number; safety_stock: number; lead_time_days: number; order_by_date: string | null }>;
  need_by_days: number;
  need_by_date: string | null;
  in_transit: TransitRow[];
  scenarios: Scenario[];
  solver: { engine: string; status: string; objective: number };
  recommended_key: string;
  explanation: string[];
}

export interface WhatIfPoint { p_stockout: number; expected_shortage: number; total_cost: number; arrival_date: string | null; p_arrive_by_need: number | null }

export interface WhatIfResult {
  item: ItemRef;
  delays: Record<string, number>;
  demand_change_pct: number;
  horizon_days: number;
  need_by_date: string | null;
  baseline_recommended: string;
  whatif_best: string;
  replanned_recommended: string;
  scenarios: { key: string; label: string; tags: ScenarioTag[]; before: WhatIfPoint; after: WhatIfPoint }[];
  summary: string[];
}

export type RecommendationStatus = "PENDING" | "APPROVED" | "REJECTED" | "SUPERSEDED";

export interface RecommendationRow {
  id: number;
  run_id: string;
  item: ItemRef;
  status: RecommendationStatus;
  created_at: string;
  as_of: string;
  is_current: boolean;
  risk_level: RiskLevel | null;
  need_by_date: string | null;
  order_by_date: string | null;
  lines: ScenarioLine[];
  quantity: number;
  purchase_value: number;
  expected_cost: number;
  p_stockout: number | null;
  expected_shortage: number | null;
  no_order_p_stockout: number | null;
  headline: string;
  generated_by: string | null;
  decided_by: string | null;
  decided_at: string | null;
  decision_reason: string | null;
  modified: boolean;
  final_lines: { supplier_id: number; supplier: string; quantity: number; unit_price: number }[] | null;
  orders: { id: number; reference: string; supplier: string; quantity: number; status: string; expected_date: string }[];
}

export interface RecommendationDetail extends RecommendationRow {
  cost_breakdown: ScenarioCosts;
  metrics: ScenarioMetrics;
  scenarios: Scenario[];
  replenishment: ReplenishmentCalc;
  in_transit: TransitRow[];
  explanation: string[];
  settings_snapshot: CostSettingsValues;
  solver: Record<string, unknown>;
  scenario_key: string;
  final_evaluation: Scenario | null;
}

export interface GenerateResult {
  run_id: string;
  created: number;
  superseded: number;
  orders_recommended: number;
  purchase_value: number;
  solver: { status: string; objective: number; purchase: number; budget: number | null; budget_binding: boolean };
  skipped: { item_id: number; reason: string }[];
  recommendations: RecommendationRow[];
}

export interface ArrivalEvaluation {
  n: number;
  orders?: number;
  windows?: string[];
  observed_rate?: number;
  mean_predicted?: number;
  brier_model?: number;
  brier_quote_certain?: number;
  calibration?: { from: number; to: number; n: number; mean_predicted: number; observed: number }[];
}

/* ------------------------------------------------------------------ V6 knowledge graph */

export interface GraphSyncRun {
  id: number;
  started_at: string;
  finished_at: string | null;
  status: "RUNNING" | "SUCCESS" | "FAILED";
  trigger: string;
  backend: string;
  verified: boolean;
  duration_ms: number | null;
  removed_nodes: number;
  node_counts: Record<string, number> | null;
  edge_counts: Record<string, number> | null;
  graph_node_counts: Record<string, number> | null;
  graph_edge_counts: Record<string, number> | null;
  error: string | null;
}

export interface GraphStatus {
  backend: string;
  url: string;
  configured: boolean;
  available: boolean;
  error: string | null;
  current: boolean | null;
  changed: string[];
  last_success: GraphSyncRun | null;
  runs: GraphSyncRun[];
  note: string;
}

export interface GraphResult<T> { synced_at: string | null; sync_run_id: number | null; data: T }

export type GraphLabel = "Hospital" | "Department" | "Category" | "Item" | "Procedure" | "Supplier" | "Batch" | "Forecast"
  | "StockoutRisk" | "SupplierOrder" | "ProcurementRecommendation";

export interface GraphNodeRef { label: GraphLabel; id: number | string; name: string }
export interface SubgraphNode { id: string; label: GraphLabel; name: string; column: number; detail: string }
export interface SubgraphEdge { from: string; to: string; type: string }

export interface ExplainStep { kind: string; title: string; lines: string[]; nodes: GraphNodeRef[]; via: string | null }

export interface GraphExplain {
  found: boolean;
  item: { id: number; name: string; sku: string; unit: string };
  summary: string;
  steps: ExplainStep[];
  graph: { nodes: SubgraphNode[]; edges: SubgraphEdge[] };
  cypher: Record<string, string>;
}

export interface ChainLink { label: GraphLabel; name: string; detail: string }

export interface SupplierImpact {
  found: boolean;
  supplier: { id: number; name: string; code: string; reliability_score: number | null; grade: string | null };
  summary: string[];
  items: {
    id: number; item: string; sku: string; unit: string; usable_stock: number; preferred: boolean; unit_price: number;
    alternatives: string[]; sole_source: boolean; severity: "critical" | "high" | "watch" | "low";
    risk_level: RiskLevel | null; risk_probability: number | null; days_left: number | null;
  }[];
  procedures: { id: number; procedure: string; department: string | null; scheduled_next_14: number;
    items: { item: string; sku: string; sole_source: boolean; per_procedure: number }[]; sole_source_items: number }[];
  departments: { id: number; department: string; items: number; units_90: number; item_names: string[] }[];
  open_orders: { reference: string; item: string; outstanding: number; expected_date: string; overdue_days: number }[];
  recommendations: { id: number; item: string; quantity: number; purchase_value: number }[];
  chains: ChainLink[][];
  cypher: Record<string, string>;
}

export interface ItemImpact {
  found: boolean;
  item: { id: number; name: string; sku: string; unit: string; usable_stock: number; risk_level: RiskLevel | null };
  summary: string[];
  procedures: { id: number; procedure: string; department: string | null; scheduled_next_14: number; completed_last_90: number;
    per_procedure: number; units_next_14: number; kit_items: number; procedures_covered_by_stock: number | null }[];
  departments: { id: number; department: string; units_90: number | null; share: number | null; procedures: string[]; procedure_units_14: number }[];
  suppliers: { supplier: string; code: string; active: boolean; unit_price: number; window_k: number | null; window_n: number | null }[];
  units_needed_14: number;
  cypher: Record<string, string>;
}

export interface GraphQueryDef { name: string; title: string; params: ("item" | "supplier")[]; cypher: string }
export interface GraphQueryResult { name: string; title: string; cypher: string; params: Record<string, unknown>; columns: string[]; rows: Record<string, unknown>[] }
export interface GraphSchema {
  labels: { label: GraphLabel; count: number }[];
  relationships: { type: string; from: GraphLabel; to: GraphLabel; meaning: string; count: number }[];
}

/* ---------------- V7 AI operations assistant ---------------- */

export interface AssistantLink { label: string; href: string }
export interface AssistantToolCall { tool: string; label: string; args: Record<string, unknown>; ok: boolean; facts: string[]; error: string | null; ms: number }
export interface AssistantAnswer { title: string; summary: string; points: string[]; notes: string[]; links: AssistantLink[]; follow_ups: string[] }
export interface AssistantReply {
  conversation_id: number; message_id: number; question: string; answer: AssistantAnswer; evidence: AssistantToolCall[];
  mode: "deterministic" | "llm"; provider: string; model: string | null; intent: string | null; grounded: boolean;
  fallback_reason: string | null; latency_ms: number;
}
export interface AssistantStatus {
  provider: string; mode: "deterministic" | "llm"; model: string | null; llm_configured: boolean; read_only: boolean; max_steps: number;
  tools: { name: string; description: string }[]; examples: string[]; capabilities: string[];
}
export interface AssistantConversation { id: number; title: string; created_at: string; updated_at: string; message_count: number }
export interface AssistantMessage {
  id: number; role: "user" | "assistant"; content: string; answer: AssistantAnswer | null; tool_calls: AssistantToolCall[] | null;
  provider: string | null; model: string | null; intent: string | null; grounded: boolean | null; fallback_reason: string | null;
  latency_ms: number | null; created_at: string;
}
export interface AssistantConversationDetail {
  id: number; title: string; context: Record<string, unknown> | null; created_at: string; updated_at: string; messages: AssistantMessage[];
}


/* ---------------- V8 multi-hospital SaaS ---------------- */

export type TenantStatus = "ACTIVE" | "SUSPENDED";
export interface OrgBrief { id: number; name: string; code: string; status: TenantStatus }
export interface MembershipBrief {
  membership_id: number; hospital_id: number; hospital_name: string; hospital_code: string; organization_id: number;
  organization_name: string; role: Role; department_id: number | null; status: TenantStatus; available: boolean;
}
export interface Organization {
  id: number; name: string; code: string; status: TenantStatus; is_demo: boolean; created_at: string;
  hospital_count: number; member_count: number; admin_count: number;
}
export interface HospitalAdmin {
  id: number; name: string; code: string; city: string | null; state: string | null; bed_count: number | null;
  status: TenantStatus; is_demo: boolean; organization_id: number; organization_name: string; member_count: number;
  my_role: Role | null; my_membership_status: TenantStatus | null; can_switch: boolean; can_admin: boolean;
}
export interface HospitalDetail extends HospitalAdmin {
  expiry_warning_days: number; admin_basis: string;
  departments: { id: number; code: string; name: string; is_active: boolean }[];
}
export interface Member {
  membership_id: number; user_id: number; email: string; full_name: string; role: Role; department_id: number | null;
  department: string | null; status: TenantStatus; account_active: boolean; other_hospitals: number;
  last_login_at: string | null; created_at: string;
}
export interface OrgHospitalRow {
  hospital_id: number; hospital: string; code: string; status: TenantStatus; city: string | null; active_members: number;
  items: number; risk_high: number; risk_medium: number; risk_low: number; risk_as_of: string | null; active_alerts: number;
  critical_alerts: number; pending_recommendations: number; pending_with_order: number; pending_purchase_value: number;
  supplier_otif_rate: number | null; supplier_orders_decided: number;
}
export interface OrgOverview {
  organization: Organization;
  hospitals: OrgHospitalRow[];
  totals: Record<string, number>;
  contributing_hospitals: string[];
  supplier_comparison: { code: string; names: string[]; by_hospital: { hospital_id: number; hospital: string; orders: number;
    otif_rate: number | null; reliability_score: number | null; grade: string | null }[] }[];
  risk_as_of_range: string[] | null;
  notes: string[];
}
export interface OrgAdmin { user_id: number; email: string; full_name: string; status: TenantStatus }
export interface OnboardResult {
  hospital: HospitalAdmin; admin_user_id: number; admin_created: boolean; departments: number;
  procurement_settings: "defaults" | "custom"; next_steps: string[];
}
export interface ImportResult {
  kind: string; rows: number; valid: number; created: number; dry_run: boolean;
  errors: { line: number; error: string; row: Record<string, string> }[]; created_categories: string[];
}

/* ---------------- V9 integrations & data exchange ---------------- */
export type Connector = "upload" | "api_push" | "rest_pull";
export type SourceHealth = "healthy" | "degraded" | "failing" | "never_run" | "disabled";
export type RunStatus = "RUNNING" | "SUCCESS" | "PARTIAL" | "FAILED";
export interface EntityField { name: string; kind: string; required: boolean; description: string }
export interface EntityInfo { name: string; label: string; description: string; transactional: boolean; fields: EntityField[] }
export interface IntegrationSource {
  id: number; name: string; system_type: string; connector: Connector; enabled: boolean; entities: string[];
  config: Record<string, unknown>; is_simulated: boolean; last_run_at: string | null; last_success_at: string | null;
  last_failure_at: string | null; last_error: string | null; created_at: string;
}
export interface SourceMonitor {
  id: number; name: string; system_type: string; connector: Connector; enabled: boolean; is_simulated: boolean;
  entities: string[]; health: SourceHealth; last_run_at: string | null; last_success_at: string | null;
  last_failure_at: string | null; last_error: string | null;
  freshness: { age_hours: number | null; stale_after_hours: number; stale: boolean }; schedule_minutes: number | null;
  runs_7d: number; received_7d: number; created_7d: number; updated_7d: number; unchanged_7d: number; rejected_7d: number;
  open_reconciliation: number; active_keys: number;
  entity_status: { entity: string; status: RunStatus; at: string; run_id: number; rejected: number }[];
}
export interface IntegrationOverview {
  sources: SourceMonitor[];
  totals: { sources: number; enabled: number; healthy: number; degraded: number; failing: number; never_run: number;
    received_7d: number; rejected_7d: number; created_7d: number; updated_7d: number; open_reconciliation: number };
  reference_erp_enabled: boolean;
}
export interface FieldMapping { entity: string; field_map: Record<string, string>; defaults: Record<string, unknown>; date_format: string | null; customized: boolean }
export interface IntegrationCredential { id: number; label: string; key_prefix: string; created_at: string; last_used_at: string | null; revoked_at: string | null }
export interface SyncRun {
  id: number; source_id: number; source_name: string; entity: string; mode: string; trigger: string; status: RunStatus;
  started_at: string; completed_at: string | null; records_received: number; records_created: number;
  records_updated: number; records_unchanged: number; records_rejected: number;
  error_summary: { fatal?: string | null; reasons?: Record<string, number>; info?: Record<string, number> } | null;
  checkpoint_before: string | null; checkpoint_after: string | null; file_name: string | null; file_sha256: string | null;
  parent_run_id: number | null; triggered_by: { id: number; full_name: string } | null;
}
export interface SyncRecord {
  id: number; row_number: number; external_id: string | null; raw: Record<string, unknown>;
  errors: { field: string | null; code: string; message: string }[]; status: "REJECTED" | "RETRIED"; retried_in_run_id: number | null;
}
export interface SyncRunDetail extends SyncRun { rejected: SyncRecord[] }
export interface ReconciliationIssue {
  id: number; source_id: number; source_name: string; run_id: number | null;
  item: { id: number; sku: string; name: string; unit: string }; as_of: string; external_quantity: number;
  medflow_quantity: number; difference: number; medflow_now: number; status: "OPEN" | "RESOLVED"; resolution: string | null;
  note: string | null; resolved_by: { id: number; full_name: string } | null; resolved_at: string | null; created_at: string;
}

/* ---------------- V10 real hospital pilot & business validation ---------------- */
export type PilotStatus = "planned" | "active" | "paused" | "completed" | "cancelled";
export interface NamedRef { id: number; name: string }
export interface Pilot {
  id: number; hospital_id: number; organization_id: number; hospital_name: string; name: string; description: string | null;
  status: PilotStatus; data_classification: "observed" | "synthetic"; owner: NamedRef | null;
  baseline_start: string; baseline_end: string; pilot_start: string; pilot_end: string; actual_end: string | null;
  notes: string | null; external_factors: { factor: string; note: string; period: string; recorded_by: string; at: string }[];
  departments: NamedRef[]; users: NamedRef[]; data_sources: NamedRef[]; open_issues: number; created_at: string; updated_at: string;
}
export type MetricUnit = "count" | "units" | "pct" | "money" | "days" | "hours";
export interface PilotMetric {
  key: string; label: string; section: string; value: number | null; unit: MetricUnit; n: number | null; min_n: number;
  sufficient: boolean; formula: string; source: string; note: string | null;
  models?: { name: string; type: string; trained_at: string; data_end: string; holdout_wape: number | null }[] | null;
}
export interface PilotPeriod { kind: string; start: string; end: string; days: number; ledger_end: string | null; ledger_days: number; partial: boolean }
export interface SourceReliability {
  id: number; name: string; connector: string; system_type: string; is_simulated: boolean; enabled: boolean; runs: number;
  successful_runs: number; failed_runs: number; last_success_at: string | null; last_failure_at: string | null;
  records_processed: number; records_rejected: number; avg_duration_s: number | null; runs_per_active_day: number | null;
  schedule_minutes: number | null; freshness: { age_hours: number | null; stale_after_hours: number; stale: boolean };
}
export interface PeriodMetrics {
  period: PilotPeriod; metrics: PilotMetric[]; integration_sources: SourceReliability[]; rejection_reasons: Record<string, number>;
  recent_runs: Record<string, unknown>[];
}
export interface PilotDashboard {
  pilot: Pilot; classification_label: string; period: PeriodMetrics; issues: Record<"open" | "investigating" | "resolved" | "ignored", number>;
  readiness: { done: number; total: number };
}
export interface ComparisonRow {
  key: string; label: string; section: string; unit: MetricUnit; baseline: number | null; pilot: number | null;
  baseline_n: number | null; pilot_n: number | null; min_n: number; normalized: string | null; difference: number | null;
  difference_pp: number | null; relative_change: number | null; status: "ok" | "insufficient_data";
}
export interface PilotComparison {
  label: string; causality_statement: string; classification_label: string; baseline: PilotPeriod; pilot: PilotPeriod;
  rows: ComparisonRow[]; external_factors: Pilot["external_factors"]; notes: string[];
}
export interface PilotIssue {
  id: number; pilot_id: number; category: string; severity: "low" | "medium" | "high" | "critical"; title: string;
  description: string | null; source: string; source_ref: string | null; impact: string | null;
  status: "open" | "investigating" | "resolved" | "ignored"; assigned_to: NamedRef | null; created_by: NamedRef | null;
  resolution: string | null; resolved_by: NamedRef | null; resolved_at: string | null; created_at: string; updated_at: string;
}
export interface PilotFeedbackItem {
  id: number; pilot_id: number; user: NamedRef | null; target_type: string; recommendation_id: number | null; rating: string;
  reasons: string[]; comment: string | null; created_at: string;
}
export interface PilotFeedbackSummary { items: PilotFeedbackItem[]; by_rating: Record<string, number>; by_reason: Record<string, number>; by_target: Record<string, number> }
export interface DecisionRow {
  id: number; item: string; sku: string; created_at: string; as_of: string; risk_level: string | null; quantity: number;
  has_order: boolean; viewed: boolean; views: number; first_viewed_at: string | null;
  outcome: "approved" | "modified" | "rejected" | "expired" | "pending"; decided_by: NamedRef | null; decided_at: string | null;
  reason: string | null; hours_to_decision: number | null;
}
export interface ReadinessItem {
  key: string; label: string; mode: "auto" | "manual" | "both"; hint: string; done: boolean; auto_ok: boolean | null;
  evidence: string | null; confirmed: boolean; note: string | null; confirmed_by_id: number | null; confirmed_at: string | null;
}
export interface PilotReadiness { groups: { group: string; items: ReadinessItem[] }[]; done: number; total: number; statement: string }
export interface PilotReport {
  title: string; generated_at: string; classification: "observed" | "synthetic"; banner: string | null; result_label: string;
  comparison_label: string; causality_statement: string; limitations: string[]; conclusion: string[]; markdown: string;
  sections: { key: string; title: string; rows: ComparisonRow[]; pilot_only: PilotMetric[] }[];
  issues: { id: number; category: string; severity: string; title: string; status: string; impact: string | null; resolution: string | null }[];
}
export interface ReportSnapshot { id: number; pilot_id: number; generated_at: string; generated_by: NamedRef | null; data_classification: string }

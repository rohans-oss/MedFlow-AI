from datetime import date, datetime

from pydantic import BaseModel

from app.schemas.common import ORMModel


class ModelMetrics(BaseModel):
    mae: float | None
    rmse: float | None
    wape: float | None
    bias: float | None
    n_points: int
    actual_total: float | None = None
    predicted_total: float | None = None


class ModelVersionOut(ORMModel):
    id: int
    run_id: str
    name: str
    model_type: str
    trained_at: datetime
    data_start: date
    data_end: date
    test_start: date
    test_end: date
    horizon_days: int
    n_series: int
    n_train_rows: int
    dataset_hash: str
    features: list | None
    params: dict | None
    metrics: ModelMetrics
    feature_importance: dict | None
    is_active: bool
    notes: str | None


class ItemMetricOut(BaseModel):
    consumable_id: int
    name: str
    sku: str
    unit: str
    actual_total: float
    predicted_total: float
    mae: float
    rmse: float
    wape: float | None
    bias: float | None
    n_points: int


class ModelDetail(BaseModel):
    model: ModelVersionOut
    run_candidates: list[ModelVersionOut]
    items: list[ItemMetricOut]


class HorizonTotal(BaseModel):
    days: int
    predicted: float
    lower: float
    upper: float


class DailyForecast(BaseModel):
    date: date
    predicted: float
    lower: float
    upper: float


class HistoryPoint(BaseModel):
    date: date
    actual: float
    censored: bool


class Driver(BaseModel):
    feature: str
    label: str
    units: float


class Backtest(BaseModel):
    test_start: date
    test_end: date
    actual_total: float
    predicted_total: float
    mae: float
    rmse: float
    wape: float | None
    bias: float | None
    daily: list[dict]


# ---------------- V2B ----------------


class ProcedureTypeImpact(BaseModel):
    procedure_type_id: int
    code: str
    name: str
    department: str
    count: int
    quantity_per_procedure: float
    expected_quantity: float


class ModelComparison(BaseModel):
    """V2A (consumption) vs V2B (procedure-aware) on the same holdout, from the active model's training run."""

    v2a_model: str | None
    v2a_wape: float | None
    v2b_model: str | None
    v2b_wape: float | None
    v2b_trained: bool
    improved: bool
    active_source: str  # procedure_aware | consumption | baseline
    summary: str
    schedule_through: date | None = None
    falls_back_to_v2a_from: date | None = None
    schedule_changed_since_training: bool = False


class ProcedureImpact(BaseModel):
    horizon_days: int
    start: date
    scheduled_procedures: int
    expected_quantity: float  # Σ scheduled count × kit quantity (configured mappings)
    types: list[ProcedureTypeImpact]
    departments: list[str]
    daily: dict[str, float]  # date → kit-expected units
    v2a_forecast: float | None  # consumption-only model total for the horizon
    v2b_forecast: float | None  # procedure-aware model total for the horizon
    procedure_effect: float | None  # v2b − v2a
    model_procedure_contribution: float | None  # SHAP units attributed to procedure features (V2B)
    share_of_forecast: float | None  # expected_quantity / served forecast
    note: str


class ItemForecast(BaseModel):
    item: str
    item_id: int
    sku: str
    unit: str
    horizon_days: int
    predicted_demand: int
    lower: int
    upper: int
    interval: str
    model_version: str
    model_type: str
    trained_at: datetime
    data_through: date
    is_stale: bool
    horizons: list[HorizonTotal]
    daily: list[DailyForecast]
    history: list[HistoryPoint]
    drivers_horizon: int | None
    base_level: float | None
    drivers: list[Driver]
    explanation: list[str]
    backtest: Backtest | None
    usable_stock: int
    avg_daily_last_30: float
    days_of_cover: float | None
    # ---- V2B additions (all optional-safe; V2A fields above are unchanged) ----
    forecast_source: str = "consumption"  # procedure_aware | consumption | baseline
    scheduled_procedures: int = 0
    procedure_driven_demand: float = 0.0  # kit-expected units from scheduled procedures in the horizon
    procedure_impact: ProcedureImpact | None = None
    model_comparison: ModelComparison | None = None
    comparison_daily: list[DailyForecast] = []  # the V2A forecast when V2B is served (for the chart)
    expected_shortage: int = 0  # max(0, horizon demand − usable stock)
    stock_covers_horizon: bool = True
    days_of_stock_remaining: int | None = None  # first day cumulative forecast exceeds usable stock (None: >30)


class ForecastListItem(BaseModel):
    consumable_id: int
    name: str
    sku: str
    unit: str
    category: str | None
    predicted_demand: float
    lower: float
    upper: float
    avg_daily_last_30: float
    change_vs_recent: float | None  # forecast daily rate vs last-30-day daily rate
    usable_stock: int
    days_of_cover: float | None
    backtest_wape: float | None
    scheduled_procedures: int = 0  # V2B
    procedure_driven_demand: float = 0.0  # V2B: kit-expected units
    expected_shortage: int = 0  # V2B


class ForecastOverview(BaseModel):
    model: ModelVersionOut | None
    horizon_days: int
    is_stale: bool
    total_value: float
    items: list[ForecastListItem]
    comparison: ModelComparison | None = None  # V2B


class TrainResult(BaseModel):
    run_id: str
    active_model: str
    model_type: str
    data_start: date
    data_end: date
    test_start: date
    test_end: date
    n_series: int
    n_items: int
    candidates: dict[str, ModelMetrics]
    procedure_model: dict | None = None  # V2B: trained?, v2a/v2b WAPE, improved?
    seconds: float

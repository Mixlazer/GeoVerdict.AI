from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, TypedDict

from pydantic import BaseModel, Field


class AnalysisStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class VerdictType(str, Enum):
    recommend = "recommend"
    acceptable = "acceptable"
    avoid = "avoid"


class AddressInfo(BaseModel):
    display_name: str
    city: str
    district: str | None = None
    road: str | None = None
    house_number: str | None = None


class Poi(BaseModel):
    name: str
    kind: str
    address: str | None = None
    lat: float
    lng: float
    distance_m: float
    weight: float = 1.0


class BuildingCandidate(BaseModel):
    osm_id: str
    name: str
    building_type: str
    address: str
    category_label: str | None = None
    year_built: str | None = None
    levels: str | None = None
    lat: float
    lng: float
    distance_m: float
    source: Literal["live", "fallback"] = "live"
    match_score: float = 0.0


class Competitor(BaseModel):
    name: str
    category: str
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    distance_m: float
    saturation_weight: float


class GeoContext(BaseModel):
    address: AddressInfo
    street_type: Literal["arterial", "local", "mixed"]
    neighborhood_type: str | None = None
    neighborhood_reason: str | None = None
    transit_stops: list[Poi] = Field(default_factory=list)
    anchor_pois: list[Poi] = Field(default_factory=list)
    nearby_pois: list[Poi] = Field(default_factory=list)
    data_source: Literal["live", "fallback"] = "live"
    confidence_penalty: float = 0.0


class TrafficAssessment(BaseModel):
    level: Literal["low", "medium", "high"]
    score: float
    pedestrian_flow_score: int = Field(ge=1, le=10, default=5)
    transport_access_score: int = Field(ge=1, le=10, default=5)
    street_retail_score: int = Field(ge=1, le=10, default=5)
    transport_fit_explanation: str | None = None
    rationale: str | None = None
    drivers: list[str] = Field(default_factory=list)


class StreetInsight(BaseModel):
    street_name: str | None = None
    retail_attractiveness_score: int = Field(ge=1, le=10)
    pedestrian_flow_score: int = Field(ge=1, le=10)
    supporting_signals: list[str] = Field(default_factory=list)
    source_facts: list[str] = Field(default_factory=list)
    explanation: str | None = None


class BuildingInsight(BaseModel):
    year_built: str | None = None
    reconstruction_notes: list[str] = Field(default_factory=list)
    review_signals: list[str] = Field(default_factory=list)
    condition_summary: str | None = None
    building_risks: list[str] = Field(default_factory=list)
    source_notes: list[str] = Field(default_factory=list)


class LocationScore(BaseModel):
    overall_score: float = Field(ge=0, le=100)
    foot_traffic_estimate: Literal["low", "medium", "high"]
    competition_level: Literal["low", "medium", "high"]
    pedestrian_flow_score: int = Field(ge=1, le=10, default=5)
    transport_access_score: int = Field(ge=1, le=10, default=5)
    street_retail_score: int = Field(ge=1, le=10, default=5)
    visibility_score: float = Field(ge=0, le=100)
    infrastructure_score: float = Field(ge=0, le=100)
    accessibility_score: float = Field(ge=0, le=100)
    neighborhood_type: str | None = None
    confidence: float = Field(ge=0, le=1)
    key_risks: list[str] = Field(default_factory=list)
    key_strengths: list[str] = Field(default_factory=list)


class OptimizationSuggestion(BaseModel):
    lat: float
    lng: float
    improvement_percent: float
    distance_meters: float
    reason: str
    address: str | None = None
    same_building: bool = False


class AnalysisStepStatus(BaseModel):
    key: str
    label: str
    status: Literal["pending", "running", "done", "error"] = "pending"
    detail: str | None = None
    provider: str | None = None
    latency_ms: int | None = None
    updated_at: datetime | None = None


class AnalysisResult(BaseModel):
    request_id: str
    status: AnalysisStatus
    verdict: VerdictType | None = None
    score: LocationScore | None = None
    building_insight: BuildingInsight | None = None
    geo_context: GeoContext | None = None
    traffic: TrafficAssessment | None = None
    street_insight: StreetInsight | None = None
    competitors: list[Competitor] = Field(default_factory=list)
    optimization: OptimizationSuggestion | None = None
    reasoning: str | None = None
    steps: list[AnalysisStepStatus] = Field(default_factory=list)
    total_cost_usd: float = 0.0
    llm_metrics: dict = Field(default_factory=dict)
    provider_usage: list[dict] = Field(default_factory=list)
    llm_calls: list[dict] = Field(default_factory=list)
    a2a_handoffs: list[dict] = Field(default_factory=list)
    observability: dict = Field(default_factory=dict)
    processing_time_ms: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AnalysisRequestCreate(BaseModel):
    lat: float = Field(ge=41.0, le=82.0)
    lng: float = Field(ge=19.0, le=191.0)
    city: str
    business_type: str = Field(min_length=2, max_length=120)
    comparison_radius_m: int = Field(default=500, ge=50, le=1000)
    selected_building_id: str | None = None
    selected_building_name: str | None = None
    selected_building_address: str | None = None
    selected_building_type: str | None = None


class AnalysisRequestRead(BaseModel):
    request_id: str
    status: AnalysisStatus
    city: str
    business_type: str
    lat: float
    lng: float
    comparison_radius_m: int = 500
    selected_building_name: str | None = None
    selected_building_address: str | None = None
    selected_building_type: str | None = None
    created_at: datetime
    updated_at: datetime
    result: AnalysisResult | None = None


class UserRegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=6, max_length=120)
    full_name: str | None = Field(default=None, max_length=120)


class UserLoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=6, max_length=120)


class UserSessionRead(BaseModel):
    token: str
    user_id: int
    username: str
    full_name: str | None = None
    created_at: datetime


class HistorySummary(BaseModel):
    total_analyses: int
    average_score: float
    recommend_count: int
    acceptable_count: int
    avoid_count: int


class HistoryAnalysisRow(BaseModel):
    request_id: str
    city: str
    business_type: str
    selected_building_name: str | None = None
    selected_building_address: str | None = None
    selected_building_type: str | None = None
    overall_score: float | None = None
    verdict: str | None = None
    competition_level: str | None = None
    foot_traffic_estimate: str | None = None
    neighborhood_type: str | None = None
    confidence: float | None = None
    created_at: datetime


class HistoryResponse(BaseModel):
    summary: HistorySummary
    items: list[HistoryAnalysisRow]


class ProviderStatus(BaseModel):
    provider: str
    healthy: bool
    mode: Literal["live", "mock", "disabled"]
    detail: str
    model: str | None = None
    retries: int = 0
    last_latency_ms: float = 0.0


class OpsOverview(BaseModel):
    total_requests: int
    completed_requests: int
    avg_score: float
    recommend_share: float
    avg_latency_ms: float
    total_cost_usd: float
    active_providers: int


class TraceRecord(BaseModel):
    request_id: str
    city: str
    business_type: str
    verdict: str | None
    duration_ms: int | None
    confidence: float | None
    reasoning: str | None
    total_cost_usd: float = 0.0
    model: str | None = None
    log_url: str | None = None
    created_at: datetime


class AgentMetric(BaseModel):
    agent: str
    completed: int
    error_count: int
    avg_latency_ms: float
    success_rate: float


class CostBreakdownItem(BaseModel):
    label: str
    amount_usd: float
    share: float


class FeedbackCreate(BaseModel):
    request_id: str | None = None
    message: str = Field(min_length=3, max_length=2000)
    rating: int = Field(ge=1, le=5)


class FeedbackRead(BaseModel):
    id: int
    request_id: str | None = None
    username: str | None = None
    message: str
    rating: int
    created_at: datetime


class ProviderConfig(BaseModel):
    provider: str
    enabled: bool = False
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None


class AgentRuntimeConfig(BaseModel):
    agent: str
    provider: str
    fallback_order: list[str] = Field(default_factory=list)
    model: str | None = None


class RuntimeConfigPayload(BaseModel):
    providers: list[ProviderConfig]
    agents: list[AgentRuntimeConfig] = Field(default_factory=list)


class AnalysisState(TypedDict, total=False):
    request_id: str
    lat: float
    lng: float
    city: str
    business_type: str
    selected_building_name: str | None
    selected_building_address: str | None
    selected_building_type: str | None
    geo_context: GeoContext
    building_insight: BuildingInsight
    street_insight: StreetInsight
    traffic: TrafficAssessment
    competitors: list[Competitor]
    competition_level: str
    score: LocationScore
    verdict: VerdictType
    optimization: OptimizationSuggestion | None
    reasoning: str
    llm_metrics: dict
    provider_usage: list[dict]
    llm_calls: list[dict]
    a2a_handoffs: list[dict]
    observability: dict

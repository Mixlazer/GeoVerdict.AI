from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.agents.orchestrator import default_steps, run_analysis
from app.config import settings
from app.geo.osm_client import fetch_buildings, reverse_geocode
from app.llm.router import LLMRouter
from app.metrics.prometheus import analysis_duration_seconds, analysis_requests_total
from app.models.schemas import (
    AgentMetric,
    BuildingCandidate,
    AnalysisRequestCreate,
    AnalysisRequestRead,
    AnalysisResult,
    AnalysisStatus,
    CostBreakdownItem,
    FeedbackCreate,
    FeedbackRead,
    HistoryAnalysisRow,
    HistoryResponse,
    HistorySummary,
    OpsOverview,
    ProviderStatus,
    RuntimeConfigPayload,
    TraceRecord,
    UserLoginRequest,
    UserRegisterRequest,
    UserSessionRead,
)
from app.services.repository import AnalysisRepository

router = APIRouter()


def get_repository(request: Request) -> AnalysisRepository:
    return request.app.state.repository


def get_llm_router(request: Request) -> LLMRouter:
    return request.app.state.llm_router


async def get_current_user_optional(
    request: Request, repository: AnalysisRepository = Depends(get_repository)
):
    token = _extract_session_token(request)
    if not token:
        return None
    session = await repository.get_session(token)
    if session is None:
        return None
    return await repository.get_user(session.user_id)


async def get_current_user(
    request: Request, repository: AnalysisRepository = Depends(get_repository)
):
    user = await get_current_user_optional(request, repository)
    if user is None:
        raise HTTPException(status_code=401, detail="Требуется вход в аккаунт")
    return user


@router.get("/health")
async def health() -> dict:
    return {"status": "healthy", "time": datetime.now(timezone.utc).isoformat()}


@router.get("/geo/reverse")
async def geo_reverse(
    lat: float,
    lng: float,
    user=Depends(get_current_user),
) -> dict:
    address = await reverse_geocode(lat, lng)
    return address.model_dump()


@router.get("/geo/buildings", response_model=list[BuildingCandidate])
async def geo_buildings(
    lat: float,
    lng: float,
    city: str,
    radius: int = 120,
    user=Depends(get_current_user),
) -> list[BuildingCandidate]:
    return await fetch_buildings(lat, lng, city, radius)


@router.post("/auth/register", response_model=UserSessionRead)
async def register(
    payload: UserRegisterRequest,
    repository: AnalysisRepository = Depends(get_repository),
) -> UserSessionRead:
    username = payload.username.strip().lower()
    user = await repository.create_user(
        username=username,
        password_hash=_hash_password(username, payload.password),
        full_name=payload.full_name.strip() if payload.full_name else None,
    )
    if user is None:
        raise HTTPException(status_code=409, detail="Пользователь с таким логином уже существует")
    token = secrets.token_urlsafe(24)
    session = await repository.create_session(user.id, token)
    return UserSessionRead(
        token=token,
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        created_at=session.created_at,
    )


@router.post("/auth/login", response_model=UserSessionRead)
async def login(
    payload: UserLoginRequest,
    repository: AnalysisRepository = Depends(get_repository),
) -> UserSessionRead:
    username = payload.username.strip().lower()
    lockout = await _login_lock_status(username, repository)
    if lockout["locked"]:
        raise HTTPException(
            status_code=423,
            detail=(
                "Логин временно заблокирован после 3 неуспешных попыток за 6 часов. "
                f"Попробуйте после {lockout['until']}."
            ),
        )
    user = await repository.get_user_by_username(username)
    if user is None or user.password_hash != _hash_password(username, payload.password):
        await repository.record_login_attempt(username, success=False)
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    await repository.record_login_attempt(username, success=True)
    token = secrets.token_urlsafe(24)
    session = await repository.create_session(user.id, token)
    return UserSessionRead(
        token=token,
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        created_at=session.created_at,
    )


@router.get("/auth/me", response_model=UserSessionRead)
async def auth_me(
    request: Request,
    repository: AnalysisRepository = Depends(get_repository),
    user=Depends(get_current_user),
) -> UserSessionRead:
    token = _extract_session_token(request)
    session = await repository.get_session(token)
    return UserSessionRead(
        token=token,
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        created_at=session.created_at,
    )


@router.post("/analysis/analyze", response_model=AnalysisRequestRead)
async def create_analysis(
    payload: AnalysisRequestCreate,
    request: Request,
    repository: AnalysisRepository = Depends(get_repository),
    llm_router: LLMRouter = Depends(get_llm_router),
    user=Depends(get_current_user),
) -> AnalysisRequestRead:
    business_type = _normalize_business_type(payload.business_type)
    request_id = f"an_{uuid4().hex[:12]}"
    initial_result = AnalysisResult(
        request_id=request_id,
        status=AnalysisStatus.processing,
        steps=default_steps(),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    record = await repository.create(
        request_id=request_id,
        city=payload.city,
        business_type=business_type,
        lat=payload.lat,
        lng=payload.lng,
        status=AnalysisStatus.processing.value,
        result_payload=initial_result.model_dump(mode="json"),
        user_id=user.id,
        selected_building_name=payload.selected_building_name,
        selected_building_address=payload.selected_building_address,
        selected_building_type=payload.selected_building_type,
    )
    asyncio.create_task(
        _process_analysis(
            repository=repository,
            llm_router=llm_router,
            request_id=request_id,
            city=payload.city,
            business_type=business_type,
            lat=payload.lat,
            lng=payload.lng,
            user_id=str(user.id),
            comparison_radius_m=payload.comparison_radius_m,
            selected_building_name=payload.selected_building_name,
            selected_building_address=payload.selected_building_address,
            selected_building_type=payload.selected_building_type,
        )
    )
    return _record_to_read(record)


@router.get("/analysis/{request_id}", response_model=AnalysisRequestRead)
async def get_analysis(
    request_id: str, repository: AnalysisRepository = Depends(get_repository)
) -> AnalysisRequestRead:
    record = await repository.get(request_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return _record_to_read(record)


@router.get("/history/analyses", response_model=HistoryResponse)
async def history_analyses(
    user=Depends(get_current_user),
    repository: AnalysisRepository = Depends(get_repository),
) -> HistoryResponse:
    records = await repository.list_recent_by_user(user.id, limit=100)
    return _history_response(records)


@router.get("/history/comparison", response_model=HistoryResponse)
async def history_comparison(
    user=Depends(get_current_user),
    repository: AnalysisRepository = Depends(get_repository),
) -> HistoryResponse:
    records = await repository.list_recent_by_user(user.id, limit=100)
    return _history_response(records)


@router.post("/feedback", response_model=FeedbackRead)
async def create_feedback(
    payload: FeedbackCreate,
    request: Request,
    repository: AnalysisRepository = Depends(get_repository),
) -> FeedbackRead:
    user = await get_current_user_optional(request, repository)
    record = await repository.create_feedback(
        message=payload.message,
        rating=payload.rating,
        request_id=payload.request_id,
        user_id=user.id if user else None,
    )
    return FeedbackRead(
        id=record.id,
        request_id=record.request_id,
        username=user.username if user else None,
        message=record.message,
        rating=record.rating,
        created_at=record.created_at,
    )


@router.get("/ops/overview", response_model=OpsOverview)
async def ops_overview(
    x_ops_token: str | None = Header(default=None),
    repository: AnalysisRepository = Depends(get_repository),
    llm_router: LLMRouter = Depends(get_llm_router),
) -> OpsOverview:
    _check_ops_token(x_ops_token)
    records = await repository.list_recent()
    completed = [record for record in records if record.status == AnalysisStatus.completed.value]
    scores = [item.result_payload["score"]["overall_score"] for item in completed if item.result_payload and item.result_payload.get("score")]
    recommend_count = sum(
        1
        for item in completed
        if item.result_payload and item.result_payload.get("verdict") == "recommend"
    )
    total_cost = sum(
        item.result_payload.get("total_cost_usd", 0.0)
        for item in completed
        if item.result_payload
    )
    latencies = [item.result_payload.get("processing_time_ms", 0) for item in completed if item.result_payload]
    runtime_config = llm_router.get_runtime_config()
    active_providers = sum(1 for item in runtime_config["providers"] if item.get("enabled"))
    return OpsOverview(
        total_requests=len(records),
        completed_requests=len(completed),
        avg_score=round(sum(scores) / len(scores), 1) if scores else 0.0,
        recommend_share=round(recommend_count / len(completed), 2) if completed else 0.0,
        avg_latency_ms=round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
        total_cost_usd=round(total_cost, 4),
        active_providers=active_providers,
    )


@router.get("/ops/providers/status", response_model=list[ProviderStatus])
async def ops_provider_status(
    x_ops_token: str | None = Header(default=None),
    llm_router: LLMRouter = Depends(get_llm_router),
) -> list[ProviderStatus]:
    _check_ops_token(x_ops_token)
    raw = await llm_router.get_provider_statuses()
    return [ProviderStatus(**item) for item in raw]


@router.get("/ops/runtime-config")
async def ops_runtime_config(
    x_ops_token: str | None = Header(default=None),
    llm_router: LLMRouter = Depends(get_llm_router),
) -> dict:
    _check_ops_token(x_ops_token)
    return llm_router.get_runtime_config()


@router.post("/ops/runtime-config")
async def ops_runtime_config_update(
    payload: RuntimeConfigPayload,
    x_ops_token: str | None = Header(default=None),
    llm_router: LLMRouter = Depends(get_llm_router),
) -> dict:
    _check_ops_token(x_ops_token)
    return llm_router.update_runtime_config(payload.model_dump())


@router.get("/ops/agents/metrics", response_model=list[AgentMetric])
async def ops_agent_metrics(
    x_ops_token: str | None = Header(default=None),
    repository: AnalysisRepository = Depends(get_repository),
) -> list[AgentMetric]:
    _check_ops_token(x_ops_token)
    records = await repository.list_recent()
    aggregate: dict[str, dict] = {}
    for record in records:
        payload = record.result_payload or {}
        for step in payload.get("steps", []):
            bucket = aggregate.setdefault(
                step["label"],
                {"completed": 0, "errors": 0, "latency_total": 0, "latency_count": 0},
            )
            if step["status"] == "done":
                bucket["completed"] += 1
            if step["status"] == "error":
                bucket["errors"] += 1
            if step.get("latency_ms"):
                bucket["latency_total"] += step["latency_ms"]
                bucket["latency_count"] += 1
    metrics: list[AgentMetric] = []
    for label, bucket in aggregate.items():
        total = bucket["completed"] + bucket["errors"]
        metrics.append(
            AgentMetric(
                agent=label,
                completed=bucket["completed"],
                error_count=bucket["errors"],
                avg_latency_ms=round(bucket["latency_total"] / bucket["latency_count"], 1)
                if bucket["latency_count"]
                else 0.0,
                success_rate=round(bucket["completed"] / total, 2) if total else 1.0,
            )
        )
    return sorted(metrics, key=lambda item: item.agent)


@router.get("/ops/costs/by-user", response_model=list[CostBreakdownItem])
async def ops_costs(
    x_ops_token: str | None = Header(default=None),
    repository: AnalysisRepository = Depends(get_repository),
) -> list[CostBreakdownItem]:
    _check_ops_token(x_ops_token)
    records = await repository.list_recent()
    provider_total = sum((record.result_payload or {}).get("total_cost_usd", 0.0) for record in records)
    heuristic_total = 0.0
    combined = provider_total + heuristic_total
    return [
        CostBreakdownItem(
            label="Explainability provider",
            amount_usd=round(provider_total, 4),
            share=round(provider_total / combined, 2) if combined else 0.0,
        ),
        CostBreakdownItem(
            label="Geo / heuristic compute",
            amount_usd=0.0,
            share=0.0,
        ),
    ]


@router.get("/ops/costs/by-agent")
async def ops_costs_by_agent(
    x_ops_token: str | None = Header(default=None),
    repository: AnalysisRepository = Depends(get_repository),
) -> list[dict]:
    _check_ops_token(x_ops_token)
    records = await repository.list_recent(limit=100)
    agent_totals: dict[str, float] = {}
    for record in records:
        payload = record.result_payload or {}
        for call in payload.get("llm_calls", []):
            agent = call.get("agent", "unknown")
            agent_totals[agent] = agent_totals.get(agent, 0.0) + float(call.get("cost_usd", 0.0) or 0.0)
    grand_total = sum(agent_totals.values()) or 1.0
    return [
        {"agent": agent, "amount_usd": round(amount, 4), "share": round(amount / grand_total, 2)}
        for agent, amount in sorted(agent_totals.items())
    ]


@router.get("/ops/costs/by-user/detail")
async def ops_costs_user_detail(
    x_ops_token: str | None = Header(default=None),
    repository: AnalysisRepository = Depends(get_repository),
) -> list[dict]:
    _check_ops_token(x_ops_token)
    records = await repository.list_recent(limit=100)
    user_totals: dict[str, float] = {}
    for record in records:
        key = "anonymous"
        if record.user_id:
            user = await repository.get_user(record.user_id)
            key = user.username if user else f"user:{record.user_id}"
        user_totals[key] = user_totals.get(key, 0.0) + (record.result_payload or {}).get("total_cost_usd", 0.0)
    grand_total = sum(user_totals.values()) or 1.0
    return [
        {"user": user, "amount_usd": round(amount, 4), "share": round(amount / grand_total, 2)}
        for user, amount in sorted(user_totals.items())
    ]


@router.get("/ops/traces", response_model=list[TraceRecord])
async def ops_traces(
    x_ops_token: str | None = Header(default=None),
    repository: AnalysisRepository = Depends(get_repository),
) -> list[TraceRecord]:
    _check_ops_token(x_ops_token)
    records = await repository.list_recent(limit=30)
    traces: list[TraceRecord] = []
    for record in records:
        payload = record.result_payload or {}
        score = payload.get("score") or {}
        traces.append(
            TraceRecord(
                request_id=record.id,
                city=record.city,
                business_type=record.business_type,
                verdict=payload.get("verdict"),
                duration_ms=payload.get("processing_time_ms"),
                confidence=score.get("confidence"),
                reasoning=payload.get("reasoning"),
                total_cost_usd=payload.get("total_cost_usd", 0.0),
                model=(payload.get("llm_metrics") or {}).get("model"),
                log_url=f"/api/v1/ops/traces/{record.id}/log?token={settings.ops_token}",
                created_at=record.created_at,
            )
        )
    return traces


@router.get("/ops/quality/metrics")
async def ops_quality(
    x_ops_token: str | None = Header(default=None),
    repository: AnalysisRepository = Depends(get_repository),
) -> dict:
    _check_ops_token(x_ops_token)
    records = await repository.list_recent()
    completed = [record for record in records if record.status == AnalysisStatus.completed.value]
    confidence_values = [
        record.result_payload["score"]["confidence"]
        for record in completed
        if record.result_payload and record.result_payload.get("score")
    ]
    verdicts = [record.result_payload.get("verdict") for record in completed if record.result_payload]
    return {
        "count": len(completed),
        "avg_confidence": round(sum(confidence_values) / len(confidence_values), 2) if confidence_values else 0.0,
        "verdict_distribution": {
            "recommend": verdicts.count("recommend"),
            "acceptable": verdicts.count("acceptable"),
            "avoid": verdicts.count("avoid"),
        },
    }


@router.get("/ops/llm/metrics")
async def ops_llm_metrics(
    x_ops_token: str | None = Header(default=None),
    repository: AnalysisRepository = Depends(get_repository),
) -> dict:
    _check_ops_token(x_ops_token)
    records = await repository.list_recent(limit=50)
    completed = [record for record in records if (record.result_payload or {}).get("llm_metrics")]
    metrics = [record.result_payload["llm_metrics"] for record in completed]
    if not metrics:
        return {
            "avg_total_tokens": 0,
            "avg_generation_latency_ms": 0,
            "retry_rate": 0,
            "avg_retry_count": 0,
            "provider_calls_avg": 0,
            "live_provider_calls_avg": 0,
            "tool_calls_avg": 0,
            "tool_error_rate": 0,
            "fallback_rate": 0,
            "completion_rate": 0,
        }
    count = len(metrics)
    return {
        "avg_total_tokens": round(sum(item.get("total_tokens", 0) for item in metrics) / count, 1),
        "avg_generation_latency_ms": round(sum(item.get("generation_latency_ms", 0) for item in metrics) / count, 1),
        "retry_rate": round(sum(1 for item in metrics if item.get("retry_count", 0) > 0) / count, 2),
        "avg_retry_count": round(sum(item.get("retry_count", 0) for item in metrics) / count, 2),
        "provider_calls_avg": round(sum(item.get("provider_calls", 0) for item in metrics) / count, 1),
        "live_provider_calls_avg": round(sum(item.get("live_provider_calls", 0) for item in metrics) / count, 1),
        "tool_calls_avg": round(sum(item.get("tool_calls", 0) for item in metrics) / count, 1),
        "tool_error_rate": round(sum(item.get("tool_error_rate", 0) for item in metrics) / count, 2),
        "fallback_rate": round(sum(item.get("fallback_rate", 0) for item in metrics) / count, 2),
        "completion_rate": round(sum(item.get("completion_rate", 0) for item in metrics) / count, 2),
    }


@router.get("/ops/traces/{request_id}/log")
async def ops_trace_log(
    request_id: str,
    x_ops_token: str | None = Header(default=None),
    token: str | None = None,
    repository: AnalysisRepository = Depends(get_repository),
) -> JSONResponse:
    _check_ops_token(x_ops_token or token)
    record = await repository.get(request_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    payload = record.result_payload or {}
    steps = payload.get("steps", [])
    llm_metrics = payload.get("llm_metrics", {})
    trace_log = {
        "request_id": record.id,
        "city": record.city,
        "business_type": record.business_type,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "status": record.status,
        "selected_building_name": record.selected_building_name,
        "selected_building_address": record.selected_building_address,
        "processing_time_ms": payload.get("processing_time_ms"),
        "total_cost_usd": payload.get("total_cost_usd", 0.0),
        "confidence": ((payload.get("score") or {}).get("confidence")),
        "llm_metrics": llm_metrics,
        "provider_usage": payload.get("provider_usage", []),
        "llm_calls": payload.get("llm_calls", []),
        "a2a_handoffs": payload.get("a2a_handoffs", []),
        "observability": payload.get("observability", {}),
        "steps": steps,
        "reasoning": payload.get("reasoning"),
        "score": payload.get("score"),
        "street_insight": payload.get("street_insight"),
        "geo_context": payload.get("geo_context"),
        "optimization": payload.get("optimization"),
        "building_insight": payload.get("building_insight"),
    }
    return JSONResponse(trace_log, headers={"Content-Disposition": f'attachment; filename="{request_id}-trace.json"'})


@router.get("/ops/charts")
async def ops_charts(
    x_ops_token: str | None = Header(default=None),
    repository: AnalysisRepository = Depends(get_repository),
) -> dict:
    _check_ops_token(x_ops_token)
    records = await repository.list_recent(limit=100)
    buckets: dict[str, dict[str, float]] = {}
    for record in records:
        bucket = record.created_at.astimezone(timezone.utc).strftime("%m-%d %H:00")
        buckets.setdefault(bucket, {"requests": 0, "cost": 0.0, "latencies": []})
        buckets[bucket]["requests"] += 1
        buckets[bucket]["cost"] += (record.result_payload or {}).get("total_cost_usd", 0.0)
        if (record.result_payload or {}).get("processing_time_ms"):
            buckets[bucket]["latencies"].append((record.result_payload or {}).get("processing_time_ms", 0))
    labels = sorted(buckets.keys())[-12:]
    return {
        "labels": labels,
        "load": [buckets[label]["requests"] for label in labels],
        "cost": [round(buckets[label]["cost"], 4) for label in labels],
        "latency_avg": [_safe_avg(buckets[label]["latencies"]) for label in labels],
        "latency_p50": [_percentile(buckets[label]["latencies"], 50) for label in labels],
        "latency_p95": [_percentile(buckets[label]["latencies"], 95) for label in labels],
        "latency_p99": [_percentile(buckets[label]["latencies"], 99) for label in labels],
    }


@router.get("/ops/feedback", response_model=list[FeedbackRead])
async def ops_feedback(
    x_ops_token: str | None = Header(default=None),
    repository: AnalysisRepository = Depends(get_repository),
) -> list[FeedbackRead]:
    _check_ops_token(x_ops_token)
    feedback = await repository.list_feedback(limit=100)
    rows: list[FeedbackRead] = []
    for item in feedback:
        username = None
        if item.user_id:
            user = await repository.get_user(item.user_id)
            username = user.username if user else None
        rows.append(
            FeedbackRead(
                id=item.id,
                request_id=item.request_id,
                username=username,
                message=item.message,
                rating=item.rating,
                created_at=item.created_at,
            )
        )
    return rows


async def _process_analysis(
    repository: AnalysisRepository,
    llm_router: LLMRouter,
    request_id: str,
    city: str,
    business_type: str,
    lat: float,
    lng: float,
    user_id: str | None = None,
    comparison_radius_m: int = 500,
    selected_building_name: str | None = None,
    selected_building_address: str | None = None,
    selected_building_type: str | None = None,
) -> None:
    started = datetime.now(timezone.utc)

    async def push_steps(steps) -> None:
        record = await repository.get(request_id)
        if record is None:
            return
        payload = record.result_payload or {}
        payload["steps"] = [step.model_dump(mode="json") for step in steps]
        payload["status"] = AnalysisStatus.processing.value
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        await repository.update(request_id, result_payload=payload, status=AnalysisStatus.processing.value)

    try:
        result = await run_analysis(
            request_id=request_id,
            lat=lat,
            lng=lng,
            city=city,
            business_type=business_type,
            router=llm_router,
            step_callback=push_steps,
            user_id=user_id,
            comparison_radius_m=comparison_radius_m,
            selected_building_name=selected_building_name,
            selected_building_address=selected_building_address,
            selected_building_type=selected_building_type,
        )
        if result.optimization and not result.optimization.address:
            try:
                optimized_address = await reverse_geocode(result.optimization.lat, result.optimization.lng)
                result.optimization.address = optimized_address.display_name
                result.optimization.reason = (
                    f"{result.optimization.reason}. Адрес кандидата: {optimized_address.display_name}"
                )
            except Exception:
                pass
        analysis_duration_seconds.observe(max(0.001, result.processing_time_ms / 1000))
        analysis_requests_total.labels(
            status=result.status.value, verdict=result.verdict.value if result.verdict else "unknown"
        ).inc()
        await repository.update(
            request_id,
            status=AnalysisStatus.completed.value,
            result_payload=result.model_dump(mode="json"),
        )
    except Exception as exc:
        elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        failed = AnalysisResult(
            request_id=request_id,
            status=AnalysisStatus.failed,
            reasoning=f"Пайплайн не завершился: {exc}",
            steps=default_steps(),
            processing_time_ms=elapsed_ms,
            created_at=started,
            updated_at=datetime.now(timezone.utc),
        )
        analysis_requests_total.labels(status="failed", verdict="unknown").inc()
        await repository.update(
            request_id,
            status=AnalysisStatus.failed.value,
            result_payload=failed.model_dump(mode="json"),
            error_message=str(exc),
        )


def _record_to_read(record) -> AnalysisRequestRead:
    payload = record.result_payload
    return AnalysisRequestRead(
        request_id=record.id,
        status=AnalysisStatus(record.status),
        city=record.city,
        business_type=record.business_type,
        lat=record.lat,
        lng=record.lng,
        comparison_radius_m=(payload or {}).get("comparison_radius_m", 500),
        selected_building_name=record.selected_building_name,
        selected_building_address=record.selected_building_address,
        selected_building_type=record.selected_building_type,
        created_at=record.created_at,
        updated_at=record.updated_at,
        result=AnalysisResult.model_validate(payload) if payload else None,
    )


def _check_ops_token(token: str | None) -> None:
    from app.config import settings

    if token != settings.ops_token:
        raise HTTPException(status_code=403, detail="Ops token is invalid")


def _extract_session_token(request: Request) -> str | None:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip()
    custom_header = request.headers.get("X-Session-Token")
    return custom_header.strip() if custom_header else None


def _hash_password(username: str, password: str) -> str:
    normalized = f"{username.strip().lower()}::{password}::GeoVerdict"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _history_response(records) -> HistoryResponse:
    items: list[HistoryAnalysisRow] = []
    scores: list[float] = []
    verdicts: list[str] = []
    for record in records:
        payload = record.result_payload or {}
        score = payload.get("score") or {}
        overall_score = score.get("overall_score")
        verdict = payload.get("verdict")
        if overall_score is not None:
            scores.append(overall_score)
        if verdict:
            verdicts.append(verdict)
        items.append(
            HistoryAnalysisRow(
                request_id=record.id,
                city=record.city,
                business_type=record.business_type,
                selected_building_name=record.selected_building_name,
                selected_building_address=record.selected_building_address,
                selected_building_type=record.selected_building_type,
                overall_score=overall_score,
                verdict=verdict,
                competition_level=score.get("competition_level"),
                foot_traffic_estimate=score.get("foot_traffic_estimate"),
                neighborhood_type=score.get("neighborhood_type"),
                confidence=score.get("confidence"),
                created_at=record.created_at,
            )
        )
    return HistoryResponse(
        summary=HistorySummary(
            total_analyses=len(items),
            average_score=round(sum(scores) / len(scores), 1) if scores else 0.0,
            recommend_count=verdicts.count("recommend"),
            acceptable_count=verdicts.count("acceptable"),
            avoid_count=verdicts.count("avoid"),
        ),
        items=items,
    )


async def _login_lock_status(username: str, repository: AnalysisRepository) -> dict:
    now = datetime.now(timezone.utc)
    attempts = await repository.list_failed_login_attempts_since(username, now - timedelta(hours=6))
    if len(attempts) < 3:
        return {"locked": False, "until": None}
    last_attempt_at = attempts[0].created_at
    if last_attempt_at.tzinfo is None:
        last_attempt_at = last_attempt_at.replace(tzinfo=timezone.utc)
    lock_until = last_attempt_at + timedelta(hours=24)
    if lock_until > now:
        return {"locked": True, "until": lock_until.astimezone().strftime("%d.%m %H:%M")}
    return {"locked": False, "until": None}


def _normalize_business_type(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.strip().lower())
    suspicious_markers = (
        "<",
        ">",
        "{",
        "}",
        ";",
        "--",
        "drop table",
        "ignore previous",
        "system prompt",
        "rm -rf",
        "select *",
        "union all",
    )
    if (
        len(normalized) < 2
        or len(normalized) > 60
        or any(marker in normalized for marker in suspicious_markers)
        or not re.fullmatch(r"[a-zа-яё0-9\s\-/().,+]+", normalized, flags=re.IGNORECASE)
    ):
        raise HTTPException(status_code=422, detail="Недопустимый тип заведения")
    return normalized


def _safe_avg(values: list[float]) -> float:
    return round(sum(values) / len(values), 1) if values else 0.0


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    index = max(0, min(len(sorted_values) - 1, round((percentile / 100) * (len(sorted_values) - 1))))
    return round(sorted_values[index], 1)

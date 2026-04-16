from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone

from langgraph.graph import END, START, StateGraph

from app.agents.a2a import build_handoff
from app.config import settings
from app.geo.osm_client import (
    analyze_building_insight,
    analyze_street_insight,
    build_geo_context,
    fetch_buildings,
)
from app.llm.providers.base import LLMResponse
from app.llm.router import LLMRouter
from app.models.schemas import (
    AnalysisResult,
    AnalysisState,
    AnalysisStatus,
    AnalysisStepStatus,
    BuildingCandidate,
    BuildingInsight,
    CandidateScoreSummary,
    Competitor,
    GeoContext,
    LocationScore,
    OptimizationSuggestion,
    StreetInsight,
    TrafficAssessment,
    VerdictType,
)
from app.observability.tracing import AnalysisTraceCollector, traceable


BUSINESS_COMPETITOR_KEYS: dict[str, set[str]] = {
    "pharmacy": {"pharmacy", "chemist"},
    "coffee": {"cafe", "coffee_shop", "bakery"},
    "fastfood": {"fast_food", "restaurant", "burger"},
    "grocery": {"supermarket", "convenience", "grocery"},
    "apparel": {"clothes", "boutique", "shoes"},
    "services": {"hairdresser", "beauty", "repair"},
}


def default_steps() -> list[AnalysisStepStatus]:
    labels = [
        ("geo", "Geo-Agent"),
        ("building", "Building-Agent"),
        ("street", "Street-Agent"),
        ("competitors", "Competitor-Agent"),
        ("traffic", "Traffic-Agent"),
        ("analyst", "Analyst-Agent"),
        ("optimizer", "Radius Optimizer"),
    ]
    return [AnalysisStepStatus(key=key, label=label) for key, label in labels]


@traceable(name="geoverdict.run_analysis", run_type="chain")
async def run_analysis(
    request_id: str,
    lat: float,
    lng: float,
    city: str,
    business_type: str,
    router: LLMRouter,
    step_callback,
    user_id: str | None = None,
    comparison_radius_m: int = 500,
    selected_building_name: str | None = None,
    selected_building_address: str | None = None,
    selected_building_type: str | None = None,
    candidate_buildings: list[BuildingCandidate] | None = None,
) -> AnalysisResult:
    started = datetime.now(timezone.utc)
    steps = default_steps()
    llm_calls: list[dict] = []
    provider_usage: list[dict] = []
    a2a_handoffs: list[dict] = []
    trace = AnalysisTraceCollector(
        request_id=request_id,
        user_id=user_id,
        session_id=request_id,
        city=city,
        business_type=business_type,
    )

    async def mark_step(
        step_key: str,
        status: str,
        detail: str,
        provider: str | None = None,
        latency_ms: int | None = None,
    ) -> None:
        for step in steps:
            if step.key == step_key:
                step.status = status
                step.detail = detail
                step.provider = provider
                step.latency_ms = latency_ms
                step.updated_at = datetime.now(timezone.utc)
        await step_callback(steps)

    def record_handoff(from_agent: str, to_agent: str, payload: dict) -> None:
        handoff = build_handoff(from_agent, to_agent, payload)
        a2a_handoffs.append(handoff)
        trace.log_handoff(from_agent, to_agent, payload)

    def record_llm(agent: str, response: LLMResponse, purpose: str) -> None:
        call_meta = {
            "agent": agent,
            "purpose": purpose,
            "provider": response.provider,
            "model": response.model,
            "latency_ms": round(response.latency_ms, 1),
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "retries": response.retries,
            "cost_usd": response.cost_usd,
            "attempts": response.attempts or [],
        }
        llm_calls.append(trace.log_llm_call(agent, call_meta))
        provider_usage.append(call_meta)

    async def enrich_with_llm(agent: str, system_prompt: str, user_prompt: str, purpose: str) -> LLMResponse:
        response = await router.complete(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            agent=agent,
        )
        record_llm(agent, response, purpose)
        return response

    async def refine_score_with_llm(
        *,
        heuristic_score: LocationScore,
        heuristic_verdict: VerdictType,
        geo_context: GeoContext,
        building_insight: BuildingInsight,
        street_insight: StreetInsight,
        competitors: list[Competitor],
        competition_level: str,
        traffic: TrafficAssessment,
        subject_label: str,
        purpose: str,
    ) -> tuple[LocationScore, VerdictType]:
        response = await enrich_with_llm(
            "analyst",
            (
                "Ты оцениваешь офлайн-локацию для ритейла в России. "
                "Верни только JSON без markdown и пояснений. "
                "Не выдумывай новые факты, опирайся только на вход. "
                "Можно корректировать heuristic baseline умеренно, а не радикально. "
                "Поля JSON: overall_score, pedestrian_flow_score, transport_access_score, "
                "street_retail_score, visibility_score, infrastructure_score, accessibility_score, "
                "confidence, key_strengths, key_risks, verdict."
            ),
            (
                f"subject={subject_label}\n"
                f"business_type={business_type}\n"
                f"heuristic_baseline={json.dumps(heuristic_score.model_dump(mode='json'), ensure_ascii=False)}\n"
                f"heuristic_verdict={heuristic_verdict.value}\n"
                f"competition_level={competition_level}\n"
                f"competitors={json.dumps([_competitor_label(item) for item in competitors[:6]], ensure_ascii=False)}\n"
                f"traffic={json.dumps(traffic.model_dump(mode='json'), ensure_ascii=False)}\n"
                f"street={json.dumps(street_insight.model_dump(mode='json'), ensure_ascii=False)}\n"
                f"building={json.dumps(building_insight.model_dump(mode='json'), ensure_ascii=False)}\n"
                f"geo={{\"street_type\": \"{geo_context.street_type}\", "
                f"\"neighborhood_type\": {json.dumps(geo_context.neighborhood_type, ensure_ascii=False)}, "
                f"\"district\": {json.dumps(geo_context.address.district, ensure_ascii=False)}, "
                f"\"address\": {json.dumps(geo_context.address.display_name, ensure_ascii=False)}, "
                f"\"transit_stops\": {len(geo_context.transit_stops)}, "
                f"\"anchors\": {len(geo_context.anchor_pois)}, "
                f"\"confidence_penalty\": {geo_context.confidence_penalty}}}\n"
                "Верни строгий JSON."
            ),
            purpose,
        )
        payload = _extract_json_payload(response.content)
        if not payload:
            return heuristic_score, heuristic_verdict
        merged = heuristic_score.model_dump(mode="json")
        for field in (
            "overall_score",
            "pedestrian_flow_score",
            "transport_access_score",
            "street_retail_score",
            "visibility_score",
            "infrastructure_score",
            "accessibility_score",
            "confidence",
            "key_strengths",
            "key_risks",
        ):
            if field in payload:
                merged[field] = payload[field]
        try:
            refined_score = LocationScore.model_validate(merged)
        except Exception:
            return heuristic_score, heuristic_verdict
        verdict_raw = str(payload.get("verdict") or "").strip().lower()
        if verdict_raw in {item.value for item in VerdictType}:
            refined_verdict = VerdictType(verdict_raw)
        else:
            refined_verdict = (
                VerdictType.recommend
                if refined_score.overall_score >= 72
                else VerdictType.acceptable
                if refined_score.overall_score >= 54
                else VerdictType.avoid
            )
        return refined_score, refined_verdict

    @traceable(name="geoverdict.geo_agent", run_type="chain")
    async def geo_node(state: AnalysisState) -> dict:
        geo_started = datetime.now(timezone.utc)
        await mark_step("geo", "running", "Собираем адрес, POI, транспорт и районный контекст")
        with trace.span("geo-agent", input_data={"lat": lat, "lng": lng, "city": city}):
            geo_context = await build_geo_context(lat, lng, city, preferred_address=selected_building_address)
        anchor_preview = ", ".join(item.name for item in geo_context.anchor_pois[:3]) or "без выраженного коммерческого контура"
        await mark_step(
            "geo",
            "done",
            (
                f"Контекст: {geo_context.neighborhood_type or 'район не определён'}, "
                f"транспорт: {len(geo_context.transit_stops)}, "
                f"контур: {anchor_preview}, источник: {geo_context.data_source}"
            ),
            latency_ms=_latency_ms(geo_started),
        )
        record_handoff(
            "geo",
            "building",
            {
                "district": geo_context.address.district,
                "road": geo_context.address.road,
                "street_type": geo_context.street_type,
                "neighborhood_type": geo_context.neighborhood_type,
            },
        )
        return {"geo_context": geo_context}

    @traceable(name="geoverdict.building_agent", run_type="chain")
    async def building_node(state: AnalysisState) -> dict:
        building_started = datetime.now(timezone.utc)
        geo_context = state["geo_context"]
        await mark_step("building", "running", "Проверяем состояние здания и открытые сигналы")
        with trace.span("building-agent", input_data={"address": geo_context.address.display_name}):
            try:
                building_insight = await asyncio.wait_for(
                    analyze_building_insight(
                        lat=lat,
                        lng=lng,
                        name=selected_building_name or (geo_context.address.display_name.split(",")[0] if geo_context.address.display_name else None),
                        address=selected_building_address or geo_context.address.display_name,
                        city=city,
                        include_web_search=True,
                    ),
                    timeout=16,
                )
            except TimeoutError:
                building_insight = BuildingInsight(
                    condition_summary="Справочная оценка: базовые сигналы собраны, но расширенный веб-поиск по зданию не успел завершиться.",
                    source_notes=["timeout fallback"],
                )
        summary_response = await enrich_with_llm(
            "building",
            "Сделай одно короткое русскоязычное резюме о рисках и состоянии здания для ритейла. Без markdown, без звездочек и без заголовков.",
            (
                f"address={selected_building_address or geo_context.address.display_name}; building_name={selected_building_name}; year={building_insight.year_built}; "
                f"reconstruction_notes={building_insight.reconstruction_notes}; review_signals={building_insight.review_signals}; "
                f"building_risks={building_insight.building_risks}; current_summary={building_insight.condition_summary}"
            ),
            "building-summary",
        )
        if summary_response.content.strip():
            building_insight.condition_summary = _clean_text(summary_response.content)
        await mark_step(
            "building",
            "done",
            building_insight.condition_summary or "Собрали сигналы по зданию",
            provider=summary_response.provider,
            latency_ms=_latency_ms(building_started),
        )
        record_handoff(
            "building",
            "street",
            {
                "building_summary": building_insight.condition_summary,
                "building_risks": building_insight.building_risks[:2],
            },
        )
        return {"building_insight": building_insight}

    @traceable(name="geoverdict.street_agent", run_type="chain")
    async def street_node(state: AnalysisState) -> dict:
        street_started = datetime.now(timezone.utc)
        geo_context = state["geo_context"]
        await mark_step("street", "running", "Ищем сигналы по улице и торговой привлекательности")
        with trace.span("street-agent", input_data={"road": geo_context.address.road, "district": geo_context.address.district}):
            try:
                street_insight = await asyncio.wait_for(analyze_street_insight(geo_context, business_type), timeout=10)
            except TimeoutError:
                street_insight = StreetInsight(
                    street_name=geo_context.address.road or "улица без названия",
                    retail_attractiveness_score=5,
                    pedestrian_flow_score=5,
                    supporting_signals=[geo_context.neighborhood_reason or "быстрый fallback по уличному контексту"],
                    source_facts=[],
                    explanation="Уличный контекст оценён по базовым городским сигналам без расширенного веб-поиска.",
                )
        explanation_response = await enrich_with_llm(
            "street",
            "Сделай короткое русскоязычное объяснение привлекательности именно той улицы, на которой стоит выбранный объект. Используй факты из web/wiki. Без markdown.",
            (
                f"business_type={business_type}; street={street_insight.street_name}; "
                f"retail_score={street_insight.retail_attractiveness_score}; pedestrian_score={street_insight.pedestrian_flow_score}; "
                f"signals={street_insight.supporting_signals}; source_facts={street_insight.source_facts}; existing={street_insight.explanation}"
            ),
            "street-explanation",
        )
        if explanation_response.content.strip():
            street_insight.explanation = _clean_text(explanation_response.content)
        await mark_step(
            "street",
            "done",
            (
                f"{street_insight.street_name or 'улица'}: {street_insight.retail_attractiveness_score}/10; "
                f"факты: {', '.join((street_insight.source_facts or street_insight.supporting_signals)[:2]) or 'нет дополнительных фактов'}"
            ),
            provider=explanation_response.provider,
            latency_ms=_latency_ms(street_started),
        )
        record_handoff(
            "street",
            "competitors",
            {
                "street_name": street_insight.street_name,
                "retail_score": street_insight.retail_attractiveness_score,
                "pedestrian_score": street_insight.pedestrian_flow_score,
            },
        )
        return {"street_insight": street_insight}

    @traceable(name="geoverdict.competitor_agent", run_type="chain")
    async def competitors_node(state: AnalysisState) -> dict:
        comp_started = datetime.now(timezone.utc)
        geo_context = state["geo_context"]
        await mark_step("competitors", "running", "Оцениваем конкурентное давление в шаговой доступности")
        with trace.span("competitor-agent", input_data={"business_type": business_type}):
            competitors, competition_level = analyse_competition(geo_context, business_type)
        await mark_step(
            "competitors",
            "done",
            (
                f"Релевантных конкурентов: {len(competitors)}"
                + (f" · {', '.join(_competitor_label(item) for item in competitors[:3])}" if competitors else "")
            ),
            latency_ms=_latency_ms(comp_started),
        )
        record_handoff("competitors", "traffic", {"competition_level": competition_level, "competitor_count": len(competitors)})
        return {"competitors": competitors, "competition_level": competition_level}

    @traceable(name="geoverdict.traffic_agent", run_type="chain")
    async def traffic_node(state: AnalysisState) -> dict:
        traffic_started = datetime.now(timezone.utc)
        geo_context = state["geo_context"]
        street_insight = state["street_insight"]
        await mark_step("traffic", "running", "Взвешиваем пешеходный поток и транспортную доступность")
        with trace.span("traffic-agent", input_data={"business_type": business_type}):
            traffic = estimate_traffic(geo_context, street_insight, business_type)
        await mark_step(
            "traffic",
            "done",
            (
                f"Поток {traffic.pedestrian_flow_score}/10, "
                f"транспорт {traffic.transport_access_score}/10, "
                f"улица {traffic.street_retail_score}/10 · {traffic.rationale or 'без дополнительного пояснения'}"
            ),
            latency_ms=_latency_ms(traffic_started),
        )
        record_handoff(
            "traffic",
            "analyst",
            {
                "pedestrian_flow_score": traffic.pedestrian_flow_score,
                "transport_access_score": traffic.transport_access_score,
                "street_retail_score": traffic.street_retail_score,
            },
        )
        return {"traffic": traffic}

    @traceable(name="geoverdict.analyst_agent", run_type="chain")
    async def analyst_node(state: AnalysisState) -> dict:
        analyst_started = datetime.now(timezone.utc)
        geo_context = state["geo_context"]
        building_insight = state["building_insight"]
        street_insight = state["street_insight"]
        competitors = state["competitors"]
        competition_level = state["competition_level"]
        traffic = state["traffic"]
        await mark_step("analyst", "running", "Собираем итоговый скоринг и бизнес-вердикт")
        with trace.span("analyst-agent", input_data={"city": city, "business_type": business_type}):
            score, verdict = score_location(
                geo_context=geo_context,
                competitors=competitors,
                competition_level=competition_level,
                traffic=traffic,
                building_insight=building_insight,
                street_insight=street_insight,
                business_type=business_type,
            )
            score, verdict = await refine_score_with_llm(
                heuristic_score=score,
                heuristic_verdict=verdict,
                geo_context=geo_context,
                building_insight=building_insight,
                street_insight=street_insight,
                competitors=competitors,
                competition_level=competition_level,
                traffic=traffic,
                subject_label=selected_building_address or geo_context.address.display_name,
                purpose="location-score",
            )
            llm_summary = await enrich_with_llm(
                "analyst",
                "Сделай короткое бизнес-объяснение на русском языке.",
                (
                    f"city={city}; business_type={business_type}; overall_score={int(score.overall_score)}; "
                    f"verdict={verdict.value}; pedestrian_flow_score={score.pedestrian_flow_score}; "
                    f"transport_access_score={score.transport_access_score}; "
                    f"street_retail_score={score.street_retail_score}; neighborhood={score.neighborhood_type}; "
                    f"strengths={score.key_strengths}; risks={score.key_risks}; "
                    f"building_summary={building_insight.condition_summary}; street={street_insight.explanation}"
                ),
                "final-verdict",
            )
        await mark_step(
            "analyst",
            "done",
            "Скоринг и объяснение готовы",
            provider=llm_summary.provider,
            latency_ms=_latency_ms(analyst_started),
        )
        record_handoff("analyst", "optimizer", {"overall_score": score.overall_score, "verdict": verdict.value})
        return {"score": score, "verdict": verdict, "reasoning": llm_summary.content}

    @traceable(name="geoverdict.optimizer_agent", run_type="chain")
    async def optimizer_node(state: AnalysisState) -> dict:
        optimizer_started = datetime.now(timezone.utc)
        geo_context = state["geo_context"]
        building_insight = state["building_insight"]
        street_insight = state["street_insight"]
        traffic = state["traffic"]
        score = state["score"]
        await mark_step("optimizer", "running", "Сканируем соседние точки и сравниваем микрорасположение")
        with trace.span("optimizer-agent", input_data={"base_score": score.overall_score}):
            optimization, candidate_scores = await find_better_location(
                lat=lat,
                lng=lng,
                city=city,
                business_type=business_type,
                base_score=score,
                base_geo_context=geo_context,
                base_building_insight=building_insight,
                base_street_insight=street_insight,
                base_traffic=traffic,
                scan_radius_m=comparison_radius_m,
                selected_building_address=selected_building_address,
                selected_building_name=selected_building_name,
                selected_building_type=selected_building_type,
                candidate_buildings=candidate_buildings or [],
                score_refiner=refine_score_with_llm,
            )
            optimizer_provider = None
            if optimization and not optimization.same_building:
                optimizer_reason = await enrich_with_llm(
                    "optimizer",
                    "Сделай короткое и честное объяснение, почему соседняя точка лучше исходной.",
                    (
                        f"business_type={business_type}; current_score={score.overall_score}; "
                        f"candidate_address={optimization.address}; candidate_improvement={optimization.improvement_percent}; "
                        f"candidate_reason={optimization.reason}"
                    ),
                    "optimizer-reason",
                )
                optimization.reason = optimizer_reason.content.strip() or optimization.reason
                optimizer_provider = optimizer_reason.provider
        await mark_step(
            "optimizer",
            "done",
            (
                "Текущее здание стало лучшим среди просчитанных кандидатов"
                if optimization and optimization.same_building
                else "Найдена более сильная точка"
                if optimization
                else "Надёжного улучшения в заданном радиусе не найдено"
            ),
            provider=optimizer_provider,
            latency_ms=_latency_ms(optimizer_started),
        )
        return {"optimization": optimization, "candidate_scores": candidate_scores}

    graph = StateGraph(AnalysisState)
    graph.add_node("geo", geo_node)
    graph.add_node("building", building_node)
    graph.add_node("street", street_node)
    graph.add_node("competitors", competitors_node)
    graph.add_node("traffic", traffic_node)
    graph.add_node("analyst", analyst_node)
    graph.add_node("optimizer", optimizer_node)
    graph.add_edge(START, "geo")
    graph.add_edge("geo", "building")
    graph.add_edge("building", "street")
    graph.add_edge("street", "competitors")
    graph.add_edge("competitors", "traffic")
    graph.add_edge("traffic", "analyst")
    graph.add_edge("analyst", "optimizer")
    graph.add_edge("optimizer", END)
    compiled = graph.compile()
    final_state = await compiled.ainvoke(
        {
            "request_id": request_id,
            "lat": lat,
            "lng": lng,
            "city": city,
            "business_type": business_type,
            "comparison_radius_m": comparison_radius_m,
            "selected_building_name": selected_building_name,
            "selected_building_address": selected_building_address,
            "selected_building_type": selected_building_type,
        }
    )

    finished = datetime.now(timezone.utc)
    total_cost = round(sum(item.get("cost_usd", 0.0) for item in provider_usage), 6)
    total_input_tokens = sum(item.get("input_tokens", 0) for item in provider_usage)
    total_output_tokens = sum(item.get("output_tokens", 0) for item in provider_usage)
    retry_count = sum(item.get("retries", 0) for item in provider_usage)
    generation_latencies = [item.get("latency_ms", 0.0) for item in provider_usage if item.get("latency_ms") is not None]
    fallbacks = [
        call
        for call in provider_usage
        if any(attempt.get("status") in {"error", "unhealthy"} for attempt in (call.get("attempts") or [])[:-1])
    ]

    return AnalysisResult(
        request_id=request_id,
        status=AnalysisStatus.completed,
        verdict=final_state["verdict"],
        score=final_state["score"],
        building_insight=final_state["building_insight"],
        geo_context=final_state["geo_context"],
        traffic=final_state["traffic"],
        street_insight=final_state["street_insight"],
        competitors=final_state["competitors"],
        candidate_scores=final_state.get("candidate_scores", []),
        optimization=final_state.get("optimization"),
        reasoning=final_state["reasoning"],
        steps=steps,
        total_cost_usd=total_cost,
        llm_metrics={
            "provider": provider_usage[-1]["provider"] if provider_usage else "mock",
            "model": provider_usage[-1]["model"] if provider_usage else "heuristic-summary",
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "total_tokens": total_input_tokens + total_output_tokens,
            "retry_count": retry_count,
            "provider_calls": len(provider_usage),
            "live_provider_calls": sum(1 for item in provider_usage if item.get("provider") != "mock"),
            "generation_latency_ms": round(sum(generation_latencies) / len(generation_latencies), 1) if generation_latencies else 0.0,
            "error_rate": round(
                sum(1 for item in provider_usage for attempt in item.get("attempts") or [] if attempt.get("status") == "error")
                / max(1, sum(len(item.get("attempts") or []) for item in provider_usage)),
                2,
            ),
            "tool_calls": len(steps),
            "tool_error_rate": 0.0,
            "fallback_rate": round(len(fallbacks) / max(1, len(provider_usage)), 2),
            "completion_rate": 1.0,
        },
        provider_usage=provider_usage,
        llm_calls=llm_calls,
        a2a_handoffs=a2a_handoffs,
        observability=trace.dump(),
        processing_time_ms=int((finished - started).total_seconds() * 1000),
        created_at=started,
        updated_at=finished,
    )


def analyse_competition(geo_context: GeoContext, business_type: str) -> tuple[list[Competitor], str]:
    keys = BUSINESS_COMPETITOR_KEYS.get(business_type, set())
    detected: list[Competitor] = []
    for poi in geo_context.nearby_pois:
        if poi.kind in keys:
            detected.append(
                Competitor(
                    name=poi.name,
                    category=poi.kind,
                    address=getattr(poi, "address", None),
                    lat=poi.lat,
                    lng=poi.lng,
                    distance_m=poi.distance_m,
                    saturation_weight=max(0.5, 1.6 - min(poi.distance_m, 300) / 250),
                )
            )
    if len(detected) >= 5:
        level = "high"
    elif len(detected) >= 2:
        level = "medium"
    else:
        level = "low"
    return sorted(detected, key=lambda item: item.distance_m)[:8], level


def estimate_traffic(
    geo_context: GeoContext,
    street_insight: StreetInsight,
    business_type: str,
) -> TrafficAssessment:
    profile = _business_profile(business_type)
    transit_factor = min(10, 2 + len(geo_context.transit_stops) * 2 + (1 if geo_context.street_type == "arterial" else 0))
    anchors_factor = min(10, 2 + len(geo_context.anchor_pois))
    major_road_bonus = 2 if geo_context.street_type == "arterial" else 1 if geo_context.street_type == "mixed" else 0
    pedestrian_flow_score = max(
        1,
        min(10, round((anchors_factor * 0.55) + (street_insight.pedestrian_flow_score * 0.35) + major_road_bonus)),
    )
    transport_access_score = max(
        1,
        min(
            10,
            round(
                transit_factor * profile["transit_multiplier"]
                + major_road_bonus * profile["road_multiplier"]
                + 1
            ),
        ),
    )
    street_retail_score = max(1, min(10, round((street_insight.retail_attractiveness_score * 0.7) + (anchors_factor * 0.3))))

    raw = (
        pedestrian_flow_score * 5.8
        + transport_access_score * 3.1
        + street_retail_score * 2.4
        - geo_context.confidence_penalty * 14
    )
    score = max(20.0, min(95.0, round(raw, 1)))
    level = "high" if pedestrian_flow_score >= 8 else "medium" if pedestrian_flow_score >= 5 else "low"
    drivers = [
        f"пешеходный поток {pedestrian_flow_score}/10",
        f"торговая привлекательность улицы {street_retail_score}/10",
        f"транспортная доступность {transport_access_score}/10",
    ]
    if geo_context.neighborhood_reason:
        drivers.append(geo_context.neighborhood_reason)
    return TrafficAssessment(
        level=level,
        score=score,
        pedestrian_flow_score=pedestrian_flow_score,
        transport_access_score=transport_access_score,
        street_retail_score=street_retail_score,
        transport_fit_explanation=profile["transport_explanation"],
        rationale=_traffic_rationale(geo_context, street_insight, business_type),
        drivers=drivers,
    )


def score_location(
    geo_context: GeoContext,
    competitors: list[Competitor],
    competition_level: str,
    traffic: TrafficAssessment,
    building_insight: BuildingInsight,
    street_insight: StreetInsight,
    business_type: str,
) -> tuple[LocationScore, VerdictType]:
    visibility = 88 if geo_context.street_type == "arterial" else 72 if geo_context.street_type == "mixed" else 60
    infrastructure = min(95.0, 38 + len(geo_context.anchor_pois) * 6 + len(geo_context.transit_stops) * 5)
    accessibility = min(95.0, 35 + traffic.transport_access_score * 6.2)
    competition_penalty = {"low": 6, "medium": 16, "high": 28}[competition_level]
    building_penalty = min(1.4, len(building_insight.building_risks) * 0.45)
    neighborhood_score = {"центр": 88, "спальный район": 72, "окраина": 58, "пригород": 44}.get(
        geo_context.neighborhood_type or "",
        60,
    )
    overall = (
        (traffic.pedestrian_flow_score * 10) * 0.38
        + (traffic.transport_access_score * 10) * 0.16
        + (traffic.street_retail_score * 10) * 0.16
        + visibility * 0.12
        + infrastructure * 0.10
        + accessibility * 0.04
        + neighborhood_score * 0.04
        - competition_penalty
        - building_penalty
        - geo_context.confidence_penalty * 8
    )
    overall = round(max(15.0, min(95.0, overall)), 1)
    confidence = max(0.48, min(0.95, 0.9 - geo_context.confidence_penalty - max(0, len(competitors) - 4) * 0.02))
    strengths = [
        f"пешеходный поток {traffic.pedestrian_flow_score}/10",
        f"торговая привлекательность улицы {street_insight.retail_attractiveness_score}/10",
        f"район: {geo_context.neighborhood_type or 'городской микс'}",
    ]
    if geo_context.transit_stops:
        strengths.append("есть входящий поток от остановок и/или станций")
    risks = []
    if competition_level != "low":
        risks.append(f"конкуренция вокруг точки: {competition_level}")
    if street_insight.retail_attractiveness_score <= 4:
        risks.append("улица выглядит слабой для торговой витрины без сильного бренда и навигации")
    if traffic.pedestrian_flow_score <= 4:
        risks.append("пешеходный поток низкий и может не вытянуть повседневный спрос")
    if traffic.transport_access_score <= 4:
        risks.append(traffic.transport_fit_explanation or "доступность транспорта ниже оптимума для этого формата")
    risks.extend(building_insight.building_risks[:1])
    verdict = VerdictType.recommend if overall >= 72 else VerdictType.acceptable if overall >= 54 else VerdictType.avoid
    return (
        LocationScore(
            overall_score=overall,
            foot_traffic_estimate=traffic.level,
            competition_level=competition_level,
            pedestrian_flow_score=traffic.pedestrian_flow_score,
            transport_access_score=traffic.transport_access_score,
            street_retail_score=traffic.street_retail_score,
            visibility_score=round(visibility, 1),
            infrastructure_score=round(infrastructure, 1),
            accessibility_score=round(accessibility, 1),
            neighborhood_type=geo_context.neighborhood_type,
            confidence=round(confidence, 2),
            key_risks=risks,
            key_strengths=strengths,
        ),
        verdict,
    )


async def find_better_location(
    lat: float,
    lng: float,
    city: str,
    business_type: str,
    base_score: LocationScore,
    base_geo_context: GeoContext,
    base_building_insight: BuildingInsight,
    base_street_insight: StreetInsight,
    base_traffic: TrafficAssessment,
    scan_radius_m: int = 500,
    selected_building_address: str | None = None,
    selected_building_name: str | None = None,
    selected_building_type: str | None = None,
    candidate_buildings: list[BuildingCandidate] | None = None,
    score_refiner=None,
) -> tuple[OptimizationSuggestion | None, list[CandidateScoreSummary]]:
    nearby_buildings = candidate_buildings or await fetch_buildings(lat, lng, city, radius_m=max(120, scan_radius_m))
    candidates = [
        item
        for item in nearby_buildings
        if item.distance_m >= 12
        and (not selected_building_address or item.address.strip().lower() != selected_building_address.strip().lower())
    ][:12]
    if not candidates:
        selected_summary = CandidateScoreSummary(
            osm_id="selected-building",
            name=selected_building_name or (selected_building_address or "Выбранное здание"),
            address=selected_building_address or "Адрес не указан",
            building_type=selected_building_type or "Здание",
            lat=lat,
            lng=lng,
            distance_m=0.0,
            overall_score=base_score.overall_score,
            pedestrian_flow_score=base_score.pedestrian_flow_score,
            transport_access_score=base_score.transport_access_score,
            street_retail_score=base_score.street_retail_score,
            verdict=VerdictType.recommend if base_score.overall_score >= 72 else VerdictType.acceptable if base_score.overall_score >= 54 else VerdictType.avoid,
            is_selected=True,
            is_best=True,
            reason="Просчитана только исходная точка",
        )
        return (
            OptimizationSuggestion(
                lat=lat,
                lng=lng,
                improvement_percent=0.0,
                distance_meters=0.0,
                reason="Текущее здание набрало максимальный score среди доступных для сравнения точек.",
                address=selected_building_address,
                same_building=True,
            ),
            [selected_summary],
        )

    async def evaluate_candidate(candidate: BuildingCandidate) -> tuple[BuildingCandidate, LocationScore, TrafficAssessment, GeoContext, str]:
        geo_context = await build_geo_context(candidate.lat, candidate.lng, city, preferred_address=candidate.address)
        building_insight = await analyze_building_insight(
            candidate.lat,
            candidate.lng,
            candidate.name,
            candidate.address,
            city,
            include_web_search=False,
        )
        street_insight = await analyze_street_insight(geo_context, business_type)
        competitors, competition_level = analyse_competition(geo_context, business_type)
        traffic = estimate_traffic(geo_context, street_insight, business_type)
        score, _ = score_location(
            geo_context=geo_context,
            competitors=competitors,
            competition_level=competition_level,
            traffic=traffic,
            building_insight=building_insight,
            street_insight=street_insight,
            business_type=business_type,
        )
        if score_refiner is not None:
            try:
                score, _ = await asyncio.wait_for(
                    score_refiner(
                        heuristic_score=score,
                        heuristic_verdict=VerdictType.recommend
                        if score.overall_score >= 72
                        else VerdictType.acceptable
                        if score.overall_score >= 54
                        else VerdictType.avoid,
                        geo_context=geo_context,
                        building_insight=building_insight,
                        street_insight=street_insight,
                        competitors=competitors,
                        competition_level=competition_level,
                        traffic=traffic,
                        subject_label=candidate.address,
                        purpose="candidate-score",
                    ),
                    timeout=4,
                )
            except Exception:
                pass
        reason = _build_candidate_reason(
            base_score,
            score,
            base_traffic,
            traffic,
            base_street_insight,
            street_insight,
            base_geo_context,
            geo_context,
        )
        return candidate, score, traffic, geo_context, reason

    async def evaluate_candidate_safe(candidate: BuildingCandidate):
        try:
            return await asyncio.wait_for(evaluate_candidate(candidate), timeout=18)
        except Exception:
            fallback_score, fallback_traffic = _fast_candidate_score(candidate, base_score, base_traffic)
            return (
                candidate,
                fallback_score,
                fallback_traffic,
                base_geo_context,
                "Быстрая оценка кандидата без полного гео-обхода",
            )

    evaluated = await asyncio.gather(*(evaluate_candidate_safe(candidate) for candidate in candidates))
    candidate_summaries: list[CandidateScoreSummary] = [
        CandidateScoreSummary(
            osm_id="selected-building",
            name=selected_building_name or (selected_building_address or "Выбранное здание"),
            address=selected_building_address or "Адрес не указан",
            building_type=selected_building_type or "Здание",
            lat=lat,
            lng=lng,
            distance_m=0.0,
            overall_score=base_score.overall_score,
            pedestrian_flow_score=base_score.pedestrian_flow_score,
            transport_access_score=base_score.transport_access_score,
            street_retail_score=base_score.street_retail_score,
            verdict=VerdictType.recommend if base_score.overall_score >= 72 else VerdictType.acceptable if base_score.overall_score >= 54 else VerdictType.avoid,
            is_selected=True,
            is_best=False,
            reason="Исходная выбранная точка",
        )
    ]
    best: OptimizationSuggestion | None = None
    best_candidate_record: tuple[BuildingCandidate, CandidateScoreSummary] | None = None
    best_overall_score = base_score.overall_score
    best_summary_index = 0
    for item in evaluated:
        candidate, candidate_score, candidate_traffic, candidate_geo_context, reason = item
        candidate_verdict = VerdictType.recommend if candidate_score.overall_score >= 72 else VerdictType.acceptable if candidate_score.overall_score >= 54 else VerdictType.avoid
        candidate_summaries.append(
            CandidateScoreSummary(
                osm_id=candidate.osm_id,
                name=candidate.name,
                address=candidate.address,
                building_type=candidate.building_type,
                lat=candidate.lat,
                lng=candidate.lng,
                distance_m=candidate.distance_m,
                overall_score=candidate_score.overall_score,
                pedestrian_flow_score=candidate_score.pedestrian_flow_score,
                transport_access_score=candidate_score.transport_access_score,
                street_retail_score=candidate_score.street_retail_score,
                verdict=candidate_verdict,
                is_selected=False,
                reason=reason,
            )
        )
        improvement = ((candidate_score.overall_score - base_score.overall_score) / max(base_score.overall_score, 1)) * 100
        if candidate_score.overall_score > best_overall_score:
            best_overall_score = candidate_score.overall_score
            best_summary_index = len(candidate_summaries) - 1
            best_candidate_record = (candidate, candidate_summaries[-1])
        candidate_has_frontage_edge = (
            candidate_geo_context.street_type in {"arterial", "mixed"} and base_geo_context.street_type == "local"
        ) or (
            candidate_score.visibility_score > base_score.visibility_score
            and candidate_traffic.pedestrian_flow_score >= base_traffic.pedestrian_flow_score
        )
        min_required_improvement = settings.optimizer_min_improvement_pct
        if candidate_has_frontage_edge:
            min_required_improvement = max(4.0, min_required_improvement - 3.0)
        if improvement < min_required_improvement:
            continue
        if (
            candidate_score.visibility_score < base_score.visibility_score
            and candidate_traffic.pedestrian_flow_score <= base_traffic.pedestrian_flow_score
            and candidate_geo_context.street_type == "local"
        ):
            continue
        suggestion = OptimizationSuggestion(
            lat=candidate.lat,
            lng=candidate.lng,
            improvement_percent=round(improvement, 1),
            distance_meters=round(candidate.distance_m, 1),
            reason=reason,
            address=candidate.address,
            same_building=False,
        )
        if best is None or suggestion.improvement_percent > best.improvement_percent:
            best = suggestion
    if candidate_summaries:
        candidate_summaries[best_summary_index].is_best = True
    candidate_summaries = sorted(candidate_summaries, key=lambda item: item.overall_score, reverse=True)
    if candidate_summaries and candidate_summaries[0].is_selected:
        return (
            OptimizationSuggestion(
                lat=lat,
                lng=lng,
                improvement_percent=0.0,
                distance_meters=0.0,
                reason="Текущее здание набрало максимальный score среди всех просчитанных кандидатов.",
                address=selected_building_address,
                same_building=True,
            ),
            candidate_summaries,
        )
    if best is None and best_candidate_record is not None:
        top_candidate, top_summary = best_candidate_record
        improvement = ((top_summary.overall_score - base_score.overall_score) / max(base_score.overall_score, 1)) * 100
        best = OptimizationSuggestion(
            lat=top_candidate.lat,
            lng=top_candidate.lng,
            improvement_percent=round(improvement, 1),
            distance_meters=round(top_candidate.distance_m, 1),
            reason=top_summary.reason or "Эта точка набрала максимальный score среди просчитанных кандидатов.",
            address=top_candidate.address,
            same_building=False,
        )
    return best, candidate_summaries


def _build_candidate_reason(
    base_score: LocationScore,
    candidate_score: LocationScore,
    base_traffic: TrafficAssessment,
    candidate_traffic: TrafficAssessment,
    base_street: StreetInsight,
    candidate_street: StreetInsight,
    base_geo_context: GeoContext,
    candidate_geo_context: GeoContext,
) -> str:
    reasons: list[str] = []
    if candidate_traffic.pedestrian_flow_score > base_traffic.pedestrian_flow_score:
        reasons.append("выше входящий пешеходный поток")
    if candidate_traffic.transport_access_score > base_traffic.transport_access_score:
        reasons.append("лучше транспортная доступность")
    if candidate_street.retail_attractiveness_score > base_street.retail_attractiveness_score:
        reasons.append("улица сильнее по торговому сценарию")
    if candidate_score.visibility_score > base_score.visibility_score:
        if candidate_geo_context.street_type in {"arterial", "mixed"} and base_geo_context.street_type == "local":
            reasons.append("точка лучше сидит на красной линии и заметнее относительно улицы")
        else:
            reasons.append("заметнее посадка относительно улицы")
    if not reasons:
        reasons.append("микропозиция выглядит устойчивее по совокупному скорингу")
    return ", ".join(reasons)


def _fast_candidate_score(
    candidate: BuildingCandidate,
    base_score: LocationScore,
    base_traffic: TrafficAssessment,
) -> tuple[LocationScore, TrafficAssessment]:
    building_type = (candidate.building_type or "").lower()
    frontage_bonus = 0.0
    if any(token in building_type for token in ("коммер", "торгов", "офис", "сервис")):
        frontage_bonus += 2.5
    if any(token in building_type for token in ("апартамент", "жил", "дом")):
        frontage_bonus -= 1.0
    distance_penalty = min(10.0, candidate.distance_m / 35)
    overall = max(15.0, min(95.0, round(base_score.overall_score + frontage_bonus - distance_penalty, 1)))
    pedestrian = max(1, min(10, round(base_traffic.pedestrian_flow_score + frontage_bonus * 0.2 - distance_penalty * 0.08)))
    transport = max(1, min(10, round(base_traffic.transport_access_score + frontage_bonus * 0.15 - distance_penalty * 0.04)))
    retail = max(1, min(10, round(base_traffic.street_retail_score + frontage_bonus * 0.2 - distance_penalty * 0.05)))
    traffic = TrafficAssessment(
        level="high" if pedestrian >= 8 else "medium" if pedestrian >= 5 else "low",
        score=max(20.0, min(95.0, round(overall + 2.0, 1))),
        pedestrian_flow_score=pedestrian,
        transport_access_score=transport,
        street_retail_score=retail,
        transport_fit_explanation=base_traffic.transport_fit_explanation,
        rationale="Быстрая оценка по расстоянию, типу здания и базовому профилю точки",
        drivers=[f"быстрый fallback для кандидата {candidate.address}"],
    )
    score = LocationScore.model_validate(
        {
            **base_score.model_dump(mode="json"),
            "overall_score": overall,
            "pedestrian_flow_score": pedestrian,
            "transport_access_score": transport,
            "street_retail_score": retail,
            "confidence": max(0.45, round(base_score.confidence - 0.12, 2)),
            "key_strengths": [f"кандидат в {round(candidate.distance_m)} м от исходной точки", *base_score.key_strengths[:2]],
            "key_risks": ["оценка построена по ускоренному контуру без полного гео-обхода", *base_score.key_risks[:1]],
        }
    )
    return score, traffic


def _extract_json_payload(content: str | None) -> dict | None:
    if not content:
        return None
    text = content.strip()
    candidates = [text]
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    candidates.extend(fenced)
    inline = re.findall(r"(\{.*\})", text, flags=re.S)
    candidates.extend(inline[:1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _competitor_label(item: Competitor) -> str:
    return f"{item.name} ({item.address})" if item.address else item.name


def _traffic_rationale(geo_context: GeoContext, street_insight: StreetInsight, business_type: str) -> str:
    facts: list[str] = []
    if geo_context.transit_stops:
        facts.append(f"{len(geo_context.transit_stops)} остановок/станций рядом")
    if geo_context.anchor_pois:
        facts.append(f"{min(len(geo_context.anchor_pois), 5)} коммерческих генераторов поблизости")
    if geo_context.street_type == "arterial":
        facts.append("магистральный фронт улицы")
    elif geo_context.street_type == "mixed":
        facts.append("смешанный городской фронт")
    if any(token in business_type for token in ("кофе", "coffee", "апте", "pharmacy")):
        facts.append("формат чувствителен к ежедневному пешему спросу")
    return ", ".join(facts[:3]) or street_insight.explanation or "базовый транспортно-пешеходный профиль"


def _clean_text(text: str | None) -> str | None:
    if not text:
        return text
    cleaned = text.replace("**", "").replace("__", "")
    cleaned = cleaned.replace("###", "").replace("##", "").replace("#", "")
    return " ".join(cleaned.split()).strip()


def _business_profile(business_type: str) -> dict[str, float | str]:
    normalized = business_type.lower()
    if any(token in normalized for token in ("гипермаркет", "hypermarket", "cash&carry")):
        return {
            "transit_multiplier": 0.55,
            "road_multiplier": 1.4,
            "transport_explanation": "для крупного формата особенно важны магистральная улица и удобный автоподъезд",
        }
    if any(token in normalized for token in ("фитнес", "fitness", "gym")):
        return {
            "transit_multiplier": 0.82,
            "road_multiplier": 0.9,
            "transport_explanation": "для фитнес-формата значим общественный транспорт и удобный доступ после работы",
        }
    if any(token in normalized for token in ("апте", "pharmacy", "кофе", "coffee", "кафе")):
        return {
            "transit_multiplier": 0.72,
            "road_multiplier": 0.75,
            "transport_explanation": "для ежедневного спроса важнее простой пеший доступ, чем автоподъезд",
        }
    return {
        "transit_multiplier": 0.68,
        "road_multiplier": 1.0,
        "transport_explanation": "транспортная доступность должна поддерживать и пеший, и транзитный спрос",
    }


def _latency_ms(started: datetime) -> int:
    return int((datetime.now(timezone.utc) - started).total_seconds() * 1000)

# Observability / Evals Spec

## Что собирается

### Prometheus

- `geoverdict_http_requests_total`
- `geoverdict_http_request_latency_seconds`
- `geoverdict_analysis_duration_seconds`
- `geoverdict_analysis_requests_total`
- `geoverdict_provider_health_status`

### LLMOps

- total requests
- completed requests
- average score
- recommend share
- avg latency
- total cost
- total tokens
- generation latency
- retry rate
- average retries
- tool calls
- tool error rate
- fallback rate
- hallucination risk
- quality score
- completion rate

## Traces

- request id
- city / business type
- selected building
- score
- llm metrics
- provider usage per agent
- local A2A handoffs
- LangGraph node spans
- step traces
- reasoning
- building / street / geo context
- optimization

## Dashboards

- Grafana:
  - HTTP load
  - analysis latency p50/p95/p99
  - analysis throughput
  - provider health
- LLMOps:
  - overview
  - quality
  - costs
  - traces
  - feedback

## Evals / checks

- smoke test on auth + geo + analysis + history
- lockout scenario
- invalid business type scenario
- provider runtime save / reload
- ollama live completion test

## External integrations

- `Langfuse`
  Включается env-переменными и принимает span / trace hooks из backend pipeline.
- `LangSmith`
  Используется для traceable instrumentation и eval-ready graph execution.
- `Grafana + Prometheus`
  Дают системный слой поверх application traces.

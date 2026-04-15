from prometheus_client import Counter, Gauge, Histogram


http_requests_total = Counter(
    "geoverdict_http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)

http_request_latency_seconds = Histogram(
    "geoverdict_http_request_latency_seconds",
    "HTTP request latency",
    ["method", "path"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
)

analysis_duration_seconds = Histogram(
    "geoverdict_analysis_duration_seconds",
    "Duration of full analysis pipeline",
    buckets=(1, 2, 5, 10, 20, 30, 45, 60),
)

analysis_requests_total = Counter(
    "geoverdict_analysis_requests_total",
    "Total analysis requests",
    ["status", "verdict"],
)

provider_health_status = Gauge(
    "geoverdict_provider_health_status",
    "Health of configured providers",
    ["provider"],
)

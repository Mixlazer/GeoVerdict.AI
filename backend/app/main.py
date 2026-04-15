from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from prometheus_client import make_asgi_app

from app.api.v1.routes import router
from app.config import settings
from app.llm.router import LLMRouter
from app.metrics.prometheus import (
    http_request_latency_seconds,
    http_requests_total,
    provider_health_status,
)
from app.models.database import init_db
from app.services.repository import AnalysisRepository


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    app.state.repository = AnalysisRepository()
    app.state.llm_router = LLMRouter()
    for provider_name in settings.llm_provider_priority:
        provider_health_status.labels(provider=provider_name).set(1 if provider_name == "mock" else 0)
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.mount("/metrics", make_asgi_app())
app.include_router(router, prefix=settings.api_prefix)

STATIC_DIR = Path(__file__).parent / "static"

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def instrumentation_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid4()))
    started = perf_counter()
    response = await call_next(request)
    elapsed = perf_counter() - started
    path = request.url.path
    response.headers["X-Request-ID"] = request_id
    http_requests_total.labels(method=request.method, path=path, status=str(response.status_code)).inc()
    http_request_latency_seconds.labels(method=request.method, path=path).observe(elapsed)
    return response


@app.get("/", include_in_schema=False)
async def root_redirect():
    return RedirectResponse(url="/app")


@app.get("/app", include_in_schema=False)
async def app_ui():
    return FileResponse(STATIC_DIR / "app.html")


@app.get("/ops-ui", include_in_schema=False)
async def ops_ui():
    return FileResponse(STATIC_DIR / "ops.html")


@app.get("/grafana", include_in_schema=False)
async def grafana_redirect():
    return await _monitor_redirect_or_help(
        target_url="http://127.0.0.1:3001",
        probe_url="http://127.0.0.1:3001/api/health",
        title="Grafana",
    )


@app.get("/prometheus", include_in_schema=False)
async def prometheus_redirect():
    return await _monitor_redirect_or_help(
        target_url="http://127.0.0.1:9090",
        probe_url="http://127.0.0.1:9090/-/healthy",
        title="Prometheus",
    )


async def _monitor_redirect_or_help(target_url: str, probe_url: str, title: str):
    try:
        async with httpx.AsyncClient(timeout=2.5, follow_redirects=True) as client:
            response = await client.get(probe_url)
            if response.status_code < 400:
                return RedirectResponse(url=target_url)
    except Exception:
        pass
    return HTMLResponse(
        f"""
        <!doctype html>
        <html lang="ru">
          <head>
            <meta charset="utf-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1" />
            <title>{title} недоступен</title>
            <style>
              body {{ font-family: "Segoe UI", system-ui, sans-serif; margin: 0; background: #f6f1e8; color: #1d211b; }}
              main {{ max-width: 760px; margin: 48px auto; padding: 28px; background: rgba(255,255,255,.92); border: 1px solid rgba(74,76,68,.12); border-radius: 24px; }}
              a {{ color: #2162f3; }}
              code {{ background: #f3efe6; padding: 2px 6px; border-radius: 8px; }}
            </style>
          </head>
          <body>
            <main>
              <h1>{title} сейчас недоступен</h1>
              <p>Ссылка работает, но целевой сервис не отвечает в этой локальной среде.</p>
              <p>Обычно причина одна из двух: Docker Desktop не запущен или WSL integration для Docker выключена.</p>
              <p>Когда monitoring поднимется, эта же ссылка автоматически начнёт открывать живой {title}.</p>
              <p>Целевой адрес: <code>{target_url}</code></p>
              <p><a href="/ops-ui">Вернуться в LLMOps</a></p>
            </main>
          </body>
        </html>
        """.strip()
    )

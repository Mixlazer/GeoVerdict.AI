from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from app.config import settings

try:
    from langfuse import Langfuse
except Exception:  # pragma: no cover - optional dependency
    Langfuse = None  # type: ignore[assignment]

try:
    from langsmith import traceable
except Exception:  # pragma: no cover - optional dependency
    def traceable(*args, **kwargs):  # type: ignore[override]
        def decorator(fn):
            return fn

        return decorator


class _NoOpSpan:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def update(self, **kwargs):
        return None

    def end(self, **kwargs):
        return None


@dataclass
class AnalysisTraceCollector:
    request_id: str
    user_id: str | None = None
    session_id: str | None = None
    city: str | None = None
    business_type: str | None = None
    _events: list[dict[str, Any]] = field(default_factory=list)
    _langfuse: Any = None

    def __post_init__(self) -> None:
        if settings.langfuse_enabled and Langfuse is not None:
            try:
                self._langfuse = Langfuse(
                    secret_key=settings.langfuse_secret_key,
                    public_key=settings.langfuse_public_key,
                    host=settings.langfuse_host,
                )
            except Exception:
                self._langfuse = None

    @contextmanager
    def span(self, name: str, *, input_data: dict[str, Any] | None = None, metadata: dict[str, Any] | None = None):
        started = perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()
        remote_span = self._start_langfuse_span(name, input_data=input_data, metadata=metadata)
        error_message: str | None = None
        try:
            yield remote_span
        except Exception as exc:
            error_message = str(exc)
            self._events.append(
                {
                    "type": "span",
                    "name": name,
                    "status": "error",
                    "started_at": started_at,
                    "ended_at": datetime.now(timezone.utc).isoformat(),
                    "latency_ms": round((perf_counter() - started) * 1000, 1),
                    "input": input_data or {},
                    "metadata": metadata or {},
                    "error": error_message,
                }
            )
            try:
                remote_span.end(output={"error": error_message}, level="ERROR")
            except Exception:
                pass
            raise
        else:
            self._events.append(
                {
                    "type": "span",
                    "name": name,
                    "status": "ok",
                    "started_at": started_at,
                    "ended_at": datetime.now(timezone.utc).isoformat(),
                    "latency_ms": round((perf_counter() - started) * 1000, 1),
                    "input": input_data or {},
                    "metadata": metadata or {},
                }
            )
            try:
                remote_span.end()
            except Exception:
                pass

    def log_handoff(self, from_agent: str, to_agent: str, payload: dict[str, Any]) -> dict[str, Any]:
        item = {
            "type": "handoff",
            "from_agent": from_agent,
            "to_agent": to_agent,
            "payload": payload,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._events.append(item)
        return item

    def log_llm_call(self, agent: str, response_meta: dict[str, Any]) -> dict[str, Any]:
        item = {
            "type": "llm_call",
            "agent": agent,
            "created_at": datetime.now(timezone.utc).isoformat(),
            **response_meta,
        }
        self._events.append(item)
        return item

    def dump(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "langfuse_enabled": bool(self._langfuse),
            "langsmith_enabled": bool(settings.langsmith_tracing),
            "city": self.city,
            "business_type": self.business_type,
            "events": self._events,
        }

    def _start_langfuse_span(
        self,
        name: str,
        *,
        input_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        if self._langfuse is None:
            return _NoOpSpan()
        try:
            return self._langfuse.start_as_current_span(
                name=name,
                input=input_data or {},
                metadata={
                    "request_id": self.request_id,
                    "city": self.city,
                    "business_type": self.business_type,
                    **(metadata or {}),
                },
                user_id=self.user_id,
                session_id=self.session_id,
            )
        except Exception:
            return _NoOpSpan()

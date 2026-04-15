from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from app.config import settings
from app.llm.providers.base import (
    AnthropicProvider,
    BaseProvider,
    LLMResponse,
    MockProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
)


class LLMRouter:
    def __init__(self) -> None:
        self.providers: dict[str, BaseProvider] = {
            "mock": MockProvider(),
            "openai": OpenAICompatibleProvider("openai"),
            "anthropic": AnthropicProvider(),
            "ollama": OllamaProvider(),
            "vllm": OpenAICompatibleProvider("vllm"),
        }
        self.priority = settings.llm_provider_priority
        self.runtime_config_path = Path(__file__).resolve().parents[2] / "data" / "runtime-config.json"
        self.runtime_config = self._load_runtime_config()
        self.last_metrics = {
            provider: {"retries": 0, "last_latency_ms": 0.0, "model": cfg.get("model")}
            for provider, cfg in self.runtime_config["providers"].items()
        }

    async def complete(self, messages: list[dict], **kwargs) -> LLMResponse:
        agent_name = kwargs.pop("agent", "analyst")
        agent_config = self.runtime_config["agents"].get(agent_name, {})
        provider_chain = [agent_config.get("provider"), *(agent_config.get("fallback_order") or []), *self.priority]
        checked: list[str] = []
        attempts: list[dict] = []
        for provider_name in provider_chain:
            if not provider_name or provider_name in checked:
                continue
            checked.append(provider_name)
            provider = self.providers.get(provider_name)
            provider_config = self.runtime_config["providers"].get(provider_name, {})
            resolved_model = self._resolve_model(agent_config, provider_config)
            if provider is None or not provider_config.get("enabled", False):
                attempts.append(
                    {
                        "provider": provider_name,
                        "enabled": provider_config.get("enabled", False),
                        "healthy": False,
                        "model": resolved_model,
                        "status": "skipped",
                        "reason": "provider disabled or missing",
                    }
                )
                continue
            healthy = await provider.health_check(provider_config=provider_config)
            if healthy:
                try:
                    response = await provider.complete(
                        messages,
                        provider_config=provider_config,
                        model=resolved_model,
                        **kwargs,
                    )
                    response.model = resolved_model or response.model
                    response.retries = len([item for item in attempts if item.get("status") == "error"])
                    attempts.append(
                        {
                            "provider": provider_name,
                            "enabled": True,
                            "healthy": True,
                            "model": response.model,
                            "status": "success",
                            "latency_ms": round(response.latency_ms, 1),
                        }
                    )
                    response.attempts = attempts
                    self.last_metrics[provider_name] = {
                        "retries": response.retries,
                        "last_latency_ms": response.latency_ms,
                        "model": response.model,
                    }
                    return response
                except Exception as exc:
                    attempts.append(
                        {
                            "provider": provider_name,
                            "enabled": True,
                            "healthy": True,
                            "model": resolved_model,
                            "status": "error",
                            "reason": str(exc),
                        }
                    )
                    self.last_metrics[provider_name] = {
                        "retries": self.last_metrics.get(provider_name, {}).get("retries", 0) + 1,
                        "last_latency_ms": 0.0,
                        "model": resolved_model,
                    }
                    continue
            attempts.append(
                {
                    "provider": provider_name,
                    "enabled": True,
                    "healthy": False,
                    "model": resolved_model,
                    "status": "unhealthy",
                    "reason": "health_check failed",
                }
            )
        response = await self.providers["mock"].complete(messages, **kwargs)
        attempts.append(
            {
                "provider": "mock",
                "enabled": True,
                "healthy": True,
                "model": response.model,
                "status": "success",
                "latency_ms": round(response.latency_ms, 1),
                "reason": "all configured providers failed or were unavailable",
            }
        )
        response.retries = len([item for item in attempts if item.get("status") == "error"])
        response.attempts = attempts
        self.last_metrics["mock"] = {
            "retries": response.retries,
            "last_latency_ms": response.latency_ms,
            "model": response.model,
        }
        return response

    async def get_provider_statuses(self) -> list[dict]:
        statuses: list[dict] = []
        for provider_name in self.priority:
            provider = self.providers.get(provider_name)
            provider_config = self.runtime_config["providers"].get(provider_name, {})
            provider_metrics = self.last_metrics.get(provider_name, {})
            if provider is None:
                statuses.append(
                    {
                        "provider": provider_name,
                        "healthy": False,
                        "mode": "disabled",
                        "detail": "Провайдер не реализован в текущем runtime",
                        "model": provider_config.get("model"),
                        "retries": provider_metrics.get("retries", 0),
                        "last_latency_ms": provider_metrics.get("last_latency_ms", 0.0),
                    }
                )
                continue
            enabled = provider_config.get("enabled", False)
            statuses.append(
                {
                    "provider": provider_name,
                    "healthy": await provider.health_check(provider_config=provider_config) if enabled else False,
                    "mode": "mock" if provider_name == "mock" else ("live" if enabled else "disabled"),
                    "detail": "Доступен и участвует в runtime" if enabled else "Сконфигурирован, но отключён",
                    "model": provider_config.get("model"),
                    "retries": provider_metrics.get("retries", 0),
                    "last_latency_ms": provider_metrics.get("last_latency_ms", 0.0),
                }
            )
        return statuses

    def get_runtime_config(self) -> dict:
        snapshot = deepcopy(self.runtime_config)
        return {
            "providers": [{"provider": provider, **config} for provider, config in snapshot["providers"].items()],
            "agents": [
                {
                    "agent": agent,
                    **{
                        **config,
                        "model": "" if (config.get("model") or "").startswith("heuristic-") else config.get("model"),
                    },
                }
                for agent, config in snapshot["agents"].items()
            ],
            "provider_options": list(self.priority),
        }

    def update_runtime_config(self, payload: dict) -> dict:
        providers = payload.get("providers", [])
        for item in providers:
            provider_name = item["provider"]
            self.runtime_config["providers"].setdefault(
                provider_name,
                {"enabled": False, "model": None, "api_key": None, "base_url": None},
            )
            self.runtime_config["providers"][provider_name].update(
                {
                    "enabled": item.get("enabled", False),
                    "model": item.get("model"),
                    "api_key": item.get("api_key"),
                    "base_url": item.get("base_url"),
                }
            )
            self.last_metrics.setdefault(provider_name, {})
            self.last_metrics[provider_name]["model"] = item.get("model")
        for item in payload.get("agents", []):
            agent_name = item["agent"]
            self.runtime_config["agents"].setdefault(
                agent_name,
                {"provider": "mock", "fallback_order": ["openai"], "model": None},
            )
            fallback_order = item.get("fallback_order", [])
            if isinstance(fallback_order, str):
                fallback_order = [part.strip() for part in fallback_order.split(",") if part.strip()]
            self.runtime_config["agents"][agent_name].update(
                {
                    "provider": item.get("provider", "mock"),
                    "fallback_order": fallback_order,
                    "model": item.get("model") or None,
                }
            )
        self._persist_runtime_config()
        return self.get_runtime_config()

    def _load_runtime_config(self) -> dict:
        default = {
            "providers": {
                "mock": {"enabled": True, "model": "heuristic-summary", "api_key": None, "base_url": None},
                "openai": {"enabled": False, "model": "gpt-5.2-mini", "api_key": settings.openai_api_key, "base_url": "https://api.openai.com/v1"},
                "anthropic": {"enabled": False, "model": "claude-sonnet-4-5", "api_key": settings.anthropic_api_key, "base_url": "https://api.anthropic.com"},
                "ollama": {"enabled": False, "model": "gemma4:31b-cloud", "api_key": None, "base_url": "https://ollama.com"},
                "vllm": {"enabled": False, "model": "meta-llama/Meta-Llama-3.1-8B-Instruct", "api_key": None, "base_url": settings.vllm_base_url},
            },
            "agents": {
                "geo": {"provider": "mock", "fallback_order": ["ollama", "openai", "vllm"], "model": None},
                "building": {"provider": "mock", "fallback_order": ["ollama", "openai", "anthropic"], "model": None},
                "street": {"provider": "mock", "fallback_order": ["ollama", "openai"], "model": None},
                "competitors": {"provider": "mock", "fallback_order": ["ollama", "openai"], "model": None},
                "traffic": {"provider": "mock", "fallback_order": ["ollama", "openai", "vllm"], "model": None},
                "analyst": {"provider": "mock", "fallback_order": ["ollama", "openai", "anthropic", "vllm"], "model": None},
                "optimizer": {"provider": "mock", "fallback_order": ["ollama", "openai"], "model": None},
            },
        }
        if not self.runtime_config_path.exists():
            return default
        try:
            loaded = json.loads(self.runtime_config_path.read_text(encoding="utf-8"))
            default["providers"].update(loaded.get("providers", {}))
            default["agents"].update(loaded.get("agents", {}))
        except Exception:
            return default
        return default

    def _persist_runtime_config(self) -> None:
        self.runtime_config_path.parent.mkdir(parents=True, exist_ok=True)
        self.runtime_config_path.write_text(
            json.dumps(self.runtime_config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _resolve_model(agent_config: dict, provider_config: dict) -> str | None:
        agent_model = (agent_config.get("model") or "").strip()
        if not agent_model or agent_model.startswith("heuristic-"):
            return provider_config.get("model")
        return agent_model

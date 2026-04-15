from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import re
from time import perf_counter

import httpx


PRICE_ESTIMATES_PER_1K: dict[str, tuple[float, float]] = {
    "openai": (0.0015, 0.0060),
    "anthropic": (0.0030, 0.0150),
    "ollama": (0.0008, 0.0016),
    "vllm": (0.0, 0.0),
    "mock": (0.0, 0.0),
}


@dataclass
class LLMResponse:
    content: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    model: str
    provider: str
    retries: int = 0
    latency_ms: float = 0.0
    attempts: list[dict] | None = None


class BaseProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def complete(self, messages: list[dict], **kwargs) -> LLMResponse:
        raise NotImplementedError

    @abstractmethod
    async def health_check(self, **kwargs) -> bool:
        raise NotImplementedError


class MockProvider(BaseProvider):
    name = "mock"

    async def complete(self, messages: list[dict], **kwargs) -> LLMResponse:
        started = perf_counter()
        prompt = "\n".join(str(message.get("content", "")) for message in messages)
        score_match = re.search(r"overall_score[=:]\s*(\d+)", prompt)
        score = int(score_match.group(1)) if score_match else 67
        if score >= 75:
            summary = (
                "Локация выглядит сильной: высокий поток, хорошая видимость и понятный сценарий запуска. "
                "Рекомендуется протестировать точку и сравнить аренду с соседним микросмещением."
            )
        elif score >= 55:
            summary = (
                "Локация рабочая, но чувствительна к исполнению. "
                "Запуск возможен при сильной витрине, понятной навигации и контроле конкурентного давления."
            )
        else:
            summary = (
                "Локация слабая для быстрого запуска. "
                "Лучше искать более видимую точку рядом с транзитным трафиком или переработать гипотезу."
            )
        return LLMResponse(
            content=summary,
            input_tokens=max(50, len(prompt) // 4),
            output_tokens=max(30, len(summary) // 4),
            cost_usd=_estimate_cost(self.name, max(50, len(prompt) // 4), max(30, len(summary) // 4)),
            model="heuristic-summary",
            provider=self.name,
            retries=0,
            latency_ms=(perf_counter() - started) * 1000,
        )

    async def health_check(self, **kwargs) -> bool:
        return True


class OpenAICompatibleProvider(BaseProvider):
    def __init__(self, name: str) -> None:
        self.name = name

    async def complete(self, messages: list[dict], **kwargs) -> LLMResponse:
        started = perf_counter()
        provider_config = kwargs.get("provider_config") or {}
        model = kwargs.get("model") or provider_config.get("model")
        base_url = (provider_config.get("base_url") or "").rstrip("/")
        api_key = provider_config.get("api_key")
        endpoint = _openai_endpoint(base_url, "chat/completions")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {"model": model, "messages": messages, "temperature": 0.2, "max_tokens": 180}
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        choice = ((data.get("choices") or [{}])[0]).get("message") or {}
        usage = data.get("usage") or {}
        content = choice.get("content") or "Провайдер не вернул текст"
        return LLMResponse(
            content=content,
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            cost_usd=_estimate_cost(self.name, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))),
            model=model or "unknown-model",
            provider=self.name,
            retries=0,
            latency_ms=(perf_counter() - started) * 1000,
        )

    async def health_check(self, **kwargs) -> bool:
        provider_config = kwargs.get("provider_config") or {}
        base_url = (provider_config.get("base_url") or "").rstrip("/")
        if not base_url:
            return False
        headers = {}
        if provider_config.get("api_key"):
            headers["Authorization"] = f"Bearer {provider_config['api_key']}"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(_openai_endpoint(base_url, "models"), headers=headers)
                return response.status_code < 400
        except Exception:
            return False


class AnthropicProvider(BaseProvider):
    name = "anthropic"

    async def complete(self, messages: list[dict], **kwargs) -> LLMResponse:
        started = perf_counter()
        provider_config = kwargs.get("provider_config") or {}
        model = kwargs.get("model") or provider_config.get("model")
        base_url = (provider_config.get("base_url") or "https://api.anthropic.com").rstrip("/")
        api_key = provider_config.get("api_key")
        if not api_key:
            raise httpx.HTTPError("Anthropic API key is missing")
        system_parts = [msg.get("content", "") for msg in messages if msg.get("role") == "system"]
        user_messages = [
            {"role": msg.get("role", "user"), "content": msg.get("content", "")}
            for msg in messages
            if msg.get("role") != "system"
        ]
        payload = {
            "model": model,
            "max_tokens": 500,
            "system": "\n".join(system_parts),
            "messages": user_messages,
        }
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(f"{base_url}/v1/messages", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        content_blocks = data.get("content") or []
        content = " ".join(block.get("text", "") for block in content_blocks if isinstance(block, dict)).strip()
        usage = data.get("usage") or {}
        return LLMResponse(
            content=content or "Провайдер не вернул текст",
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            cost_usd=_estimate_cost(self.name, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))),
            model=model or "unknown-model",
            provider=self.name,
            retries=0,
            latency_ms=(perf_counter() - started) * 1000,
        )

    async def health_check(self, **kwargs) -> bool:
        provider_config = kwargs.get("provider_config") or {}
        return bool(provider_config.get("api_key"))


class OllamaProvider(BaseProvider):
    name = "ollama"

    async def complete(self, messages: list[dict], **kwargs) -> LLMResponse:
        started = perf_counter()
        provider_config = kwargs.get("provider_config") or {}
        model = kwargs.get("model") or provider_config.get("model")
        base_url = (provider_config.get("base_url") or "http://localhost:11434").rstrip("/")
        api_key = provider_config.get("api_key")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": 180},
        }
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(f"{base_url}/api/chat", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        message = data.get("message") or {}
        content = message.get("content") or "Провайдер не вернул текст"
        return LLMResponse(
            content=content,
            input_tokens=int(data.get("prompt_eval_count", 0)),
            output_tokens=int(data.get("eval_count", 0)),
            cost_usd=_estimate_cost(self.name, int(data.get("prompt_eval_count", 0)), int(data.get("eval_count", 0))),
            model=model or "unknown-model",
            provider=self.name,
            retries=0,
            latency_ms=(perf_counter() - started) * 1000,
        )

    async def health_check(self, **kwargs) -> bool:
        provider_config = kwargs.get("provider_config") or {}
        base_url = (provider_config.get("base_url") or "http://localhost:11434").rstrip("/")
        api_key = provider_config.get("api_key")
        model = provider_config.get("model")
        if "ollama.com" in base_url and api_key and model:
            return True
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"{base_url}/api/tags", headers=headers)
                return response.status_code < 400
        except Exception:
            return False


def _openai_endpoint(base_url: str, suffix: str) -> str:
    trimmed = base_url.rstrip("/")
    if trimmed.endswith(suffix):
        return trimmed
    if trimmed.endswith("/v1"):
        return f"{trimmed}/{suffix}"
    return f"{trimmed}/v1/{suffix}"


def _estimate_cost(provider: str, input_tokens: int, output_tokens: int) -> float:
    in_price, out_price = PRICE_ESTIMATES_PER_1K.get(provider, (0.0, 0.0))
    estimated = (input_tokens / 1000) * in_price + (output_tokens / 1000) * out_price
    return round(estimated, 6)

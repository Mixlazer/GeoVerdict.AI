# Agent / Orchestrator Spec

## Порядок шагов

1. `Geo-Agent`
2. `Building-Agent`
3. `Street-Agent`
4. `Competitor-Agent`
5. `Traffic-Agent`
6. `Analyst-Agent`
7. `Radius Optimizer`

## Правила переходов

- следующая зависимая нода стартует только после фиксации handoff предыдущей;
- handoff между агентами пишется в local A2A-style envelope;
- `optimizer` не должен ломать весь verdict при своей ошибке;
- `analyst` читает уже агрегированные признаки, а не raw outputs.

## Stop condition

- `completed` result сохранён;
- или запрос провалился с невосстановимой ошибкой.

## Retry / fallback

- гео retrieval: `1 attempt + fallback profile`
- provider routing:
  - fast health check
  - primary provider
  - ordered fallback providers
  - final fallback на `mock`
- completion exception на live provider не завершает pipeline аварийно
- building/street web search ограничен timeout и не должен держать pipeline бесконечно

## Guardrails

- поиск и анализ только для авторизованного пользователя;
- business type проходит allowlist-like validation;
- brute-force login lockout;
- ops routes закрыты токеном.

## Реализация

- оркестратор уже работает на `LangGraph StateGraph`;
- traces сохраняются локально и готовы к отправке в `Langfuse` / `LangSmith`;
- текущий A2A слой локальный, на уровне handoff envelopes внутри pipeline.

# Serving / Config Spec

## Локальный запуск

- `make local-up`
- `make local-down`

## Основные entrypoints

- `http://localhost:8000/app`
- `http://localhost:8000/ops-ui`
- `http://localhost:8000/docs`
- `http://localhost:9090`
- `http://localhost:3001`

## Runtime config

- провайдеры сохраняются в `backend/data/runtime-config.json`
- хранится:
  - enabled flag
  - model
  - base_url
  - api_key
- конфиг задаётся отдельно для каждого агента

## Поддерживаемые provider types

- `mock`
- `openai`
- `anthropic`
- `ollama`
- `vllm`

## Secrets

- `.env`
- runtime-config JSON
- не коммитить локальные ключи

## Версии и режимы

- backend: FastAPI + Python 3.12
- UI demo path: backend-hosted static pages
- monitoring: Prometheus + Grafana containers

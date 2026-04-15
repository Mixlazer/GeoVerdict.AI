# Tools / APIs Spec

## Geo API

### `GET /api/v1/geo/reverse`

- назначение: адрес точки клика
- auth: требуется
- timeout: до `14s`
- side effects: нет

### `GET /api/v1/geo/buildings`

- назначение: список зданий вокруг точки
- auth: требуется
- query: `lat,lng,city,radius`
- side effects: нет

## Analysis API

### `POST /api/v1/analysis/analyze`

- назначение: создать анализ
- auth: требуется
- side effect: создаёт `analysis_request`
- execution: background pipeline

### `GET /api/v1/analysis/{id}`

- назначение: статус и финальный payload
- auth: bearer по желанию для чтения своей сессии UI

## Auth API

### `POST /api/v1/auth/register`

- создаёт пользователя и сессию

### `POST /api/v1/auth/login`

- создаёт сессию
- guardrail: lockout `3 failed / 6h -> 24h block`

## Ops API

- `GET /api/v1/ops/providers/status`
- `GET/POST /api/v1/ops/runtime-config`
- `GET /api/v1/ops/traces`
- `GET /api/v1/ops/charts`

### Защита

- все ops routes требуют `X-Ops-Token`

## Внешние side effects

- запросы в гео-API;
- запросы в model providers;
- запись runtime-config в локальный JSON;
- запись feedback и analysis traces в SQLite.

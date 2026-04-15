# Memory / Context Spec

## Session State

- bearer token пользователя;
- выбранный город;
- точка клика;
- список building candidates;
- выбранное здание.

## Persisted State

- analyses;
- users;
- sessions;
- login attempts;
- feedback.

## Context Policy

- в LLM передаётся только summary признаков;
- сырые внешние ответы не прокидываются в prompt целиком;
- у каждого completed analysis есть traceable result payload.

## Context Budget

- адрес;
- тип района;
- street / pedestrian / transport scores;
- competition level;
- короткие strengths / risks;
- краткая building summary.

## Retention

- PoC retention не ограничен TTL;
- production target: добавить TTL/архивацию для trace logs и сырых artefacts.

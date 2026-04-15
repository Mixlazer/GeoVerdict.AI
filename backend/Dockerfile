FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/backend

WORKDIR /app/backend

COPY backend/pyproject.toml /app/backend/pyproject.toml
RUN pip install --no-cache-dir \
    aiosqlite>=0.21.0 \
    fastapi>=0.115.0 \
    httpx>=0.28.0 \
    prometheus-client>=0.22.0 \
    pydantic-settings>=2.6.0 \
    sqlalchemy>=2.0.36 \
    uvicorn[standard]>=0.32.0

COPY backend /app/backend

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ===== AIVIS MES service (품질·생산실적, FastAPI) =====
# AIVIS api 와 별도 컨테이너. 자체 DB(MES_DATABASE_URL) + AIVIS 연계 테이블 폴링
# (MES_AIVIS_DB_URL). docs/MES.md 참조. shared-types 에 의존하지 않는다(연계 규격만 본다).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY services/mes/requirements.txt ./
RUN pip install -r requirements.txt

COPY services/mes/mes_app ./mes_app

EXPOSE 8100

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fsS http://localhost:${PORT:-8100}/health || exit 1

# PORT: Render 등 플랫폼이 주는 포트(없으면 8100).
CMD ["sh", "-c", "exec uvicorn mes_app.main:app --host 0.0.0.0 --port ${PORT:-8100}"]

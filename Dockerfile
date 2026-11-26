FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt pytest.ini ./
COPY migration_baseline_sha256.json stage3a1_migration_baseline_sha256.json stage3a2_migration_baseline_sha256.json stage3a3_migration_baseline_sha256.json ./
COPY stage3d_migration_baseline_sha256.json ./
COPY stage6c1_migration_baseline_sha256.json ./
COPY stage6e_migration_baseline_sha256.json ./
RUN pip install --no-cache-dir -r requirements.txt pytest
COPY universal_supplier ./universal_supplier
COPY sterbrust_matching ./sterbrust_matching
COPY competitor_intel ./competitor_intel
COPY content_studio ./content_studio
COPY scripts ./scripts
COPY tests ./tests
COPY migrations ./migrations
COPY .dockerignore .env.example Dockerfile Dockerfile.server docker-compose.yml docker-compose.server.yml schema.sql README.md ./

COPY config ./config
RUN test -f /app/config/yml_feed.json
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin app
USER app
CMD ["python", "-m", "pytest", "-q"]

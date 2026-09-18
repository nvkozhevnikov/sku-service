FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt pytest.ini ./
COPY stage3d_migration_baseline_sha256.json ./
COPY stage6c1_migration_baseline_sha256.json ./
COPY stage6e_migration_baseline_sha256.json ./
RUN pip install --no-cache-dir -r requirements.txt pytest
COPY universal_supplier ./universal_supplier
COPY sterbrust_matching ./sterbrust_matching
COPY scripts ./scripts
COPY tests ./tests
COPY migrations ./migrations

COPY config ./config
RUN test -f /app/config/yml_feed.json
CMD ["python", "-m", "pytest", "-q"]

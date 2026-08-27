# Pontis application image: engine, ingestion, tests, API, and the built
# React app -- one container serves the whole thing.
#
# Stage 1 builds the frontend; stage 2 is the Python runtime. The same image
# plays three roles in docker-compose: applying migrations and seeds (both
# idempotent, so running them at every start is safe), running the test suite
# against the containerized database, and serving.

FROM node:22-slim AS webbuild
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ .
RUN npm run build

FROM python:3.12-slim

# psql applies the migrations -- the same files, the same order, as everywhere
# else. No second migration mechanism to drift.
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY schema/ schema/
COPY engine/ engine/
COPY ingest/ ingest/
COPY api/ api/
COPY tests/ tests/
COPY --from=webbuild /web/dist web/dist
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
CMD ["serve"]

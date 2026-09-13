# Stage 11: Production-Ready Docker Compose Deployment

## Overview

This deployment configuration provides a **production-ready Docker Compose setup for a controlled/staging environment**. It includes:

- Multi-stage Docker builds with non-root user
- Dedicated migration service with PostgreSQL advisory locks
- Health checks for all services
- Redis AOF persistence
- Graceful shutdown handling
- Security scanning in CI
- Observability-ready configuration

## ⚠️ Production Scope

This deployment is suitable for:
- **Staging environments**
- **Controlled internal deployments**
- **Development teams**

This deployment **is NOT suitable for internet-facing production** without additional layers:
- ❌ No TLS/reverse proxy configuration
- ❌ No rate limiting
- ❌ No external backup/restore procedures
- ❌ No load balancing

For internet-facing production, add:
- Reverse proxy (nginx, Traefik) with TLS termination
- Rate limiting middleware
- Automated backup solutions
- Monitoring and alerting
- Load balancer for multi-instance deployments

## Architecture

```
┌─────────────┐     ┌─────────────┐
│  postgres   │     │    redis    │
│  (pg16 +    │     │   (AOF on)  │
│  pgvector)  │     │             │
└──────┬──────┘     └──────┬──────┘
       │                   │
       └───────┬───────────┘
               │
        ┌──────▼──────┐
        │   migrate   │ (one-shot)
        │  service    │
        └──────┬──────┘
               │
       ┌───────┴───────┐
       │               │
  ┌────▼────┐    ┌────▼────┐
  │   api   │    │  worker │
  │ :8000   │    │         │
  └─────────┘    └─────────┘
```

## Services

### 1. PostgreSQL (`postgres`)
- **Image**: `pgvector/pgvector:pg16`
- **Purpose**: Primary database with vector extension
- **Health check**: `pg_isready`
- **Data persistence**: `postgres_data` volume

### 2. Redis (`redis`)
- **Image**: `redis:7`
- **Purpose**: Job queue with AOF persistence
- **Health check**: `redis-cli ping`
- **Persistence**: AOF with `appendfsync everysec`
- **Data persistence**: `redis_data` volume

**⚠️ Redis Durability Note:**
- AOF persists Redis data to disk
- BLPOP-style consumption can lose an in-flight job if the worker crashes mid-processing
- Current Stage 7D retry logic remains unchanged
- Full job durability would require different queue patterns (not in scope for Stage 11)

### 3. Migration Service (`migrate`)
- **Purpose**: One-shot database migration runner
- **Runs**: `alembic upgrade head` via `app.migrate`
- **Advisory lock**: Prevents concurrent migrations
- **Restart policy**: `no` (runs once)
- **Dependencies**: Waits for postgres health check

**Migration Strategy:**
- Dedicated service runs migrations before API/worker start
- PostgreSQL advisory lock prevents concurrent migrations
- `Base.metadata.create_all()` remains in `app/main.py` until:
  - Initial migration is generated ✅
  - Migration is manually reviewed ✅
  - Fresh DB migration is tested
  - Existing DB stamp strategy is verified

### 4. API Service (`api`)
- **Purpose**: FastAPI REST API
- **Port**: 8000
- **Health endpoints**:
  - `GET /health/live` - Process liveness (always succeeds if running)
  - `GET /health/ready` - PostgreSQL + Redis readiness
- **Restart policy**: `unless-stopped`
- **Dependencies**: postgres, redis (healthy), migrate (completed)

### 5. Worker Service (`worker`)
- **Purpose**: Background job processor
- **Queue**: Redis BLPOP from `supportflow:ticket_queue`
- **Graceful shutdown**: SIGTERM/SIGINT handling
- **Restart policy**: `unless-stopped`
- **Dependencies**: postgres, redis (healthy), migrate (completed)

## Setup Instructions

### 1. Prerequisites

- Docker 20.10+ and Docker Compose V2
- `.env` file (copy from `.env.example`)

### 2. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and set:

```bash
# PostgreSQL
DATABASE_URL=postgresql://supportflow_user:supportflow_password@postgres:5432/supportflow

# Redis
REDIS_HOST=redis
REDIS_PORT=6379
TICKET_QUEUE_NAME=supportflow:ticket_queue

# OpenAI API (REQUIRED)
OPENAI_API_KEY=sk-...

# JWT (REQUIRED - min 32 chars)
JWT_SECRET_KEY=your-secret-key-here-min-32-chars
JWT_ALGORITHM=HS256

# OpenTelemetry (optional)
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
OTEL_SERVICE_NAME=supportflow
```

**⚠️ SECURITY WARNING:**
- Never commit `.env` to Git
- If `.env` was ever committed to Git history, you MUST:
  1. Remove it from history using `git-filter-repo` or BFG Repo-Cleaner
  2. Rotate ALL secrets immediately
  3. Force-push the cleaned repository
- Simply adding `.env` to `.gitignore` is NOT sufficient

### 3. Build and Start Services

```bash
# Build images
docker-compose build

# Start all services
docker-compose up -d

# View logs
docker-compose logs -f

# Check service health
curl http://localhost:8000/health/ready
```

### 4. Verify Deployment

```bash
# Check all services are running
docker-compose ps

# Expected output:
# NAME                  STATUS
# supportflow-postgres  Up (healthy)
# supportflow-redis     Up (healthy)
# supportflow-migrate   Exited (0)
# supportflow-api       Up (healthy)
# supportflow-worker    Up
```

### 5. Stop Services

```bash
# Graceful shutdown
docker-compose down

# Remove volumes (⚠️ deletes data)
docker-compose down -v
```

## Docker Image

### Build Details

- **Base**: `python:3.11-slim`
- **User**: Non-root (`appuser`, UID 1000)
- **Size**: ~800MB (varies with dependencies)
- **Multi-stage**: Yes (base → builder → production)

### Security Features

- ✅ Non-root user
- ✅ No secrets in image (via `.dockerignore`)
- ✅ Minimal system dependencies
- ✅ Security scanning in CI (Trivy)

### Size Optimization

Current image size is reported in CI but does not fail the build. To optimize:

1. Use `python:3.11-slim-alpine` (reduces size by ~200MB)
2. Remove test/evaluation code from production image
3. Use multi-stage builds to exclude build tools

## Health Checks

### Liveness Probe: `/health/live`

```bash
curl http://localhost:8000/health/live
# {"status": "alive"}
```

- **Purpose**: Process-only check
- **Success**: Returns 200 if process is running
- **Failure**: Only fails if process is crashed/unresponsive
- **Does NOT check**: Database or Redis connectivity

### Readiness Probe: `/health/ready`

```bash
curl http://localhost:8000/health/ready
# {"status": "ready", "checks": {"postgres": "ready", "redis": "ready"}}
```

- **Purpose**: Dependency connectivity check
- **Success**: Returns 200 if PostgreSQL AND Redis are reachable
- **Failure**: Returns 503 if either dependency is unavailable
- **Use case**: Load balancer routing decisions

## Database Migrations

### Running Migrations

Migrations run automatically via the `migrate` service on startup.

Manual migration commands:

```bash
# Run migrations
docker-compose run --rm migrate

# Generate new migration
docker-compose run --rm api alembic revision --autogenerate -m "description"

# Check current version
docker-compose run --rm api alembic current

# Migration history
docker-compose run --rm api alembic history
```

### Migration Lock

The migration service uses PostgreSQL advisory lock ID `987654321` to prevent concurrent migrations. If a migration is already running, subsequent containers will wait for the lock to be released.

## Observability

### Logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f api
docker-compose logs -f worker

# Recent logs
docker-compose logs --tail=100 api
```

### OpenTelemetry

Configure OTEL environment variables in `.env`:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://your-collector:4317
OTEL_SERVICE_NAME=supportflow
```

Traces include:
- HTTP requests (via FastAPI instrumentation)
- Worker job processing
- Classification operations
- Queue operations

## Troubleshooting

### Migration service keeps restarting

```bash
# Check migration logs
docker-compose logs migrate

# Common causes:
# - DATABASE_URL not set or incorrect
# - postgres service not healthy
# - Advisory lock held by another process
```

### API not responding

```bash
# Check health endpoints
curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready

# Check logs
docker-compose logs api

# Common causes:
# - Database connection failed (check DATABASE_URL)
# - Redis connection failed (check REDIS_HOST)
# - Missing environment variables (OPENAI_API_KEY, JWT_SECRET_KEY)
```

### Worker not processing jobs

```bash
# Check worker logs
docker-compose logs worker

# Verify Redis connectivity
docker-compose exec redis redis-cli ping

# Check queue length
docker-compose exec redis redis-cli llen supportflow:ticket_queue

# Common causes:
# - Redis not reachable
# - Queue name mismatch (check TICKET_QUEUE_NAME)
# - Worker crashed (check logs for exceptions)
```

### Secrets detected in image

```bash
# Scan image for secrets
docker run --rm supportflow:latest find /app -name "*.env"

# Should return nothing
# If .env files are found, rebuild with correct .dockerignore
```

## CI/CD

### GitHub Actions

The CI pipeline runs:

1. **Test job**:
   - Runs deterministic tests (no LLM calls)
   - Uses service containers for postgres + redis
   - Preserves `Base.metadata.create_all()` for tests

2. **Docker job**:
   - Builds production image
   - Verifies non-root user
   - Tests health endpoints
   - Scans for secrets in image
   - Reports image size (does NOT fail on size)
   - Runs Trivy vulnerability scan

### Security Scanning

- **Trivy**: Scans for CVEs in base image and dependencies
- **Severity**: Reports CRITICAL and HIGH vulnerabilities
- **Exit code**: 0 (does not fail CI, reports only)

## Backup and Restore

### PostgreSQL Backup

```bash
# Backup
docker-compose exec postgres pg_dump -U supportflow_user supportflow > backup.sql

# Restore
docker-compose exec -T postgres psql -U supportflow_user supportflow < backup.sql
```

### Redis Backup

Redis AOF files are stored in `redis_data` volume.

```bash
# Copy AOF file
docker-compose exec redis redis-cli BGSAVE
docker cp supportflow-redis:/data/dump.rdb ./redis-backup.rdb
```

## Scaling

### Horizontal Scaling

To run multiple worker instances:

```bash
# Scale workers
docker-compose up -d --scale worker=3
```

API instances require a load balancer (not included in this configuration).

## Performance Tuning

### PostgreSQL

Edit `docker-compose.yml` to add PostgreSQL tuning:

```yaml
postgres:
  command: postgres -c shared_buffers=256MB -c max_connections=200
```

### Redis

Redis is configured with AOF persistence (`appendfsync everysec`). For higher throughput:

```yaml
redis:
  command: redis-server --appendonly yes --appendfsync no
```

⚠️ **Warning**: `appendfsync no` reduces durability.

## Monitoring

### Recommended Metrics

- API response times (`/health/ready` endpoint)
- Worker queue length (`LLEN supportflow:ticket_queue`)
- Database connection pool usage
- Redis memory usage
- Container resource usage (CPU, memory)

### Health Check Endpoints

Use `/health/ready` for load balancer health checks:

```yaml
# Example nginx upstream health check
upstream supportflow_api {
  server api:8000 max_fails=3 fail_timeout=30s;
  check interval=10000 rise=2 fall=3 timeout=5000 type=http;
  check_http_send "GET /health/ready HTTP/1.0\r\n\r\n";
  check_http_expect_alive http_2xx;
}
```

## Security Checklist

- [ ] `.env` never committed to Git
- [ ] All secrets rotated if ever committed
- [ ] `OPENAI_API_KEY` set securely
- [ ] `JWT_SECRET_KEY` is random and >= 32 characters
- [ ] Non-root user verified in CI
- [ ] No `.env` files in Docker image
- [ ] Trivy scan reviewed
- [ ] TLS configured (if internet-facing)
- [ ] Rate limiting enabled (if internet-facing)

## Known Limitations

1. **Redis job durability**: BLPOP can lose in-flight jobs on worker crash
2. **No TLS**: Requires reverse proxy for HTTPS
3. **No rate limiting**: Add at reverse proxy or middleware layer
4. **Single-instance API**: Load balancer needed for HA
5. **No automated backups**: Requires external backup solution
6. **Image size**: ~800MB (can be optimized further)

## Next Steps

For production internet-facing deployment, add:

1. **Reverse proxy** (nginx, Traefik) with TLS certificates
2. **Rate limiting** (nginx, Kong, or FastAPI middleware)
3. **Automated backups** (scheduled pg_dump, Redis snapshots)
4. **Monitoring stack** (Prometheus, Grafana, or cloud provider)
5. **Log aggregation** (ELK stack, Loki, or cloud logging)
6. **Secret management** (Vault, AWS Secrets Manager, etc.)
7. **Load balancer** for API service
8. **Health check monitoring** and alerting

## Support

For issues or questions:
1. Check logs: `docker-compose logs <service>`
2. Verify environment variables in `.env`
3. Review health endpoints: `/health/live` and `/health/ready`
4. Check CI pipeline for build/test failures

# Stage 11 Verification Checklist

## Pre-Deployment Verification

### 1. Secret Management ✅
- [x] `.env` is NOT tracked by Git
- [x] `.env` has NO history in Git (`git log --all -- .env` returns empty)
- [x] `.env.example` exists as template
- [x] `.gitignore` contains security warnings
- [x] `.dockerignore` excludes `.env`
- [x] Docker image contains no `.env` files (verified)

**Action Required**: If deploying:
1. Copy `.env.example` to `.env`
2. Set `OPENAI_API_KEY` (required)
3. Set `JWT_SECRET_KEY` (min 32 chars, required)
4. Ensure `DATABASE_URL` points to `postgres` (not `localhost`)

### 2. Docker Build ✅
- [x] Multi-stage Dockerfile builds successfully
- [x] Non-root user (UID 1000) verified
- [x] Image size ~3.5GB (reported, non-blocking)
- [x] No secrets in image

**Commands to verify:**
```bash
docker-compose build
docker run --rm supportflow-api:latest id
# Expected: uid=1000(appuser)

docker run --rm supportflow-api:latest find /app -name "*.env"
# Expected: empty output
```

### 3. Health Endpoints ✅
- [x] `GET /health/live` implemented (process-only)
- [x] `GET /health/ready` implemented (postgres + redis)
- [x] Health checks don't cause liveness to fail on dependency issues
- [x] Integrated into FastAPI app

**Test after deployment:**
```bash
curl http://localhost:8000/health/live
# Expected: {"status": "alive"}

curl http://localhost:8000/health/ready
# Expected: {"status": "ready", "checks": {...}}
```

### 4. Database Migrations ✅
- [x] Alembic initialized
- [x] Initial migration generated (empty, tables exist)
- [x] `app/migrate.py` with PostgreSQL advisory lock
- [x] `Base.metadata.create_all()` PRESERVED (per requirements)

**Note**: Migration is empty because tables already exist. The strategy is:
1. Keep `create_all()` in existing code ✅
2. Use migration service in Docker ✅
3. Test on fresh DB before removing `create_all()` ⏳
4. Document stamp strategy for existing DBs ⏳

**Migration locations with create_all():**
- `app/main.py:26`
- `app/approval.py:15`
- `app/kb.py:17`
- `app/tools.py:11`

### 5. Docker Compose Configuration ✅
- [x] PostgreSQL with health check
- [x] Redis with AOF persistence (`appendfsync everysec`)
- [x] Dedicated migration service (one-shot)
- [x] API service with health check
- [x] Worker service
- [x] Proper service dependencies

**Service startup order:**
```
postgres + redis (healthy)
  ↓
migrate (completes successfully)
  ↓
api + worker (start)
```

### 6. Redis Durability Documentation ✅
- [x] AOF persistence documented
- [x] BLPOP job loss limitation documented
- [x] Stage 7D retry logic unchanged
- [x] No claims of full job durability

From `DEPLOYMENT.md`:
> - AOF persists Redis data to disk
> - BLPOP-style consumption can lose an in-flight job if the worker crashes mid-processing
> - Current Stage 7D retry logic remains unchanged

### 7. Production Scope Documentation ✅
- [x] Clearly labeled as "controlled/staging environment"
- [x] Limitations documented (no TLS, rate limiting, etc.)
- [x] Production hardening steps documented

From documentation:
> **Production-ready Docker Compose deployment for a controlled/staging environment**
>
> NOT suitable for internet-facing production without:
> - TLS/reverse proxy
> - Rate limiting
> - External backup/restore

### 8. CI/CD Pipeline ✅
- [x] Test job runs deterministic tests
- [x] Docker job builds and validates image
- [x] Non-root user verification
- [x] Health endpoint testing
- [x] Secret scanning (no .env in image)
- [x] Image size reporting (non-blocking)
- [x] Trivy vulnerability scan (report only)

**GitHub Actions jobs:**
1. `test` - Runs pytest with service containers
2. `docker` - Builds image and runs security checks

### 9. Graceful Shutdown ✅
- [x] Worker handles SIGTERM/SIGINT
- [x] API shutdown handler closes connections
- [x] Redis and database connections cleaned up

### 10. Preserved Existing System ✅
- [x] No changes to classifier prompt
- [x] No changes to Stage 9 evaluation
- [x] No changes to BANKING77 dataset
- [x] No changes to Stage 10 trace semantics
- [x] No changes to authorization rules
- [x] No changes to idempotency semantics
- [x] No changes to retry behavior
- [x] No changes to business logic

## Deployment Testing Checklist

### Phase 1: Environment Setup
```bash
# 1. Copy environment template
cp .env.example .env

# 2. Edit .env - REQUIRED CHANGES:
#    DATABASE_URL=postgresql://supportflow_user:supportflow_password@postgres:5432/supportflow
#    OPENAI_API_KEY=sk-...
#    JWT_SECRET_KEY=<random-32-char-string>
```

### Phase 2: Build and Start
```bash
# 3. Build images
docker-compose build

# 4. Start services
docker-compose up -d

# 5. Check service status
docker-compose ps
# Expected:
# - postgres: Up (healthy)
# - redis: Up (healthy)
# - migrate: Exited (0)
# - api: Up (healthy)
# - worker: Up
```

### Phase 3: Health Checks
```bash
# 6. Test liveness
curl -f http://localhost:8000/health/live || echo "❌ Liveness failed"

# 7. Test readiness
curl -f http://localhost:8000/health/ready || echo "❌ Readiness failed"
```

### Phase 4: Functional Testing
```bash
# 8. Register user
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "test@example.com", "password": "testpass123"}'

# 9. Login and get JWT
TOKEN=$(curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "test@example.com", "password": "testpass123"}' \
  | jq -r '.access_token')

# 10. Create ticket
curl -X POST http://localhost:8000/tickets \
  -H "Content-Type: application/json" \
  -d '{"message": "I need help with my order"}'

# 11. Check worker logs
docker-compose logs worker | tail -20
# Should show ticket processing

# 12. Test support/process endpoint
curl -X POST http://localhost:8000/support/process \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"message": "What is the status of my order?"}'
```

### Phase 5: Graceful Shutdown
```bash
# 13. Test graceful shutdown
docker-compose down
# Should show clean shutdown logs

# 14. Verify data persistence
docker-compose up -d
# Data should still exist in postgres_data and redis_data volumes
```

### Phase 6: Migration Testing (Fresh DB)
```bash
# 15. Stop all services
docker-compose down -v  # ⚠️ This deletes data!

# 16. Start fresh
docker-compose up -d

# 17. Verify migration ran
docker-compose logs migrate
# Should show: "✅ Migrations completed successfully."

# 18. Verify tables exist
docker-compose exec postgres psql -U supportflow_user -d supportflow -c "\dt"
# Should list all tables
```

## Known Issues / Limitations

### 1. Image Size
- **Current**: ~3.5GB per image
- **Reason**: ML dependencies (sentence-transformers, torch)
- **Status**: Reported in CI but non-blocking (per requirements)
- **Future**: Consider python:3.11-slim-alpine or exclude evaluation code

### 2. Multiple create_all() Calls
- **Location**: app/main.py, app/approval.py, app/kb.py, app/tools.py
- **Status**: Intentionally preserved per requirements
- **Reason**: Migration strategy needs verification before removal
- **Next**: Test fresh DB, existing DB stamp, then remove

### 3. .env Configuration
- **Issue**: .env may have `localhost` instead of `postgres` for Docker
- **Fix**: Update DATABASE_URL in .env before running docker-compose
- **Required**: `DATABASE_URL=postgresql://...@postgres:5432/...`

### 4. Health Check Dependency
- **Issue**: Health check uses curl
- **Status**: Curl included in Dockerfile
- **Alternative**: Could use Python-based health checks

## CI/CD Testing Checklist

### GitHub Actions
- [ ] Push to GitHub
- [ ] Verify test job passes
- [ ] Verify docker job passes
- [ ] Check Trivy scan results (should report, not fail)
- [ ] Verify image size is reported
- [ ] Verify no secrets detected in image

## Security Checklist

- [x] `.env` never committed to Git
- [x] `.env` has no Git history
- [x] `.env.example` provided
- [x] Non-root user in Docker (UID 1000)
- [x] No secrets in Docker image
- [x] `.dockerignore` excludes secrets
- [x] Trivy security scanning enabled
- [ ] `OPENAI_API_KEY` set securely in production
- [ ] `JWT_SECRET_KEY` is random >= 32 chars
- [ ] TLS configured (if internet-facing)
- [ ] Rate limiting enabled (if internet-facing)

## Documentation Checklist

- [x] `README.md` - Quick start guide
- [x] `DEPLOYMENT.md` - Comprehensive deployment guide
- [x] `STAGE11_IMPLEMENTATION.md` - Implementation summary
- [x] `STAGE11_VERIFICATION_CHECKLIST.md` - This file
- [x] `.env.example` - Environment template
- [x] Production scope clearly documented
- [x] Redis durability limitations documented
- [x] Security warnings in `.gitignore`

## Final Verification

### Before Considering Complete:
1. [ ] Full stack deployed successfully with docker-compose
2. [ ] All health endpoints working
3. [ ] Migration service completes successfully
4. [ ] API accepts requests
5. [ ] Worker processes tickets
6. [ ] Graceful shutdown works
7. [ ] Existing tests pass
8. [ ] CI pipeline passes
9. [ ] Fresh DB migration tested
10. [ ] Existing DB stamp strategy documented

### Current Status:
- ✅ All code implemented per specifications
- ✅ Docker images build successfully
- ✅ Non-root user verified
- ✅ No secrets in images
- ✅ Documentation complete
- ⏳ Full stack deployment requires valid .env
- ⏳ CI pipeline needs GitHub push to verify
- ⏳ Fresh DB migration needs testing
- ⏳ Existing DB stamp strategy needs documentation

## Notes

1. **Do NOT commit yet** - Per instructions, implementation complete but not committed
2. **Migration strategy** - Keep create_all() until migration tested on fresh + existing DBs
3. **Image size** - Large but acceptable for ML workload, documented
4. **Production scope** - Clearly labeled as staging/controlled environment
5. **Redis durability** - AOF + limitations documented per requirements

## Next Steps

1. Update `.env` with correct DATABASE_URL for Docker services
2. Test full stack deployment: `docker-compose up -d`
3. Verify all services healthy: `docker-compose ps`
4. Test health endpoints
5. Run functional tests (register, login, create ticket, process)
6. Test graceful shutdown: `docker-compose down`
7. Test fresh DB migration (with -v flag)
8. Push to GitHub to verify CI
9. Document existing DB stamp strategy
10. THEN commit if all verifications pass

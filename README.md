# Support Flow - AI-Powered Customer Support System

Production-ready Docker Compose deployment for a controlled/staging environment.

## Quick Start

```bash
# 1. Copy environment template
cp .env.example .env

# 2. Edit .env and set required secrets:
#    - OPENAI_API_KEY
#    - JWT_SECRET_KEY (min 32 characters)

# 3. Build and start services
docker-compose up -d

# 4. Verify deployment
curl http://localhost:8000/health/ready
```

## Architecture

- **API Service** (port 8000): FastAPI REST API with JWT authentication
- **Worker Service**: Background job processor for ticket classification
- **PostgreSQL**: Database with pgvector extension for semantic search
- **Redis**: Job queue with AOF persistence
- **Migration Service**: One-shot Alembic migration runner

## Features

- ✅ Multi-stage Docker builds with non-root user
- ✅ Dedicated migration service with PostgreSQL advisory locks
- ✅ Health checks (`/health/live`, `/health/ready`)
- ✅ Redis AOF persistence
- ✅ Graceful shutdown handling
- ✅ Security scanning in CI (Trivy)
- ✅ OpenTelemetry observability support

## Documentation

- **[DEPLOYMENT.md](DEPLOYMENT.md)** - Complete deployment guide
  - Setup instructions
  - Service architecture
  - Health checks
  - Troubleshooting
  - Security checklist
  - Backup/restore procedures

## API Endpoints

### Authentication
- `POST /auth/register` - Register new customer
- `POST /auth/login` - Login and receive JWT token

### Support
- `POST /support/process` - Process support message (requires JWT)
- `POST /tickets` - Create support ticket
- `GET /tickets/{id}` - Get ticket status

### Orders
- `GET /orders/{id}` - Get order status (requires JWT + ownership)

### Health
- `GET /health/live` - Liveness probe (process-only)
- `GET /health/ready` - Readiness probe (PostgreSQL + Redis)

## Development

### Run Tests

```bash
# Install dependencies
pip install -r requirements.txt
pip install -r requirements-test.txt

# Run tests
pytest -v
```

### Local Development (without Docker)

```bash
# Start PostgreSQL and Redis
docker-compose up -d postgres redis

# Update .env for local services
DATABASE_URL=postgresql://supportflow_user:supportflow_password@localhost:5432/supportflow
REDIS_HOST=localhost

# Run migrations
python -m app.migrate

# Start API
uvicorn app.main:app --reload

# Start worker (in another terminal)
python -m app.worker
```

## Production Scope

This deployment is suitable for:
- ✅ Staging environments
- ✅ Controlled internal deployments
- ✅ Development teams

**NOT suitable for internet-facing production** without:
- ❌ TLS/reverse proxy
- ❌ Rate limiting
- ❌ External backup/restore
- ❌ Load balancing

See [DEPLOYMENT.md](DEPLOYMENT.md) for production hardening steps.

## Security

⚠️ **CRITICAL**: Never commit `.env` to Git.

If `.env` was ever committed:
1. Remove from Git history (use `git-filter-repo` or BFG Repo-Cleaner)
2. Rotate ALL secrets immediately
3. Force-push the cleaned repository

See `.gitignore` for security warnings.

## CI/CD

GitHub Actions pipeline includes:
- Deterministic test suite (no LLM calls)
- Docker image build
- Non-root user verification
- Health endpoint testing
- Secret scanning
- Image size reporting (non-blocking)
- Trivy vulnerability scanning

## License

See [LICENSE](LICENSE) file.

#!/bin/bash
set -e

# Entrypoint script for Support Flow containers
# Handles service-specific initialization

SERVICE_TYPE="${SERVICE_TYPE:-api}"

echo "🚀 Starting Support Flow - Service: ${SERVICE_TYPE}"

case "${SERVICE_TYPE}" in
  api)
    echo "📡 Starting API server..."
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000
    ;;
  
  worker)
    echo "⚙️  Starting background worker..."
    exec python -m app.worker
    ;;
  
  migrate)
    echo "🗄️  Running database migrations..."
    exec python -m app.migrate
    ;;
  
  *)
    echo "❌ Unknown SERVICE_TYPE: ${SERVICE_TYPE}"
    echo "Valid options: api, worker, migrate"
    exit 1
    ;;
esac

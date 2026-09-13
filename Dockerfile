# Multi-stage build for production-ready Docker image
# Base stage with shared dependencies
FROM python:3.11-slim AS base

# Security: Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser -u 1000 appuser

WORKDIR /app

# Install system dependencies required for PostgreSQL, vector extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Builder stage: copy application code
FROM base AS builder
COPY app/ ./app/
COPY evaluation/ ./evaluation/

# Production stage
FROM base AS production

# Copy application code from builder
COPY --from=builder /app/app ./app
COPY --from=builder /app/evaluation ./evaluation

# Copy Alembic configuration
COPY alembic.ini .
COPY alembic/ ./alembic/

# Copy entrypoint script
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# Change ownership to non-root user
RUN chown -R appuser:appuser /app

# Create home directory and cache directories for appuser
RUN mkdir -p /home/appuser/.cache/huggingface && \
    chown -R appuser:appuser /home/appuser

# Set environment variables for model cache
ENV HOME=/home/appuser
ENV HF_HOME=/home/appuser/.cache/huggingface
ENV TRANSFORMERS_CACHE=/home/appuser/.cache/huggingface

# Switch to non-root user
USER appuser

# Expose API port
EXPOSE 8000

# Default to API service (can be overridden)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

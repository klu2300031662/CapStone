# ============================================================
# Dockerfile for Adaptive Traffic Protection Platform
# B.Tech Capstone Project
# ============================================================

# ---------------------
# Stage 1: Base image
# ---------------------
FROM python:3.11-slim AS base

# Set environment variables to prevent Python from writing .pyc files
# and to ensure stdout/stderr are unbuffered
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ---------------------
# Stage 2: Dependencies
# ---------------------
FROM base AS dependencies

WORKDIR /app

# Copy only the requirements file first to leverage Docker layer caching.
# This means pip install only re-runs when requirements.txt actually changes.
COPY requirements.txt .

# Install all Python dependencies (FastAPI, Streamlit, Locust, etc.)
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# ---------------------
# Stage 3: Final image
# ---------------------
FROM dependencies AS final

WORKDIR /app

# Create a non-root user for security best practices
RUN adduser --disabled-password --gecos "" appuser

# Copy the entire project into the container
COPY . .

# Change ownership of the app directory to the non-root user
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose the gateway port
EXPOSE 8000

# Default command: run the gateway
# This can be overridden in docker-compose.yml per-service
CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]

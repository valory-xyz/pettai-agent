# Dockerfile for Pett Agent - Olas SDK Compliant
# Image naming convention: <author_name>/oar-<agent_name>:<agent_package_hash>
#
# Multi-platform build support:
# - Build for AMD64: docker build --platform linux/amd64 -t <image:tag> .
# - Build for ARM64: docker build --platform linux/arm64 -t <image:tag> .
# - Build for current platform: docker build -t <image:tag> .
#
# Note: When pushing, only the platform you built will be pushed (not a multi-arch manifest)

# ARG for platform (automatically set by Docker when using --platform flag)
ARG TARGETPLATFORM
ARG BUILDPLATFORM

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies including Node.js 20
RUN apt-get update && apt-get install -y \
    git \
    curl \
    build-essential \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g yarn \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the agent code
COPY agent/ ./agent/
COPY run.py .
COPY typing_extensions_patch.py .

# Copy frontend code
COPY frontend/ ./frontend/

# Build React app
RUN cd frontend && \
    yarn install --frozen-lockfile && \
    yarn build && \
    rm -rf node_modules

# Create necessary directories
RUN mkdir -p logs

# Expose health check port (Olas SDK requirement)
EXPOSE 8716

# Health check (Olas SDK requirement)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8716/healthcheck || exit 1

# Set environment variables
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# give perms to write on logs directory and log.txt file
RUN chmod -R 777 /app/

# Make run.py executable
RUN chmod +x run.py

# ENTRYPOINT as required by Olas SDK
ENTRYPOINT ["python", "run.py"]

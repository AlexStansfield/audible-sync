# Use a lightweight Python base image
FROM python:3.12-slim-trixie

# Install UV
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install ffmpeg and system dependencies
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY uv.lock .
COPY pyproject.toml .
RUN uv sync --locked --no-dev

# Copy the rest of your source code
COPY . .

# The API listens here; see src/service.py for the environment variables
EXPOSE 8080

# Unauthenticated health check, so a bad token cannot make the container look down
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python3 -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4).status == 200 else 1)"

# Run the background service. For a single sync and exit, run `python -m src.main` instead.
CMD ["uv", "run", "python", "-m", "src.service"]

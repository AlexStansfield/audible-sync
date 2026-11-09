# The web app: built here, only its dist/ reaches the image
FROM node:24-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

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

# The dependencies first, in a layer of their own that a source change does not invalidate
COPY uv.lock .
COPY pyproject.toml .
RUN uv sync --locked --no-dev --no-install-project

# Then the source, and the project itself, installed editable so its paths stay under /app
COPY . .
RUN uv sync --locked --no-dev

# The web app's build, served by the service at every path outside /api
COPY --from=web /web/dist /app/web/dist

# The API listens here; see src/audible_sync/service.py for the environment variables
EXPOSE 8080

# Unauthenticated health check, so a bad token cannot make the container look down
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python3 -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4).status == 200 else 1)"

# Run the background service. For a single sync and exit, run `python -m audible_sync.main` instead.
# --no-sync: the environment was installed above; without it `uv run` would re-sync at every
# start, pulling the dev group in and needing the network just to boot.
CMD ["uv", "run", "--no-sync", "python", "-m", "audible_sync.service"]

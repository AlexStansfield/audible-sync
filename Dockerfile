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
RUN uv sync --locked

# Copy the rest of your source code
COPY . .

# Set the default command to run your script
CMD ["uv", "run", "python", "-m", "src.main"]
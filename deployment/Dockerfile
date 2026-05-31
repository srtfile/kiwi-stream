# ── Kiwi Stream API — Docker Image ───────────────────────────────────────────
# Runs FlareSolverr (Chrome headless) + FastAPI in one container
#
# Build:  docker build -t kiwi-stream .
# Run:    docker run -p 8000:8000 kiwi-stream
# Pull:   docker pull ghcr.io/srtfile/kiwi-stream:latest

FROM python:3.11-slim

WORKDIR /app

# ── System deps for Chrome (FlareSolverr needs it) ────────────────────────────
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    gnupg \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libxss1 \
    libxtst6 \
    xdg-utils \
    supervisor \
    && rm -rf /var/lib/apt/lists/*

# ── Install FlareSolverr ──────────────────────────────────────────────────────
RUN pip install --no-cache-dir flaresolverr

# ── Install Python dependencies ───────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Copy app files ────────────────────────────────────────────────────────────
COPY kiwi_extractor_flaresolverr.py .
COPY api_server.py .

# ── Supervisor config: run FlareSolverr + API together ───────────────────────
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

EXPOSE 8000
EXPOSE 8191

# Start both services via supervisor
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]

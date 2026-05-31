# ── Kiwi Stream API — Docker Image ───────────────────────────────────────────
# Two services in one container:
#   1. FlareSolverr (port 8191) — Cloudflare bypass via headless Chrome
#   2. FastAPI server (port 8000) — Kiwi Stream REST API
#
# Build:  docker build -t kiwi-stream .
# Run:    docker run -p 8000:8000 kiwi-stream
# Pull:   docker pull ghcr.io/srtfile/kiwi-stream:latest

FROM python:3.11-slim

WORKDIR /app

# ── System dependencies ───────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    gnupg \
    ca-certificates \
    supervisor \
    xvfb \
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
    && rm -rf /var/lib/apt/lists/*

# ── Install Google Chrome ─────────────────────────────────────────────────────
RUN wget -q -O /tmp/chrome.deb \
    https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get install -y /tmp/chrome.deb \
    && rm /tmp/chrome.deb

# ── Install FlareSolverr ──────────────────────────────────────────────────────
RUN wget -q -O /tmp/flaresolverr.tar.gz \
    https://github.com/FlareSolverr/FlareSolverr/releases/download/v3.3.21/flaresolverr_linux_x64.tar.gz \
    && tar -xzf /tmp/flaresolverr.tar.gz -C /opt \
    && rm /tmp/flaresolverr.tar.gz \
    && chmod +x /opt/flaresolverr/flaresolverr

# ── Install Python dependencies ───────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Copy app files ────────────────────────────────────────────────────────────
COPY kiwi_extractor_flaresolverr.py .
COPY api_server.py .

# ── Supervisor config ─────────────────────────────────────────────────────────
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

EXPOSE 8000
EXPOSE 8191

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]

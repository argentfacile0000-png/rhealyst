FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WEBAPP_HOST=0.0.0.0 \
    WEBAPP_TRUSTED_PROXIES=*

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fontconfig ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt yt-dlp

COPY . .

VOLUME ["/app/data", "/app/secrets"]
EXPOSE 8080

CMD ["python", "-m", "storyforge", "run"]

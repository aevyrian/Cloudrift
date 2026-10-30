FROM python:3.12-slim-bookworm@sha256:57d96f71de145a98e7ceca18ee4ade77bba4103a33a72dfaea026b23625c6bf0 AS vaapi
COPY tools/build_vaapi.sh /tmp/build_vaapi.sh
COPY tools/vaapi/ /tmp/vaapi-cache/
RUN mkdir /runtime && \
    if [ -f /tmp/vaapi-cache/runtime.tar ]; then \
      cd /tmp/vaapi-cache && sha256sum -c SHA256SUMS && tar -xf runtime.tar -C /runtime; \
    else \
      sh /tmp/build_vaapi.sh /tmp/vaapi-build /tmp/vaapi-cache/libva-2.22.0.tar.gz && \
      tar -xf /tmp/vaapi-build/runtime.tar -C /runtime; \
    fi

FROM python:3.12-slim-bookworm@sha256:57d96f71de145a98e7ceca18ee4ade77bba4103a33a72dfaea026b23625c6bf0
COPY --from=vaapi /runtime/ /
WORKDIR /app
LABEL org.opencontainers.image.title="Cloudrift" \
    org.opencontainers.image.source="https://github.com/aevyrian/Cloudrift" \
    org.opencontainers.image.licenses="GPL-3.0" \
    org.opencontainers.image.version="1.4.0"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/opt/python \
    APP_DATA=/data APP_MEDIA=/media APP_CORE=/app/vendor/TikTokDownloader
COPY requirements.txt ./
COPY tools/prepare_ffmpeg.py /tmp/prepare_ffmpeg.py
COPY tools/ffmpeg/ /tmp/ffmpeg/
RUN python /tmp/prepare_ffmpeg.py /usr/local/bin /tmp/ffmpeg/ffmpeg.tar.xz && rm -rf /tmp/ffmpeg /tmp/prepare_ffmpeg.py
COPY vendor/TikTokDownloader/requirements.txt vendor/TikTokDownloader/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=bind,source=tools/wheels,target=/wheels \
    if [ -f /wheels/requirements.sha256 ]; then \
      sha256sum -c /wheels/requirements.sha256 && \
      pip install --no-index --find-links=/wheels --require-hashes --target=/opt/python -r /wheels/requirements.txt; \
    else \
      pip install --only-binary=:all: --timeout=120 --retries=5 --target=/opt/python -r requirements.txt; \
    fi
COPY app ./app
COPY LICENSE THIRD_PARTY_NOTICES.md ./
COPY vendor/TikTokDownloader ./vendor/TikTokDownloader
COPY userscripts ./userscripts
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=5)"]
CMD ["python", "-m", "app"]

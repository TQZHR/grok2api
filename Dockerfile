FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    VIRTUAL_ENV=/opt/venv

ENV PATH="$VIRTUAL_ENV/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN python -m venv "$VIRTUAL_ENV" \
    && pip install --no-cache-dir uv

COPY pyproject.toml uv.lock /app/

RUN uv sync --frozen --no-dev --no-install-project --active

# Pre-install Playwright Chromium + OS deps to make auto-register/solver usable in Docker
# without doing `apt-get` at runtime.
RUN python -m playwright install --with-deps chromium

COPY config.defaults.toml /app/config.defaults.toml
COPY app /app/app
COPY main.py /app/main.py
COPY scripts /app/scripts

# When building on Windows, shell scripts may be copied with CRLF endings and
# without executable bit. Normalize both to keep ENTRYPOINT reliable.
RUN sed -i 's/\r$//' /app/scripts/*.sh || true \
    && chmod +x /app/scripts/*.sh || true

RUN mkdir -p /app/data /app/data/tmp /app/logs

EXPOSE 8000

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["/app/scripts/start.sh"]

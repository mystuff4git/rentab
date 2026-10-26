# syntax=docker/dockerfile:1
# Rentab в контейнере (ADR 0010):
#
#   docker compose up --build          # http://localhost:8501
#
# или без compose — с теми же ограничениями, что в compose.yaml:
#
#   docker build -t rentab .
#   docker run --rm -p 127.0.0.1:8501:8501 --read-only --tmpfs /tmp:size=64m,mode=1777 \
#     --cap-drop ALL --security-opt no-new-privileges rentab
#
# Python — 3.13, как в CI (ячейка с LibreOffice) и на Streamlit Community Cloud.
# Зависимости — строго по uv.lock: ядро и группа `app`, без dev и legacy.
#
# За прокси, который подменяет сертификаты (TLS-инспекция в корпоративной сети),
# передайте его корневой сертификат секретом — в образ он не попадёт:
#
#   docker build --secret id=ca,src=corporate-ca.pem -t rentab .
ARG PYTHON_IMAGE=python:3.13-slim-trixie

FROM ghcr.io/astral-sh/uv:0.11.32 AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
# Байткод компилируется при сборке: в образе только для чтения его не записать.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/rentab
WORKDIR /src
# Сначала зависимости: слой не пересобирается, пока не изменился uv.lock.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=secret,id=ca,required=false \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    if [ -f /run/secrets/ca ]; then export SSL_CERT_FILE=/run/secrets/ca; fi \
    && uv sync --locked --no-default-groups --group app --no-install-project --no-editable
# Потом сам Rentab — колесом в окружение, исходники ядра в образ не попадают.
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export SSL_CERT_FILE=/run/secrets/ca; fi \
    && uv sync --locked --no-default-groups --group app --no-editable

FROM ${PYTHON_IMAGE}
ARG VERSION=1.0.0
LABEL org.opencontainers.image.title="Rentab" \
      org.opencontainers.image.description="Стоимость и рентабельность проектов IP-юридического консалтинга (РФ, РК)" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.source="https://github.com/mystuff4git/rentab" \
      org.opencontainers.image.licenses="MIT"
# Непривилегированный пользователь без домашнего каталога: писать ему некуда,
# кроме /tmp (tmpfs при запуске). Файлы образа принадлежат root.
RUN groupadd --system --gid 10001 rentab \
    && useradd --system --uid 10001 --gid rentab --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin rentab
COPY --from=build /opt/rentab /opt/rentab
WORKDIR /app
# Streamlit читает .streamlit/config.toml из рабочего каталога.
COPY .streamlit/config.toml ./.streamlit/config.toml
COPY app ./app
# browser.serverAddress задан, поэтому Streamlit при старте не спрашивает свой
# внешний IP у checkip.amazonaws.com (он нужен только для строки «External URL»).
ENV PATH=/opt/rentab/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_FILE_WATCHER_TYPE=none \
    STREAMLIT_BROWSER_SERVER_ADDRESS=localhost
USER rentab:rentab
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --start-interval=2s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=4)"]
CMD ["streamlit", "run", "app/Home.py"]

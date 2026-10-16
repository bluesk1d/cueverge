# syntax=docker/dockerfile:1

ARG PYTHON_IMAGE=python:3.12-slim-bookworm
ARG NODE_IMAGE=node:22-bookworm-slim
ARG UV_VERSION=0.11.23

FROM ${NODE_IMAGE} AS web-build

WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM ${PYTHON_IMAGE} AS python-build

ARG UV_VERSION
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /build
RUN pip install --no-cache-dir "uv==${UV_VERSION}"
COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
RUN uv sync --locked --no-dev --no-editable

FROM ${PYTHON_IMAGE} AS runtime

ARG INSTALL_CLAMAV=false
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates ffmpeg \
    && if [ "${INSTALL_CLAMAV}" = "true" ]; then \
         apt-get install --yes --no-install-recommends clamav; \
       fi \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system cueverge \
    && useradd --system --gid cueverge --home-dir /var/lib/cueverge cueverge \
    && mkdir -p /opt/cueverge/web/dist /var/lib/cueverge/gui \
    && chown -R cueverge:cueverge /var/lib/cueverge

WORKDIR /opt/cueverge
COPY --from=python-build /build/.venv /opt/cueverge/.venv
COPY --from=web-build /build/web/dist /opt/cueverge/web/dist
COPY shows/demo /opt/cueverge/shows/demo
COPY docker/entrypoint.sh /usr/local/bin/cueverge-entrypoint

ENV PATH="/opt/cueverge/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CUEVERGE_SHOW_PACKAGE=/opt/cueverge/shows/demo \
    CUEVERGE_GUI_DATA_ROOT=/var/lib/cueverge/gui \
    CUEVERGE_GUI_WEB_ROOT=/opt/cueverge/web/dist

VOLUME ["/var/lib/cueverge"]
EXPOSE 8000 7443
USER cueverge

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3).read()"]

ENTRYPOINT ["cueverge-entrypoint"]
CMD ["python", "-m", "uvicorn", "cueverge.controller.app:app", "--host", "0.0.0.0", "--port", "8000"]

# syntax=docker/dockerfile:1

ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ARG NODE_IMAGE=node:22-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392
ARG UV_VERSION=0.11.23

# The web bundle is architecture-independent, so build it natively instead of under emulation.
FROM --platform=$BUILDPLATFORM ${NODE_IMAGE} AS web-build

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

# Offline media agent bundles the controller serves for managed agent updates. Bundles are
# cross-resolved for every agent platform, so build them once natively instead of per target
# architecture; for native targets these steps share the python-build cache.
FROM --platform=$BUILDPLATFORM ${PYTHON_IMAGE} AS agent-bundles

ARG UV_VERSION
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /build
RUN pip install --no-cache-dir "uv==${UV_VERSION}"
COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
RUN uv sync --locked --no-dev --no-editable

ARG CUEVERGE_BUILD_LABEL=image
ARG CUEVERGE_AGENT_PLATFORMS="linux_x86_64 linux_aarch64 windows_amd64"
RUN set -eu; \
    platforms=""; \
    for platform in ${CUEVERGE_AGENT_PLATFORMS}; do platforms="$platforms --platform $platform"; done; \
    /build/.venv/bin/python -m cueverge.agent_bundle --output /build/agent-bundles \
      --project /build --build "${CUEVERGE_BUILD_LABEL}" $platforms

FROM ${PYTHON_IMAGE} AS runtime

ARG INSTALL_CLAMAV=false
COPY deploy/controller-native-dependencies.json /opt/cueverge/deploy/controller-native-dependencies.json
COPY docker/native_dependency_preflight.py /opt/cueverge/deploy/native_dependency_preflight.py
RUN apt-get update \
    && REQUIRED_PACKAGES="$(python /opt/cueverge/deploy/native_dependency_preflight.py \
         /opt/cueverge/deploy/controller-native-dependencies.json --apt-packages)" \
    && apt-get install --yes --no-install-recommends ${REQUIRED_PACKAGES} \
    && if [ "${INSTALL_CLAMAV}" = "true" ]; then \
         OPTIONAL_PACKAGES="$(python /opt/cueverge/deploy/native_dependency_preflight.py \
           /opt/cueverge/deploy/controller-native-dependencies.json \
           --apt-packages --include-optional)"; \
         apt-get install --yes --no-install-recommends ${OPTIONAL_PACKAGES}; \
       fi \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system cueverge \
    && useradd --system --gid cueverge --home-dir /var/lib/cueverge cueverge \
    && mkdir -p /opt/cueverge/web/dist /var/lib/cueverge/gui \
    && chown -R cueverge:cueverge /var/lib/cueverge

WORKDIR /opt/cueverge
COPY --from=python-build /build/.venv /opt/cueverge/.venv
COPY --from=web-build /build/web/dist /opt/cueverge/web/dist
COPY --from=agent-bundles /build/agent-bundles /opt/cueverge/agent-bundles
COPY shows/demo /opt/cueverge/shows/demo
COPY docker/entrypoint.sh /usr/local/bin/cueverge-entrypoint

ENV PATH="/opt/cueverge/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CUEVERGE_SHOW_PACKAGE=/opt/cueverge/shows/demo \
    CUEVERGE_GUI_DATA_ROOT=/var/lib/cueverge/gui \
    CUEVERGE_GUI_WEB_ROOT=/opt/cueverge/web/dist \
    CUEVERGE_AGENT_BUNDLE_ROOT=/opt/cueverge/agent-bundles

VOLUME ["/var/lib/cueverge"]
EXPOSE 8000 7443
USER cueverge

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3).read()"]

ENTRYPOINT ["cueverge-entrypoint"]
CMD ["python", "-m", "uvicorn", "cueverge.controller.app:app", "--host", "0.0.0.0", "--port", "8000"]

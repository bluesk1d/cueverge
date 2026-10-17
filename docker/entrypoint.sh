#!/bin/sh
set -eu

PREFLIGHT_ARGUMENTS=""
if [ "${CUEVERGE_CLAMAV_REQUIRED:-false}" = "true" ]; then
    PREFLIGHT_ARGUMENTS="--require-optional"
fi
python /opt/cueverge/deploy/native_dependency_preflight.py \
    /opt/cueverge/deploy/controller-native-dependencies.json \
    ${PREFLIGHT_ARGUMENTS}

if [ "${CUEVERGE_GUI_ENABLED:-false}" = "true" ]; then
    if [ ! -f "${CUEVERGE_GUI_WEB_ROOT}/index.html" ]; then
        echo "GUI is enabled but its production build is unavailable" >&2
        exit 1
    fi
    if [ ! -d "${CUEVERGE_GUI_DATA_ROOT}" ] || [ ! -w "${CUEVERGE_GUI_DATA_ROOT}" ]; then
        echo "GUI data root is unavailable or not writable: ${CUEVERGE_GUI_DATA_ROOT}" >&2
        exit 1
    fi
fi

exec "$@"

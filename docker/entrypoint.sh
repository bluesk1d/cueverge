#!/bin/sh
set -eu

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "required deployment tool is unavailable: $1" >&2
        exit 1
    fi
}

require_command ffmpeg
require_command ffprobe

if [ "${CUEVERGE_CLAMAV_REQUIRED:-false}" = "true" ]; then
    require_command clamscan
    if ! clamscan --version | grep -qE '/[0-9]+'; then
        echo "ClamAV is required but no virus definitions are installed" >&2
        exit 1
    fi
fi

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

echo "ffmpeg: $(ffmpeg -version | sed -n '1p')"
echo "ffprobe: $(ffprobe -version | sed -n '1p')"
if command -v clamscan >/dev/null 2>&1; then
    echo "clamscan: $(clamscan --version | sed -n '1p')"
fi

exec "$@"

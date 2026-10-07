#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
runtime_dir="$project_root/.runtime"
uv_dir="$runtime_dir/uv"
uv_path="$uv_dir/uv"

# Keep the Python runtime, dependencies and startup tool local to this project.
export UV_CACHE_DIR="$runtime_dir/cache"
export UV_PYTHON_INSTALL_DIR="$runtime_dir/python"
export UV_PYTHON_INSTALL_BIN=0
export UV_PYTHON_INSTALL_REGISTRY=0
export PYTHONUTF8=1
export PYTHONUNBUFFERED=1
if [ -z "${UV_DEFAULT_INDEX:-}" ] && [ -z "${UV_INDEX_URL:-}" ]; then
    export UV_DEFAULT_INDEX='https://pypi.tuna.tsinghua.edu.cn/simple'
fi
export UV_PYTHON_INSTALL_MIRROR="${UV_PYTHON_INSTALL_MIRROR:-https://registry.npmmirror.com/-/binary/python-build-standalone}"

if [ ! -x "$uv_path" ]; then
    if ! command -v unzip >/dev/null 2>&1; then
        echo '[ERROR] Install unzip, then run sh start.sh again.' >&2
        exit 1
    fi
    case "$(uname -s)-$(uname -m)" in
        Darwin-x86_64) platform='macos-x86_64' ;;
        Darwin-arm64) platform='macos-aarch64' ;;
        Linux-x86_64)
            platform='linux-x86_64'
            if ldd --version 2>&1 | grep -qi musl; then platform='linux-x86_64-musl'; fi
            ;;
        Linux-aarch64|Linux-arm64) platform='linux-aarch64' ;;
        *) echo '[ERROR] EasyWrite startup supports 64-bit Linux / macOS (x64 / ARM64).' >&2; exit 1 ;;
    esac
    artifact=$(awk -F '|' -v target="$platform" '$1 == target { print $2; exit }' "$project_root/scripts/uv-downloads.txt")
    expected_sha=$(awk -F '|' -v target="$platform" '$1 == target { print $3; exit }' "$project_root/scripts/uv-downloads.txt")
    if [ -z "$artifact" ] || [ -z "$expected_sha" ]; then
        echo '[ERROR] No startup tool is available for this platform.' >&2
        exit 1
    fi
    mkdir -p "$uv_dir"
    wheel="$runtime_dir/uv.whl"
    uv_mirror="${EASYWRITE_UV_MIRROR:-https://pypi.tuna.tsinghua.edu.cn}"
    wheel_url="${uv_mirror%/}/$artifact"
    echo '[INFO] Downloading the project-local startup tool from the package mirror...'
    if command -v curl >/dev/null 2>&1; then
        curl --fail --location --silent --show-error --connect-timeout 20 --max-time 120 \
            "$wheel_url" --output "$wheel"
    elif command -v wget >/dev/null 2>&1; then
        wget --timeout=60 "$wheel_url" -O "$wheel"
    else
        echo '[ERROR] Install curl or wget, then run sh start.sh again.' >&2
        exit 1
    fi
    if command -v sha256sum >/dev/null 2>&1; then
        actual_sha=$(sha256sum "$wheel" | awk '{ print $1 }')
    elif command -v shasum >/dev/null 2>&1; then
        actual_sha=$(shasum -a 256 "$wheel" | awk '{ print $1 }')
    else
        echo '[ERROR] Install sha256sum or shasum, then run sh start.sh again.' >&2
        exit 1
    fi
    if [ "$actual_sha" != "$expected_sha" ]; then
        echo '[ERROR] The startup tool download has an invalid SHA256. Please retry.' >&2
        exit 1
    fi
    unzip -p "$wheel" 'uv-*.data/scripts/uv' > "$uv_path.part"
    chmod +x "$uv_path.part"
    mv "$uv_path.part" "$uv_path"
fi

echo '[INFO] Preparing Python 3.12 and dependencies. The first launch may take a few minutes.'
exec "$uv_path" run --no-config --no-project --isolated --managed-python --python 3.12 \
    --with-requirements "$project_root/backend/requirements.txt" \
    -- python -u "$project_root/scripts/start.py" "$@"

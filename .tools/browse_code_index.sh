#!/usr/bin/env bash
set -u

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
HOST=${KB_HOST:-127.0.0.1}
PORT=${KB_PORT:-8765}
JSON_PATH="$REPO_ROOT/.tools/code_index.json"
URL="http://$HOST:$PORT/"

if [ "${KB_REINDEX:-auto}" = "1" ] || { [ "${KB_REINDEX:-auto}" = "auto" ] && [ ! -s "$JSON_PATH" ]; }; then
    echo "Updating source index..."
    (cd "$REPO_ROOT" && python3 .tools/index_code.py)
    if [ $? -ne 0 ]; then
        echo "ERROR: source index update failed" >&2
        exit 1
    fi
fi

if [ ! -s "$JSON_PATH" ]; then
    echo "ERROR: $JSON_PATH does not exist. Run: python3 .tools/index_code.py" >&2
    exit 1
fi

echo "Source index browser:"
echo "  $URL"
echo
echo "Controls:"
echo "  KB_HOST=$HOST KB_PORT=$PORT KB_REINDEX=${KB_REINDEX:-auto}"
echo "  Ctrl-C to stop"
echo

cd "$REPO_ROOT" || exit 1
exec python3 .tools/serve_code_index.py --host "$HOST" --port "$PORT"

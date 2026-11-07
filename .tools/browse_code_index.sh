#!/usr/bin/env bash
#
# Open the knowledge base and source index in a browser, rebuilding the index first if it is absent.
#
# Serves .tools/serve_code_index.py on http://127.0.0.1:8765 (KB_HOST / KB_PORT to move it) and
# reindexes when the index is missing, or always with KB_REINDEX=1.
#
# *** IT TESTS THE SQLITE, AND IT ASKS index_code.py FOR THE PATH. ***
#
# It used to test `.tools/code_index.json`: the wrong FILE and the wrong NAME. Wrong file because
# serve_code_index.py forwards every query to query_code_index.py, which opens the SQLITE and never
# reads the export -- and the export is off by default now, so that test would never pass again.
# Wrong name because the real index is branch-suffixed via current_branch_slug(), so the unsuffixed
# path never existed and the "is it missing?" test was ALWAYS true: every launch silently reindexed.
#
# The path is computed by importing index_code rather than rebuilt in shell, because the slug
# sanitises branch names (a branch like test/0.1-foo would otherwise become a directory).
#
set -u

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
HOST=${KB_HOST:-127.0.0.1}
PORT=${KB_PORT:-8765}
DB_PATH=$(cd "$REPO_ROOT" && python3 -c "import sys; sys.path.insert(0, '.tools'); import index_code; print(index_code.DEFAULT_DB)" 2>/dev/null)
if [ -z "$DB_PATH" ]; then
    echo "ERROR: could not ask index_code.py where the index lives" >&2
    exit 1
fi
URL="http://$HOST:$PORT/"

if [ "${KB_REINDEX:-auto}" = "1" ] || { [ "${KB_REINDEX:-auto}" = "auto" ] && [ ! -s "$DB_PATH" ]; }; then
    echo "Updating source index..."
    (cd "$REPO_ROOT" && python3 .tools/index_code.py)
    if [ $? -ne 0 ]; then
        echo "ERROR: source index update failed" >&2
        exit 1
    fi
fi

if [ ! -s "$DB_PATH" ]; then
    echo "ERROR: $DB_PATH does not exist. Run: python3 .tools/index_code.py" >&2
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

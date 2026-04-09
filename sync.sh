#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="$SCRIPT_DIR/config.yaml"

VAULT=$(awk '/^vault_path:/ { print $2 }' "$CONFIG")

cd "$SCRIPT_DIR"
uv run wiki-feeds sync

cd "$VAULT"
git add _raw/
if git diff --cached --quiet; then
    echo "Nothing to commit."
    exit 0
fi
git commit -m "wiki-feeds: sync $(date -u +%Y-%m-%d)"
git push

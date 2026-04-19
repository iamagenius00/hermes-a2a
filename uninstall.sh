#!/bin/bash
# Uninstall hermes-a2a from your Hermes Agent installation.
# Usage: ./uninstall.sh [HERMES_DIR]

set -e

HERMES_DIR="${1:-$HOME/.hermes/hermes-agent}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -f "$HERMES_DIR/run_agent.py" ]; then
    echo "Error: Hermes Agent not found at $HERMES_DIR"
    echo "Usage: $0 /path/to/hermes-agent"
    exit 1
fi

echo "Uninstalling hermes-a2a from $HERMES_DIR ..."

# Remove A2A files
for f in tools/a2a_security.py tools/a2a_tools.py gateway/platforms/a2a.py; do
    if [ -f "$HERMES_DIR/$f" ]; then
        rm "$HERMES_DIR/$f"
        echo "  - $f"
    fi
done

# Reverse the patch
if [ -f "$SCRIPT_DIR/patches/hermes-a2a.patch" ]; then
    echo ""
    echo "Reversing patch..."
    cd "$HERMES_DIR"
    if git apply -R "$SCRIPT_DIR/patches/hermes-a2a.patch" 2>/dev/null; then
        echo "  Patch reversed successfully."
    else
        echo "  Warning: Could not auto-reverse patch. You may need to manually revert changes in:"
        echo "    - gateway/config.py (remove Platform.A2A)"
        echo "    - gateway/run.py (remove A2A adapter registration)"
        echo "    - toolsets.py (remove a2a toolset)"
        echo "    - hermes_cli/platforms.py (remove a2a entry)"
        echo "    - pyproject.toml (remove aiohttp dependency)"
    fi
fi

# Clean up .bak files
for f in tools/a2a_security.py.bak tools/a2a_tools.py.bak gateway/platforms/a2a.py.bak; do
    [ -f "$HERMES_DIR/$f" ] && rm "$HERMES_DIR/$f"
done

echo ""
echo "Done. Remove A2A_ENABLED from ~/.hermes/.env and restart gateway."

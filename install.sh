#!/bin/bash
# Install hermes-a2a into your Hermes Agent installation.
# Usage: ./install.sh [HERMES_DIR]

set -e

HERMES_DIR="${1:-$HOME/.hermes/hermes-agent}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -f "$HERMES_DIR/run_agent.py" ]; then
    echo "Error: Hermes Agent not found at $HERMES_DIR"
    echo "Usage: $0 /path/to/hermes-agent"
    exit 1
fi

echo "Installing hermes-a2a into $HERMES_DIR ..."

# Copy gateway adapter
cp "$SCRIPT_DIR/gateway_adapter/a2a.py" "$HERMES_DIR/gateway/platforms/a2a.py"
echo "  + gateway/platforms/a2a.py"

# Copy client tools
cp "$SCRIPT_DIR/client_tools/a2a_tools.py" "$HERMES_DIR/tools/a2a_tools.py"
echo "  + tools/a2a_tools.py"

echo ""
echo "Files copied. You still need to:"
echo ""
echo "1. Add Platform.A2A to gateway/config.py"
echo "2. Register A2AAdapter in gateway/run.py"
echo "3. Add 'a2a' to hermes_cli/tools_config.py PLATFORMS"
echo "4. Set A2A_ENABLED=true in ~/.hermes/.env"
echo ""
echo "See README.md for details, or apply the patch:"
echo "  cd $HERMES_DIR && git apply $SCRIPT_DIR/patches/hermes-a2a.patch"

#!/bin/bash
# Install hermes-a2a as a Hermes plugin.
# Usage: ./install.sh

set -e

PLUGIN_DIR="$HOME/.hermes/plugins/a2a"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_DIR="$SCRIPT_DIR/plugin"

if [ ! -d "$SOURCE_DIR" ]; then
    echo "Error: plugin/ directory not found"
    exit 1
fi

if [ -d "$PLUGIN_DIR" ]; then
    echo "Backing up existing plugin to $PLUGIN_DIR.bak.$(date +%Y%m%d%H%M%S)"
    cp -r "$PLUGIN_DIR" "$PLUGIN_DIR.bak.$(date +%Y%m%d%H%M%S)"
fi

mkdir -p "$HOME/.hermes/plugins"
cp -r "$SOURCE_DIR" "$PLUGIN_DIR"

echo "Installed to $PLUGIN_DIR"
echo ""
echo "Add to ~/.hermes/.env:"
echo "  A2A_ENABLED=true"
echo "  A2A_PORT=8081"
echo "  # A2A_AUTH_TOKEN=your-secret  (optional)"
echo ""
echo "Then restart Hermes."

#!/bin/bash
# Uninstall hermes-a2a plugin.

set -e

PLUGIN_DIR="$HOME/.hermes/plugins/a2a"

if [ -d "$PLUGIN_DIR" ]; then
    rm -rf "$PLUGIN_DIR"
    echo "Removed $PLUGIN_DIR"
else
    echo "Plugin not found at $PLUGIN_DIR"
fi

echo "Remove A2A_ENABLED from ~/.hermes/.env and restart Hermes."

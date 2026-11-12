#!/usr/bin/env bash
# Remove the zipp-keepalive LaunchAgent. Leaves the script folder in place.
set -euo pipefail
LABEL="com.zippkeepalive"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl unload "$PLIST" 2>/dev/null || true
rm -f "$PLIST"
echo "Uninstalled $LABEL (removed $PLIST)."

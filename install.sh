#!/usr/bin/env bash
# Install zipp-keepalive as a macOS LaunchAgent (starts at login, restarts if it dies).
#
#   ./install.sh                         # eco default (plugged-in only)
#   ./install.sh --nudge-on-battery      # keep awake on battery too
#   ./install.sh --quiet-hours 1-7       # any daemon flags are passed straight through
#
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.zippkeepalive"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY="$(command -v python3 || true)"

[ -n "$PY" ] || { echo "error: python3 not found on PATH" >&2; exit 1; }
mkdir -p "$HOME/Library/LaunchAgents"

# Build the plist for THIS machine (paths + any flags) via plistlib - no fragile text editing.
"$PY" - "$DIR" "$PY" "$PLIST" "$@" <<'PYEOF'
import sys, plistlib
dir_, py, plist, *flags = sys.argv[1:]
plistlib.dump({
    "Label": "com.zippkeepalive",
    "ProgramArguments": [py, f"{dir_}/zipp_keepalive.py", *flags],
    "RunAtLoad": True,
    "KeepAlive": True,
    "ProcessType": "Background",
    "StandardOutPath": f"{dir_}/zipp-keepalive.err",
    "StandardErrorPath": f"{dir_}/zipp-keepalive.err",
}, open(plist, "wb"))
PYEOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo "Installed and loaded $LABEL"
echo "  python : $PY"
echo "  script : $DIR/zipp_keepalive.py"
echo "  flags  : ${*:-"(none - eco default)"}"
echo "  log    : $DIR/zipp-keepalive.log"
echo
echo "Confirm it's running:  launchctl list | grep zipp   (a PID on the left = running)"
echo "Watch it:              tail -f $DIR/zipp-keepalive.log"

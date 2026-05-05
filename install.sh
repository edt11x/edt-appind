#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

install -Dm755 "$SCRIPT_DIR/sni-host.py" "$HOME/.local/bin/sni-host.py"
install -Dm644 "$SCRIPT_DIR/sni-host.service" \
    "$HOME/.config/systemd/user/sni-host.service"

systemctl --user daemon-reload
systemctl --user enable --now sni-host.service

echo "Installed. Status:"
systemctl --user status sni-host.service --no-pager

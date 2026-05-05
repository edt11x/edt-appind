#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

install -Dm755 "$SCRIPT_DIR/sni-host.py" "$HOME/.local/bin/sni-host.py"

# --- systemd user service ---
install -Dm644 "$SCRIPT_DIR/sni-host.service" \
    "$HOME/.config/systemd/user/sni-host.service"
systemctl --user daemon-reload
systemctl --user enable --now sni-host.service

# --- XDG autostart (fallback for XFCE and other non-systemd sessions) ---
# Installs alongside the service; harmless if both are active (the second
# launch will exit immediately because the DBus watcher name is already owned).
install -Dm644 "$SCRIPT_DIR/sni-host.desktop" \
    "$HOME/.config/autostart/sni-host.desktop"

echo ""
echo "Installed:"
echo "  ~/.local/bin/sni-host.py"
echo "  ~/.config/systemd/user/sni-host.service  (enabled)"
echo "  ~/.config/autostart/sni-host.desktop     (XDG autostart fallback)"
echo ""
echo "Systemd service status:"
systemctl --user status sni-host.service --no-pager || true

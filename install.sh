#!/usr/bin/env bash
# Install dell-battery-balance and its timers. Run as root.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "run as root: sudo ./install.sh" >&2
    exit 1
fi

src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

install -Dm755 "$src/dell-battery-balance" /usr/local/bin/dell-battery-balance
install -Dm644 "$src/README.md" /usr/local/share/doc/dell-battery-balance/README.md
install -d -m755 /var/lib/dell-battery-balance

for u in "$src"/systemd/*.service "$src"/systemd/*.timer; do
    install -Dm644 "$u" "/etc/systemd/system/$(basename "$u")"
done

# Polkit action, so the Plasma applet can apply ceilings without a terminal.
install -Dm644 "$src/polkit/com.chiefgyk3d.dellbatterybalance.policy" \
    /usr/share/polkit-1/actions/com.chiefgyk3d.dellbatterybalance.policy

systemctl daemon-reload
systemctl enable --now dell-battery-balance-sample.timer

# The Plasma applet is per-user, so it must not be installed as root.
if [[ -n "${SUDO_USER:-}" ]] && command -v kpackagetool6 >/dev/null; then
    echo "Installing the Plasma applet for $SUDO_USER..."
    sudo -u "$SUDO_USER" kpackagetool6 --type Plasma/Applet \
        --upgrade "$src/plasmoid/package" 2>/dev/null \
        || sudo -u "$SUDO_USER" kpackagetool6 --type Plasma/Applet \
            --install "$src/plasmoid/package"
fi

echo
echo "Installed. Sampling is live; the balancing timer is NOT enabled yet."
echo "Let it gather a week of data first, then:"
echo "    dell-battery-balance report"
echo "    systemctl enable --now dell-battery-balance.timer"
echo
echo "Plasma applet: right-click the panel or system tray -> Add Widgets"
echo "               -> search \"Dell Battery Balance\"."

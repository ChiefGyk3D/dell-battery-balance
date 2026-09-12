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
install -d -m750 /var/lib/dell-battery-balance

for u in "$src"/systemd/*.service "$src"/systemd/*.timer; do
    install -Dm644 "$u" "/etc/systemd/system/$(basename "$u")"
done

systemctl daemon-reload
systemctl enable --now dell-battery-balance-sample.timer

echo
echo "Installed. Sampling is live; the balancing timer is NOT enabled yet."
echo "Let it gather a week of data first, then:"
echo "    dell-battery-balance report"
echo "    systemctl enable --now dell-battery-balance.timer"

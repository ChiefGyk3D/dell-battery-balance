#!/usr/bin/env bash
# Reverse install.sh. Run as root. Pass --purge to also remove
# /etc/dell-battery-balance and /var/lib/dell-battery-balance.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run as root: sudo ./uninstall.sh" >&2; exit 1; }
SVC=dell-battery-balance
purge=0
case "${1:-}" in
    "") ;;
    --purge) purge=1 ;;
    *) echo "usage: ./uninstall.sh [--purge]" >&2; exit 2 ;;
esac

systemctl disable --now "$SVC.timer" 2>/dev/null || true

if [[ -n "${SUDO_USER:-}" ]] && command -v kpackagetool6 >/dev/null; then
    sudo -u "$SUDO_USER" kpackagetool6 --type Plasma/Applet \
        --remove com.chiefgyk3d.dellbatterybalance 2>/dev/null || true
fi

rm -f /etc/systemd/system/$SVC.service /etc/systemd/system/$SVC.timer
rm -f /etc/udev/rules.d/90-$SVC.rules
rm -f /usr/share/polkit-1/actions/com.chiefgyk3d.dellbatterybalance.control.policy \
      /usr/share/polkit-1/actions/com.chiefgyk3d.dellbatterybalance.configure.policy
rm -f /usr/share/knotifications6/dell_battery_balance.notifyrc
rm -f /usr/local/libexec/dell-battery-balance-grant \
      /usr/local/libexec/dbb-control \
      /usr/local/libexec/dbb-configure
rm -f /usr/local/bin/dell-battery-balance
rm -rf /usr/local/lib/$SVC
rm -rf /usr/local/share/doc/$SVC

udevadm control --reload
systemctl daemon-reload

getent passwd "$SVC" >/dev/null && userdel "$SVC" 2>/dev/null || true
getent group "$SVC" >/dev/null && groupdel "$SVC" 2>/dev/null || true

if [[ $purge -eq 1 ]]; then
    rm -rf /etc/$SVC /var/lib/$SVC
    echo "Purged /etc/$SVC and /var/lib/$SVC."
else
    echo "Left /etc/$SVC and /var/lib/$SVC in place. Re-run with --purge to remove them."
fi

echo "Uninstalled."

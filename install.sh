#!/usr/bin/env bash
# Install dell-battery-balance with a scoped service account. Run as root.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run as root: sudo ./install.sh" >&2; exit 1; }
src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SVC=dell-battery-balance
ddv_cycles=0
case "${1:-}" in
    "") ;;
    --ddv-cycles) ddv_cycles=1 ;;
    *) echo "usage: sudo ./install.sh [--ddv-cycles]" >&2; exit 2 ;;
esac

# Python 3.13 or newer is required (argparse "--" handling; see README).
python3 -c 'import sys; sys.exit(sys.version_info < (3, 13))' || {
    echo "Python 3.13 or newer is required (found $(python3 -V 2>&1)). Supported: Debian 13, Parrot, Pop!_OS with a 3.13 interpreter." >&2
    exit 1
}

# Stop anything from the previous layout.
systemctl disable --now dell-battery-balance-sample.timer 2>/dev/null || true
systemctl disable --now dell-battery-balance.timer 2>/dev/null || true
rm -f /etc/systemd/system/dell-battery-balance-sample.{service,timer} \
      /usr/share/polkit-1/actions/com.chiefgyk3d.dellbatterybalance.policy

getent group "$SVC" >/dev/null || groupadd --system "$SVC"
getent passwd "$SVC" >/dev/null || \
    useradd --system --gid "$SVC" --home-dir /var/lib/$SVC --shell /usr/sbin/nologin \
            --comment "dell-battery-balance service" "$SVC"

install -d -m755 /usr/local/lib/$SVC
rm -rf /usr/local/lib/$SVC/dbb
cp -r "$src/dbb" /usr/local/lib/$SVC/
find /usr/local/lib/$SVC -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
# ProtectSystem=strict makes the tree read-only for the service, so
# precompile now -- otherwise every tick would try (and fail) to write
# __pycache__ and recompile from source every 2 minutes.
python3 -m compileall -q /usr/local/lib/$SVC/dbb
chmod -R a+rX /usr/local/lib/$SVC
install -Dm755 "$src/dell-battery-balance" /usr/local/bin/dell-battery-balance
install -Dm755 "$src/libexec/dell-battery-balance-grant" /usr/local/libexec/dell-battery-balance-grant
install -Dm755 "$src/libexec/dell-battery-balance-wake"  /usr/local/libexec/dell-battery-balance-wake
install -Dm755 "$src/libexec/dell-battery-balance-cycles" /usr/local/libexec/dell-battery-balance-cycles
install -Dm755 "$src/libexec/dbb-control"   /usr/local/libexec/dbb-control
install -Dm755 "$src/libexec/dbb-configure" /usr/local/libexec/dbb-configure
install -Dm644 "$src/README.md" /usr/local/share/doc/$SVC/README.md

install -d -m2775 -o "$SVC" -g "$SVC" /var/lib/$SVC
chown -R "$SVC:$SVC" /var/lib/$SVC
find /var/lib/$SVC -type f -exec chmod 664 {} +
# Root-only home for the wake helper's ownership marker -- kept out of the
# service-writable state dir above so the service account can neither forge
# nor clear it (see libexec/dell-battery-balance-wake).
install -d -m755 -o root -g root /var/lib/$SVC-wake
install -d -m2775 -o root -g "$SVC" /etc/$SVC
[[ -e /etc/$SVC/config.toml ]] || install -m664 -o root -g "$SVC" "$src/config/config.toml.default" /etc/$SVC/config.toml

install -Dm644 "$src/polkit/com.chiefgyk3d.dellbatterybalance.control.policy"   /usr/share/polkit-1/actions/com.chiefgyk3d.dellbatterybalance.control.policy
install -Dm644 "$src/polkit/com.chiefgyk3d.dellbatterybalance.configure.policy" /usr/share/polkit-1/actions/com.chiefgyk3d.dellbatterybalance.configure.policy
install -Dm644 "$src/plasmoid/notifyrc/dell_battery_balance.notifyrc" /usr/share/knotifications6/dell_battery_balance.notifyrc
install -Dm644 "$src/udev/90-dell-battery-balance.rules" /etc/udev/rules.d/90-dell-battery-balance.rules
install -Dm644 "$src/systemd/dell-battery-balance.service" /etc/systemd/system/dell-battery-balance.service
install -Dm644 "$src/systemd/dell-battery-balance.timer"   /etc/systemd/system/dell-battery-balance.timer
install -Dm644 "$src/systemd/dell-battery-balance-check.service" /etc/systemd/system/dell-battery-balance-check.service

# Firmware cycle count. Where the batteries implement ACPI _BIF rather than
# _BIX (the Latitude 5430 Rugged), the kernel's cycle_count is a placeholder
# 0; Dell's DDV WMI interface has the pack's real count, readable only with
# the acpi_call module. Report what this machine has; enable on request.
DDV_GUID=8A42EA14-4F2A-FD45-6422-0087F7A7E608
MODLOAD=/etc/modules-load.d/$SVC-acpi_call.conf
echo
echo "Firmware cycle count:"
placeholder=1
for b in /sys/class/power_supply/BAT*; do
    [[ -r $b/cycle_count ]] || continue
    c=$(<"$b/cycle_count")
    echo "  ${b##*/}: kernel cycle_count = $c"
    [[ $c == 0 ]] || placeholder=0
done
if ! compgen -G "/sys/bus/wmi/devices/$DDV_GUID*" >/dev/null; then
    echo "  No Dell DDV WMI interface (dell-wmi-ddv); only the kernel's value is available."
elif [[ $ddv_cycles -eq 1 ]]; then
    if ! modinfo acpi_call >/dev/null 2>&1; then
        if command -v apt-get >/dev/null; then
            apt-get install -y acpi-call-dkms
        else
            echo "  acpi_call is not installed; install your distribution's acpi_call (DKMS) package and rerun." >&2
        fi
    fi
    if modprobe acpi_call; then
        echo acpi_call > "$MODLOAD"
        echo "  acpi_call loaded and set to load at boot ($MODLOAD)."
        echo "  /proc/acpi/call mode: $(stat -c %a /proc/acpi/call) (root only is expected)"
        /usr/local/libexec/dell-battery-balance-cycles
        if [[ -s /run/$SVC-cycles/cycles ]]; then
            sed 's/^/  DDV WMI cycle count: /' /run/$SVC-cycles/cycles
        else
            echo "  DDV WMI returned no cycle count this machine's packs could be matched to; nothing will be read." >&2
        fi
    fi
elif [[ -e $MODLOAD ]]; then
    echo "  Read from the packs through Dell WMI (acpi_call, enabled earlier)."
elif [[ $placeholder -eq 1 ]]; then
    echo "  All 0: likely the ACPI placeholder, not the packs. Confirmed on the"
    echo "  Latitude 5430 Rugged, where the packs' real count is readable through"
    echo "  Dell WMI. To enable it (installs acpi-call-dkms; see README, Firmware"
    echo "  cycle count):  sudo $src/install.sh --ddv-cycles"
fi

udevadm control --reload
/usr/local/libexec/dell-battery-balance-grant
systemctl daemon-reload
systemctl enable --now dell-battery-balance.timer

if [[ -n "${SUDO_USER:-}" ]] && command -v kpackagetool6 >/dev/null; then
    sudo -u "$SUDO_USER" kpackagetool6 --type Plasma/Applet --upgrade "$src/plasmoid/package" 2>/dev/null \
    || sudo -u "$SUDO_USER" kpackagetool6 --type Plasma/Applet --install "$src/plasmoid/package"
fi

echo
echo "Installed. Service account: $SVC. Timer: dell-battery-balance.timer (tick every 2 min)."
echo "Config: /etc/$SVC/config.toml   State: /var/lib/$SVC"
echo "Verify:  sudo -u $SVC /usr/local/bin/dell-battery-balance status"

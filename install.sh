#!/usr/bin/env bash
# Install dell-battery-balance with a scoped service account. Run as root.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run as root: sudo ./install.sh" >&2; exit 1; }
src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SVC=dell-battery-balance

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
install -Dm755 "$src/libexec/dbb-control"   /usr/local/libexec/dbb-control
install -Dm755 "$src/libexec/dbb-configure" /usr/local/libexec/dbb-configure
install -Dm644 "$src/README.md" /usr/local/share/doc/$SVC/README.md

install -d -m2775 -o "$SVC" -g "$SVC" /var/lib/$SVC
chown -R "$SVC:$SVC" /var/lib/$SVC
find /var/lib/$SVC -type f -exec chmod 664 {} +
install -d -m2775 -o root -g "$SVC" /etc/$SVC
[[ -e /etc/$SVC/config.toml ]] || install -m664 -o root -g "$SVC" "$src/config/config.toml.default" /etc/$SVC/config.toml

install -Dm644 "$src/polkit/com.chiefgyk3d.dellbatterybalance.control.policy"   /usr/share/polkit-1/actions/com.chiefgyk3d.dellbatterybalance.control.policy
install -Dm644 "$src/polkit/com.chiefgyk3d.dellbatterybalance.configure.policy" /usr/share/polkit-1/actions/com.chiefgyk3d.dellbatterybalance.configure.policy
install -Dm644 "$src/udev/90-dell-battery-balance.rules" /etc/udev/rules.d/90-dell-battery-balance.rules
install -Dm644 "$src/systemd/dell-battery-balance.service" /etc/systemd/system/dell-battery-balance.service
install -Dm644 "$src/systemd/dell-battery-balance.timer"   /etc/systemd/system/dell-battery-balance.timer

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

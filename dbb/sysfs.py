#
# dell-battery-balance - wear tracking and charge-ceiling balancing for the
# two battery packs in a Dell Latitude Rugged.
#
# Copyright (C) 2026 ChiefGyk3D
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. See the LICENSE file for the full text.
#
"""sysfs and dell-wmi-sysman firmware I/O: reading sensors, writing charge ceilings."""

import os
import time
from pathlib import Path

SYSFS_ROOT = Path(os.environ.get("DBB_SYSFS_ROOT", "/sys"))
PS = SYSFS_ROOT / "class/power_supply"
SYSMAN = SYSFS_ROOT / "class/firmware-attributes/dell-wmi-sysman"
BATS = ("BAT0", "BAT1")

# Slot -> the dell-wmi-sysman attribute names that control it.
SYSMAN_ATTRS = {
    "BAT0": ("PrimaryBattChargeCfg", "CustomChargeStart", "CustomChargeStop"),
    "BAT1": ("SliceBattChargeCfg", "SliceBattCustomChargeStart", "SliceBattCustomChargeStop"),
}

# Firmware clamps: stop is 55-100, start is 50-95, and start must stay below stop.
STOP_MIN, STOP_MAX = 55, 100
START_MIN, START_MAX = 50, 95


# --------------------------------------------------------------------------
# sysfs helpers
# --------------------------------------------------------------------------

def read_str(path):
    try:
        return path.read_text().strip()
    except OSError:
        return None


def read_int(path):
    v = read_str(path)
    if v is None:
        return None
    try:
        return int(v)
    except ValueError:
        return None


def boot_id():
    forced = os.environ.get("DBB_BOOT_ID")
    if forced:
        return forced
    return read_str(Path("/proc/sys/kernel/random/boot_id")) or "unknown"


# --------------------------------------------------------------------------
# measurement
# --------------------------------------------------------------------------

def sample_all():
    """One synchronous read of both packs plus AC state."""
    ac = read_int(PS / "AC" / "online")
    out = {
        "ts": time.time(),
        "boot_id": boot_id(),
        "ac_online": ac,
        "bats": {},
    }
    for b in BATS:
        d = PS / b
        if not d.exists() or read_int(d / "present") != 1:
            continue
        out["bats"][b] = {
            "status": read_str(d / "status"),
            "capacity": read_int(d / "capacity"),
            "charge_now_uah": read_int(d / "charge_now"),
            "charge_full_uah": read_int(d / "charge_full"),
            "charge_full_design_uah": read_int(d / "charge_full_design"),
            "voltage_now_uv": read_int(d / "voltage_now"),
            "voltage_min_design_uv": read_int(d / "voltage_min_design"),
            "current_now_ua": read_int(d / "current_now"),
            "temp_dc": read_int(d / "temp"),
        }
    return out


# --------------------------------------------------------------------------
# charge ceiling control
# --------------------------------------------------------------------------

def clamp_band(start, stop):
    stop = max(STOP_MIN, min(STOP_MAX, int(stop)))
    start = max(START_MIN, min(START_MAX, int(start)))
    if start >= stop:
        start = max(START_MIN, stop - 5)
    return start, stop


def write_sysman(attr, value, password=None):
    """Write one dell-wmi-sysman attribute. Returns None on success, else why."""
    target = SYSMAN / "attributes" / attr / "current_value"
    if not target.exists():
        return f"{attr}: no such attribute"
    if password:
        pw = SYSMAN / "authentication" / "Admin" / "current_password"
        try:
            pw.write_text(password)
        except OSError as e:
            return f"admin password rejected: {e}"
    try:
        target.write_text(str(value))
    except OSError as e:
        return f"{attr}: {e}"
    return None


def write_powersupply_band(bat, start, stop):
    """Fallback path for BAT0, which dell_laptop exposes directly.

    BAT1 has no charge_control_* files at all, so this only ever helps BAT0.
    """
    d = PS / bat
    e_path, s_path = d / "charge_control_end_threshold", d / "charge_control_start_threshold"
    if not e_path.exists():
        return f"{bat}: no charge_control_end_threshold"
    # The kernel validates start < end, so which write order succeeds depends
    # on the values already in place. Try both orders.
    orders = (((e_path, stop), (s_path, start)),
              ((s_path, start), (e_path, stop)))
    err = None
    for order in orders:
        try:
            for path, value in order:
                path.write_text(str(value))
            return None
        except OSError as e:
            err = e
    return f"{bat}: {err}"


def apply_band(bat, start, stop, password=None, dry_run=True):
    start, stop = clamp_band(start, stop)
    mode_attr, start_attr, stop_attr = SYSMAN_ATTRS[bat]
    if dry_run:
        return f"[dry-run] {bat} -> Custom {start}/{stop}", None

    # Mode must be Custom before the start/stop values are writable
    # (CustomChargeStart carries [ReadOnlyIfNot:PrimaryBattChargeCfg=Custom]).
    err = write_sysman(mode_attr, "Custom", password)
    if err is None:
        err = write_sysman(start_attr, start, password) or write_sysman(stop_attr, stop, password)

    if err is not None and bat == "BAT0":
        fallback = write_powersupply_band(bat, start, stop)
        if fallback is None:
            return f"{bat} -> Custom {start}/{stop} (via power_supply)", None
        err = f"{err}; fallback: {fallback}"

    if err is not None:
        return None, err
    return f"{bat} -> Custom {start}/{stop}", None


def _to_int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def read_applied(bat):
    """Best-effort read-back of what the firmware actually holds."""
    mode_attr, start_attr, stop_attr = SYSMAN_ATTRS[bat]
    out = {}
    for label, attr in (("mode", mode_attr), ("start", start_attr), ("stop", stop_attr)):
        out[label] = read_str(SYSMAN / "attributes" / attr / "current_value")
    if out["mode"] is None:
        out["charge_types"] = read_str(PS / bat / "charge_types")
        out["start"] = read_str(PS / bat / "charge_control_start_threshold")
        out["stop"] = read_str(PS / bat / "charge_control_end_threshold")
    out["start"] = _to_int_or_none(out["start"])
    out["stop"] = _to_int_or_none(out["stop"])
    return out


def read_applied_band(slot):
    """(start, stop) as ints from the firmware, or None if unreadable."""
    a = read_applied(slot)
    try:
        return int(a["start"]), int(a["stop"])
    except (TypeError, ValueError, KeyError):
        return None

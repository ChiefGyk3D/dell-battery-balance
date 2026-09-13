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
"""Persistent counters and sample log, stored under STATE_DIR."""

import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dbb.sysfs import BATS

STATE_DIR = Path(os.environ.get("DBB_STATE_DIR", "/var/lib/dell-battery-balance"))
STATE_FILE = STATE_DIR / "state.json"
SAMPLE_LOG = STATE_DIR / "samples.csv"

CSV_FIELDS = [
    "ts", "boot_id", "ac_online", "bat", "status", "capacity",
    "charge_now_uah", "charge_full_uah", "voltage_now_uv",
    "current_now_ua", "temp_dc",
]


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_state():
    return {
        "version": 1, "created": now_iso(), "slots": {}, "last": None,
        "discharge_first": {b: 0 for b in BATS}, "sessions": 0, "policy": None,
        "ac_run_start_ts": None, "profile_switched_ts": None,
        "one_off_revert_hours": None, "firmware": {}, "events": [],
        "config_error": None, "config_snapshot": None,
    }


def add_event(state, kind, detail):
    state.setdefault("events", []).append({"ts": now_iso(), "kind": kind, "detail": detail})
    del state["events"][:-500]


def load_state():
    try:
        exists = STATE_FILE.exists()
    except PermissionError:
        sys.exit(f"error: cannot read {STATE_DIR} as this user.\n"
                 f"       Fix the install with: sudo chmod 755 {STATE_DIR}")
    if exists:
        try:
            state = json.loads(STATE_FILE.read_text())
        except PermissionError:
            sys.exit(f"error: cannot read {STATE_FILE} as this user.\n"
                     f"       Fix the install with: sudo chmod 644 {STATE_FILE}")
        except (OSError, json.JSONDecodeError) as e:
            sys.exit(f"error: cannot read {STATE_FILE}: {e}")
        base = new_state()
        for k, v in base.items():
            state.setdefault(k, v)
        return state
    return new_state()


def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    os.replace(tmp, STATE_FILE)
    # Only root writes, but any user must be able to read `status`/`report`.
    # Battery telemetry carries nothing sensitive.
    _make_readable(STATE_DIR, 0o755)
    _make_readable(STATE_FILE, 0o644)


def _make_readable(path, mode):
    """Best effort; a non-root caller simply cannot, and does not need to."""
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def append_log(s):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    new = not SAMPLE_LOG.exists()
    with SAMPLE_LOG.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        if new:
            w.writeheader()
        for b, v in s["bats"].items():
            w.writerow({
                "ts": f"{s['ts']:.0f}",
                "boot_id": s["boot_id"],
                "ac_online": s["ac_online"],
                "bat": b,
                "status": v["status"],
                "capacity": v["capacity"],
                "charge_now_uah": v["charge_now_uah"],
                "charge_full_uah": v["charge_full_uah"],
                "voltage_now_uv": v["voltage_now_uv"],
                "current_now_ua": v["current_now_ua"],
                "temp_dc": v["temp_dc"],
            })
    _make_readable(SAMPLE_LOG, 0o644)

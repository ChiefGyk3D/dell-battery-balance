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
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from dbb.sysfs import BATS

STATE_DIR = Path(os.environ.get("DBB_STATE_DIR", "/var/lib/dell-battery-balance"))
STATE_FILE = STATE_DIR / "state.json"

CSV_FIELDS = [
    "ts", "boot_id", "ac_online", "bat", "status", "capacity",
    "charge_now_uah", "charge_full_uah", "voltage_now_uv",
    "current_now_ua", "temp_dc",
]


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_state():
    return {
        "version": 2, "created": now_iso(),
        "packs": {}, "tenures": [], "slots": {b: None for b in BATS}, "pending": {},
        "next_tenure_id": 1, "next_event_id": 1, "last": None,
        "discharge_first": {b: 0 for b in BATS}, "sessions": 0, "policy": None,
        "ac_run_start_ts": None, "profile_switched_ts": None,
        "one_off_revert_hours": None,
        "battery_run_start_ts": None, "last_battery_stint_end_ts": None, "revert_warned_ts": None,
        "firmware": {}, "events": [],
        "absent_since_ts": None, "absent_samples": 0,
        "config_error": None, "config_snapshot": None,
    }


def add_event(state, kind, detail):
    # Ids are monotonic and never reused, so the applet can notify once per
    # event with a single high-water mark even after the list is trimmed.
    eid = state.get("next_event_id", 1)
    state.setdefault("events", []).append(
        {"id": eid, "ts": now_iso(), "kind": kind, "detail": detail})
    state["next_event_id"] = eid + 1
    del state["events"][:-500]


def _number_events(state):
    """Events gained ids in 0.3. Number any older ones once, in order."""
    evs = state.get("events", [])
    if any("id" not in e for e in evs):
        for i, e in enumerate(evs, 1):
            e["id"] = i
        state["next_event_id"] = len(evs) + 1


def load_state():
    try:
        exists = STATE_FILE.exists()
    except PermissionError:
        sys.exit(f"error: cannot read {STATE_DIR} as this user.\n"
                 f"       Fix the install with: sudo chmod 2775 {STATE_DIR}")
    if exists:
        try:
            state = json.loads(STATE_FILE.read_text())
        except PermissionError:
            sys.exit(f"error: cannot read {STATE_FILE} as this user.\n"
                     f"       Fix the install with: sudo chmod 664 {STATE_FILE}")
        except (OSError, json.JSONDecodeError) as e:
            sys.exit(f"error: cannot read {STATE_FILE}: {e}")
        if state.get("version", 1) < 2:
            from dbb import registry   # local import: registry imports state
            state = registry.migrate_v1(state)
        base = new_state()
        for k, v in base.items():
            state.setdefault(k, v)
        _number_events(state)
        return state
    return new_state()


def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    os.replace(tmp, STATE_FILE)
    # The service account writes; root may too (a `sudo` run), and with the
    # state directory setgid to the service group a root-created file stays
    # group-writable, so one stray root run never locks the service account
    # out. Any user may read.
    _make_readable(STATE_DIR, 0o2775)
    _make_readable(STATE_FILE, 0o664)


def _make_readable(path, mode):
    """Best effort; a non-root caller simply cannot, and does not need to."""
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def sample_log_path(ts):
    year = datetime.fromtimestamp(ts, timezone.utc).year
    return STATE_DIR / f"samples-{year}.csv"


SAMPLE_LOG_RE = re.compile(r"^samples-(\d{4})\.csv$")


def prune_sample_logs(now_ts, keep_years):
    """Delete per-year sample logs older than the newest `keep_years`
    calendar years (UTC), counting the current year as one. Only files
    named exactly samples-YYYY.csv are candidates; the orphaned 0.1
    samples.csv and anything else in the directory are never touched."""
    cutoff = datetime.fromtimestamp(now_ts, timezone.utc).year - int(keep_years) + 1
    try:
        names = sorted(os.listdir(STATE_DIR))
    except OSError:
        return []
    removed = []
    for name in names:
        m = SAMPLE_LOG_RE.match(name)
        if m and int(m.group(1)) < cutoff:
            try:
                os.remove(STATE_DIR / name)
                removed.append(name)
            except OSError:
                pass
    return removed


def append_log(s):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    log = sample_log_path(s["ts"])
    new = not log.exists()
    with log.open("a", newline="") as fh:
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
    _make_readable(log, 0o664)

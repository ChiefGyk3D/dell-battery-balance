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
"""Conference profile: the night window, the departure time, the timed
top-off and the RTC wake request.

Everything here is on the LOCAL clock (time.localtime / time.mktime), so a
night that crosses a DST change is as long as the wall clock says it is.
The firmware can only cap charging -- it cannot lower a full pack -- so the
whole point of the state machine is to stop the charge at the hold band the
moment the laptop is plugged in for the night, and to lift the cap just
early enough that both packs are full at the departure time.
"""
import os
import time

from dbb.state import STATE_DIR, _make_readable, add_event
from dbb.sysfs import BATS, clamp_band

PHASES = ("off", "charging_full", "holding", "topping")
CV_TAIL_S = 20 * 60           # constant-voltage tail per pack, on top of the CC estimate
FALLBACK_CHARGE_UA = 2_000_000
CHARGE_UA_KEEP = 48           # charging currents remembered per slot (~1.6 h of ticks)
WAKEALARM_FILE = STATE_DIR / "wakealarm"


# ------------------------------------------------------------ local clock

def parse_hhmm(text):
    try:
        h, m = text.split(":")
        h, m = int(h), int(m)
    except (ValueError, AttributeError, TypeError):
        raise ValueError(f"bad time {text!r}; use HH:MM")
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"bad time {text!r}; use HH:MM")
    return h, m


def _local_at(now_ts, h, m, day_offset=0):
    lt = time.localtime(now_ts)
    # tm_mday overflow is normalised by mktime; tm_isdst=-1 lets libc decide.
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + day_offset, h, m, 0, 0, 0, -1))


def next_occurrence(now_ts, hhmm, tomorrow=False):
    """Epoch of the next local HH:MM strictly after now_ts (tomorrow's when
    `tomorrow`)."""
    h, m = parse_hhmm(hhmm)
    if tomorrow:
        return _local_at(now_ts, h, m, 1)
    cand = _local_at(now_ts, h, m)
    return cand if cand > now_ts else _local_at(now_ts, h, m, 1)


def in_window(now_ts, night_from, leave_at):
    """[night_from, leave_at) on the local clock; wraps midnight when
    leave_at is earlier in the day than night_from."""
    lt = time.localtime(now_ts)
    now_m = lt.tm_hour * 60 + lt.tm_min
    nf_h, nf_m = parse_hhmm(night_from)
    la_h, la_m = parse_hhmm(leave_at)
    nf, la = nf_h * 60 + nf_m, la_h * 60 + la_m
    if nf < la:
        return nf <= now_m < la
    return now_m >= nf or now_m < la


def fmt_local(ts):
    return time.strftime("%H:%M", time.localtime(ts)) if ts is not None else "?"


# ---------------------------------------------------------------- state

def blank():
    return {"phase": "off", "since_ts": None, "topoff_start_ts": None, "leave_ts": None,
            "manual": None, "leave_at_override_ts": None,
            "charge_ua": {b: [] for b in BATS}}


def ensure(state):
    ov = state.get("overnight")
    if not isinstance(ov, dict):
        ov = state["overnight"] = blank()
    for k, v in blank().items():
        ov.setdefault(k, v)
    for b in BATS:
        ov["charge_ua"].setdefault(b, [])
    return ov


# ------------------------------------------------------------- estimate

def record_charge_current(state, sample):
    ov = ensure(state)
    for b, v in sample["bats"].items():
        if v.get("status") == "Charging" and v.get("current_now_ua"):
            lst = ov["charge_ua"].setdefault(b, [])
            lst.append(int(abs(v["current_now_ua"])))
            del lst[:-CHARGE_UA_KEEP]


def median_charge_ua(state, slot):
    lst = sorted(ensure(state)["charge_ua"].get(slot) or [])
    if not lst:
        return FALLBACK_CHARGE_UA
    return lst[len(lst) // 2]


def estimate_topoff_s(state, sample, margin_min):
    """Seconds to bring every present pack to full: the EC charges the packs
    one after the other, so the per-pack times ADD (spec §3)."""
    total = float(margin_min) * 60.0
    for b, v in sample["bats"].items():
        full, now_uah = v.get("charge_full_uah"), v.get("charge_now_uah")
        if not full or now_uah is None:
            continue
        deficit = full - now_uah
        if deficit <= 0:
            continue
        total += deficit / median_charge_ua(state, b) * 3600.0 + CV_TAIL_S
    return total


# ------------------------------------------------------- state machine

def is_topoff_profile(profile):
    ov = profile.get("overnight")
    return bool(ov) and ov.get("mode") == "topoff"


def leave_ts(state, profile, now_ts):
    """The departure the estimate targets: a one-off override while it is
    still ahead, else the next occurrence of the profile's leave_at."""
    ov = ensure(state)
    o = ov["leave_at_override_ts"]
    if o is not None:
        if o > now_ts:
            return o
        ov["leave_at_override_ts"] = None
        add_event(state, "overnight",
                  f"leave-at override {fmt_local(o)} has passed; back to {profile['overnight']['leave_at']}")
    return next_occurrence(now_ts, profile["overnight"]["leave_at"])


def set_manual(state, which):
    if which not in ("topoff", "night"):
        raise ValueError(which)
    ensure(state)["manual"] = which


def set_leave_at(state, hhmm, now_ts, tomorrow=False):
    ov = ensure(state)
    ov["leave_at_override_ts"] = None if hhmm is None else next_occurrence(now_ts, hhmm, tomorrow)
    return ov["leave_at_override_ts"]


def _reset(ov):
    ov.update(phase="off", since_ts=None, topoff_start_ts=None, leave_ts=None, manual=None)


def reset(state):
    """Idle the overnight machine AND drop any leave-at override. Used by
    policy.switch_profile when switching to a different profile, so that
    switching between two conference profiles (or away from one) never
    leaves a stale phase/manual flag/override behind for the new profile to
    inherit."""
    ov = ensure(state)
    _reset(ov)
    ov["leave_at_override_ts"] = None
    return ov


def _enter(state, ov, phase, now_ts, detail):
    ov["phase"] = phase
    ov["since_ts"] = now_ts
    if phase != "holding":
        ov["topoff_start_ts"] = None      # empties the wake request
    add_event(state, "overnight", detail)


def _plan_hold(state, ov, prof, sample, now_ts):
    o = prof["overnight"]
    ov["leave_ts"] = leave_ts(state, prof, now_ts)
    ov["topoff_start_ts"] = ov["leave_ts"] - estimate_topoff_s(state, sample, o["margin_min"])


def step(cfg, state, sample, now_ts):
    """Advance the machine one tick. Returns the phase. Only the tick and the
    control-class commands call this; status never does."""
    ov = ensure(state)
    record_charge_current(state, sample)
    prof = cfg["profiles"][cfg["general"]["active_profile"]]
    if not is_topoff_profile(prof) or sample["ac_online"] != 1:
        if ov["phase"] != "off":
            why = "unplugged" if is_topoff_profile(prof) else "profile changed"
            add_event(state, "overnight", f"{why}; overnight off")
        _reset(ov)
        return "off"
    o = prof["overnight"]
    hold = o["hold"]
    if ov["manual"] == "topoff" and ov["phase"] != "topping":
        ov["leave_ts"] = leave_ts(state, prof, now_ts)
        _enter(state, ov, "topping", now_ts, f"top-off started now; ready by {fmt_local(ov['leave_ts'])}")
        return ov["phase"]
    if ov["phase"] in ("off", "charging_full"):
        if ov["manual"] == "night" or in_window(now_ts, o["night_from"], o["leave_at"]):
            above = [f"{b} is at {v['capacity']}%, above the {hold[1]}% hold; the firmware cannot lower it"
                     for b, v in sorted(sample["bats"].items())
                     if v.get("capacity") is not None and v["capacity"] > hold[1]]
            _plan_hold(state, ov, prof, sample, now_ts)
            _enter(state, ov, "holding", now_ts,
                   f"holding {hold[0]}/{hold[1]}, top-off {fmt_local(ov['topoff_start_ts'])} "
                   f"for {fmt_local(ov['leave_ts'])}" + "".join("; " + a for a in above))
        elif ov["phase"] == "off":
            _enter(state, ov, "charging_full", now_ts,
                   f"charging to {prof['bands']['all'][1]}% until {o['night_from']}, then hold")
    if ov["phase"] == "holding":
        _plan_hold(state, ov, prof, sample, now_ts)
        if now_ts >= ov["topoff_start_ts"]:
            _enter(state, ov, "topping", now_ts, f"top-off started; ready by {fmt_local(ov['leave_ts'])}")
    return ov["phase"]


def apply_phase(profile, state, bands):
    """Pure: while holding, every present slot that is not pinned takes the
    hold band. Everything else is the profile's own band."""
    if not is_topoff_profile(profile):
        return bands
    ov = state.get("overnight") or {}
    if ov.get("phase") != "holding":
        return bands
    hold = clamp_band(*profile["overnight"]["hold"])
    pins = profile.get("pins", {})
    return {s: (hold if s not in pins else b) for s, b in bands.items()}


def describe(profile, state, sample, now_ts):
    """The one-line status of the overnight machinery, or None for a
    profile without an overnight table. Shared by status, the CLI actions
    and the applet."""
    o = profile.get("overnight")
    if not o:
        return None
    if o["mode"] == "full":
        return "overnight: mode full, packs stay at 100% on AC"
    ov = ensure(state)
    note = ""
    if ov["leave_at_override_ts"] is not None and ov["leave_at_override_ts"] > now_ts:
        note = f" (leaving at {fmt_local(ov['leave_at_override_ts'])} set)"
    if sample.get("ac_online") != 1:
        return (f"overnight: on battery; on AC it holds {o['hold'][0]}/{o['hold'][1]} from "
                f"{o['night_from']} and tops off for {o['leave_at']}{note}")
    ph = ov["phase"]
    if ph == "charging_full":
        return f"overnight: charging to {profile['bands']['all'][1]}% until {o['night_from']}, then hold{note}"
    if ph == "holding":
        mins = max(0, int((ov["topoff_start_ts"] - now_ts) // 60))
        return (f"overnight: holding {o['hold'][0]}/{o['hold'][1]}, top-off {fmt_local(ov['topoff_start_ts'])} "
                f"({mins // 60}h{mins % 60:02d}m) for {fmt_local(ov['leave_ts'])}{note}")
    if ph == "topping":
        return f"overnight: topping off, ready by {fmt_local(ov['leave_ts'])}{note}"
    return f"overnight: off{note}"


# ------------------------------------------------------- wake request

def wakealarm_text(state):
    ov = state.get("overnight") or {}
    ts = ov.get("topoff_start_ts") if ov.get("phase") == "holding" else None
    return f"{int(ts)}\n" if ts else ""


def write_wakealarm(state, path=None):
    """Rewritten every tick: the epoch the root wake helper should program
    into the RTC while holding, empty otherwise."""
    path = path or WAKEALARM_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(wakealarm_text(state))
    os.replace(tmp, path)
    _make_readable(path, 0o664)

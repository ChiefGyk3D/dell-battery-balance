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

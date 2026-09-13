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
"""Wear model: cycle counting and calendar-aging stress for each pack."""

import sys

from dbb.state import now_iso
from dbb.sysfs import BATS

# A sample gap longer than this is recorded but flagged; the machine was
# probably off or suspended.
GAP_FLAG_SECONDS = 900


def calendar_stress(soc_pct, temp_c):
    """Relative calendar-aging rate versus a 50% SoC / 25 C baseline.

    Heuristic, not a datasheet curve: Li-ion calendar fade rises steeply with
    state of charge and roughly doubles per 10 C (Arrhenius rule of thumb).
    Used only to compare the two packs against each other, so the absolute
    scale does not matter -- both packs are measured the same way.
    """
    soc_term = 1.0 + 3.0 * max(0.0, (soc_pct - 50.0) / 50.0) ** 2
    temp_term = 2.0 ** ((temp_c - 25.0) / 10.0)
    return soc_term * temp_term


def efc(slot):
    """Equivalent full cycles: cumulative charge out / design capacity."""
    if not slot.get("design_uah"):
        return 0.0
    return slot["discharge_uah"] / slot["design_uah"]


def blank_slot(design_uah):
    return {
        "design_uah": design_uah,
        "discharge_uah": 0.0,      # cumulative charge out -> drives EFC
        "charge_uah": 0.0,         # cumulative charge in, for sanity checks
        "calendar_score": 0.0,     # SoC- and temperature-weighted hours
        "soc_hours": 0.0,          # plain hours observed, for averaging
        "soc_hours_sum": 0.0,      # SoC-weighted hours, for mean SoC
        "seconds_ge_90": 0.0,
        "seconds_observed": 0.0,
        "samples": 0,
        "first_seen": now_iso(),
    }


def integrate(state, s):
    """Fold one sample into the cumulative counters."""
    last = state.get("last")
    state.setdefault("slots", {})

    for b, v in s["bats"].items():
        design = v["charge_full_design_uah"]
        slot = state["slots"].get(b)
        if slot is None:
            slot = blank_slot(design)
            state["slots"][b] = slot
        # A different pack in the slot invalidates the running totals. These
        # packs report an identical serial ("88") and ePPID, so a design
        # capacity change is the only automatic signal available.
        if design and slot.get("design_uah") and design != slot["design_uah"]:
            print(f"warning: {b} design capacity changed "
                  f"({slot['design_uah']} -> {design} uAh); pack may have been "
                  f"swapped. Run --reset-slot {b} to restart its counters.",
                  file=sys.stderr)
        slot["samples"] += 1

    if not last:
        state["last"] = s
        return {"counted": False, "reason": "first sample"}

    dt = s["ts"] - last["ts"]
    if dt <= 0:
        return {"counted": False, "reason": "clock went backwards"}

    gap = dt > GAP_FLAG_SECONDS
    first_faller = None
    fall_uah = 0

    for b, v in s["bats"].items():
        prev = last["bats"].get(b)
        slot = state["slots"][b]
        if not prev or prev["charge_now_uah"] is None or v["charge_now_uah"] is None:
            continue

        delta = v["charge_now_uah"] - prev["charge_now_uah"]
        if delta < 0:
            slot["discharge_uah"] += -delta
            if -delta > fall_uah:
                fall_uah, first_faller = -delta, b
        elif delta > 0:
            slot["charge_uah"] += delta

        # Calendar stress accrues whenever we are observing, charging or not.
        soc = v["capacity"]
        temp_dc = v["temp_dc"]
        if soc is not None and temp_dc is not None and not gap:
            hours = dt / 3600.0
            slot["calendar_score"] += hours * calendar_stress(soc, temp_dc / 10.0)
            slot["soc_hours"] += hours
            slot["soc_hours_sum"] += hours * soc
            slot["seconds_observed"] += dt
            if soc >= 90:
                slot["seconds_ge_90"] += dt

    # Which pack does the EC reach for first? Credited once per unplug, to the
    # first pack that falls while the others still hold. Counting every
    # draining interval instead would just re-measure EFC.
    if last["ac_online"] == 1 and s["ac_online"] == 0:
        state["session_open"] = True
        state["session_credited"] = False
        state["sessions"] = state.get("sessions", 0) + 1
    elif s["ac_online"] == 1:
        state["session_open"] = False

    if state.get("session_open") and not state.get("session_credited") and first_faller:
        others_flat = all(
            (s["bats"][o]["charge_now_uah"] - last["bats"][o]["charge_now_uah"]) >= 0
            for o in s["bats"]
            if o != first_faller and o in last["bats"]
            and s["bats"][o]["charge_now_uah"] is not None
            and last["bats"][o]["charge_now_uah"] is not None
        )
        if others_flat:
            state.setdefault("discharge_first", {b: 0 for b in BATS})
            state["discharge_first"][first_faller] = \
                state["discharge_first"].get(first_faller, 0) + 1
            state["session_credited"] = True

    state["last"] = s
    return {"counted": True, "dt": dt, "gap": gap}

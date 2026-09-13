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


def _track_ac_run(state, last, s):
    ac = s["ac_online"]
    if ac != 1:
        state["ac_run_start_ts"] = None
        return
    if last is None or last["ac_online"] != 1 or state.get("ac_run_start_ts") is None:
        # A gap with AC on both sides is still one run: the charger held SoC
        # the whole time, so the pack was floating throughout.
        state["ac_run_start_ts"] = s["ts"]


def integrate(state, s):
    """Fold one sample into the open tenure of each present slot."""
    from dbb import registry   # local import: registry imports wear
    last = state.get("last")
    dt = None
    if last:
        dt = s["ts"] - last["ts"]
        if dt <= 0:
            return {"counted": False, "reason": "clock went backwards"}

    changed_slots = set()
    for b in BATS:
        v = s["bats"].get(b)
        if not v or v.get("present", 1) != 1:
            registry.note_absent(state, b, s["ts"])
            continue
        t, changed = registry.observe(state, b, v, s, dt)
        if changed:
            changed_slots.add(b)
        else:
            # The tick that trips a change opens a brand-new tenure for an
            # interval that belongs to nobody; counting it as a sample on
            # that tenure would overstate its data before anything accrued.
            t["samples"] += 1

    if not last:
        _track_ac_run(state, None, s)
        state["last"] = s
        return {"counted": False, "reason": "first sample"}

    gap = dt > GAP_FLAG_SECONDS
    first_faller = None
    fall_uah = 0

    for b, v in s["bats"].items():
        prev = last["bats"].get(b)
        slot = registry.open_tenure(state, b)
        if slot is None or b in changed_slots:
            continue
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
        if soc is not None and temp_dc is not None:
            hours = dt / 3600.0
            if gap and not (last["ac_online"] == 1 and s["ac_online"] == 1):
                continue
            prev_soc = prev["capacity"] if prev["capacity"] is not None else soc
            soc_eff = soc if not gap else (soc + prev_soc) / 2.0
            slot["calendar_score"] += hours * calendar_stress(soc_eff, temp_dc / 10.0)
            slot["soc_hours"] += hours
            slot["soc_hours_sum"] += hours * soc_eff
            slot["seconds_observed"] += dt
            if soc_eff >= 90:
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

    _track_ac_run(state, last, s)
    state["last"] = s
    return {"counted": True, "dt": dt, "gap": gap}

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
"""Turn config + measured state into the bands each slot should hold."""
from dataclasses import dataclass, field

from dbb.config import profile_type
from dbb.state import add_event
from dbb.sysfs import BATS, clamp_band
from dbb.wear import efc


@dataclass
class Resolution:
    bands: dict
    profile: str
    profile_type: str
    roles: dict | None
    why: str
    revert: dict | None = None


def efc_by_slot(state):
    return {b: efc(s) for b, s in state.get("slots", {}).items()}


def decide_roles(efc_map, deadband):
    """'protect' the pack ahead on wear, 'work' the other; None with one pack."""
    present = sorted(efc_map)
    if len(present) < 2:
        return None, "only one pack tracked so far"
    a, c = present[0], present[1]
    diff = efc_map[a] - efc_map[c]
    if abs(diff) < deadband:
        return ({b: "neutral" for b in present},
                f"packs within {deadband:.2f} EFC ({efc_map[a]:.2f} vs {efc_map[c]:.2f})"
                " - holding neutral band")
    leader, laggard = (a, c) if diff > 0 else (c, a)
    return ({leader: "protect", laggard: "work"},
            f"{leader} is ahead by {abs(diff):.2f} EFC - protecting it, {laggard} takes the load")


def revert_due(profile, state, now):
    rv = profile.get("revert")
    if not rv:
        return None
    switched = state.get("profile_switched_ts")
    after = state.get("one_off_revert_hours") or rv.get("after_hours")
    if after and switched is not None and (now - switched) / 3600.0 >= after:
        return "after_hours"
    on_ac = rv.get("on_ac_hours")
    run = state.get("ac_run_start_ts")
    if on_ac and run is not None and (now - run) / 3600.0 >= on_ac:
        return "on_ac_hours"
    return None


def resolve_revert_target(cfg, profile_name):
    rv = cfg["profiles"][profile_name].get("revert", {})
    to = rv.get("to", "previous")
    if to == "previous":
        to = cfg["general"].get("previous_profile", "daily")
    if to not in cfg["profiles"] or cfg["profiles"][to].get("revert"):
        to = "daily"
    return to


def switch_profile(cfg, state, name, now, reason):
    g = cfg["general"]
    if name != g["active_profile"]:
        g["previous_profile"] = g["active_profile"]
    g["active_profile"] = name
    state["profile_switched_ts"] = now
    state["one_off_revert_hours"] = None
    add_event(state, "profile", f"-> {name} ({reason})")


def _bands_for_profile(profile, roles, present):
    bands = profile["bands"]
    out = {}
    if "all" in bands:
        for b in present:
            out[b] = tuple(bands["all"])
    else:
        for b in present:
            role = (roles or {}).get(b, "neutral")
            out[b] = tuple(bands[role])
    for slot, pin in profile.get("pins", {}).items():
        if slot not in present:
            continue
        if "role" in pin:
            out[slot] = tuple(bands.get(pin["role"], bands.get("all", (50, 80))))
        else:
            out[slot] = (pin["start"], pin["stop"])
    return {b: clamp_band(*v) for b, v in out.items()}


def bands_for(cfg, profile_name):
    """Convenience: the clamped bands table for a profile, keyed as authored."""
    bands = cfg["profiles"][profile_name]["bands"]
    return {k: clamp_band(*v) for k, v in bands.items()}


def resolve(cfg, state, sample, now):
    name = cfg["general"]["active_profile"]
    profile = cfg["profiles"][name]
    revert = None
    reason = revert_due(profile, state, now)
    if reason:
        target = resolve_revert_target(cfg, name)
        revert = {"to": target, "reason": reason}
        name, profile = target, cfg["profiles"][target]

    present = [b for b in BATS if b in sample["bats"]]
    ptype = profile_type(profile)
    roles, why = None, f"{name}: fixed band"
    if ptype == "balancing":
        efcs = {b: v for b, v in efc_by_slot(state).items() if b in present}
        roles, why = decide_roles(efcs, cfg["general"]["deadband_efc"])
        if roles is None and present:
            why = f"{name}: single pack, neutral band"
    bands = _bands_for_profile(profile, roles, present)
    return Resolution(bands=bands, profile=name, profile_type=ptype,
                      roles=roles, why=why, revert=revert)

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
from dataclasses import dataclass

from dbb import overnight, registry
from dbb.config import profile_type
from dbb.state import add_event
from dbb.sysfs import BATS, clamp_band
from dbb.wear import ACTIVE_DAY_STINT_MIN, ACTIVE_DAY_WINDOW_H


class PolicyError(ValueError):
    pass


def active_day(state, now):
    end = state.get("last_battery_stint_end_ts")
    return end is not None and (now - end) < ACTIVE_DAY_WINDOW_H * 3600.0


@dataclass
class Resolution:
    bands: dict
    profile: str
    profile_type: str
    roles: dict | None
    why: str
    revert: dict | None = None


def efc_by_slot(state):
    return {b: e for b in BATS if (e := registry.efc_for_slot(state, b)) is not None}


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
    switched = state.get("profile_switched_ts")
    one_off = state.get("one_off_revert_hours")
    if one_off is not None:
        # --for / --stay belong to the SWITCH, not the profile (spec §4): for
        # this switch they REPLACE the profile's own triggers -- longer or
        # shorter than after_hours -- and --stay (0) means no automatic
        # revert at all. Issue #1 was this override acting as a floor.
        if one_off > 0 and switched is not None and (now - switched) / 3600.0 >= one_off:
            return "after_hours"
        return None
    rv = profile.get("revert")
    if not rv:
        return None
    after = rv.get("after_hours")
    if after and switched is not None and (now - switched) / 3600.0 >= after:
        return "after_hours"
    on_ac = rv.get("on_ac_hours")
    run = state.get("ac_run_start_ts")
    if on_ac and run is not None and (now - run) / 3600.0 >= on_ac and not active_day(state, now):
        return "on_ac_hours"
    return None


def revert_eta(profile, state, now):
    """Hours until the soonest live trigger fires; None when nothing will."""
    switched = state.get("profile_switched_ts")
    one_off = state.get("one_off_revert_hours")
    if one_off is not None:
        if one_off <= 0 or switched is None:
            return None
        return one_off - (now - switched) / 3600.0
    rv = profile.get("revert") or {}
    etas = []
    if rv.get("after_hours") and switched is not None:
        etas.append(rv["after_hours"] - (now - switched) / 3600.0)
    run = state.get("ac_run_start_ts")
    if rv.get("on_ac_hours") and run is not None and not active_day(state, now):
        etas.append(rv["on_ac_hours"] - (now - run) / 3600.0)
    return min(etas) if etas else None


def revert_soon(profile, state, now, within_h=1.0):
    eta = revert_eta(profile, state, now)
    return eta is not None and 0.0 < eta <= within_h


def extend(cfg, state, hours, now):
    """Push the active profile's revert out by `hours`: the switch time moves
    forward (both after_hours and a --for one-off measure from it) and the
    on-AC clock restarts."""
    name = cfg["general"]["active_profile"]
    prof = cfg["profiles"][name]
    one_off = state.get("one_off_revert_hours")
    if one_off is None and not prof.get("revert"):
        raise PolicyError(f"nothing to extend: {name} has no auto-revert")
    if one_off is not None and one_off <= 0:
        raise PolicyError(f"nothing to extend: {name} was switched with --stay")
    if state.get("profile_switched_ts") is None:
        state["profile_switched_ts"] = now
    state["profile_switched_ts"] += hours * 3600.0
    if state.get("ac_run_start_ts") is not None:
        state["ac_run_start_ts"] = now
    state["revert_warned_ts"] = None
    add_event(state, "profile", f"{name}: revert extended by {hours:g}h")


def resolve_revert_target(cfg, profile_name):
    # A profile with no [revert] table (e.g. travel, when reached only via a
    # one-off --for override) still resolves "to" as "previous".
    rv = cfg["profiles"][profile_name].get("revert") or {}
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
    state["revert_warned_ts"] = None
    overnight.ensure(state)["leave_at_override_ts"] = None
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
            # A fixed profile has only "all"; a balancing profile has no
            # "all", so the role always exists in `bands` there. No
            # hard-coded fallback: whichever key applies is authoritative.
            out[slot] = tuple(bands["all"]) if "all" in bands else tuple(bands[pin["role"]])
        else:
            out[slot] = (pin["start"], pin["stop"])
    return {b: clamp_band(*v) for b, v in out.items()}


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
    if ptype == "conference":
        bands = overnight.apply_phase(profile, state, bands)
        why = f"{name}: overnight {(state.get('overnight') or {}).get('phase', 'off')}"
    return Resolution(bands=bands, profile=name, profile_type=ptype,
                      roles=roles, why=why, revert=revert)

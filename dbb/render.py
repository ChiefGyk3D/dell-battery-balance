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
"""Human-readable and JSON status views over persisted state and a live sample."""

import time

from dbb import policy
from dbb.config import profile_type
from dbb.sysfs import BATS, read_applied
from dbb.state import STATE_FILE, now_iso
from dbb.wear import efc


def wh(uah, uv):
    if not uah or not uv:
        return 0.0
    return (uah / 1e6) * (uv / 1e6)


def _revert_info(cfg, state, name, prof, now):
    rv = (prof or {}).get("revert") or {}
    one_off = state.get("one_off_revert_hours")
    # A profile with no [revert] table (e.g. travel) still has revert info to
    # report once a one-off --for override is armed on it.
    if not rv and not one_off:
        return None
    switched = state.get("profile_switched_ts")
    after = one_off or rv.get("after_hours")
    run = state.get("ac_run_start_ts")
    return {
        "to": policy.resolve_revert_target(cfg, name),
        "after_hours_left": (after - (now - switched) / 3600.0) if (after and switched is not None) else None,
        "on_ac_hours_left": (rv["on_ac_hours"] - (now - run) / 3600.0) if (rv.get("on_ac_hours") and run is not None) else None,
    }


def fmt_status(state, s, cfg):
    slots = state.get("slots", {})
    lines = []
    err = state.get("config_error")
    if err:
        lines.append(f"CONFIG ERROR: {err}")
    lines.append(f"dell-battery-balance   {now_iso()}")
    lines.append(f"state: {STATE_FILE}")
    ac = "AC connected" if s["ac_online"] else "on battery"
    lines.append(f"power: {ac}")
    name = cfg["general"]["active_profile"]
    profile = cfg["profiles"].get(name, {})
    lines.append(f"profile: {profile.get('label', name)} ({name}, {profile_type(profile)})")
    rv = _revert_info(cfg, state, name, profile, time.time())
    if rv:
        left = rv["after_hours_left"] if rv["after_hours_left"] is not None else rv["on_ac_hours_left"]
        if left is not None:
            lines.append(f"revert in {max(left, 0.0):.1f}h -> {rv['to']}")
    lines.append("")

    hdr = f"{'':6} {'now':>16} {'EFC':>7} {'discharged':>12} {'cal.score':>10} {'mean SoC':>9} {'>=90%':>8}"
    lines.append(hdr)
    lines.append("-" * len(hdr))

    for b in BATS:
        v = s["bats"].get(b)
        slot = slots.get(b)
        if not v:
            lines.append(f"{b:6} {'absent':>16}")
            continue
        nominal = v["voltage_min_design_uv"]
        now = f"{v['capacity']}% {v['status']}"
        if not slot:
            lines.append(f"{b:6} {now:>16} {'(no history)':>7}")
            continue
        mean_soc = (slot["soc_hours_sum"] / slot["soc_hours"]) if slot["soc_hours"] else 0.0
        pct90 = (100.0 * slot["seconds_ge_90"] / slot["seconds_observed"]) \
            if slot["seconds_observed"] else 0.0
        lines.append(
            f"{b:6} {now:>16} {efc(slot):>7.2f} "
            f"{wh(slot['discharge_uah'], nominal):>10.1f}Wh "
            f"{slot['calendar_score']:>10.1f} {mean_soc:>8.1f}% {pct90:>7.1f}%")

    lines.append("")
    both = [b for b in BATS if b in slots]
    if len(both) == 2:
        d = abs(efc(slots[both[0]]) - efc(slots[both[1]]))
        lines.append(f"cycle divergence: {d:.2f} EFC")
        cd = abs(slots[both[0]]["calendar_score"] - slots[both[1]]["calendar_score"])
        lines.append(f"calendar divergence: {cd:.1f}")

    first = state.get("discharge_first", {})
    if any(first.values()):
        tot = sum(first.values()) or 1
        order = ", ".join(f"{b} {100*n/tot:.0f}%" for b, n in first.items() if n)
        lines.append(f"EC reaches for first: {order}  ({tot} unplug events)")
    else:
        lines.append("EC drain order: not yet observed "
                     f"(needs unplug events; {state.get('sessions', 0)} seen)")

    fw = state.get("firmware", {})
    if fw:
        lines.append("")
        lines.append("firmware:")
        for slot, rec in sorted(fw.items()):
            req = rec.get("requested")
            obs = rec.get("observed")
            req_s = f"{req[0]}/{req[1]}" if req else "?"
            obs_s = f"{obs[0]}/{obs[1]}" if obs else "unknown"
            line = f"  {slot}: {req_s} -> {obs_s}"
            if rec.get("error"):
                line += f"  ERROR {rec['error']}"
            lines.append(line)

    pol = state.get("policy")
    if pol:
        roles = pol.get("roles") or {}
        lines.append(f"policy applied {pol['ts']}: " +
                     (", ".join(f"{b}={r}" for b, r in roles.items())
                      if roles else f"fixed band ({pol.get('profile', pol.get('mode'))})"))
    else:
        lines.append("policy: never applied")
    return "\n".join(lines)


def state_json(state, s, cfg):
    """Machine-readable view of everything `status` prints, for the applet."""
    slots = state.get("slots", {})
    name = cfg["general"]["active_profile"]
    prof = cfg["profiles"][name]
    out = {
        "ts": now_iso(),
        "ac_online": s["ac_online"],
        "bats": {},
        "sessions": state.get("sessions", 0),
        "drain_first": state.get("discharge_first", {}),
        "policy": state.get("policy"),
        "field_mode": profile_type(prof) == "fixed" and prof["bands"]["all"][1] >= 95,
    }

    for b in BATS:
        v = s["bats"].get(b)
        if not v:
            out["bats"][b] = {"present": False}
            continue
        slot = slots.get(b)
        applied = read_applied(b)
        entry = {
            "present": True,
            "capacity": v["capacity"],
            "status": v["status"],
            "temp_c": (v["temp_dc"] / 10.0) if v["temp_dc"] is not None else None,
            "mode": applied.get("mode"),
            "start": applied.get("start"),
            "stop": applied.get("stop"),
            "efc": None,
            "calendar_score": None,
            "mean_soc": None,
            "pct_ge90": None,
            "discharged_wh": None,
        }
        if slot:
            entry["efc"] = round(efc(slot), 3)
            entry["calendar_score"] = round(slot["calendar_score"], 2)
            entry["discharged_wh"] = round(
                wh(slot["discharge_uah"], v["voltage_min_design_uv"]), 2)
            if slot["soc_hours"]:
                entry["mean_soc"] = round(
                    slot["soc_hours_sum"] / slot["soc_hours"], 1)
            if slot["seconds_observed"]:
                entry["pct_ge90"] = round(
                    100.0 * slot["seconds_ge_90"] / slot["seconds_observed"], 1)
        out["bats"][b] = entry

    both = [b for b in BATS if b in slots]
    if len(both) == 2:
        out["divergence_efc"] = round(
            abs(efc(slots[both[0]]) - efc(slots[both[1]])), 3)
        out["divergence_calendar"] = round(
            abs(slots[both[0]]["calendar_score"]
                - slots[both[1]]["calendar_score"]), 2)
    else:
        out["divergence_efc"] = None
        out["divergence_calendar"] = None

    g = cfg["general"]
    out["profile"] = {"name": name, "label": prof["label"], "type": profile_type(prof),
                      "description": prof.get("description", ""), "previous": g["previous_profile"]}
    out["profiles"] = [{"name": n, "label": p["label"], "type": profile_type(p), "active": n == name}
                       for n, p in sorted(cfg["profiles"].items())]
    out["revert"] = _revert_info(cfg, state, name, prof, time.time())
    out["firmware"] = state.get("firmware", {})
    out["config_error"] = state.get("config_error")
    out["events"] = state.get("events", [])[-10:]

    res = policy.resolve(cfg, state, s, time.time())
    out["recommendation"] = {
        "why": res.why,
        "roles": res.roles,
        "bands": {slot: list(band) for slot, band in res.bands.items()},
    }
    return out

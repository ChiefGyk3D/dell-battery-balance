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

from dbb.policy import DEFAULT_BANDS, DEFAULT_DEADBAND, decide_roles
from dbb.sysfs import BATS, read_applied
from dbb.state import STATE_FILE, now_iso
from dbb.wear import efc


def wh(uah, uv):
    if not uah or not uv:
        return 0.0
    return (uah / 1e6) * (uv / 1e6)


def fmt_status(state, s):
    slots = state.get("slots", {})
    lines = []
    lines.append(f"dell-battery-balance   {now_iso()}")
    lines.append(f"state: {STATE_FILE}")
    ac = "AC connected" if s["ac_online"] else "on battery"
    lines.append(f"power: {ac}")
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

    pol = state.get("policy")
    if pol:
        lines.append(f"policy applied {pol['ts']}: " +
                     ", ".join(f"{b}={r}" for b, r in pol["roles"].items()))
    else:
        lines.append("policy: never applied")
    return "\n".join(lines)


def state_json(state, s):
    """Machine-readable view of everything `status` prints, for the applet."""
    slots = state.get("slots", {})
    out = {
        "ts": now_iso(),
        "ac_online": s["ac_online"],
        "bats": {},
        "sessions": state.get("sessions", 0),
        "drain_first": state.get("discharge_first", {}),
        "policy": state.get("policy"),
        "field_mode": bool(state.get("policy")
                           and state["policy"].get("mode") == "field"),
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

    roles, why = decide_roles(state, DEFAULT_DEADBAND)
    out["recommendation"] = {
        "why": why,
        "roles": roles,
        "bands": {b: DEFAULT_BANDS[r] for b, r in (roles or {}).items()},
    }
    return out

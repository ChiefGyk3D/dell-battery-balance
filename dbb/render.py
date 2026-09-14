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

from dbb import VERSION, overnight, policy, registry
from dbb.config import profile_type
from dbb.sysfs import BATS, read_applied
from dbb.state import STATE_FILE, now_iso
from dbb.wear import efc


def wh(uah, uv):
    if not uah or not uv:
        return 0.0
    return (uah / 1e6) * (uv / 1e6)


def _power_w(v):
    """Signed watts: negative while discharging. Dell reports current as a
    magnitude, so the sign comes from the status string."""
    if v["current_now_ua"] is None or v["voltage_now_uv"] is None:
        return None
    w = abs(v["current_now_ua"]) * v["voltage_now_uv"] / 1e12
    return round(-w if v["status"] == "Discharging" else w, 2)


def _revert_info(cfg, state, name, prof, now):
    rv = (prof or {}).get("revert") or {}
    one_off = state.get("one_off_revert_hours")
    if one_off is None and not rv:
        return None
    switched = state.get("profile_switched_ts")
    run = state.get("ac_run_start_ts")
    if one_off is not None:
        # a --for / --stay switch replaces the profile's triggers (policy.revert_due)
        after, on_ac, stay = (one_off or None), None, one_off == 0
    else:
        after, on_ac, stay = rv.get("after_hours"), rv.get("on_ac_hours"), False
    eta = policy.revert_eta(prof or {}, state, now)
    on_ac_expired = bool(on_ac and run is not None and (now - run) / 3600.0 >= on_ac)
    return {
        "to": policy.resolve_revert_target(cfg, name),
        "stay": stay,
        "after_hours_left": (after - (now - switched) / 3600.0) if (after and switched is not None) else None,
        "on_ac_hours_left": (on_ac - (now - run) / 3600.0) if (on_ac and run is not None) else None,
        "eta_hours": eta,
        "warning": policy.revert_soon(prof or {}, state, now),
        "guard_blocking": on_ac_expired and policy.active_day(state, now),
        "last_battery_stint_end_ts": state.get("last_battery_stint_end_ts"),
    }


def _display_efc_cal(state, t, now, bench_temp_c):
    """(efc, calendar_score) to SHOW for slot's open tenure `t`: the pack
    total when identified (what the policy actually balances on), else the
    tenure's own figures."""
    if t["pack"]:
        tot = registry.pack_totals(state, t["pack"], now, bench_temp_c)
        return tot["efc"], tot["calendar_score"]
    return efc(t), t["calendar_score"]


def fmt_status(state, s, cfg):
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
    now = time.time()
    bench_t = cfg["general"]["bench_temp_c"]
    rv = _revert_info(cfg, state, name, profile, now)
    if rv:
        if rv["stay"]:
            lines.append(f"revert: none - stays on {name} until you change it")
        else:
            left = rv["eta_hours"]
            if left is not None:
                line = f"revert in {max(left, 0.0):.1f}h -> {rv['to']}"
                if rv["warning"]:
                    line += f"  (warning: reverts in {int(round(left * 60))}m - 'profile extend 24h' keeps it)"
                lines.append(line)
            if rv["guard_blocking"]:
                ago = (now - rv["last_battery_stint_end_ts"]) / 3600.0
                lines.append(f"on-AC revert held: on battery {ago:.0f}h ago (still going out daily)")
    lines.append("")

    hdr = f"{'':6} {'now':>20} {'EFC':>7} {'discharged':>12} {'cal.score':>10} {'mean SoC':>9} {'>=90%':>8}"
    lines.append(hdr)
    lines.append("-" * len(hdr))

    slot_vals = {}
    for b in BATS:
        v = s["bats"].get(b)
        t = registry.open_tenure(state, b)
        if not v:
            # Same rule as state_json: an absent slot contributes no
            # divergence, even if nothing has closed its tenure yet.
            lines.append(f"{b:6} {'absent':>20}")
            continue
        nominal = v["voltage_min_design_uv"]
        now_col = f"{(t['pack'] if t and t['pack'] else '?')} {v['capacity']}% {v['status']}"
        if not t:
            lines.append(f"{b:6} {now_col:>20} {'(no history)':>7}")
            continue
        slot_vals[b] = _display_efc_cal(state, t, now, bench_t)
        e, cal = slot_vals[b]
        mean_soc = (t["soc_hours_sum"] / t["soc_hours"]) if t["soc_hours"] else 0.0
        pct90 = (100.0 * t["seconds_ge_90"] / t["seconds_observed"]) \
            if t["seconds_observed"] else 0.0
        lines.append(
            f"{b:6} {now_col:>20} {e:>7.2f} "
            f"{wh(t['discharge_uah'], nominal):>10.1f}Wh "
            f"{cal:>10.1f} {mean_soc:>8.1f}% {pct90:>7.1f}%")

    lines.append("")
    both = [b for b in BATS if b in slot_vals]
    if len(both) == 2:
        e0, c0 = slot_vals[both[0]]
        e1, c1 = slot_vals[both[1]]
        lines.append(f"cycle divergence: {abs(e0 - e1):.2f} EFC")
        lines.append(f"calendar divergence: {abs(c0 - c1):.1f}")

    packs = registry.all_packs(state, now, bench_t)
    if packs:
        lines.append("")
        lines.append(f"{'pack':16} {'EFC':>6} {'cal.':>7} {'where':>8} {'note'}")
        for r in packs:
            where = r["in_slot"] or ("retired" if r["retired"] else "bench")
            note = ""
            if not r["in_slot"] and not r["retired"] and r["removed_at_soc"] is not None:
                note = f"out {r['bench_hours']:.0f}h at {r['removed_at_soc']}%"
            lines.append(f"{r['name']:16} {r['efc']:>6.2f} {r['calendar_score']:>7.1f} {where:>8} {note}")
    hint = registry.rotation_hint(state, cfg["general"]["deadband_efc"], now, bench_t, rows=packs)
    if hint:
        lines.append(f"swap in next: {hint['swap_in']} for {hint['replace']} ({hint['behind_by_efc']:.2f} EFC behind)")
    pend = state.get("pending", {})
    for slot, q in sorted(pend.items()):
        prev = f", was {q['previous_pack']}" if q.get("previous_pack") else ""
        lines.append(f"PENDING {slot}: which pack is this? guess={q['guess']} ({q['reason']}{prev}); "
                     f"answer with: pack same {slot} | pack assign {slot} NAME | pack new {slot} NAME")

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
    name = cfg["general"]["active_profile"]
    prof = cfg["profiles"][name]
    now = time.time()
    bench_t = cfg["general"]["bench_temp_c"]
    out = {
        "ts": now_iso(),
        "version": VERSION,
        "ac_online": s["ac_online"],
        "bats": {},
        "sessions": state.get("sessions", 0),
        "drain_first": state.get("discharge_first", {}),
        "policy": state.get("policy"),
        "field_mode": profile_type(prof) == "fixed" and prof["bands"]["all"][1] >= 95,
    }

    slot_vals = {}
    for b in BATS:
        v = s["bats"].get(b)
        t = registry.open_tenure(state, b)
        if not v:
            out["bats"][b] = {"present": False}
            continue
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
            "tenure_efc": None,
            "tenure_calendar_score": None,
            "mean_soc": None,
            "pct_ge90": None,
            "discharged_wh": None,
            "power_w": _power_w(v),
            "voltage_v": (round(v["voltage_now_uv"] / 1e6, 2)
                          if v["voltage_now_uv"] is not None else None),
            "health_pct": (round(100.0 * v["charge_full_uah"] / v["charge_full_design_uah"], 1)
                           if v["charge_full_uah"] and v["charge_full_design_uah"] else None),
            "in_slot_hours": round((now - t["start_ts"]) / 3600.0, 1) if t and t["start_ts"] is not None else None,
        }
        entry["pack"] = t["pack"] if t else None
        entry["tenure_id"] = t["id"] if t else None
        entry["pending"] = state.get("pending", {}).get(b)
        if t:
            e, cal = _display_efc_cal(state, t, now, bench_t)
            slot_vals[b] = (e, cal)
            entry["efc"] = round(e, 3)
            entry["calendar_score"] = round(cal, 2)
            entry["tenure_efc"] = round(efc(t), 3)
            entry["tenure_calendar_score"] = round(t["calendar_score"], 2)
            entry["discharged_wh"] = round(
                wh(t["discharge_uah"], v["voltage_min_design_uv"]), 2)
            if t["soc_hours"]:
                entry["mean_soc"] = round(
                    t["soc_hours_sum"] / t["soc_hours"], 1)
            if t["seconds_observed"]:
                entry["pct_ge90"] = round(
                    100.0 * t["seconds_ge_90"] / t["seconds_observed"], 1)
        out["bats"][b] = entry

    both = [b for b in BATS if b in slot_vals]
    if len(both) == 2:
        e0, c0 = slot_vals[both[0]]
        e1, c1 = slot_vals[both[1]]
        out["divergence_efc"] = round(abs(e0 - e1), 3)
        out["divergence_calendar"] = round(abs(c0 - c1), 2)
    else:
        out["divergence_efc"] = None
        out["divergence_calendar"] = None

    g = cfg["general"]
    out["profile"] = {"name": name, "label": prof["label"], "type": profile_type(prof),
                      "description": prof.get("description", ""), "previous": g["previous_profile"]}
    out["profiles"] = [{"name": n, "label": p["label"], "type": profile_type(p), "active": n == name}
                       for n, p in sorted(cfg["profiles"].items())]
    out["revert"] = _revert_info(cfg, state, name, prof, now)
    ov = overnight.ensure(state)
    out["overnight"] = {"phase": ov["phase"], "topoff_start_ts": ov["topoff_start_ts"]}
    out["firmware"] = state.get("firmware", {})
    out["config_error"] = state.get("config_error")
    out["events"] = state.get("events", [])[-10:]

    out["packs"] = registry.all_packs(state, now, bench_t)
    out["pending"] = state.get("pending", {})
    out["rotation"] = registry.rotation_hint(state, cfg["general"]["deadband_efc"], now, bench_t, rows=out["packs"])

    res = policy.resolve(cfg, state, s, now)
    out["recommendation"] = {
        "why": res.why,
        "roles": res.roles,
        "bands": {slot: list(band) for slot, band in res.bands.items()},
    }
    return out

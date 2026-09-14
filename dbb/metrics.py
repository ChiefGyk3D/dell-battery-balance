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
"""Prometheus text exposition of the status view, for a node_exporter
textfile collector. Built from render.state_json() so the numbers are
exactly what `status --json` and the applet show.

Every tick rewrites STATE_DIR/metrics.prom atomically; `status --prometheus`
prints the same text. Absent values (None/NaN) are simply not emitted --
Prometheus has no null, and a stale-looking gauge is worse than a gap.
"""
import math
import os

from dbb.state import STATE_DIR, _make_readable

METRICS_FILE = STATE_DIR / "metrics.prom"


def _esc(v):
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _num(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


class _Out:
    def __init__(self):
        self.lines = []
        self.declared = set()

    def add(self, name, help_text, value, labels=None, kind="gauge"):
        value = _num(value)
        if value is None:
            return
        if name not in self.declared:
            self.lines += [f"# HELP {name} {help_text}", f"# TYPE {name} {kind}"]
            self.declared.add(name)
        lbl = ""
        if labels:
            lbl = "{" + ",".join(f'{k}="{_esc(v)}"' for k, v in labels.items()) + "}"
        self.lines.append(f"{name}{lbl} {value}")

    def text(self):
        return "\n".join(self.lines) + "\n"


def render_prometheus(j):
    """`j` is render.state_json() output."""
    o = _Out()
    o.add("dbb_info", "dell-battery-balance version.", 1, {"version": j.get("version", "")})
    o.add("dbb_ac_online", "1 when the charger is connected.", j.get("ac_online"))
    prof = j.get("profile") or {}
    if prof.get("name"):
        o.add("dbb_profile_info", "Active profile and its type.", 1,
              {"profile": prof["name"], "type": prof.get("type", "")})
    o.add("dbb_field_mode", "1 when the active profile floats both packs at 95% or above.", j.get("field_mode"))
    ov = j.get("overnight") or {}
    phase = ov.get("phase") or "off"
    for ph in ("off", "charging_full", "holding", "topping"):
        o.add("dbb_overnight_phase", "1 for the conference profile's current overnight phase.",
              1 if phase == ph else 0, {"phase": ph})
    o.add("dbb_topoff_start_timestamp_seconds", "When the timed top-off is due to start (while holding).",
          ov.get("topoff_start_ts"))
    o.add("dbb_leave_timestamp_seconds", "Departure time the top-off targets.", ov.get("leave_ts"))
    o.add("dbb_revert_warning", "1 when the active profile auto-reverts within the hour.",
          1 if (j.get("revert") or {}).get("warning") else 0)
    o.add("dbb_config_error", "1 when the config file failed to load and a snapshot is in use.",
          1 if j.get("config_error") else 0)
    o.add("dbb_pending_questions", "Slots waiting for a pack-identity answer.", len(j.get("pending") or {}))
    o.add("dbb_unplug_sessions_total", "Unplug events observed (AC on -> off).",
          j.get("sessions"), kind="counter")
    for slot, n in sorted((j.get("drain_first") or {}).items()):
        o.add("dbb_drain_first_total", "Unplug events in which this slot was the first to discharge.",
              n, {"slot": slot}, kind="counter")

    if j.get("divergence_efc") is not None:
        o.add("dbb_divergence_efc", "Absolute EFC difference between the two inserted packs.", j["divergence_efc"])
    if j.get("divergence_calendar") is not None:
        o.add("dbb_divergence_calendar_score", "Absolute calendar-score difference between the two inserted packs.",
              j["divergence_calendar"])

    roles = ((j.get("policy") or {}).get("roles")) or {}
    for slot, role in sorted(roles.items()):
        o.add("dbb_slot_role", "Role the last applied policy gave this slot.", 1, {"slot": slot, "role": role})

    fw = j.get("firmware") or {}
    for slot, b in sorted((j.get("bats") or {}).items()):
        L = {"slot": slot}
        present = bool(b.get("present"))
        o.add("dbb_slot_present", "1 when a pack is in the slot.", 1 if present else 0, L)
        rec = fw.get(slot) or {}
        if rec:
            o.add("dbb_slot_firmware_error", "1 when the last ceiling write or read-back for this slot failed.",
                  1 if rec.get("error") else 0, L)
            obs = rec.get("observed")
            if obs:
                o.add("dbb_slot_ceiling_start_percent", "Charge-start threshold the firmware reports.", obs[0], L)
                o.add("dbb_slot_ceiling_stop_percent", "Charge-stop threshold (ceiling) the firmware reports.", obs[1], L)
        if not present:
            continue
        if b.get("pack"):
            o.add("dbb_slot_pack_info", "Which named pack the slot holds.", 1, {"slot": slot, "pack": b["pack"]})
        if b.get("status"):
            o.add("dbb_slot_status", "Kernel charge status of the pack in this slot.", 1,
                  {"slot": slot, "status": b["status"]})
        o.add("dbb_slot_capacity_percent", "State of charge.", b.get("capacity"), L)
        o.add("dbb_slot_power_watts", "Signed power; negative while discharging.", b.get("power_w"), L)
        o.add("dbb_slot_voltage_volts", "Pack voltage.", b.get("voltage_v"), L)
        o.add("dbb_slot_temperature_celsius", "Pack temperature.", b.get("temp_c"), L)
        o.add("dbb_slot_health_percent", "charge_full as a percentage of design capacity.", b.get("health_pct"), L)
        o.add("dbb_slot_in_slot_hours", "Hours since this tenure opened.", b.get("in_slot_hours"), L)
        o.add("dbb_slot_tenure_efc", "EFC accrued by the current tenure alone.", b.get("tenure_efc"), L)
        o.add("dbb_slot_tenure_calendar_score", "Calendar score accrued by the current tenure alone.",
              b.get("tenure_calendar_score"), L)
        o.add("dbb_slot_mean_soc_percent", "Time-weighted mean state of charge over the tenure.", b.get("mean_soc"), L)
        o.add("dbb_slot_time_ge90_percent", "Share of observed time at or above 90%.", b.get("pct_ge90"), L)

    for p in j.get("packs") or []:
        L = {"pack": p["name"]}
        o.add("dbb_pack_efc", "Equivalent full cycles across every tenure of this pack.", p.get("efc"), L)
        o.add("dbb_pack_calendar_score", "Calendar-aging score across every tenure, plus the bench estimate.",
              p.get("calendar_score"), L)
        o.add("dbb_pack_retired", "1 when the pack is retired.", 1 if p.get("retired") else 0, L)
        o.add("dbb_pack_tenures", "Tenures recorded for this pack.", p.get("tenures"), L)
        if p.get("in_slot"):
            o.add("dbb_pack_in_slot", "1 for the slot this pack is currently in.", 1,
                  {"pack": p["name"], "slot": p["in_slot"]})
        elif not p.get("retired"):
            o.add("dbb_pack_bench_hours", "Hours this pack has been out of the machine.", p.get("bench_hours"), L)
            o.add("dbb_pack_bench_soc_percent", "State of charge at which the pack was removed.",
                  p.get("removed_at_soc"), L)
    return o.text()


def write_metrics(text, path=None):
    """Atomic rewrite; the file is world-readable like the rest of the state
    directory so node_exporter (any user) can read it."""
    path = path or METRICS_FILE
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
    _make_readable(path, 0o664)

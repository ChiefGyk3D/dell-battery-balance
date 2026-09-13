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
"""Pack registry: named physical packs, slot tenures, and occupancy changes.

These packs expose no readable identity (identical serial, ePPID and
manufacture date), so a slot is modelled as a sequence of *tenures* -- one
per continuous occupancy. Wear accrues into the open tenure and never
pauses; identity is a label on the tenure that the user confirms.
"""
import re

from dbb.state import add_event, now_iso
from dbb.sysfs import BATS
from dbb.wear import blank_slot, efc

PACK_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,16}$")
MIGRATION_LETTERS = {"BAT0": "A", "BAT1": "B"}

SAME_GUESS_PCT = 3.0
DISCONTINUITY_PCT = 10.0
DISCONTINUITY_WINDOW_S = 120.0
BENCH_WARN_SOC = 70


class RegistryError(ValueError):
    pass


# ----------------------------------------------------------------- tenures

def new_tenure(state, slot, v, ts):
    t = blank_slot(v["charge_full_design_uah"])
    t.update({
        "id": state["next_tenure_id"], "slot": slot, "start_ts": ts, "end_ts": None,
        "pack": None,
        "start_charge_uah": v["charge_now_uah"], "start_charge_full_uah": v["charge_full_uah"],
        "last_charge_uah": v["charge_now_uah"], "last_charge_full_uah": v["charge_full_uah"],
        "last_capacity": v["capacity"],
    })
    state["next_tenure_id"] += 1
    state["tenures"].append(t)
    state["slots"][slot] = t["id"]
    return t


def tenure_by_id(state, tid):
    for t in state.get("tenures", []):
        if t["id"] == tid:
            return t
    return None


def open_tenure(state, slot):
    tid = state.get("slots", {}).get(slot)
    return tenure_by_id(state, tid) if tid is not None else None


slot_counters = open_tenure


def last_closed_tenure(state, slot):
    closed = [t for t in state.get("tenures", []) if t["slot"] == slot and t["end_ts"] is not None]
    return max(closed, key=lambda t: t["end_ts"]) if closed else None


def close_tenure(state, slot, ts):
    t = open_tenure(state, slot)
    if t is None:
        return None
    t["end_ts"] = ts
    state["slots"][slot] = None
    state.get("pending", {}).pop(slot, None)
    if t["pack"]:
        # Invariant: every path that sets t["pack"] (assign/_new_pack, reassign,
        # rename_pack) guarantees state["packs"][t["pack"]] already exists.
        p = state["packs"][t["pack"]]
        soc = t.get("last_capacity")
        p["removed_at_soc"] = soc
        p["removed_ts"] = ts
        add_event(state, "pack", f"{t['pack']} removed from {slot} at {soc}%")
        if soc is not None and soc > BENCH_WARN_SOC:
            add_event(state, "warning", f"{t['pack']} removed at {soc}% - discharge to ~50% before storing")
    else:
        add_event(state, "pack", f"unidentified pack removed from {slot}")
    return t


def note_absent(state, slot, ts):
    if open_tenure(state, slot) is not None:
        close_tenure(state, slot, ts)


def allowed_delta_uah(design_uah, dt):
    """Largest charge change a real pack could show in dt seconds: 10% of
    design per two-minute tick, scaled up linearly for longer gaps (a pack on
    the charger during a suspend can move the whole way)."""
    return design_uah * DISCONTINUITY_PCT / 100.0 * max(1.0, (dt or 0.0) / DISCONTINUITY_WINDOW_S)


def occupancy_change(t, v, dt):
    if (v.get("charge_full_uah") and t.get("last_charge_full_uah")
            and v["charge_full_uah"] != t["last_charge_full_uah"]):
        return "charge_full"
    if dt is None or v.get("charge_now_uah") is None or t.get("last_charge_uah") is None:
        return None
    if abs(v["charge_now_uah"] - t["last_charge_uah"]) > allowed_delta_uah(t["design_uah"], dt):
        return "discontinuity"
    return None


def guess_identity(prev_t, v):
    """'same' only when the reading is within 3% of where the previous
    occupant left off and charge_full is unchanged; otherwise 'unsure'. Never
    'different': a pack charged off-machine looks different and is not."""
    if prev_t is None or prev_t.get("last_charge_uah") is None or v.get("charge_now_uah") is None:
        return "unsure"
    if v.get("charge_full_uah") != prev_t.get("last_charge_full_uah"):
        return "unsure"
    if abs(v["charge_now_uah"] - prev_t["last_charge_uah"]) <= prev_t["design_uah"] * SAME_GUESS_PCT / 100.0:
        return "same"
    return "unsure"


def observe(state, slot, v, s, dt):
    """Return (tenure, changed) for a present slot, opening a new tenure when
    the occupancy plausibly changed. The interval that trips a change is not
    accrued anywhere -- it belongs to nobody."""
    t = open_tenure(state, slot)
    reason = None
    if t is None:
        reason = "insert"
    else:
        reason = occupancy_change(t, v, dt)
    if reason:
        prev = close_tenure(state, slot, s["ts"]) if t is not None else last_closed_tenure(state, slot)
        t = new_tenure(state, slot, v, s["ts"])
        g = guess_identity(prev, v)
        delta = None
        if prev and prev.get("last_charge_uah") is not None and v.get("charge_now_uah") is not None:
            delta = (v["charge_now_uah"] - prev["last_charge_uah"]) / t["design_uah"] * 100.0
        state.setdefault("pending", {})[slot] = {
            "tenure_id": t["id"], "guess": g, "reason": reason,
            "previous_pack": prev["pack"] if prev else None,
            "delta_pct": delta, "opened_ts": now_iso(),
        }
        add_event(state, "pack", f"{slot}: occupancy change ({reason}), guess={g}, tenure {t['id']}")
        changed = True
    else:
        changed = False
    t["last_charge_uah"] = v["charge_now_uah"]
    t["last_charge_full_uah"] = v["charge_full_uah"]
    t["last_capacity"] = v["capacity"]
    return t, changed


def efc_for_slot(state, slot):
    t = open_tenure(state, slot)
    return efc(t) if t else None


# ---------------------------------------------------------- identification

def _check_name(name):
    if not PACK_NAME_RE.match(name or ""):
        raise RegistryError(f"bad pack name {name!r}: use 1-16 of A-Z a-z 0-9 _ -")


def packs_in_slots(state):
    return {t["pack"]: slot for slot in BATS
            if (t := open_tenure(state, slot)) and t["pack"]}


def _ensure_free(state, name, except_tenure_id=None):
    for slot in BATS:
        t = open_tenure(state, slot)
        if t and t["pack"] == name and t["id"] != except_tenure_id:
            raise RegistryError(f"pack {name} is already in {slot}")


def _new_pack(state, name):
    _check_name(name)
    if name in state["packs"]:
        raise RegistryError(f"pack {name} already exists")
    state["packs"][name] = {"label": name, "first_seen": now_iso(), "retired": False,
                            "notes": "", "removed_at_soc": None, "removed_ts": None}


def assign(state, slot, name, new=False):
    t = open_tenure(state, slot)
    if t is None:
        raise RegistryError(f"no pack present in {slot}")
    if new:
        _new_pack(state, name)
    else:
        _check_name(name)
        if name not in state["packs"]:
            raise RegistryError(f"unknown pack {name}; use 'pack new' to create it")
        if state["packs"][name]["retired"]:
            raise RegistryError(f"pack {name} is retired; 'pack unretire' it first")
    _ensure_free(state, name, except_tenure_id=t["id"])
    t["pack"] = name
    p = state["packs"][name]
    p["removed_at_soc"], p["removed_ts"] = None, None
    state.get("pending", {}).pop(slot, None)
    add_event(state, "pack", f"{slot}: tenure {t['id']} identified as {name}")
    return t


def same(state, slot):
    pending = state.get("pending", {}).get(slot)
    prev = pending.get("previous_pack") if pending else None
    if not prev:
        raise RegistryError(f"{slot}: no previous pack to confirm; use 'pack assign' or 'pack new'")
    return assign(state, slot, prev)


def reassign(state, tenure_id, name):
    t = tenure_by_id(state, tenure_id)
    if t is None:
        raise RegistryError(f"no tenure {tenure_id}")
    _check_name(name)
    if name not in state["packs"]:
        raise RegistryError(f"unknown pack {name}")
    if t["end_ts"] is None:
        _ensure_free(state, name, except_tenure_id=t["id"])
    old = t["pack"]
    t["pack"] = name
    if t["end_ts"] is None:
        state.get("pending", {}).pop(t["slot"], None)
    add_event(state, "pack", f"tenure {tenure_id} reassigned {old} -> {name}")
    return t


def rename_pack(state, old, new):
    if old not in state["packs"]:
        raise RegistryError(f"unknown pack {old}")
    _check_name(new)
    if new in state["packs"]:
        raise RegistryError(f"pack {new} already exists")
    state["packs"][new] = state["packs"].pop(old)
    state["packs"][new]["label"] = new
    for t in state["tenures"]:
        if t["pack"] == old:
            t["pack"] = new
    for p in state.get("pending", {}).values():
        if p.get("previous_pack") == old:
            p["previous_pack"] = new
    add_event(state, "pack", f"renamed {old} -> {new}")


def retire_pack(state, name):
    if name not in state["packs"]:
        raise RegistryError(f"unknown pack {name}")
    if name in packs_in_slots(state):
        raise RegistryError(f"pack {name} is in {packs_in_slots(state)[name]}; remove it first")
    state["packs"][name]["retired"] = True
    add_event(state, "pack", f"retired {name}")


def unretire_pack(state, name):
    if name not in state["packs"]:
        raise RegistryError(f"unknown pack {name}")
    state["packs"][name]["retired"] = False
    add_event(state, "pack", f"unretired {name}")


# --------------------------------------------------------------- migration

def migrate_v1(state):
    if state.get("version", 1) >= 2:
        return state
    old_slots = state.get("slots") or {}
    last = state.get("last") or {"bats": {}}
    state["packs"], state["tenures"], state["pending"] = {}, [], {}
    state["slots"] = {b: None for b in BATS}
    state["next_tenure_id"] = 1
    for slot in BATS:
        counters = old_slots.get(slot)
        if not counters:
            continue
        name = MIGRATION_LETTERS[slot]
        lb = last["bats"].get(slot) or {}
        t = dict(counters)
        t.update({
            "id": state["next_tenure_id"], "slot": slot,
            "start_ts": None, "end_ts": None, "pack": name,
            "start_charge_uah": None, "start_charge_full_uah": None,
            "last_charge_uah": lb.get("charge_now_uah"),
            "last_charge_full_uah": lb.get("charge_full_uah"),
            "last_capacity": lb.get("capacity"),
        })
        state["next_tenure_id"] += 1
        state["tenures"].append(t)
        state["slots"][slot] = t["id"]
        state["packs"][name] = {"label": name, "first_seen": counters.get("first_seen") or now_iso(),
                                "retired": False, "notes": "",
                                "removed_at_soc": None, "removed_ts": None}
    state["version"] = 2
    add_event(state, "migrate", "state v1 -> v2: slots became packs "
              + ", ".join(f"{s}={MIGRATION_LETTERS[s]}" for s in BATS if old_slots.get(s)))
    return state

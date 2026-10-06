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

A slot is modelled as a sequence of *tenures* -- one per continuous
occupancy. Wear accrues into the open tenure and never pauses; identity is a
label on the tenure. Genuine Dell packs carry a readable identity (ePPID via
dell-wmi-ddv, plus the smart-battery serial), and once a named pack's
fingerprint is learned a tenure is labelled from it without asking. Packs
that report no identity, or share one (the tool's first two packs did, as
counterfeit and third-party packs do), are asked about exactly as before.
"""
import re

from dbb.state import add_event, now_iso
from dbb.sysfs import BATS
from dbb.wear import blank_slot, calendar_stress, efc

PACK_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,16}$")
MIGRATION_LETTERS = {"BAT0": "A", "BAT1": "B"}

SAME_GUESS_PCT = 3.0
DISCONTINUITY_PCT = 10.0
DISCONTINUITY_WINDOW_S = 120.0
BENCH_WARN_SOC = 70

EPPID_MIN_LEN = 10
CONFIRM_SAMPLES = 2   # consecutive agreeing samples before a reading counts
# While a newly inserted pack's ePPID is being confirmed, its question is held
# back; it is asked only if the pack is still unnamed after this many samples.
DEFER_SAMPLES = 3
# Names given to genuine packs registered without asking, in order.
AUTO_NAMES = ("Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel",
              "India", "Juliett", "Kilo", "Lima", "Mike", "November", "Oscar", "Papa",
              "Quebec", "Romeo", "Sierra", "Tango", "Uniform", "Victor", "Whiskey",
              "Xray", "Yankee", "Zulu")
# Consecutive twin samples before the packs are judged clones. Real clones
# twin on every sample; BAT1 mirroring BAT0 lasted two samples on 2026-09-29
# and again on 2026-10-02, and at two that condemned three genuine packs.
TWIN_SAMPLES = 15
TWIN_WARNING = "reports the same identity"


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
    if (v.get("charge_full_design_uah") and t.get("design_uah")
            and v["charge_full_design_uah"] != t["design_uah"]):
        return "design"
    if not t.get("design_uah"):
        return None   # a transient sysfs read failure left us without a yardstick; wait for a good one
    if dt is None or v.get("charge_now_uah") is None or t.get("last_charge_uah") is None:
        return None
    if abs(v["charge_now_uah"] - t["last_charge_uah"]) > allowed_delta_uah(t["design_uah"], dt):
        return "discontinuity"
    return None


def guess_identity(prev_t, v, design_uah=None):
    """'same' only when the reading is within 3% of where the previous
    occupant left off and charge_full is unchanged; otherwise 'unsure'. Never
    'different' from charge alone: a pack charged off-machine looks different
    and is not. Only a changed fingerprint says 'different' (see observe)."""
    if prev_t is None or prev_t.get("last_charge_uah") is None or v.get("charge_now_uah") is None:
        return "unsure"
    if v.get("charge_full_uah") != prev_t.get("last_charge_full_uah"):
        return "unsure"
    design_uah = design_uah or prev_t.get("design_uah")
    if not design_uah:
        return "unsure"   # no yardstick to compare against
    if abs(v["charge_now_uah"] - prev_t["last_charge_uah"]) <= design_uah * SAME_GUESS_PCT / 100.0:
        return "same"
    return "unsure"


def observe(state, slot, v, s, dt, fp=None):
    """Return (tenure, changed) for a present slot, opening a new tenure when
    the occupancy plausibly changed. The interval that trips a change is not
    accrued anywhere -- it belongs to nobody. `fp` is the slot's confirmed
    fingerprint, or None: a confirmed reading that contradicts the labelled
    pack's is a change the charge heuristic cannot see (a swap that picked up
    where the last pack left off)."""
    t = open_tenure(state, slot)
    reason = None
    if t is None:
        reason = "insert"
    else:
        reason = occupancy_change(t, v, dt)
        known = state["packs"][t["pack"]].get("fingerprint") if t["pack"] else None
        if reason is None and known:
            if fp and known != fp:
                reason = "identity"
            elif fp is None and _unconfirmed_other(state, slot, known):
                # A different pack may be here but only one sample says so:
                # this interval belongs to nobody, and the labelled pack's
                # last reading stays where it left off.
                return t, True
    if reason:
        prev = close_tenure(state, slot, s["ts"]) if t is not None else last_closed_tenure(state, slot)
        t = new_tenure(state, slot, v, s["ts"])
        # The charge guess cannot overrule a fingerprint that just changed.
        g = "different" if reason == "identity" else guess_identity(prev, v, t["design_uah"])
        delta = None
        if (t["design_uah"] and prev and prev.get("last_charge_uah") is not None
                and v.get("charge_now_uah") is not None):
            delta = (v["charge_now_uah"] - prev["last_charge_uah"]) / t["design_uah"] * 100.0
        question = {
            "tenure_id": t["id"], "guess": g, "reason": reason,
            "previous_pack": prev["pack"] if prev else None,
            "delta_pct": delta, "opened_ts": now_iso(),
        }
        if _reads_identity(state, slot):
            # The pack reports an ePPID: name it from that once it is
            # confirmed, and ask only if that fails (_ask_deferred).
            t["deferred_question"] = question
        else:
            state.setdefault("pending", {})[slot] = question
        add_event(state, "pack", f"{slot}: occupancy change ({reason}), guess={g}, tenure {t['id']}")
        changed = True
    else:
        changed = False
    if not t.get("design_uah") and v.get("charge_full_design_uah"):
        t["design_uah"] = v["charge_full_design_uah"]   # self-heal once a good reading arrives
    t["last_charge_uah"] = v["charge_now_uah"]
    t["last_charge_full_uah"] = v["charge_full_uah"]
    t["last_capacity"] = v["capacity"]
    return t, changed


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
                            "notes": "", "removed_at_soc": None, "removed_ts": None,
                            "fingerprint": None, "identity_unreadable": False}


def assign(state, slot, name, new=False, now=None, bench_temp_c=25.0, how=None):
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
    # Carry the bench calendar-aging estimate accrued while this pack was out
    # into the NEW open tenure before clearing removed_at_soc/ts -- otherwise
    # it silently vanishes on reinsertion (same formula pack_totals uses for
    # a still-benched pack). Bounded by the tenure's own start, NOT `now`
    # (when the user happens to answer): the new tenure already accrues real
    # in-slot calendar every tick from its start onward, so using `now` here
    # would double-count that stretch -- or, for a pack that never actually
    # left (a charge_full/design/discontinuity trip alone), the whole
    # interval.
    if (t.get("start_ts") is not None and p.get("removed_ts") is not None
            and p.get("removed_at_soc") is not None):
        bench_hours = max(0.0, (t["start_ts"] - p["removed_ts"]) / 3600.0)
        t["calendar_score"] += bench_hours * calendar_stress(p["removed_at_soc"], bench_temp_c)
    p["removed_at_soc"], p["removed_ts"] = None, None
    state.get("pending", {}).pop(slot, None)
    t.pop("deferred_question", None)
    add_event(state, "pack", f"{slot}: tenure {t['id']} identified as {name}"
              + {"identity": " (identity read from the pack)",
                 "new": " (a new pack, named automatically)"}.get(how, ""))
    _hand_label(state, slot, name)
    return t


def same(state, slot, now=None, bench_temp_c=25.0):
    pending = state.get("pending", {}).get(slot)
    prev = pending.get("previous_pack") if pending else None
    if not prev:
        raise RegistryError(f"{slot}: no previous pack to confirm; use 'pack assign' or 'pack new'")
    return assign(state, slot, prev, now=now, bench_temp_c=bench_temp_c)


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
        _hand_label(state, t["slot"], name)
    add_event(state, "pack", f"tenure {tenure_id} reassigned {old} -> {name}")
    return t


def swap(state):
    """Exchange the labels of the two open tenures atomically: the repair for
    a pair that got mislabeled (answered wrong, or swapped while both were out
    and reinserted so plausibly nothing tripped). No bench carry is involved:
    neither pack is being re-identified as one that was out. Any pending
    question on either slot is answered by the exchange."""
    t0, t1 = open_tenure(state, "BAT0"), open_tenure(state, "BAT1")
    if t0 is None or t1 is None:
        empty = [s for s, t in (("BAT0", t0), ("BAT1", t1)) if t is None]
        raise RegistryError(f"no pack present in {', '.join(empty)}; swap needs both slots occupied")
    if not t0["pack"] and not t1["pack"]:
        raise RegistryError("neither pack is identified; nothing to swap (use 'pack assign' / 'pack new')")
    t0["pack"], t1["pack"] = t1["pack"], t0["pack"]
    for slot, t in (("BAT0", t0), ("BAT1", t1)):
        state.get("pending", {}).pop(slot, None)
        if t["pack"]:
            _hand_label(state, slot, t["pack"])
    add_event(state, "pack", f"swap: BAT0 is now {t0['pack'] or '?'}, BAT1 is now {t1['pack'] or '?'}")
    return t0, t1


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
    slots = packs_in_slots(state)
    if name in slots:
        raise RegistryError(f"pack {name} is in {slots[name]}; remove it first")
    state["packs"][name]["retired"] = True
    add_event(state, "pack", f"retired {name}")


def unretire_pack(state, name):
    if name not in state["packs"]:
        raise RegistryError(f"unknown pack {name}")
    state["packs"][name]["retired"] = False
    add_event(state, "pack", f"unretired {name}")


# ---------------------------------------------------------------- identity

def fingerprint(v):
    """The pack's ePPID, or None when it offers no usable one (no
    dell-wmi-ddv, blank, or a filler value). The serial is not used: it
    comes through ACPI with the rest of BAT1's readings, and on 2026-09-27
    BAT1's serial_number mirrored BAT0's for a whole session while BAT1's
    eppid (read through dell-wmi-ddv) stayed its own."""
    e = (v.get("eppid") or "").strip()
    if len(e) < EPPID_MIN_LEN or len(set(e)) == 1:
        return None
    return e


def _ident(state):
    ident = state.setdefault("identity", {"slots": {}, "twin_samples": 0})
    if not ident.get("eppid_only"):
        # 0.5.0-0.6.0 stored "EPPID/serial"; keep the ePPID.
        for p in state.get("packs", {}).values():
            if p.get("fingerprint"):
                p["fingerprint"] = p["fingerprint"].split("/", 1)[0]
        for rec in ident["slots"].values():
            if rec and rec.get("fp"):
                rec["fp"] = rec["fp"].split("/", 1)[0]
        ident["eppid_only"] = True
    if not ident.get("twin_by_source"):
        _restore_glitch_condemned(state)
        ident["twin_by_source"] = True
    return ident


def _restore_glitch_condemned(state):
    """0.5.0-0.7.0 marked packs unreadable after two twin samples, which a
    BAT1 mirror glitch produces. Record who set each mark; give packs the
    twin rule condemned, and that were never marked by hand, a fresh read."""
    hand = {e["detail"].split(":", 1)[0] for e in state.get("events", [])
            if e.get("kind") == "pack" and e.get("detail", "").endswith(": identity marked unreadable")}
    twin = {e["detail"].split(" ", 1)[0] for e in state.get("events", [])
            if e.get("kind") == "warning" and TWIN_WARNING in e.get("detail", "")}
    for name, p in state.get("packs", {}).items():
        if not p.get("identity_unreadable") or p.get("identity_unreadable_by"):
            continue
        if name in twin and name not in hand and not p.get("retired"):
            p["identity_unreadable"] = False
            add_event(state, "pack", f"{name}: identity read again; it was marked unreadable "
                      "after two mirrored samples, not because it shares an identity")
        else:
            # Only two things set the mark; packs renamed since have no
            # hand event under their current name, but no twin warning either.
            p["identity_unreadable_by"] = "twin" if name in twin and name not in hand else "hand"


def read_identities(state, s):
    """Fold sample s into the per-slot reading streaks and return each slot's
    confirmed fingerprint (or None). Both slots reading one value is never an
    identity: it is either BAT1's sysfs mirroring BAT0 for a sample (measured)
    or two packs sharing one identity."""
    ident = _ident(state)
    raw = {b: fingerprint(v) for b, v in s["bats"].items() if v and v.get("present", 1) == 1}
    twins = len(raw) == 2 and None not in raw.values() and len(set(raw.values())) == 1
    ident["twin_samples"] = ident.get("twin_samples", 0) + 1 if twins else 0
    ident["twin_fp"] = raw["BAT0"] if twins else None
    out = {}
    for b in BATS:
        fp = None if twins else raw.get(b)
        prev = ident["slots"].get(b) or {}
        if fp is None:
            ident["slots"][b] = None
            out[b] = None
            continue
        n = prev.get("n", 0) + 1 if prev.get("fp") == fp else 1
        ident["slots"][b] = {"fp": fp, "n": n, "model": (s["bats"].get(b) or {}).get("model")}
        out[b] = fp if n >= CONFIRM_SAMPLES else None
    return out


def _reads_identity(state, slot):
    ident = state.get("identity") or {}
    return bool((ident.get("slots") or {}).get(slot)) and not ident.get("twin_samples")


def plausible_dell_eppid(fp, model):
    """True when the ePPID carries the pack's own Dell part number: genuine
    DRPTT67 packs read CN0DRPTT... beside model "DELL DRPTT67"; the clone pair
    read CCDELLPN... beside "DELL NY5PG". Only such a pack is registered
    without asking."""
    if not fp or not model or len(fp) < 20 or fp[2] != "0":
        return False
    part = fp[3:8]
    return part.isalnum() and part in model.replace(" ", "").upper()


def _auto_name(state):
    for n in AUTO_NAMES:
        if n not in state["packs"]:
            return n
    i = len(state["packs"]) + 1
    while f"Pack{i}" in state["packs"]:
        i += 1
    return f"Pack{i}"


def _ask_deferred(state):
    """Raise a held-back question when the pack is still unnamed: it stopped
    reporting an ePPID, clones met, or DEFER_SAMPLES passed without a match."""
    for b in BATS:
        t = open_tenure(state, b)
        if t is None or not t.get("deferred_question"):
            continue
        if t["pack"]:
            t.pop("deferred_question")
            continue
        if _reads_identity(state, b) and t.get("samples", 0) < DEFER_SAMPLES:
            continue
        state.setdefault("pending", {})[b] = t.pop("deferred_question")


def _unconfirmed_other(state, slot, known):
    rec = (state.get("identity") or {}).get("slots", {}).get(slot)
    return bool(rec) and rec["n"] < CONFIRM_SAMPLES and rec["fp"] != known


def slot_identity(state, slot):
    rec = (state.get("identity") or {}).get("slots", {}).get(slot)
    return rec["fp"] if rec and rec["n"] >= CONFIRM_SAMPLES else None


def identity_state(p):
    if p.get("identity_unreadable"):
        return "unreadable"
    return "read" if p.get("fingerprint") else "asked"


def _owner(state, fp):
    names = [n for n, p in state["packs"].items()
             if p.get("fingerprint") == fp and not p["retired"] and not p.get("identity_unreadable")]
    return names[0] if len(names) == 1 else None


def _mark_unreadable(state, name):
    p = state["packs"][name]
    p["fingerprint"] = None
    if p.get("identity_unreadable"):
        return
    p["identity_unreadable"], p["identity_unreadable_by"] = True, "twin"
    add_event(state, "warning",
              f"{name} {TWIN_WARNING} (ePPID) as another pack, "
              "which is what counterfeit and third-party packs do; it will be asked "
              "about, not read. Buy Dell OEM packs through authorized channels")


def _hand_label(state, slot, name):
    """A label set by hand that contradicts what the pack in the slot reports
    wins, and the pack's learned fingerprint is dropped to be relearned."""
    p = state["packs"][name]
    fp = slot_identity(state, slot)
    if fp and p.get("fingerprint") and p["fingerprint"] != fp:
        p["fingerprint"] = None
        add_event(state, "warning", f"{name}: labelled by hand against the identity "
                  f"{slot} reports; its fingerprint is forgotten and will be relearned")


def resolve_identities(state, ids, bench_temp_c=25.0):
    """After a sample: label unidentified tenures whose confirmed fingerprint
    belongs to exactly one known pack, learn the fingerprint of labelled
    packs that have none, and give up on packs that turn out to share one."""
    ident = _ident(state)
    if ident.get("twin_samples", 0) >= CONFIRM_SAMPLES:
        if ident["twin_samples"] >= TWIN_SAMPLES:
            names = [t["pack"] for t in (open_tenure(state, b) for b in BATS) if t and t["pack"]]
            # A pack already known by another fingerprint proves a slot is
            # being misread (BAT1 mirroring BAT0), not that clones met.
            if all(state["packs"][n].get("fingerprint") in (None, ident.get("twin_fp")) for n in names):
                for n in names:
                    _mark_unreadable(state, n)
        return
    for b in BATS:
        fp, t = ids.get(b), open_tenure(state, b)
        if not fp or t is None:
            continue
        if t["pack"] is None:
            owner = _owner(state, fp)
            if owner and owner not in packs_in_slots(state):
                assign(state, b, owner, bench_temp_c=bench_temp_c, how="identity")
            elif (not any(p.get("fingerprint") == fp for p in state["packs"].values())
                  and plausible_dell_eppid(fp, ident["slots"][b].get("model"))):
                name = _auto_name(state)
                assign(state, b, name, new=True, bench_temp_c=bench_temp_c, how="new")
                state["packs"][name]["fingerprint"] = fp
                add_event(state, "pack", f"{name}: new pack registered from its ePPID; "
                          f"rename it with 'pack rename {name} <name>'")
            continue
        p = state["packs"][t["pack"]]
        if p.get("identity_unreadable") or p.get("fingerprint"):
            continue
        clash = [n for n, q in state["packs"].items() if n != t["pack"] and q.get("fingerprint") == fp]
        if clash:
            for n in clash + [t["pack"]]:
                _mark_unreadable(state, n)
            continue
        p["fingerprint"] = fp
        add_event(state, "pack", f"{t['pack']}: identity learned from the pack in {b}")


def track_cycles(state, s, last):
    """Record each labelled pack's firmware cycle_count and every change to
    it, with the tool's EFC alongside for comparison. A reading counts only
    when two consecutive samples agree, the slot has no open question, and --
    for a pack with a fingerprint -- the slot's confirmed identity is that
    pack's, so a mirrored BAT1 sample cannot move the wrong pack. Nothing is
    recorded from a sample where both slots report one identity."""
    if _ident(state).get("twin_samples", 0):
        return
    for b in BATS:
        v, t = s["bats"].get(b), open_tenure(state, b)
        if not v or t is None or not t["pack"] or state.get("pending", {}).get(b):
            continue
        n, src = v.get("cycle_count"), v.get("cycle_source", "sysfs")
        prev = ((last or {}).get("bats") or {}).get(b) or {}
        if n is None or prev.get("cycle_count") != n or prev.get("cycle_source", "sysfs") != src:
            continue
        p = state["packs"][t["pack"]]
        if p.get("fingerprint") and slot_identity(state, b) != p["fingerprint"]:
            continue
        e = _pack_efc(state, t["pack"])
        fw = p.get("fw_cycles")
        if fw is not None and fw.get("source", "sysfs") != src:
            if src == "sysfs":
                continue        # the DDV reading went stale; never fall back to the placeholder
            add_event(state, "cycles", f"{t['pack']}: firmware cycle count {n}, read from the pack "
                      f"through Dell WMI (the kernel's cycle_count read {fw['value']})")
            fw = None
        if fw is None:
            p["fw_cycles"] = {"value": n, "first_value": n, "first_ts": now_iso(),
                              "changed_ts": None, "efc_at_first": e, "source": src}
            continue
        if n == fw["value"]:
            continue
        old, fw["value"], fw["changed_ts"] = fw["value"], n, now_iso()
        if n < old:
            add_event(state, "warning", f"{t['pack']}: firmware cycle count went down, {old} -> {n}; "
                      "a genuine pack's counter only climbs, so it was reset or the pack is not what it reports")
        else:
            add_event(state, "cycles", f"{t['pack']}: firmware cycle count {old} -> {n} "
                      f"(the tool counts {e - fw['efc_at_first']:.2f} EFC since it first read {fw['first_value']})")


def _pack_efc(state, name):
    tenures = [t for t in state["tenures"] if t["pack"] == name]
    design = next((t["design_uah"] for t in tenures if t.get("design_uah")), None)
    return sum(t["discharge_uah"] for t in tenures) / design if design else 0.0


def set_identity(state, name, action):
    """'forget': drop the fingerprint (and any unreadable mark) and relearn.
    'unreadable': never read this pack's identity; always ask."""
    if name not in state["packs"]:
        raise RegistryError(f"unknown pack {name}")
    p = state["packs"][name]
    p["fingerprint"] = None
    p["identity_unreadable"] = action == "unreadable"
    p["identity_unreadable_by"] = "hand" if action == "unreadable" else None
    add_event(state, "pack", f"{name}: identity "
              + ("marked unreadable" if action == "unreadable" else "forgotten, will be relearned"))


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


# ------------------------------------------------------------------ totals

def pack_totals(state, name, now, bench_temp_c):
    p = state["packs"][name]
    tenures = [t for t in state["tenures"] if t["pack"] == name]
    discharge = sum(t["discharge_uah"] for t in tenures)
    design = next((t["design_uah"] for t in tenures if t.get("design_uah")), None)
    calendar = sum(t["calendar_score"] for t in tenures)
    in_slot = packs_in_slots(state).get(name)
    bench_hours = bench_calendar = 0.0
    if in_slot is None and not p["retired"] and p.get("removed_ts") is not None:
        bench_hours = max(0.0, (now - p["removed_ts"]) / 3600.0)
        soc = p.get("removed_at_soc")
        if soc is not None:
            bench_calendar = bench_hours * calendar_stress(soc, bench_temp_c)
    return {
        "efc": (discharge / design) if design else 0.0,
        "discharge_uah": discharge,
        "calendar_score": calendar + bench_calendar,
        "bench_calendar": bench_calendar,
        "bench_hours": bench_hours,
        "in_slot": in_slot,
        "tenures": len(tenures),
        "retired": p["retired"],
        "removed_at_soc": p.get("removed_at_soc"),
        "identity": identity_state(p),
        "firmware_cycles": (p.get("fw_cycles") or {}).get("value"),
        "efc_since_firmware_first": (round((discharge / design if design else 0.0)
                                           - p["fw_cycles"]["efc_at_first"], 3)
                                     if p.get("fw_cycles") else None),
    }


def efc_for_slot(state, slot):
    t = open_tenure(state, slot)
    if t is None:
        return None
    if t["pack"]:
        # now/bench_temp_c don't matter here: EFC is discharge/design only,
        # the bench estimate never feeds into it.
        return pack_totals(state, t["pack"], now=0.0, bench_temp_c=25.0)["efc"]
    return efc(t)


def all_packs(state, now, bench_temp_c):
    rows = [{"name": n, **pack_totals(state, n, now, bench_temp_c)} for n in state["packs"]]
    return sorted(rows, key=lambda r: (r["retired"], r["efc"], r["name"]))


def rotation_hint(state, deadband, now, bench_temp_c, rows=None):
    """Name the least-worn bench pack when it is more than `deadband` EFC
    behind the most-worn inserted pack. `rows` is all_packs() output when
    the caller already has it."""
    if rows is None:
        rows = all_packs(state, now, bench_temp_c)
    inserted = [r for r in rows if r["in_slot"]]
    bench = [r for r in rows if not r["in_slot"] and not r["retired"]]
    if not inserted or not bench:
        return None
    worst = max(inserted, key=lambda r: r["efc"])
    best = min(bench, key=lambda r: r["efc"])
    behind = worst["efc"] - best["efc"]
    if behind <= deadband:
        return None
    return {"swap_in": best["name"], "replace": worst["name"], "behind_by_efc": round(behind, 3)}

# Plan B: Pack Registry, Tenures and Swap Detection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Track wear per *physical pack* across removals, swaps and a rotation of more packs than slots, on hardware whose packs expose no readable identity — by modelling slot occupancy as tenures, detecting occupancy changes, and asking the user to confirm identity.

**Architecture:** A new `dbb/registry.py` owns packs, tenures, occupancy-change detection, identification and pack totals. `wear.integrate` accrues into the slot's *open tenure* instead of a per-slot counter dict; `state.json` moves to version 2 with a one-way migration from the Plan A shape (`slots` counters → packs `A`/`B` with one open tenure each). `policy.efc_by_slot` reads pack totals through the registry. The CLI gains `pack …` subcommands, the sample log rotates yearly, and the applet shows pending identity questions inline with the packs on the bench and a "swap in next" hint.

**Tech Stack:** Python 3.13 stdlib only; `unittest`; Plasma 6 QML.

**Spec:** `docs/superpowers/specs/2026-09-12-profiles-packs-config-design.md` — §2 (all), §4 (`pack` subcommands, `reset --pack`), §5 (state version 2, `samples-YYYY.csv`), §6.1 (bench packs, "swap in next", pending identity questions inline). §6.2/6.3 remain Plan C. Plan A is merged on `main` at `7f3219c`.

## Global Constraints

- Python stdlib only; no third-party imports anywhere in `dbb/` or `tests/`.
- Tests: `python3 -W error::ResourceWarning -m unittest discover -s tests` must pass with zero warnings at the end of every task (94 tests today; only grows). Tests never touch `/sys`, `/etc`, `/var`; they use `DBB_SYSFS_ROOT`, `DBB_STATE_DIR`, `DBB_CONFIG_DIR`, `DBB_BOOT_ID` and `tests/fakesys.FakeSys`.
- Nothing in `dbb/` calls `sudo`, `os.setuid`, or checks `geteuid()`. No CLI test may run `tick`, `profile set`, `field`, `restore`, `balance --apply`, `config set/apply`, or any `pack` write without the env overrides.
- Pack names match `^[A-Za-z0-9_-]{1,16}$` (spec §2.1). Tenure ids are integers from `state["next_tenure_id"]`, monotonically increasing, never reused.
- Identification is never automatic: the guess is `"same"` or `"unsure"`, never `"different"` (spec §2.3). Accrual never pauses; an unidentified tenure has `pack = None` (spec §2.1).
- Two open tenures may not carry the same pack name (spec §2.3).
- State is version 2 after this plan; a version-1 file is migrated on load and never written back as version 1 (spec §2.6). All state writes stay atomic, `0644`, dir `0755`.
- `polkit` classes: `pack assign|new|same` are control-class; `pack reassign|rename|retire|unretire` and `reset --pack` are configure-class (spec §10.2 table plus the rule that anything rewriting history or the registry is configure-class).
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` (the attribution in force for this session; if it changes, use the current one).
- Never write hostnames, machine serials, or the NVMe serial into any file.

## File Structure

```
dbb/registry.py            packs, tenures, open/close, occupancy-change detection,
                           identification (assign/same/new/reassign), rename/retire,
                           pack totals + bench estimate, rotation hint, v1→v2 migration
dbb/state.py               new_state() v2 keys; load_state() migrates v1; sample_log_path(ts)
dbb/wear.py                integrate() accrues into registry tenures; no per-slot counters
dbb/policy.py              efc_by_slot() via registry.efc_for_slot
dbb/render.py              packs / pending / rotation in JSON and text; per-bat pack + tenure
dbb/cli.py                 pack subcommands; reset --pack; classes
tests/test_registry.py     tenures, detection, guess, assign/conflict/reassign, totals,
                           bench, rotation, migration
tests/test_wear_model.py   simulation reads counters via registry.slot_counters
tests/test_policy.py       efc_by_slot via registry
tests/test_cli.py          pack subcommands, reset --pack, samples-YYYY.csv, migration on load
plasmoid/package/contents/ui/FullRepresentation.qml   pending questions, packs section
plasmoid/package/contents/ui/main.qml                 act() unchanged; new-pack name entry helper
README.md                  pack registry section, CLI reference rows, sample log naming
```

---

### Task 1: Registry core — tenures, migration, and re-wiring accrual

**Files:**
- Create: `dbb/registry.py`, `tests/test_registry.py`
- Modify: `dbb/state.py`, `dbb/wear.py`, `dbb/policy.py`, `dbb/render.py`, `dbb/cli.py` (`cmd_reset`), `tests/test_wear_model.py`, `tests/test_policy.py`, `tests/test_cli.py` (only where `state["slots"]` shape is assumed)

**Interfaces:**
- Consumes: `wear.blank_slot(design_uah)`, `wear.efc(counters)`, `state.add_event`, `state.now_iso`, `sysfs.BATS`.
- Produces (`dbb.registry`):
  - `class RegistryError(ValueError)`
  - `PACK_NAME_RE`, `new_tenure(state, slot, v, ts) -> dict`, `tenure_by_id(state, tid) -> dict|None`, `open_tenure(state, slot) -> dict|None`, `slot_counters = open_tenure`, `close_tenure(state, slot, ts) -> dict|None`, `last_closed_tenure(state, slot) -> dict|None`, `observe(state, slot, v, s, dt) -> (tenure, changed: bool)` (this task: opens on first sight only; Task 2 adds detection), `note_absent(state, slot, ts)`, `migrate_v1(state) -> state`, `efc_for_slot(state, slot) -> float|None` (this task: EFC of the open tenure; Task 3 switches to pack totals).
- `dbb.state.new_state()` v2 keys: `version: 2`, `packs: {}`, `tenures: []`, `slots: {"BAT0": None, "BAT1": None}`, `pending: {}`, `next_tenure_id: 1`, plus every Plan A key. `load_state()` calls `registry.migrate_v1` when `version < 2`.
- `dbb.state.sample_log_path(ts: float) -> Path` → `STATE_DIR / f"samples-{year}.csv"`; `append_log(s)` uses it. `SAMPLE_LOG` constant removed.

- [ ] **Step 1: Write the failing tests** — `tests/test_registry.py`:

```python
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
from dbb import registry, state as st, wear

DESIGN = 4600000


def bat(charge=2300000, full=DESIGN, capacity=50, present=1, status="Discharging"):
    return dict(status=status, capacity=capacity, charge_now_uah=charge, charge_full_uah=full,
                charge_full_design_uah=DESIGN, voltage_now_uv=11400000,
                voltage_min_design_uv=11400000, current_now_ua=0, temp_dc=313, present=present)


def sample(ts, ac=1, **bats):
    return dict(ts=float(ts), boot_id="b", ac_online=ac, bats=bats)


class Tenures(unittest.TestCase):
    def setUp(self):
        self.s = st.new_state()

    def test_new_state_is_v2(self):
        self.assertEqual(self.s["version"], 2)
        self.assertEqual(self.s["slots"], {"BAT0": None, "BAT1": None})
        self.assertEqual(self.s["next_tenure_id"], 1)

    def test_observe_opens_first_tenure(self):
        t, changed = registry.observe(self.s, "BAT0", bat(), sample(0), None)
        self.assertTrue(changed)
        self.assertEqual(t["id"], 1)
        self.assertEqual(t["slot"], "BAT0")
        self.assertIsNone(t["pack"])
        self.assertIsNone(t["end_ts"])
        self.assertIs(registry.open_tenure(self.s, "BAT0"), t)
        self.assertEqual(self.s["next_tenure_id"], 2)

    def test_observe_again_returns_same_tenure(self):
        t1, _ = registry.observe(self.s, "BAT0", bat(), sample(0), None)
        t2, changed = registry.observe(self.s, "BAT0", bat(charge=2200000), sample(120), 120.0)
        self.assertIs(t1, t2)
        self.assertFalse(changed)
        self.assertEqual(t2["last_charge_uah"], 2200000)

    def test_close_records_end_and_clears_slot(self):
        registry.observe(self.s, "BAT1", bat(), sample(0), None)
        t = registry.close_tenure(self.s, "BAT1", 500.0)
        self.assertEqual(t["end_ts"], 500.0)
        self.assertIsNone(self.s["slots"]["BAT1"])
        self.assertIsNone(registry.open_tenure(self.s, "BAT1"))
        self.assertIs(registry.last_closed_tenure(self.s, "BAT1"), t)

    def test_note_absent_closes(self):
        registry.observe(self.s, "BAT1", bat(), sample(0), None)
        registry.note_absent(self.s, "BAT1", 300.0)
        self.assertIsNone(registry.open_tenure(self.s, "BAT1"))
        self.assertEqual(registry.tenure_by_id(self.s, 1)["end_ts"], 300.0)

    def test_ids_never_reused(self):
        registry.observe(self.s, "BAT0", bat(), sample(0), None)
        registry.close_tenure(self.s, "BAT0", 1.0)
        t, _ = registry.observe(self.s, "BAT0", bat(), sample(2), None)
        self.assertEqual(t["id"], 2)


class IntegrateIntoTenures(unittest.TestCase):
    def test_discharge_accrues_into_open_tenure(self):
        s = st.new_state()
        wear.integrate(s, sample(0, ac=0, BAT0=bat(charge=4600000, capacity=100), BAT1=bat(charge=4600000, capacity=100)))
        wear.integrate(s, sample(120, ac=0, BAT0=bat(charge=4600000, capacity=100), BAT1=bat(charge=4370000, capacity=95)))
        t1 = registry.slot_counters(s, "BAT1")
        self.assertEqual(t1["discharge_uah"], 230000)
        self.assertEqual(registry.slot_counters(s, "BAT0")["discharge_uah"], 0)
        self.assertEqual(registry.efc_for_slot(s, "BAT1"), 230000 / DESIGN)

    def test_slot_vanishing_closes_tenure(self):
        s = st.new_state()
        wear.integrate(s, sample(0, BAT0=bat(), BAT1=bat()))
        wear.integrate(s, sample(120, BAT0=bat()))
        self.assertIsNone(registry.open_tenure(s, "BAT1"))
        self.assertEqual(registry.tenure_by_id(s, 2)["end_ts"], 120.0)

    def test_interval_after_change_is_not_accrued(self):
        s = st.new_state()
        wear.integrate(s, sample(0, BAT0=bat(charge=4600000, capacity=100)))
        wear.integrate(s, sample(120, BAT0=bat(charge=4600000, capacity=100), BAT1=bat(charge=2300000)))
        # BAT1 just appeared: its first interval must not accrue anything.
        t = registry.slot_counters(s, "BAT1")
        self.assertEqual(t["discharge_uah"], 0)
        self.assertEqual(t["soc_hours"], 0)


class MigrationV1(unittest.TestCase):
    def v1(self):
        c0 = wear.blank_slot(DESIGN); c0["discharge_uah"] = 1.5 * DESIGN; c0["calendar_score"] = 12.0
        c1 = wear.blank_slot(DESIGN); c1["discharge_uah"] = 2.0 * DESIGN
        return {
            "version": 1, "created": "2026-09-12T00:00:00+00:00",
            "slots": {"BAT0": c0, "BAT1": c1},
            "last": sample(1000, BAT0=bat(charge=4000000, capacity=87), BAT1=bat(charge=3000000, capacity=65)),
            "discharge_first": {"BAT0": 0, "BAT1": 3}, "sessions": 3, "policy": None,
            "ac_run_start_ts": 900.0, "profile_switched_ts": None, "one_off_revert_hours": None,
            "firmware": {}, "events": [], "config_error": None, "config_snapshot": None,
        }

    def test_migrates_slots_to_packs_a_b(self):
        s = registry.migrate_v1(self.v1())
        self.assertEqual(s["version"], 2)
        self.assertEqual(sorted(s["packs"]), ["A", "B"])
        self.assertEqual(s["slots"]["BAT0"], 1)
        self.assertEqual(s["slots"]["BAT1"], 2)
        a = registry.open_tenure(s, "BAT0"); b = registry.open_tenure(s, "BAT1")
        self.assertEqual(a["pack"], "A"); self.assertEqual(b["pack"], "B")
        self.assertEqual(a["discharge_uah"], 1.5 * DESIGN)
        self.assertEqual(a["calendar_score"], 12.0)
        self.assertEqual(b["discharge_uah"], 2.0 * DESIGN)
        self.assertEqual(a["last_charge_uah"], 4000000)
        self.assertEqual(b["last_capacity"], 65)
        self.assertEqual(s["discharge_first"], {"BAT0": 0, "BAT1": 3})
        self.assertEqual(s["sessions"], 3)
        self.assertEqual(s["ac_run_start_ts"], 900.0)
        self.assertEqual(s["next_tenure_id"], 3)
        self.assertEqual(s["events"][-1]["kind"], "migrate")

    def test_idempotent(self):
        s = registry.migrate_v1(self.v1())
        again = registry.migrate_v1(s)
        self.assertIs(again, s)
        self.assertEqual(s["next_tenure_id"], 3)

    def test_v1_with_one_slot(self):
        v = self.v1(); del v["slots"]["BAT1"]
        s = registry.migrate_v1(v)
        self.assertEqual(sorted(s["packs"]), ["A"])
        self.assertIsNone(s["slots"]["BAT1"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure** — `python3 -m unittest tests.test_registry -v` → ImportError `dbb.registry`.

- [ ] **Step 3: Implement `dbb/registry.py` (core part)**

```python
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
        p = state["packs"][t["pack"]]
        soc = t.get("last_capacity")
        p["removed_at_soc"] = soc
        p["removed_ts"] = ts
        add_event(state, "pack", f"{t['pack']} removed from {slot} at {soc}%")
    else:
        add_event(state, "pack", f"unidentified pack removed from {slot}")
    return t


def note_absent(state, slot, ts):
    if open_tenure(state, slot) is not None:
        close_tenure(state, slot, ts)


def observe(state, slot, v, s, dt):
    """Return (tenure, changed) for a present slot. Opens a tenure on first
    sight. Task 2 adds occupancy-change detection here."""
    t = open_tenure(state, slot)
    changed = False
    if t is None:
        t = new_tenure(state, slot, v, s["ts"])
        changed = True
        add_event(state, "pack", f"{slot}: pack seen (new tenure {t['id']})")
    t["last_charge_uah"] = v["charge_now_uah"]
    t["last_charge_full_uah"] = v["charge_full_uah"]
    t["last_capacity"] = v["capacity"]
    return t, changed


def efc_for_slot(state, slot):
    t = open_tenure(state, slot)
    return efc(t) if t else None


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
```

- [ ] **Step 4: `dbb/state.py` — v2 keys, migration on load, yearly sample log**

Replace `new_state()`:

```python
def new_state():
    return {
        "version": 2, "created": now_iso(),
        "packs": {}, "tenures": [], "slots": {b: None for b in BATS}, "pending": {},
        "next_tenure_id": 1, "last": None,
        "discharge_first": {b: 0 for b in BATS}, "sessions": 0, "policy": None,
        "ac_run_start_ts": None, "profile_switched_ts": None,
        "one_off_revert_hours": None, "firmware": {}, "events": [],
        "config_error": None, "config_snapshot": None,
    }
```

In `load_state()`, after parsing and BEFORE backfilling from `new_state()` (backfill would otherwise clobber the v1 `slots` shape):

```python
        if state.get("version", 1) < 2:
            from dbb import registry   # local import: registry imports state
            state = registry.migrate_v1(state)
        base = new_state()
        for k, v in base.items():
            state.setdefault(k, v)
        return state
```

Replace `SAMPLE_LOG` with:

```python
def sample_log_path(ts):
    year = datetime.fromtimestamp(ts, timezone.utc).year
    return STATE_DIR / f"samples-{year}.csv"
```

and in `append_log(s)`: `log = sample_log_path(s["ts"])`, `new = not log.exists()`, open `log`, `_make_readable(log, 0o644)`.

- [ ] **Step 5: `dbb/wear.py` — accrue into tenures**

Replace the body of `integrate()` from the top through the end of the accrual loop with:

```python
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
        t["samples"] += 1
        if changed:
            changed_slots.add(b)

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
        # ... the existing delta / calendar accrual block, unchanged ...
```

Everything after that (drain-order credit, `_track_ac_run`, `state["last"] = s`, return) stays as it is. Delete the old "design capacity changed" warning block and the `import sys` if now unused. Note `t["samples"] += 1` replaces the old per-slot `slot["samples"] += 1`.

- [ ] **Step 6: Callers of the old `state["slots"]` shape**

- `dbb/policy.py`: `efc_by_slot(state)` → `{b: e for b in BATS if (e := registry.efc_for_slot(state, b)) is not None}` with `from dbb import registry` at module top (registry imports `wear` and `state`, not `policy`, so no cycle).
- `dbb/render.py`: every `slots.get(b)` / `slots[both[i]]` becomes `registry.slot_counters(state, b)`; `both = [b for b in BATS if registry.slot_counters(state, b)]`. Import `from dbb import registry`.
- `dbb/cli.py cmd_reset --slot`: becomes `registry.close_tenure(state, args.slot, time.time())` then `state["last"] = None` and the event (a reset now closes the tenure; a fresh one opens on the next sample).
- `tests/test_wear_model.py`: everywhere the simulation reads `st["slots"]["BAT0"]` / `["BAT1"]`, read `registry.slot_counters(st, "BAT0")` instead (import `registry`). Assertions unchanged.
- `tests/test_policy.py`: the `slot(efc)` helper and `self.state["slots"] = {...}` setup become: create tenures via `registry.observe(state, "BAT0", bat, sample, None)` then set `t["discharge_uah"] = efc * DESIGN`. Provide a small helper `seed(state, slot, efc)` at module top doing exactly that. Assertions unchanged.
- `tests/test_cli.py`: grep for `["slots"]` and `samples.csv`; update any direct reads to `registry.slot_counters` and any log-path assertions to `samples-<year>.csv` (Task 4 adds the explicit rotation test).

- [ ] **Step 7: Run everything** — `python3 -W error::ResourceWarning -m unittest discover -s tests -v`; all pass (94 + the new registry tests). Then `DBB_STATE_DIR=/tmp/dbb-b1 DBB_CONFIG_DIR=/tmp/dbb-b1 ./dell-battery-balance status` on the machine prints without error.

- [ ] **Step 8: Commit**

```bash
git add dbb tests
git commit -m "Registry core: tenures, v1->v2 migration, accrual into open tenures, yearly sample log

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Occupancy-change detection and identification

**Files:**
- Modify: `dbb/registry.py`, `tests/test_registry.py`

**Interfaces:**
- Consumes: Task 1's tenure functions.
- Produces (`dbb.registry`): constants `SAME_GUESS_PCT = 3.0`, `DISCONTINUITY_PCT = 10.0`, `DISCONTINUITY_WINDOW_S = 120.0`, `BENCH_WARN_SOC = 70`; `allowed_delta_uah(design_uah, dt) -> float`; `occupancy_change(t, v, dt) -> "charge_full"|"discontinuity"|None`; `guess_identity(prev_t, v) -> "same"|"unsure"`; `observe()` now detects and, on a change, closes the old tenure, opens a new one, and records `state["pending"][slot] = {"tenure_id", "guess", "previous_pack", "delta_pct", "reason", "opened_ts"}`; `close_tenure` adds a `warning` event when an identified pack leaves at SoC > 70; `assign(state, slot, name, new=False) -> tenure`, `same(state, slot) -> tenure`, `reassign(state, tenure_id, name) -> tenure`, `rename_pack(state, old, new)`, `retire_pack(state, name)`, `unretire_pack(state, name)`, `packs_in_slots(state) -> {name: slot}`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_registry.py`:

```python
class Detection(unittest.TestCase):
    def setUp(self):
        self.s = st.new_state()
        registry.observe(self.s, "BAT1", bat(charge=2300000, capacity=50), sample(0), None)

    def test_small_change_is_same_tenure(self):
        t, changed = registry.observe(self.s, "BAT1", bat(charge=2200000, capacity=48), sample(120), 120.0)
        self.assertFalse(changed); self.assertEqual(t["id"], 1)

    def test_big_jump_opens_new_tenure_with_pending(self):
        t, changed = registry.observe(self.s, "BAT1", bat(charge=4500000, capacity=98), sample(120), 120.0)
        self.assertTrue(changed); self.assertEqual(t["id"], 2)
        self.assertEqual(registry.tenure_by_id(self.s, 1)["end_ts"], 120.0)
        p = self.s["pending"]["BAT1"]
        self.assertEqual(p["tenure_id"], 2); self.assertEqual(p["reason"], "discontinuity")
        self.assertEqual(p["guess"], "unsure"); self.assertIsNone(p["previous_pack"])
        self.assertAlmostEqual(p["delta_pct"], (4500000 - 2300000) / DESIGN * 100, places=3)

    def test_allowed_delta_scales_with_time(self):
        self.assertAlmostEqual(registry.allowed_delta_uah(DESIGN, 60), DESIGN * 0.10)
        self.assertAlmostEqual(registry.allowed_delta_uah(DESIGN, 1200), DESIGN * 1.0)
        # a 4-hour suspend on the charger can legitimately move the whole pack
        t, changed = registry.observe(self.s, "BAT1", bat(charge=4600000, capacity=100), sample(4 * 3600), 4 * 3600.0)
        self.assertFalse(changed)

    def test_charge_full_change_trips(self):
        t, changed = registry.observe(self.s, "BAT1", bat(charge=2300000, full=4500000), sample(120), 120.0)
        self.assertTrue(changed); self.assertEqual(self.s["pending"]["BAT1"]["reason"], "charge_full")

    def test_reinsertion_guesses_same_when_close(self):
        registry.assign(self.s, "BAT1", "B", new=True)
        registry.note_absent(self.s, "BAT1", 100.0)
        t, changed = registry.observe(self.s, "BAT1", bat(charge=2250000, capacity=49), sample(200), 100.0)
        self.assertTrue(changed)
        p = self.s["pending"]["BAT1"]
        self.assertEqual(p["reason"], "insert"); self.assertEqual(p["guess"], "same")
        self.assertEqual(p["previous_pack"], "B")

    def test_reinsertion_after_external_charge_is_unsure_not_different(self):
        registry.assign(self.s, "BAT1", "B", new=True)
        registry.note_absent(self.s, "BAT1", 100.0)
        registry.observe(self.s, "BAT1", bat(charge=4600000, capacity=100), sample(7200), 7100.0)
        self.assertEqual(self.s["pending"]["BAT1"]["guess"], "unsure")

    def test_removal_above_70_warns(self):
        registry.assign(self.s, "BAT1", "B", new=True)
        registry.observe(self.s, "BAT1", bat(charge=4500000, capacity=98), sample(120), 120.0)  # trips; fine
        registry.assign(self.s, "BAT1", "B")   # confirm it is still B
        registry.note_absent(self.s, "BAT1", 300.0)
        kinds = [e["kind"] for e in self.s["events"]]
        self.assertIn("warning", kinds)
        self.assertEqual(self.s["packs"]["B"]["removed_at_soc"], 98)
        self.assertEqual(self.s["packs"]["B"]["removed_ts"], 300.0)


class Identification(unittest.TestCase):
    def setUp(self):
        self.s = st.new_state()
        registry.observe(self.s, "BAT0", bat(), sample(0), None)
        registry.observe(self.s, "BAT1", bat(), sample(0), None)

    def test_assign_new_and_known(self):
        t = registry.assign(self.s, "BAT0", "A", new=True)
        self.assertEqual(t["pack"], "A"); self.assertIn("A", self.s["packs"])
        self.assertNotIn("BAT0", self.s["pending"])
        with self.assertRaises(registry.RegistryError):
            registry.assign(self.s, "BAT1", "C")            # unknown, not new
        with self.assertRaises(registry.RegistryError):
            registry.assign(self.s, "BAT1", "A", new=True)  # exists

    def test_conflict_same_pack_in_two_slots(self):
        registry.assign(self.s, "BAT0", "A", new=True)
        with self.assertRaises(registry.RegistryError) as cm:
            registry.assign(self.s, "BAT1", "A")
        self.assertIn("BAT0", str(cm.exception))

    def test_bad_name(self):
        with self.assertRaises(registry.RegistryError):
            registry.assign(self.s, "BAT0", "no spaces here", new=True)

    def test_same_uses_previous_pack(self):
        registry.assign(self.s, "BAT1", "B", new=True)
        registry.note_absent(self.s, "BAT1", 10.0)
        registry.observe(self.s, "BAT1", bat(), sample(20), 10.0)
        t = registry.same(self.s, "BAT1")
        self.assertEqual(t["pack"], "B")
        self.assertIsNone(self.s["packs"]["B"]["removed_ts"])
        with self.assertRaises(registry.RegistryError):
            registry.same(self.s, "BAT0")   # never had a previous pack

    def test_reassign_moves_history(self):
        registry.assign(self.s, "BAT0", "A", new=True)
        registry.assign(self.s, "BAT1", "B", new=True)
        registry.note_absent(self.s, "BAT1", 10.0)
        t = registry.reassign(self.s, 2, "A")           # closed tenure 2 now belongs to A
        self.assertEqual(t["pack"], "A")
        with self.assertRaises(registry.RegistryError):
            registry.reassign(self.s, 1, "B")           # B is not in a slot, fine... but 1 is open in BAT0
        # (tenure 1 is open in BAT0; B is free, so this should succeed:)
        t1 = registry.reassign(self.s, 1, "B")
        self.assertEqual(t1["pack"], "B")
        with self.assertRaises(registry.RegistryError):
            registry.reassign(self.s, 99, "A")

    def test_retire_and_rename(self):
        registry.assign(self.s, "BAT0", "A", new=True)
        with self.assertRaises(registry.RegistryError):
            registry.retire_pack(self.s, "A")           # in a slot
        registry.note_absent(self.s, "BAT0", 5.0)
        registry.retire_pack(self.s, "A")
        self.assertTrue(self.s["packs"]["A"]["retired"])
        registry.observe(self.s, "BAT0", bat(), sample(6), 1.0)
        with self.assertRaises(registry.RegistryError):
            registry.assign(self.s, "BAT0", "A")        # retired
        registry.unretire_pack(self.s, "A")
        registry.rename_pack(self.s, "A", "Alpha")
        self.assertIn("Alpha", self.s["packs"]); self.assertNotIn("A", self.s["packs"])
        self.assertEqual(registry.tenure_by_id(self.s, 1)["pack"], "Alpha")
        self.assertEqual(registry.packs_in_slots(self.s), {})
```

Fix the one intentionally contradictory block in `test_reassign_moves_history`: delete the two lines from `with self.assertRaises(registry.RegistryError):` through `registry.reassign(self.s, 1, "B")` that precede the comment `# (tenure 1 is open …` — the test must read: reassign 2→A succeeds; reassign 1→B succeeds (B is free); reassign 99 raises. Then add one more assertion before the `99` check: `registry.assign(self.s, "BAT1", "Z", new=True)` after re-observing BAT1 (`registry.observe(self.s, "BAT1", bat(), sample(20), 10.0)`), then `with self.assertRaises(registry.RegistryError): registry.reassign(self.s, 1, "Z")` — Z is open in BAT1, so tenure 1 (open in BAT0) cannot take it.

- [ ] **Step 2: Run to verify failure** — `python3 -m unittest tests.test_registry -v` → AttributeError on `assign`/`allowed_delta_uah`, and `test_big_jump_opens_new_tenure_with_pending` fails.

- [ ] **Step 3: Implement** — add to `dbb/registry.py` (constants near the top; functions after `note_absent`):

```python
SAME_GUESS_PCT = 3.0
DISCONTINUITY_PCT = 10.0
DISCONTINUITY_WINDOW_S = 120.0
BENCH_WARN_SOC = 70


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
```

Replace `observe()`:

```python
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
```

In `close_tenure`, after recording `removed_at_soc`, add:

```python
        if soc is not None and soc > BENCH_WARN_SOC:
            add_event(state, "warning", f"{t['pack']} removed at {soc}% - discharge to ~50% before storing")
```

Identification:

```python
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
```

- [ ] **Step 4: Run** — `python3 -W error::ResourceWarning -m unittest discover -s tests -v`; all pass. The Task 1 test `test_observe_again_returns_same_tenure` still passes (its delta is 100000 < 460000).

- [ ] **Step 5: Commit**

```bash
git add dbb/registry.py tests/test_registry.py
git commit -m "Registry: occupancy-change detection, same/unsure guess, identification and conflicts

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Pack totals, bench estimate, rotation hint; policy and render integration

**Files:**
- Modify: `dbb/registry.py`, `dbb/policy.py`, `dbb/render.py`, `tests/test_registry.py`, `tests/test_policy.py`

**Interfaces:**
- Produces (`dbb.registry`): `pack_totals(state, name, now, bench_temp_c) -> {"efc", "discharge_uah", "calendar_score", "bench_calendar", "in_slot": slot|None, "tenures": int, "retired", "removed_at_soc", "bench_hours"}`; `efc_for_slot(state, slot)` now returns the occupying **pack's** total EFC when identified (tenure-only when not); `rotation_hint(state, deadband, now, bench_temp_c) -> {"swap_in": name, "behind_by_efc": float}|None`; `all_packs(state, now, bench_temp_c) -> [ {name, **totals} ] sorted by efc`.
- `render.state_json` gains: per-bat `pack` (name|None), `tenure_id`, `pending` (the pending dict or None); top-level `packs` (from `all_packs`), `pending` (`state["pending"]`), `rotation` (hint or None). `fmt_status` prints a `packs:` table and a `pending:` line when questions exist.

- [ ] **Step 1: Tests** — append to `tests/test_registry.py`:

```python
class Totals(unittest.TestCase):
    def setUp(self):
        self.s = st.new_state()
        registry.observe(self.s, "BAT0", bat(), sample(0), None)
        registry.assign(self.s, "BAT0", "A", new=True)
        registry.open_tenure(self.s, "BAT0")["discharge_uah"] = 1.0 * DESIGN
        registry.note_absent(self.s, "BAT0", 100.0)              # A to the bench at 50%
        registry.observe(self.s, "BAT0", bat(), sample(200), 100.0)
        registry.assign(self.s, "BAT0", "C", new=True)
        registry.open_tenure(self.s, "BAT0")["discharge_uah"] = 0.2 * DESIGN
        registry.observe(self.s, "BAT1", bat(), sample(200), None)
        registry.assign(self.s, "BAT1", "B", new=True)
        registry.open_tenure(self.s, "BAT1")["discharge_uah"] = 1.4 * DESIGN

    def test_totals_sum_tenures(self):
        registry.observe(self.s, "BAT0", bat(), sample(300), 100.0)
        registry.note_absent(self.s, "BAT0", 400.0)
        registry.observe(self.s, "BAT0", bat(), sample(500), 100.0)
        registry.assign(self.s, "BAT0", "A")                       # A back in; second tenure
        registry.open_tenure(self.s, "BAT0")["discharge_uah"] = 0.5 * DESIGN
        tot = registry.pack_totals(self.s, "A", now=600.0, bench_temp_c=25.0)
        self.assertAlmostEqual(tot["efc"], 1.5)
        self.assertEqual(tot["tenures"], 2)
        self.assertEqual(tot["in_slot"], "BAT0")
        self.assertEqual(tot["bench_calendar"], 0.0)

    def test_bench_estimate_accrues_while_out(self):
        tot = registry.pack_totals(self.s, "A", now=100.0 + 10 * 3600, bench_temp_c=25.0)
        self.assertIsNone(tot["in_slot"])
        self.assertAlmostEqual(tot["bench_hours"], 10.0)
        self.assertAlmostEqual(tot["bench_calendar"], 10.0 * wear.calendar_stress(50, 25.0))
        hot = registry.pack_totals(self.s, "A", now=100.0 + 10 * 3600, bench_temp_c=35.0)
        self.assertGreater(hot["bench_calendar"], tot["bench_calendar"])

    def test_efc_for_slot_uses_pack_total(self):
        self.assertAlmostEqual(registry.efc_for_slot(self.s, "BAT1"), 1.4)
        registry.observe(self.s, "BAT1", bat(charge=4500000), sample(300), 100.0)   # trips → unidentified
        self.assertAlmostEqual(registry.efc_for_slot(self.s, "BAT1"), 0.0)          # tenure-only

    def test_rotation_hint_names_least_worn_bench_pack(self):
        hint = registry.rotation_hint(self.s, deadband=0.5, now=200.0, bench_temp_c=25.0)
        # inserted: C 0.2, B 1.4 (max); bench: A 1.0 → 1.4 - 1.0 = 0.4 < 0.5 → no hint
        self.assertIsNone(hint)
        registry.open_tenure(self.s, "BAT1")["discharge_uah"] = 2.0 * DESIGN
        hint = registry.rotation_hint(self.s, deadband=0.5, now=200.0, bench_temp_c=25.0)
        self.assertEqual(hint, {"swap_in": "A", "behind_by_efc": 1.0, "replace": "B"})

    def test_all_packs_sorted_and_flags(self):
        registry.note_absent(self.s, "BAT1", 300.0)
        registry.retire_pack(self.s, "B")
        rows = registry.all_packs(self.s, now=400.0, bench_temp_c=25.0)
        self.assertEqual([r["name"] for r in rows], ["C", "A", "B"])
        self.assertTrue(rows[2]["retired"])
        self.assertEqual(rows[0]["in_slot"], "BAT0")
```

`tests/test_policy.py`: replace the `slot(efc)`/`seed` helper so it creates an identified pack: `seed(state, slot, efc, name)` = `registry.observe(...)`, `registry.assign(state, slot, name, new=True)`, then set `discharge_uah`. Use names `A` for BAT0 and `B` for BAT1. All assertions unchanged.

- [ ] **Step 2: Run to verify failure** — `python3 -m unittest tests.test_registry -v` → AttributeError `pack_totals`.

- [ ] **Step 3: Implement** — append to `dbb/registry.py` (and `from dbb.wear import blank_slot, calendar_stress, efc`):

```python
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
    }


def efc_for_slot(state, slot):
    t = open_tenure(state, slot)
    if t is None:
        return None
    if t["pack"]:
        return pack_totals(state, t["pack"], now=0.0, bench_temp_c=25.0)["efc"]
    return efc(t)


def all_packs(state, now, bench_temp_c):
    rows = [{"name": n, **pack_totals(state, n, now, bench_temp_c)} for n in state["packs"]]
    return sorted(rows, key=lambda r: (r["retired"], r["efc"], r["name"]))


def rotation_hint(state, deadband, now, bench_temp_c):
    """Name the least-worn bench pack when it is more than `deadband` EFC
    behind the most-worn inserted pack."""
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
```

(Remove the Task 1 stub `efc_for_slot`.) `pack_totals` with `now=0.0` inside `efc_for_slot` is fine because EFC ignores the bench estimate.

`dbb/policy.py`: no change beyond Task 1 (it already calls `registry.efc_for_slot`).

`dbb/render.py`: in `state_json`, per-bat entry add
```python
        t = registry.open_tenure(state, b)
        entry["pack"] = t["pack"] if t else None
        entry["tenure_id"] = t["id"] if t else None
        entry["pending"] = state.get("pending", {}).get(b)
```
and top-level, after `events`:
```python
    now = time.time()
    bench_t = cfg["general"]["bench_temp_c"]
    out["packs"] = registry.all_packs(state, now, bench_t)
    out["pending"] = state.get("pending", {})
    out["rotation"] = registry.rotation_hint(state, cfg["general"]["deadband_efc"], now, bench_t)
```
In `fmt_status`, after the divergence lines:
```python
    packs = registry.all_packs(state, time.time(), cfg["general"]["bench_temp_c"])
    if packs:
        lines.append("")
        lines.append(f"{'pack':8} {'EFC':>6} {'cal.':>7} {'where':>8} {'note'}")
        for r in packs:
            where = r["in_slot"] or ("retired" if r["retired"] else "bench")
            note = ""
            if not r["in_slot"] and not r["retired"] and r["removed_at_soc"] is not None:
                note = f"out {r['bench_hours']:.0f}h at {r['removed_at_soc']}%"
            lines.append(f"{r['name']:8} {r['efc']:>6.2f} {r['calendar_score']:>7.1f} {where:>8} {note}")
    hint = registry.rotation_hint(state, cfg["general"]["deadband_efc"], time.time(), cfg["general"]["bench_temp_c"])
    if hint:
        lines.append(f"swap in next: {hint['swap_in']} for {hint['replace']} ({hint['behind_by_efc']:.2f} EFC behind)")
    pend = state.get("pending", {})
    for slot, q in sorted(pend.items()):
        prev = f", was {q['previous_pack']}" if q.get("previous_pack") else ""
        lines.append(f"PENDING {slot}: which pack is this? guess={q['guess']} ({q['reason']}{prev}); "
                     f"answer with: pack same {slot} | pack assign {slot} NAME | pack new {slot} NAME")
```
and in the per-bat table, append the pack name to the `now` column: `now = f"{v['capacity']}% {v['status']}"` becomes `now = f"{(t['pack'] if t and t['pack'] else '?')} {v['capacity']}% {v['status']}"` with `t = registry.open_tenure(state, b)` — widen that column to 20.

- [ ] **Step 4: Run** — full suite; then `DBB_STATE_DIR=/tmp/dbb-b3 DBB_CONFIG_DIR=/tmp/dbb-b3 ./dell-battery-balance status` and `status --json | python3 -m json.tool >/dev/null` on the machine (two `?` packs pending is the expected fresh-state output).

- [ ] **Step 5: Commit**

```bash
git add dbb tests
git commit -m "Registry: pack totals with bench estimate, rotation hint; status shows packs and pending questions

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: `pack` subcommands, `reset --pack`, sample-log rotation test, README

**Files:**
- Modify: `dbb/cli.py`, `tests/test_cli.py`, `README.md`

**Interfaces:**
- Consumes: `registry.assign/same/reassign/rename_pack/retire_pack/unretire_pack/all_packs/RegistryError`, `state.sample_log_path`.
- Produces: subcommands `pack list`, `pack assign <slot> <name>`, `pack new <slot> <name>`, `pack same <slot>`, `pack reassign <tenure-id> <name>`, `pack rename <old> <new>`, `pack retire <name>`, `pack unretire <name>`, `reset --pack <name>` (deletes a pack's tenures and the pack; refuses if in a slot). Classes: `assign|new|same` → `"control"`; `reassign|rename|retire|unretire` → `"pack-admin"`; `reset` stays `"reset"`. `CONFIGURE_CLASS` gains `"pack-admin"`.

- [ ] **Step 1: Tests** — append to `tests/test_cli.py`:

```python
class Packs(CliBase):
    def test_fresh_state_has_two_pending_questions(self):
        self.run_cli("sample")
        j = self.status()
        self.assertEqual(sorted(j["pending"]), ["BAT0", "BAT1"])
        self.assertIsNone(j["bats"]["BAT0"]["pack"])

    def test_new_assign_same_flow(self):
        self.run_cli("sample")
        code, _, err = self.run_cli("pack", "new", "BAT0", "A"); self.assertEqual(code, 0, err)
        code, _, err = self.run_cli("pack", "new", "BAT1", "B"); self.assertEqual(code, 0, err)
        j = self.status()
        self.assertEqual(j["pending"], {})
        self.assertEqual(j["bats"]["BAT0"]["pack"], "A")
        # BAT1 leaves, comes back close to where it was → guess same → confirm
        self.fs.bat("BAT1", present=0)
        self.run_cli("sample")
        self.fs.bat("BAT1", capacity=59, charge_now=2714000, status="Discharging")
        self.run_cli("sample")
        j = self.status()
        self.assertEqual(j["pending"]["BAT1"]["guess"], "same")
        self.assertEqual(j["pending"]["BAT1"]["previous_pack"], "B")
        code, _, err = self.run_cli("pack", "same", "BAT1"); self.assertEqual(code, 0, err)
        self.assertEqual(self.status()["bats"]["BAT1"]["pack"], "B")

    def test_assign_conflict_reported(self):
        self.run_cli("sample")
        self.run_cli("pack", "new", "BAT0", "A")
        code, _, err = self.run_cli("pack", "assign", "BAT1", "A")
        self.assertNotEqual(code, 0); self.assertIn("BAT0", err)

    def test_list_and_rotation(self):
        self.run_cli("sample")
        self.run_cli("pack", "new", "BAT0", "A"); self.run_cli("pack", "new", "BAT1", "B")
        code, out, _ = self.run_cli("pack", "list")
        self.assertEqual(code, 0); self.assertIn("A", out); self.assertIn("BAT0", out)

    def test_admin_commands_need_configure_class(self):
        self.run_cli("sample"); self.run_cli("pack", "new", "BAT0", "A")
        for argv in (("pack", "rename", "A", "Alpha"), ("pack", "retire", "A"),
                     ("pack", "reassign", "1", "A"), ("reset", "--pack", "A")):
            code, _, err = self.run_cli("--polkit-class", "control", *argv)
            self.assertEqual(code, 3, argv); self.assertIn("configure", err)
        code, _, err = self.run_cli("--polkit-class", "control", "pack", "same", "BAT1")
        self.assertNotEqual(code, 3)   # control-class; fails for a different reason (no previous pack)

    def test_reset_pack_refuses_when_inserted_and_deletes_when_benched(self):
        self.run_cli("sample"); self.run_cli("pack", "new", "BAT1", "B")
        code, _, err = self.run_cli("reset", "--pack", "B"); self.assertNotEqual(code, 0); self.assertIn("BAT1", err)
        self.fs.bat("BAT1", present=0); self.run_cli("sample")
        code, _, err = self.run_cli("reset", "--pack", "B"); self.assertEqual(code, 0, err)
        j = self.status()
        self.assertNotIn("B", [p["name"] for p in j["packs"]])

    def test_sample_log_is_per_year(self):
        self.run_cli("sample")
        import datetime, glob
        year = datetime.datetime.now(datetime.timezone.utc).year
        files = glob.glob(os.path.join(os.environ["DBB_STATE_DIR"], "samples-*.csv"))
        self.assertEqual([os.path.basename(f) for f in files], [f"samples-{year}.csv"])

    def test_v1_state_file_is_migrated_on_load(self):
        import json as _json
        from dbb import wear
        d = os.environ["DBB_STATE_DIR"]; os.makedirs(d, exist_ok=True)
        c0 = wear.blank_slot(4600000); c0["discharge_uah"] = 4600000 * 1.5
        v1 = {"version": 1, "slots": {"BAT0": c0}, "last": None, "discharge_first": {"BAT0": 0, "BAT1": 2},
              "sessions": 2, "policy": None}
        with open(os.path.join(d, "state.json"), "w") as fh:
            _json.dump(v1, fh)
        j = self.status()
        self.assertEqual(j["bats"]["BAT0"]["pack"], "A")
        self.assertAlmostEqual(j["bats"]["BAT0"]["efc"], 1.5, places=3)
        self.assertEqual(j["drain_first"], {"BAT0": 0, "BAT1": 2})
        with open(os.path.join(d, "state.json")) as fh:
            self.assertEqual(_json.load(fh)["version"], 1)   # status is read-only; not rewritten yet
        self.run_cli("sample")
        with open(os.path.join(d, "state.json")) as fh:
            self.assertEqual(_json.load(fh)["version"], 2)
```

- [ ] **Step 2: Run to verify failure** — `python3 -m unittest tests.test_cli.Packs -v` → argparse errors (no `pack` command).

- [ ] **Step 3: Implement** in `dbb/cli.py`:

```python
from dbb import registry

CONFIGURE_CLASS = {"config", "profile-create", "profile-edit", "profile-delete", "reset", "pack-admin"}


def _registry_op(fn, *a, **kw):
    state = load_state()
    try:
        fn(state, *a, **kw)
    except registry.RegistryError as e:
        die(f"error: {e}")
    save_state(state)


def cmd_pack_list(args):
    state, cfg, _ = _view()
    rows = registry.all_packs(state, time.time(), cfg["general"]["bench_temp_c"])
    if not rows:
        print("no packs registered yet; run 'pack new SLOT NAME' to name the inserted ones")
    for r in rows:
        where = r["in_slot"] or ("retired" if r["retired"] else "bench")
        extra = f"  out {r['bench_hours']:.0f}h at {r['removed_at_soc']}%" if (where == "bench" and r["removed_at_soc"] is not None) else ""
        print(f"{r['name']:8} EFC {r['efc']:6.2f}  cal {r['calendar_score']:7.1f}  {where:8}{extra}")
    for slot, q in sorted(state.get("pending", {}).items()):
        print(f"PENDING {slot}: guess={q['guess']} ({q['reason']}"
              + (f", was {q['previous_pack']}" if q.get("previous_pack") else "") + ")")


def cmd_pack_assign(args):   _registry_op(registry.assign, args.slot, args.name)
def cmd_pack_new(args):      _registry_op(registry.assign, args.slot, args.name, new=True)
def cmd_pack_same(args):     _registry_op(registry.same, args.slot)
def cmd_pack_reassign(args): _registry_op(registry.reassign, args.tenure_id, args.name)
def cmd_pack_rename(args):   _registry_op(registry.rename_pack, args.old, args.new)
def cmd_pack_retire(args):   _registry_op(registry.retire_pack, args.name)
def cmd_pack_unretire(args): _registry_op(registry.unretire_pack, args.name)
```

`cmd_reset` gains `--pack NAME`:

```python
    elif args.pack:
        if args.pack not in state["packs"]:
            die(f"error: unknown pack {args.pack}")
        where = registry.packs_in_slots(state).get(args.pack)
        if where:
            die(f"error: pack {args.pack} is in {where}; remove it before resetting")
        state["tenures"] = [t for t in state["tenures"] if t["pack"] != args.pack]
        del state["packs"][args.pack]
        add_event(state, "reset", f"pack {args.pack} and its tenures deleted")
```

Parser additions:

```python
    pk = sub.add_parser("pack", help="physical pack registry").add_subparsers(dest="kcmd", required=True)
    pk.add_parser("list").set_defaults(func=cmd_pack_list, cls="control")
    sp = pk.add_parser("assign"); sp.add_argument("slot", choices=BATS); sp.add_argument("name"); sp.set_defaults(func=cmd_pack_assign, cls="control")
    sp = pk.add_parser("new"); sp.add_argument("slot", choices=BATS); sp.add_argument("name"); sp.set_defaults(func=cmd_pack_new, cls="control")
    sp = pk.add_parser("same"); sp.add_argument("slot", choices=BATS); sp.set_defaults(func=cmd_pack_same, cls="control")
    sp = pk.add_parser("reassign"); sp.add_argument("tenure_id", type=int); sp.add_argument("name"); sp.set_defaults(func=cmd_pack_reassign, cls="pack-admin")
    sp = pk.add_parser("rename"); sp.add_argument("old"); sp.add_argument("new"); sp.set_defaults(func=cmd_pack_rename, cls="pack-admin")
    sp = pk.add_parser("retire"); sp.add_argument("name"); sp.set_defaults(func=cmd_pack_retire, cls="pack-admin")
    sp = pk.add_parser("unretire"); sp.add_argument("name"); sp.set_defaults(func=cmd_pack_unretire, cls="pack-admin")
```
and on the `reset` parser: `sp.add_argument("--pack", metavar="NAME")`; the `die("error: give --slot SLOT, --pack NAME or --all")` message updated.

README: add a **Packs and swapping** section (why identity is confirm-on-swap, the three answers, `pack list` output, the bench warning, rotation hint, `reassign` as the undo); add the `pack …` rows and `reset --pack` to the CLI reference; change the state section to `samples-YYYY.csv` (one per year) and state version 2 with automatic one-way migration from 0.2's `state.json`; update the applet section to mention pending questions appear in the popup.

- [ ] **Step 4: Run** — full suite with `-W error::ResourceWarning`; then on the machine `DBB_STATE_DIR=/tmp/dbb-b4 DBB_CONFIG_DIR=/tmp/dbb-b4 ./dell-battery-balance sample && … pack list && … pack new BAT0 A && … pack list` (temp dirs only; `sample`/`pack` write nothing to firmware).

- [ ] **Step 5: Commit**

```bash
git add dbb/cli.py tests/test_cli.py README.md
git commit -m "CLI: pack registry commands, reset --pack, yearly sample log; README packs section

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Applet — pending identity questions inline, bench packs, swap-in-next

**Files:**
- Modify: `plasmoid/package/contents/ui/FullRepresentation.qml`, `plasmoid/package/contents/ui/main.qml`, `plasmoid/package/contents/ui/CompactRepresentation.qml`

**Interfaces:**
- Consumes: JSON from Tasks 3–4: per-bat `pack`, `tenure_id`, `pending {tenure_id, guess, reason, previous_pack, delta_pct}`; top-level `packs [{name, efc, calendar_score, in_slot, retired, removed_at_soc, bench_hours}]`, `pending {slot: …}`, `rotation {swap_in, replace, behind_by_efc}|null`. CLI: `pack same <slot>`, `pack assign <slot> <name>`, `pack new <slot> <name>` (all control-class via `dbb-control`).
- Produces: `main.qml` `readonly property bool hasPending`; `CompactRepresentation.qml` amber dot when `root.hasPending` (red dot for field mode keeps precedence).

- [ ] **Step 1: main.qml** — add after `fieldMode`:

```qml
    readonly property bool hasPending: !!(info && info.pending && Object.keys(info.pending).length > 0)
```

and a helper for names typed in the popup (pack names must match `^[A-Za-z0-9_-]{1,16}$`; the CLI re-validates, this only prevents a doomed pkexec prompt):

```qml
    function validPackName(s) {
        return /^[A-Za-z0-9_-]{1,16}$/.test(s || "");
    }
```

- [ ] **Step 2: CompactRepresentation.qml** — below the existing red-dot `Rectangle`, add an amber one with the same geometry, `visible: root.hasPending && !root.fieldMode`, `color: Kirigami.Theme.neutralTextColor`.

- [ ] **Step 3: FullRepresentation.qml** — inside each pack row's `ColumnLayout` (after the `GridLayout`), add the pending question block:

```qml
                    Kirigami.InlineMessage {
                        Layout.fillWidth: true
                        readonly property var q: row.have ? row.bat.pending : null
                        visible: !!q
                        type: Kirigami.MessageType.Warning
                        text: {
                            if (!q) return "";
                            const why = q.reason === "insert" ? i18n("A pack was inserted") : i18n("The reading jumped");
                            const prev = q.previous_pack ? i18n(" (was %1)", q.previous_pack) : "";
                            const g = q.guess === "same" ? i18n("probably the same pack") : i18n("not sure which pack");
                            return i18n("%1 in %2%3 - %4. Which pack is this?", why, row.modelData, prev, g);
                        }
                        actions: [
                            Kirigami.Action {
                                text: q && q.previous_pack ? i18n("Same (%1)", q.previous_pack) : i18n("Same")
                                icon.name: "dialog-ok"
                                visible: !!(q && q.previous_pack)
                                enabled: !root.acting
                                onTriggered: root.act("pack same " + row.modelData, false)
                            }
                        ]
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: row.have && !!row.bat.pending
                        PlasmaComponents.ComboBox {
                            id: knownPacks
                            Layout.fillWidth: true
                            // known, non-retired packs not currently in another slot
                            model: {
                                if (!root.info || !root.info.packs) return [];
                                return root.info.packs
                                    .filter(p => !p.retired && (!p.in_slot || p.in_slot === row.modelData))
                                    .map(p => p.name);
                            }
                            enabled: !root.acting && count > 0
                        }
                        PlasmaComponents.Button {
                            text: i18n("This one")
                            enabled: !root.acting && knownPacks.count > 0
                            onClicked: root.act("pack assign " + row.modelData + " " + knownPacks.currentText, false)
                        }
                        PlasmaComponents.TextField {
                            id: newName
                            Layout.preferredWidth: Kirigami.Units.gridUnit * 6
                            placeholderText: i18n("new name")
                        }
                        PlasmaComponents.Button {
                            text: i18n("New")
                            enabled: !root.acting && root.validPackName(newName.text)
                            onClicked: { root.act("pack new " + row.modelData + " " + newName.text, false); newName.text = ""; }
                        }
                    }
```

Also in the pack row heading, show the pack name: heading text becomes `row.modelData === "BAT0" ? i18n("BAT0 - primary") : i18n("BAT1 - slice")` followed by a second `PlasmaComponents.Label` with `text: row.have && row.bat.pack ? row.bat.pack : i18n("unidentified")` and `opacity: 0.8`.

After the per-pack `Repeater` and before the `Kirigami.Separator`, add the bench section:

```qml
            PlasmaExtras.Heading {
                level: 5
                visible: root.info && root.info.packs && root.info.packs.some(p => !p.in_slot)
                text: i18n("On the bench")
            }
            Repeater {
                model: root.info && root.info.packs ? root.info.packs.filter(p => !p.in_slot) : []
                delegate: PlasmaComponents.Label {
                    required property var modelData
                    Layout.fillWidth: true
                    font: Kirigami.Theme.smallFont
                    opacity: modelData.retired ? 0.5 : 1.0
                    text: modelData.retired
                        ? i18n("%1 - retired, %2 EFC", modelData.name, modelData.efc.toFixed(2))
                        : (modelData.removed_at_soc !== null && modelData.removed_at_soc !== undefined
                            ? i18n("%1 - %2 EFC, out %3 h at %4%", modelData.name, modelData.efc.toFixed(2), Math.round(modelData.bench_hours), modelData.removed_at_soc)
                            : i18n("%1 - %2 EFC", modelData.name, modelData.efc.toFixed(2)))
                }
            }
            PlasmaComponents.Label {
                Layout.fillWidth: true
                visible: !!(root.info && root.info.rotation)
                wrapMode: Text.WordWrap
                font: Kirigami.Theme.smallFont
                text: root.info && root.info.rotation
                    ? i18n("Swap in next: %1 for %2 (%3 EFC behind)", root.info.rotation.swap_in, root.info.rotation.replace, root.info.rotation.behind_by_efc.toFixed(2))
                    : ""
            }
```

- [ ] **Step 4: Verify** — `kpackagetool6 --type Plasma/Applet --upgrade plasmoid/package && timeout 12 plasmawindowed com.chiefgyk3d.dellbatterybalance > /tmp/pw.log 2>&1; grep -iE 'error|TypeError|ReferenceError|Binding loop|is not a type' /tmp/pw.log` → nothing. (`ComboBox` is in `org.kde.plasma.components`, already imported.) Full unit suite unchanged (no Python touched) — run once as a guard.

- [ ] **Step 5: Commit**

```bash
git add plasmoid
git commit -m "Applet: identity questions inline, bench packs, swap-in-next, amber pending dot

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review notes

- **Spec §2.1** model (packs/tenures/slots, names, accrual never pauses, pack=null): Task 1 + 2. **§2.2** three trip paths: `present` 0→1 and slot absent → `note_absent`/`observe("insert")` (Task 1/2); discontinuity + `charge_full` (Task 2); boot check falls out of the same test because `integrate` compares against the pre-shutdown `last` sample with `dt` scaling the allowance (Task 2 `test_allowed_delta_scales_with_time` covers the long-gap case). **§2.3** guess same/unsure, mandatory confirmation, conflict rejection, `reassign` as repair, `same` shorthand: Task 2 + 4. **§2.4** bench estimate at `removed_at_soc`/`bench_temp_c`, warning > 70%: Task 2 (event) + 3 (estimate). **§2.5** rotation guidance: Task 3. **§2.6** migration A/B with counters, drain-order and sessions preserved, version 2 never downgraded: Task 1 (+ CLI test in Task 4). **§4** pack subcommands and `reset --pack`: Task 4 (`pack unretire` included). **§5** `samples-YYYY.csv`: Task 1 + test in Task 4. **§6.1** bench packs, swap-in-next, pending questions inline: Task 5. `pending` + amber tray dot are the applet-side of §6.3's "pending identity question" notification; the desktop notification itself is Plan C.
- **Placeholder scan:** none of the forbidden patterns. One deliberately flagged self-contradiction in Task 2 Step 1's `test_reassign_moves_history` is resolved in the text immediately below it (the engineer is told exactly which lines to delete and what to add).
- **Type consistency:** `observe(state, slot, v, s, dt) -> (tenure, changed)` is used identically in wear (Task 1), tests (1–3), and registry (2). `pack_totals(state, name, now, bench_temp_c)` and `all_packs`/`rotation_hint` share the `(now, bench_temp_c)` tail everywhere. `efc_for_slot` returns `float|None` in both its Task 1 stub and Task 3 replacement. `state["pending"][slot]` keys (`tenure_id, guess, reason, previous_pack, delta_pct, opened_ts`) match render, CLI output and the QML. Classes `"control"`/`"pack-admin"` match the `CONFIGURE_CLASS` update and the Task 4 test.
- **Plan A parked items touched here:** yearly sample log (issue #1 second bullet) is resolved in Task 1; the `--for` clipping and `must_exist` OSError items are NOT in this plan (they are one-liners for a maintenance commit, tracked in issue #1).

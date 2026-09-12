# Plan A: Profiles, Config, Policy Engine and Scoped Privilege — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hard-coded bands and root-run timer with named, editable profiles in a system-wide TOML config, a policy engine with safe auto-revert, one `tick` timer, and a scoped service account that owns exactly the eight firmware files the tool needs.

**Architecture:** The 754-line monolith is split into a stdlib-only `dbb/` package (sysfs, wear, config, state, policy, apply, render, cli) installed under `/usr/local/lib/dell-battery-balance`, with `/usr/local/bin/dell-battery-balance` as a thin launcher. Config lives in `/etc/dell-battery-balance/config.toml`; state stays in `/var/lib/dell-battery-balance/state.json` with additive fields (no version bump — Plan B does that). Root runs only a 15-line allowlist grant script; everything else runs as `dell-battery-balance`, reached from the applet via `pkexec --user dell-battery-balance` through two wrapper executables that map to two polkit actions.

**Tech Stack:** Python 3.13 stdlib only (`tomllib`, `argparse`, `json`, `unittest`); systemd; udev; polkit; Plasma 6 QML (applet touch-ups only).

**Spec:** `docs/superpowers/specs/2026-09-12-profiles-packs-config-design.md` — Sections 1, 3, 4, 5 (additive fields only), 6.1 (profile switcher only), 7, 8, 10. Sections 2 and 6.2/6.3 are Plans B and C.

## Global Constraints

- Python stdlib only; no third-party imports anywhere in `dbb/` or `tests/`.
- Firmware limits: start 50–95, stop 55–100, start < stop. Validation **rejects**, never clamps, and names the offending key.
- Profile names match `^[a-z0-9_-]{1,32}$`; `daily` must always exist.
- Unknown config keys are rejected.
- Bands are always `[start, stop]` lists of two ints in config; tuples `(start, stop)` in Python.
- All state writes are atomic (`os.replace`) and end `0644`; the state dir ends `0755`.
- Nothing in `dbb/` calls `sudo`, `os.setuid`, or checks `geteuid() == 0`. Privilege is DAC on files, established outside the tool.
- Tests: `python3 -m unittest discover -s tests -v` must pass at the end of every task. Tests never touch `/sys`, `/etc`, or `/var`; they use `DBB_STATE_DIR`, `DBB_CONFIG_DIR`, and a fake sysfs root via `DBB_SYSFS_ROOT`.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` (the attribution in force for this session; if it changes, use the current one).
- Never write hostnames, machine serials, or the NVMe serial into any file.

## File Structure

```
dell-battery-balance                 launcher: sys.path insert + dbb.cli.main()
dbb/__init__.py                      VERSION = "0.2.0"
dbb/sysfs.py                         paths (rooted at DBB_SYSFS_ROOT), read/write helpers,
                                     sample_all(), SYSMAN_ATTRS, write_sysman(),
                                     write_powersupply_band(), read_applied(), read_applied_band()
dbb/wear.py                          calendar_stress(), efc(), integrate(), blank_slot()
dbb/state.py                         load_state(), save_state(), append_log(), add_event(),
                                     STATE_DIR/STATE_FILE, new_state()
dbb/config.py                        DEFAULT_CONFIG, ConfigError, load(), validate(), emit(),
                                     save(), set_dotted(), profile_type()
dbb/policy.py                        Resolution, decide_roles(), revert_due(), resolve_revert_target(),
                                     resolve(), switch_profile()
dbb/apply.py                         apply_bands()
dbb/render.py                        state_json(), fmt_status()
dbb/cli.py                           argparse tree, cmd_* functions, main()
libexec/dell-battery-balance-grant   root-owned allowlist chgrp/chmod (sh)
libexec/dbb-control                  wrapper: exec tool --polkit-class control "$@"
libexec/dbb-configure                wrapper: exec tool --polkit-class configure "$@"
polkit/com.chiefgyk3d.dellbatterybalance.control.policy
polkit/com.chiefgyk3d.dellbatterybalance.configure.policy
udev/90-dell-battery-balance.rules
systemd/dell-battery-balance.service tick, hardened, User=dell-battery-balance, ExecStartPre=+grant
systemd/dell-battery-balance.timer   every 2 min
install.sh / uninstall.sh
config/config.toml.default           shipped default, installed if none exists
tests/test_wear_model.py             converted to unittest
tests/test_config.py
tests/test_policy.py
tests/test_apply.py
tests/fakesys.py                     helper: builds a fake /sys tree in a temp dir
plasmoid/package/contents/ui/*.qml   profile switcher, revert countdown, config error, red-dot rule
```

Old files removed at the end of Task 7: `systemd/dell-battery-balance-sample.service`, `systemd/dell-battery-balance-sample.timer`, the old daily `dell-battery-balance.service`/`.timer` contents replaced, `polkit/com.chiefgyk3d.dellbatterybalance.policy`.

---

### Task 1: Split the monolith into the `dbb` package (behaviour-preserving)

**Files:**
- Create: `dbb/__init__.py`, `dbb/sysfs.py`, `dbb/wear.py`, `dbb/state.py`, `dbb/render.py`, `dbb/cli.py`, `tests/fakesys.py`
- Modify: `dell-battery-balance` (becomes launcher), `tests/test_wear_model.py` (unittest + package imports)

**Interfaces:**
- Produces: `dbb.sysfs.PS`, `dbb.sysfs.SYSMAN`, `dbb.sysfs.BATS`, `dbb.sysfs.SYSMAN_ATTRS`, `dbb.sysfs.sample_all() -> dict`, `dbb.sysfs.read_applied(slot) -> dict`, `dbb.sysfs.write_sysman(attr, value, password=None) -> str|None`, `dbb.sysfs.write_powersupply_band(slot, start, stop) -> str|None`, `dbb.sysfs.apply_band(slot, start, stop, password=None, dry_run=True) -> (msg|None, err|None)`, `dbb.sysfs.clamp_band(start, stop) -> (int,int)`
- `dbb.wear.calendar_stress(soc, temp_c) -> float`, `dbb.wear.efc(slot) -> float`, `dbb.wear.integrate(state, sample) -> dict`, `dbb.wear.blank_slot(design_uah) -> dict`
- `dbb.state.STATE_DIR`, `STATE_FILE`, `load_state() -> dict`, `save_state(state)`, `append_log(sample)`, `new_state() -> dict`
- `dbb.render.state_json(state, sample) -> dict`, `dbb.render.fmt_status(state, sample) -> str`
- `dbb.cli.main()`
- `tests.fakesys.FakeSys(tmpdir)` with `.bat(slot, **attrs)`, `.sysman_attr(name, current, possible=None)`, `.ac(online)`, `.root` (Path)

- [ ] **Step 1: Add a fake-sysfs test helper**

Create `tests/fakesys.py`:

```python
"""Builds a fake /sys tree so tests never touch real hardware."""
import os
from pathlib import Path


class FakeSys:
    def __init__(self, tmpdir):
        self.root = Path(tmpdir)
        (self.root / "class/power_supply/AC").mkdir(parents=True)
        (self.root / "class/firmware-attributes/dell-wmi-sysman/attributes").mkdir(parents=True)
        (self.root / "class/firmware-attributes/dell-wmi-sysman/authentication/Admin").mkdir(parents=True)
        self.ac(1)

    def _w(self, rel, value):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"{value}\n")

    def ac(self, online):
        self._w("class/power_supply/AC/online", online)

    def bat(self, slot, present=1, status="Discharging", capacity=50,
            charge_now=2300000, charge_full=4600000, charge_full_design=4600000,
            voltage_now=11400000, voltage_min_design=11400000, current_now=0,
            temp=313, charge_types=None, start=None, stop=None):
        base = f"class/power_supply/{slot}/"
        for k, v in dict(present=present, status=status, capacity=capacity,
                         charge_now=charge_now, charge_full=charge_full,
                         charge_full_design=charge_full_design,
                         voltage_now=voltage_now, voltage_min_design=voltage_min_design,
                         current_now=current_now, temp=temp).items():
            self._w(base + k, v)
        if charge_types is not None:
            self._w(base + "charge_types", charge_types)
        if start is not None:
            self._w(base + "charge_control_start_threshold", start)
        if stop is not None:
            self._w(base + "charge_control_end_threshold", stop)

    def sysman_attr(self, name, current, possible="Adaptive;Standard;Express;PrimAcUse;Custom;"):
        base = f"class/firmware-attributes/dell-wmi-sysman/attributes/{name}/"
        self._w(base + "current_value", current)
        self._w(base + "possible_values", possible)

    def read(self, rel):
        return (self.root / rel).read_text().strip()
```

- [ ] **Step 2: Create `dbb/__init__.py` and `dbb/sysfs.py`**

`dbb/__init__.py`:

```python
VERSION = "0.2.0"
```

`dbb/sysfs.py` — move `read_str`, `read_int`, `boot_id`, `sample_all`, `clamp_band`, `write_sysman`, `write_powersupply_band`, `apply_band`, `read_applied` and the constants out of the monolith verbatim, with **one change**: every path is rooted at `SYSFS_ROOT`:

```python
import os
from pathlib import Path

SYSFS_ROOT = Path(os.environ.get("DBB_SYSFS_ROOT", "/sys"))
PS = SYSFS_ROOT / "class/power_supply"
SYSMAN = SYSFS_ROOT / "class/firmware-attributes/dell-wmi-sysman"
BATS = ("BAT0", "BAT1")

SYSMAN_ATTRS = {
    "BAT0": ("PrimaryBattChargeCfg", "CustomChargeStart", "CustomChargeStop"),
    "BAT1": ("SliceBattChargeCfg", "SliceBattCustomChargeStart", "SliceBattCustomChargeStop"),
}
STOP_MIN, STOP_MAX = 55, 100
START_MIN, START_MAX = 50, 95
```

`boot_id()` reads `/proc/sys/kernel/random/boot_id` and must honour `DBB_BOOT_ID` env override for tests:

```python
def boot_id():
    forced = os.environ.get("DBB_BOOT_ID")
    if forced:
        return forced
    return read_str(Path("/proc/sys/kernel/random/boot_id")) or "unknown"
```

Add one new function used by later tasks:

```python
def read_applied_band(slot):
    """(start, stop) as ints from the firmware, or None if unreadable."""
    a = read_applied(slot)
    try:
        return int(a["start"]), int(a["stop"])
    except (TypeError, ValueError, KeyError):
        return None
```

- [ ] **Step 3: Create `dbb/wear.py` and `dbb/state.py`**

`dbb/wear.py`: move `calendar_stress`, `efc`, `blank_slot`, `integrate`, `GAP_FLAG_SECONDS` verbatim. `integrate` imports `BATS` from `dbb.sysfs`.

`dbb/state.py`: move `STATE_DIR`, `STATE_FILE`, `SAMPLE_LOG`, `CSV_FIELDS`, `now_iso`, `_make_readable`, `load_state`, `save_state`, `append_log` verbatim, plus:

```python
def new_state():
    return {
        "version": 1, "created": now_iso(), "slots": {}, "last": None,
        "discharge_first": {b: 0 for b in BATS}, "sessions": 0, "policy": None,
    }
```

and make `load_state()` return `new_state()` when the file is absent (it currently inlines the dict).

- [ ] **Step 4: Create `dbb/render.py` and `dbb/cli.py`; turn the script into a launcher**

`dbb/render.py`: move `wh`, `fmt_status`, `state_json` verbatim; they import `efc` from `dbb.wear`, `read_applied` from `dbb.sysfs`, `decide_roles`/`DEFAULT_BANDS`/`DEFAULT_DEADBAND` from `dbb.policy` — **create `dbb/policy.py` now containing only** `DEFAULT_BANDS`, `DEFAULT_DEADBAND`, and `decide_roles` moved verbatim; Task 4 rewrites it.

`dbb/cli.py`: move `require_root`, all `cmd_*`, and `main` verbatim. `require_root` stays for this task only; Task 7 deletes it.

Replace `dell-battery-balance` with:

```python
#!/usr/bin/env python3
# dell-battery-balance launcher. Copyright (C) 2026 ChiefGyk3D. GPL-3.0-or-later.
import os
import sys

_here = os.path.dirname(os.path.realpath(__file__))
for candidate in (_here, "/usr/local/lib/dell-battery-balance"):
    if os.path.isdir(os.path.join(candidate, "dbb")):
        sys.path.insert(0, candidate)
        break

from dbb.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Convert the existing test to unittest against the package**

Rewrite `tests/test_wear_model.py` so it imports `from dbb import wear, policy` and wraps the existing simulation in a `unittest.TestCase` with the same five assertions (draining pack has higher EFC, idle pack higher calendar score, drain-first credits once per unplug, deadband holds neutral, clamp respects limits). Add at the top:

```python
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
```

- [ ] **Step 6: Run tests and the CLI to prove nothing changed**

Run: `python3 -m unittest discover -s tests -v`
Expected: all pass.

Run: `DBB_STATE_DIR=/tmp/dbb-t1 ./dell-battery-balance status` and `./dell-battery-balance status --json | python3 -m json.tool >/dev/null`
Expected: same output shape as before the split; exit 0.

- [ ] **Step 7: Commit**

```bash
git add dell-battery-balance dbb tests
git commit -m "Split the monolith into the dbb package

Behaviour-preserving. Paths root at DBB_SYSFS_ROOT so tests can build a
fake /sys; boot_id honours DBB_BOOT_ID for the same reason.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Config module — defaults, validation, TOML emit, dotted set

**Files:**
- Create: `dbb/config.py`, `config/config.toml.default`, `tests/test_config.py`

**Interfaces:**
- Produces: `dbb.config.CONFIG_DIR`, `CONFIG_FILE`, `ConfigError(ValueError)`, `default_config() -> dict`, `validate(cfg) -> None`, `load(path=CONFIG_FILE) -> dict`, `emit(cfg) -> str`, `save(cfg, path=CONFIG_FILE) -> None`, `set_dotted(cfg, key: str, raw: str) -> None`, `profile_type(profile: dict) -> "fixed"|"balancing"`, `SLOTS = ("BAT0","BAT1")`, `ROLES = ("neutral","protect","work")`

- [ ] **Step 1: Write the failing tests**

`tests/test_config.py`:

```python
import os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
import tomllib
from dbb import config


class DefaultsAndRoundTrip(unittest.TestCase):
    def test_default_validates(self):
        config.validate(config.default_config())

    def test_default_has_four_profiles(self):
        self.assertEqual(sorted(config.default_config()["profiles"]),
                         ["daily", "field", "storage", "travel"])

    def test_emit_parses_back_identically(self):
        cfg = config.default_config()
        text = config.emit(cfg)
        self.assertEqual(tomllib.loads(text), cfg)

    def test_emit_round_trips_pins_and_revert(self):
        cfg = config.default_config()
        cfg["profiles"]["daily"]["pins"] = {"BAT0": {"role": "protect"},
                                            "BAT1": {"start": 55, "stop": 70}}
        config.validate(cfg)
        self.assertEqual(tomllib.loads(config.emit(cfg)), cfg)

    def test_profile_type(self):
        cfg = config.default_config()
        self.assertEqual(config.profile_type(cfg["profiles"]["daily"]), "balancing")
        self.assertEqual(config.profile_type(cfg["profiles"]["field"]), "fixed")


class Validation(unittest.TestCase):
    def setUp(self):
        self.cfg = config.default_config()

    def assertRejects(self, fragment):
        with self.assertRaises(config.ConfigError) as cm:
            config.validate(self.cfg)
        self.assertIn(fragment, str(cm.exception))

    def test_unknown_general_key(self):
        self.cfg["general"]["typo"] = 1
        self.assertRejects("general.typo")

    def test_unknown_profile_key(self):
        self.cfg["profiles"]["daily"]["colour"] = "red"
        self.assertRejects("profiles.daily.colour")

    def test_missing_daily(self):
        del self.cfg["profiles"]["daily"]
        self.cfg["general"]["active_profile"] = "field"
        self.assertRejects("profiles.daily")

    def test_bad_profile_name(self):
        self.cfg["profiles"]["Bad Name"] = self.cfg["profiles"]["daily"]
        self.assertRejects("Bad Name")

    def test_active_must_exist(self):
        self.cfg["general"]["active_profile"] = "nope"
        self.assertRejects("general.active_profile")

    def test_band_start_below_min(self):
        self.cfg["profiles"]["daily"]["bands"]["neutral"] = [40, 80]
        self.assertRejects("profiles.daily.bands.neutral")

    def test_band_stop_above_max(self):
        self.cfg["profiles"]["field"]["bands"]["all"] = [90, 101]
        self.assertRejects("profiles.field.bands.all")

    def test_band_start_not_below_stop(self):
        self.cfg["profiles"]["daily"]["bands"]["work"] = [90, 90]
        self.assertRejects("profiles.daily.bands.work")

    def test_balancing_flag_must_match_bands(self):
        self.cfg["profiles"]["field"]["balancing"] = True
        self.assertRejects("profiles.field.balancing")

    def test_balancing_profile_needs_all_three_roles(self):
        del self.cfg["profiles"]["daily"]["bands"]["work"]
        self.assertRejects("profiles.daily.bands.work")

    def test_revert_needs_a_trigger(self):
        self.cfg["profiles"]["field"]["revert"] = {"to": "previous"}
        self.assertRejects("profiles.field.revert")

    def test_revert_to_must_exist(self):
        self.cfg["profiles"]["field"]["revert"]["to"] = "ghost"
        self.assertRejects("profiles.field.revert.to")

    def test_pin_role_or_band_not_both(self):
        self.cfg["profiles"]["daily"]["pins"] = {"BAT0": {"role": "work", "start": 50, "stop": 60}}
        self.assertRejects("profiles.daily.pins.BAT0")

    def test_pin_unknown_slot(self):
        self.cfg["profiles"]["daily"]["pins"] = {"BAT9": {"role": "work"}}
        self.assertRejects("profiles.daily.pins.BAT9")

    def test_deadband_type(self):
        self.cfg["general"]["deadband_efc"] = "half"
        self.assertRejects("general.deadband_efc")


class LoadSave(unittest.TestCase):
    def test_load_missing_returns_default(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = config.load(os.path.join(d, "config.toml"))
        self.assertEqual(cfg, config.default_config())

    def test_save_writes_bak_and_validates(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.toml")
            cfg = config.default_config()
            config.save(cfg, p)
            cfg["general"]["deadband_efc"] = 0.7
            config.save(cfg, p)
            self.assertTrue(os.path.exists(p + ".bak"))
            self.assertEqual(config.load(p)["general"]["deadband_efc"], 0.7)
            self.assertEqual(tomllib.load(open(p + ".bak", "rb"))["general"]["deadband_efc"], 0.5)
            cfg["general"]["deadband_efc"] = -1
            with self.assertRaises(config.ConfigError):
                config.save(cfg, p)
            self.assertEqual(config.load(p)["general"]["deadband_efc"], 0.7)

    def test_load_bad_toml_raises_configerror(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.toml")
            open(p, "w").write("[general\nbroken")
            with self.assertRaises(config.ConfigError):
                config.load(p)


class SetDotted(unittest.TestCase):
    def test_types_are_coerced(self):
        cfg = config.default_config()
        config.set_dotted(cfg, "general.deadband_efc", "0.25")
        config.set_dotted(cfg, "general.auto_balance", "false")
        config.set_dotted(cfg, "profiles.daily.bands.neutral", "55,85")
        config.set_dotted(cfg, "profiles.field.revert.after_hours", "48")
        config.set_dotted(cfg, "profiles.daily.label", "Desk")
        self.assertEqual(cfg["general"]["deadband_efc"], 0.25)
        self.assertIs(cfg["general"]["auto_balance"], False)
        self.assertEqual(cfg["profiles"]["daily"]["bands"]["neutral"], [55, 85])
        self.assertEqual(cfg["profiles"]["field"]["revert"]["after_hours"], 48)
        self.assertEqual(cfg["profiles"]["daily"]["label"], "Desk")
        config.validate(cfg)

    def test_unknown_path_rejected_by_validate(self):
        cfg = config.default_config()
        config.set_dotted(cfg, "general.bogus", "1")
        with self.assertRaises(config.ConfigError):
            config.validate(cfg)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest tests.test_config -v`
Expected: ImportError / AttributeError on `dbb.config`.

- [ ] **Step 3: Implement `dbb/config.py`**

```python
"""System-wide configuration: /etc/dell-battery-balance/config.toml."""
import copy
import json
import os
import re
import tomllib
from pathlib import Path

from dbb.sysfs import START_MIN, START_MAX, STOP_MIN, STOP_MAX

CONFIG_DIR = Path(os.environ.get("DBB_CONFIG_DIR", "/etc/dell-battery-balance"))
CONFIG_FILE = CONFIG_DIR / "config.toml"

SLOTS = ("BAT0", "BAT1")
ROLES = ("neutral", "protect", "work")
PROFILE_NAME_RE = re.compile(r"^[a-z0-9_-]{1,32}$")

GENERAL_KEYS = {
    "active_profile": str, "previous_profile": str, "deadband_efc": float,
    "auto_balance": bool, "sample_interval_s": int, "bench_temp_c": float,
    "bios_password_file": str, "firmware_write_needs_reboot": bool,
}
PROFILE_KEYS = {"label", "description", "balancing", "bands", "revert", "pins"}
REVERT_KEYS = {"after_hours", "on_ac_hours", "to"}


class ConfigError(ValueError):
    pass


def default_config():
    return copy.deepcopy({
        "general": {
            "active_profile": "daily", "previous_profile": "daily",
            "deadband_efc": 0.5, "auto_balance": True, "sample_interval_s": 120,
            "bench_temp_c": 25.0, "bios_password_file": "",
            "firmware_write_needs_reboot": False,
        },
        "profiles": {
            "daily": {"label": "Daily", "description": "Docked / desk. Wear balancing on.",
                      "balancing": True,
                      "bands": {"neutral": [50, 80], "protect": [50, 60], "work": [80, 90]}},
            "field": {"label": "Field / Conference",
                      "description": "Maximum runtime. Wear protection off.",
                      "balancing": False, "bands": {"all": [90, 100]},
                      "revert": {"after_hours": 72, "on_ac_hours": 12, "to": "previous"}},
            "travel": {"label": "Travel", "description": "Reserve without the 100% float.",
                       "balancing": True,
                       "bands": {"neutral": [70, 90], "protect": [60, 80], "work": [80, 95]}},
            "storage": {"label": "Storage", "description": "Long idle. Least wear.",
                        "balancing": False, "bands": {"all": [50, 55]}},
        },
    })


def profile_type(profile):
    return "fixed" if "all" in profile.get("bands", {}) else "balancing"


# ---------------------------------------------------------------- validation

def _band(path, v):
    if (not isinstance(v, list) or len(v) != 2
            or not all(isinstance(x, int) and not isinstance(x, bool) for x in v)):
        raise ConfigError(f"{path}: must be [start, stop] integers")
    start, stop = v
    if not START_MIN <= start <= START_MAX:
        raise ConfigError(f"{path}: start {start} outside {START_MIN}-{START_MAX}")
    if not STOP_MIN <= stop <= STOP_MAX:
        raise ConfigError(f"{path}: stop {stop} outside {STOP_MIN}-{STOP_MAX}")
    if start >= stop:
        raise ConfigError(f"{path}: start must be below stop")


def _typed(path, v, t):
    if t is float:
        ok = isinstance(v, (int, float)) and not isinstance(v, bool)
    elif t is int:
        ok = isinstance(v, int) and not isinstance(v, bool)
    else:
        ok = isinstance(v, t)
    if not ok:
        raise ConfigError(f"{path}: expected {t.__name__}")


def validate(cfg):
    if set(cfg) - {"general", "profiles"}:
        raise ConfigError(f"unknown top-level keys: {sorted(set(cfg) - {'general', 'profiles'})}")
    g = cfg.get("general", {})
    for k in g:
        if k not in GENERAL_KEYS:
            raise ConfigError(f"general.{k}: unknown key")
    for k, t in GENERAL_KEYS.items():
        if k not in g:
            raise ConfigError(f"general.{k}: missing")
        _typed(f"general.{k}", g[k], t)
    if g["deadband_efc"] < 0:
        raise ConfigError("general.deadband_efc: must be >= 0")

    profiles = cfg.get("profiles", {})
    if "daily" not in profiles:
        raise ConfigError("profiles.daily: required")
    for name, p in profiles.items():
        base = f"profiles.{name}"
        if not PROFILE_NAME_RE.match(name):
            raise ConfigError(f"{base}: bad profile name {name!r}")
        for k in p:
            if k not in PROFILE_KEYS:
                raise ConfigError(f"{base}.{k}: unknown key")
        for k in ("label", "balancing", "bands"):
            if k not in p:
                raise ConfigError(f"{base}.{k}: missing")
        _typed(f"{base}.label", p["label"], str)
        _typed(f"{base}.description", p.get("description", ""), str)
        _typed(f"{base}.balancing", p["balancing"], bool)
        bands = p["bands"]
        if "all" in bands:
            if set(bands) != {"all"}:
                raise ConfigError(f"{base}.bands: 'all' cannot be mixed with role bands")
            if p["balancing"]:
                raise ConfigError(f"{base}.balancing: must be false for a fixed profile")
            _band(f"{base}.bands.all", bands["all"])
        else:
            for r in ROLES:
                if r not in bands:
                    raise ConfigError(f"{base}.bands.{r}: missing")
                _band(f"{base}.bands.{r}", bands[r])
            if set(bands) - set(ROLES):
                raise ConfigError(f"{base}.bands: unknown roles {sorted(set(bands) - set(ROLES))}")
            if not p["balancing"]:
                raise ConfigError(f"{base}.balancing: must be true for a balancing profile")
        rv = p.get("revert")
        if rv is not None:
            for k in rv:
                if k not in REVERT_KEYS:
                    raise ConfigError(f"{base}.revert.{k}: unknown key")
            if "after_hours" not in rv and "on_ac_hours" not in rv:
                raise ConfigError(f"{base}.revert: needs after_hours and/or on_ac_hours")
            for k in ("after_hours", "on_ac_hours"):
                if k in rv:
                    _typed(f"{base}.revert.{k}", rv[k], float)
                    if rv[k] <= 0:
                        raise ConfigError(f"{base}.revert.{k}: must be > 0")
            to = rv.get("to", "previous")
            if to != "previous" and to not in profiles:
                raise ConfigError(f"{base}.revert.to: unknown profile {to!r}")
        for slot, pin in p.get("pins", {}).items():
            pb = f"{base}.pins.{slot}"
            if slot not in SLOTS:
                raise ConfigError(f"{pb}: unknown slot")
            if not isinstance(pin, dict):
                raise ConfigError(f"{pb}: must be a table")
            has_role, has_band = "role" in pin, ("start" in pin or "stop" in pin)
            if has_role == has_band:
                raise ConfigError(f"{pb}: exactly one of role or start/stop")
            if has_role:
                if set(pin) != {"role"} or pin["role"] not in ROLES:
                    raise ConfigError(f"{pb}.role: must be one of {ROLES}")
            else:
                if set(pin) != {"start", "stop"}:
                    raise ConfigError(f"{pb}: needs both start and stop")
                _band(pb, [pin["start"], pin["stop"]])
    for k in ("active_profile", "previous_profile"):
        if g[k] not in profiles:
            raise ConfigError(f"general.{k}: unknown profile {g[k]!r}")


# --------------------------------------------------------------- load / emit

def load(path=CONFIG_FILE):
    path = Path(path)
    if not path.exists():
        return default_config()
    try:
        with path.open("rb") as fh:
            cfg = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"{path}: {e}") from e
    validate(cfg)
    return cfg


def _val(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        s = repr(v)
        return s if ("." in s or "e" in s) else s + ".0"
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, list):
        return "[" + ", ".join(_val(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {_val(x)}" for k, x in v.items()) + " }"
    raise ConfigError(f"cannot emit {type(v).__name__}")


def emit(cfg):
    out = ["[general]"]
    for k in GENERAL_KEYS:
        if k in cfg["general"]:
            out.append(f"{k} = {_val(cfg['general'][k])}")
    for name in sorted(cfg["profiles"]):
        p = cfg["profiles"][name]
        out += ["", f"[profiles.{name}]"]
        for k in ("label", "description", "balancing"):
            if k in p:
                out.append(f"{k} = {_val(p[k])}")
        for k in sorted(p.get("bands", {})):
            out.append(f"bands.{k} = {_val(p['bands'][k])}")
        for k in ("after_hours", "on_ac_hours", "to"):
            if k in p.get("revert", {}):
                out.append(f"revert.{k} = {_val(p['revert'][k])}")
        for slot in sorted(p.get("pins", {})):
            out.append(f"pins.{slot} = {_val(p['pins'][slot])}")
    return "\n".join(out) + "\n"


def save(cfg, path=CONFIG_FILE):
    validate(cfg)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        bak = path.with_name(path.name + ".bak")
        bak.write_bytes(path.read_bytes())
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(emit(cfg))
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o664)
    except OSError:
        pass


def _coerce(raw):
    s = raw.strip()
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    if "," in s:
        return [_coerce(x) for x in s.split(",")]
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return s


def set_dotted(cfg, key, raw):
    parts = key.split(".")
    node = cfg
    for p in parts[:-1]:
        node = node.setdefault(p, {})
        if not isinstance(node, dict):
            raise ConfigError(f"{key}: {p} is not a table")
    node[parts[-1]] = _coerce(raw)
```

Also write `config/config.toml.default` by running `python3 -c "import sys; sys.path.insert(0,'.'); from dbb import config; print(config.emit(config.default_config()), end='')" > config/config.toml.default` and commit the result.

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest tests.test_config -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add dbb/config.py config/config.toml.default tests/test_config.py
git commit -m "Add config module: defaults, validation, TOML emit, dotted set

Validation rejects rather than clamps and names the key. Unknown keys are
errors so typos surface. The emitter covers exactly the schema subset and
round-trips through tomllib.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: State additions — AC run tracking, profile timestamps, events

**Files:**
- Modify: `dbb/state.py`, `dbb/wear.py`
- Modify: `tests/test_wear_model.py` (add AC-run tests)

**Interfaces:**
- Produces: state keys `ac_run_start_ts: float|None`, `profile_switched_ts: float|None`, `one_off_revert_hours: float|None`, `firmware: {slot: {requested:[s,e], observed:[s,e]|None, ts, error}}`, `events: [ {ts, kind, detail} ]`, `config_error: str|None`, `config_snapshot: dict|None`
- `dbb.state.add_event(state, kind: str, detail: str) -> None` (bounded to 500)
- `dbb.wear.integrate` maintains `ac_run_start_ts`

- [ ] **Step 1: Write failing tests** (append to `tests/test_wear_model.py`)

```python
class AcRunTracking(unittest.TestCase):
    def mk(self, ts, ac, c0=4600000, c1=4600000):
        one = lambda c: dict(status="Full", capacity=100, charge_now=c, charge_full_uah=4600000,
                             charge_full_design_uah=4600000, voltage_now_uv=11400000,
                             voltage_min_design_uv=11400000, current_now_ua=0, temp_dc=313,
                             charge_now_uah=c)
        return dict(ts=ts, boot_id="b", ac_online=ac, bats={"BAT0": one(c0), "BAT1": one(c1)})

    def test_run_starts_on_transition_and_survives_ac_bounded_gap(self):
        from dbb import wear, state as st
        s = st.new_state()
        wear.integrate(s, self.mk(0, 0))
        wear.integrate(s, self.mk(120, 1))
        self.assertEqual(s["ac_run_start_ts"], 120)
        wear.integrate(s, self.mk(240, 1))
        self.assertEqual(s["ac_run_start_ts"], 120)
        wear.integrate(s, self.mk(240 + 8 * 3600, 1))   # suspended on the dock
        self.assertEqual(s["ac_run_start_ts"], 120)

    def test_run_resets_on_battery(self):
        from dbb import wear, state as st
        s = st.new_state()
        wear.integrate(s, self.mk(0, 1))
        wear.integrate(s, self.mk(120, 1))
        wear.integrate(s, self.mk(240, 0))
        self.assertIsNone(s["ac_run_start_ts"])

    def test_first_sample_on_ac_starts_run(self):
        from dbb import wear, state as st
        s = st.new_state()
        wear.integrate(s, self.mk(50, 1))
        self.assertEqual(s["ac_run_start_ts"], 50)


class Events(unittest.TestCase):
    def test_bounded(self):
        from dbb import state as st
        s = st.new_state()
        for i in range(600):
            st.add_event(s, "test", str(i))
        self.assertEqual(len(s["events"]), 500)
        self.assertEqual(s["events"][-1]["detail"], "599")
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest tests.test_wear_model -v`
Expected: KeyError `ac_run_start_ts` / AttributeError `add_event`.

- [ ] **Step 3: Implement**

In `dbb/state.py` extend `new_state()`:

```python
def new_state():
    return {
        "version": 1, "created": now_iso(), "slots": {}, "last": None,
        "discharge_first": {b: 0 for b in BATS}, "sessions": 0, "policy": None,
        "ac_run_start_ts": None, "profile_switched_ts": None,
        "one_off_revert_hours": None, "firmware": {}, "events": [],
        "config_error": None, "config_snapshot": None,
    }


def add_event(state, kind, detail):
    state.setdefault("events", []).append({"ts": now_iso(), "kind": kind, "detail": detail})
    del state["events"][:-500]
```

In `load_state()`, after parsing, fill missing keys from `new_state()` so old files gain the new fields:

```python
    base = new_state()
    for k, v in base.items():
        state.setdefault(k, v)
    return state
```

In `dbb/wear.py` `integrate()`, before `state["last"] = s` at the end **and** in the first-sample branch:

```python
def _track_ac_run(state, last, s):
    ac = s["ac_online"]
    if ac != 1:
        state["ac_run_start_ts"] = None
        return
    if last is None or last["ac_online"] != 1 or state.get("ac_run_start_ts") is None:
        # A gap with AC on both sides is still one run: the charger held SoC
        # the whole time, so the pack was floating throughout.
        state["ac_run_start_ts"] = s["ts"]
```

Call `_track_ac_run(state, last, s)` in both places (first-sample branch: `_track_ac_run(state, None, s)`). Note the condition `last["ac_online"] != 1` means an AC→AC gap keeps the existing start, satisfying the "suspended on the dock" test.

Also in `integrate`, the calendar-accrual skip for gaps becomes: accrue across a gap **if** `last["ac_online"] == 1 and s["ac_online"] == 1`, using the mean of the two SoCs:

```python
        if soc is not None and temp_dc is not None:
            hours = dt / 3600.0
            if gap and not (last["ac_online"] == 1 and s["ac_online"] == 1):
                continue
            soc_eff = soc if not gap else (soc + (prev["capacity"] or soc)) / 2.0
            slot["calendar_score"] += hours * calendar_stress(soc_eff, temp_dc / 10.0)
            ...
```

(keep the rest of the accrual block as is, using `soc_eff` for `soc_hours_sum` and the `>= 90` test).

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest discover -s tests -v`
Expected: all pass, including the original simulation.

- [ ] **Step 5: Commit**

```bash
git add dbb/state.py dbb/wear.py tests/test_wear_model.py
git commit -m "Track the AC run, profile timestamps and a bounded event log in state

A gap between two on-AC samples counts as AC and accrues calendar wear:
the charger was holding SoC the whole time.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Policy engine — `resolve()`, revert triggers, pins, single slot

**Files:**
- Rewrite: `dbb/policy.py`
- Create: `tests/test_policy.py`
- Modify: `dbb/render.py` (imports `decide_roles` with new signature)

**Interfaces:**
- Produces:
  - `dbb.policy.Resolution` dataclass: `bands: dict[str, tuple[int,int]|None]`, `profile: str`, `profile_type: str`, `roles: dict[str,str]|None`, `why: str`, `revert: dict|None` (`{"to": name, "reason": "after_hours"|"on_ac_hours"}` when a revert is due)
  - `decide_roles(efc_by_slot: dict[str,float], deadband: float) -> (roles: dict|None, why: str)`
  - `revert_due(profile: dict, state: dict, now: float) -> str|None`
  - `resolve_revert_target(cfg: dict, profile_name: str) -> str`
  - `resolve(cfg, state, sample, now) -> Resolution`
  - `switch_profile(cfg, state, name, now, reason) -> None` (mutates cfg.general.active/previous, state.profile_switched_ts, clears `one_off_revert_hours`, adds event)
  - `bands_for(cfg, profile_name) -> dict` convenience
- `render.state_json` and `fmt_status` keep working (they call `decide_roles` with the new signature via a helper `efc_by_slot(state)` in `policy`).

- [ ] **Step 1: Write failing tests**

`tests/test_policy.py`:

```python
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
from dbb import config, policy, state as st, wear

DESIGN = 4600000


def slot(efc):
    s = wear.blank_slot(DESIGN)
    s["discharge_uah"] = efc * DESIGN
    return s


def sample(ac=1, present=("BAT0", "BAT1")):
    one = dict(status="Full", capacity=80, charge_now_uah=3680000, charge_full_uah=DESIGN,
               charge_full_design_uah=DESIGN, voltage_now_uv=11400000,
               voltage_min_design_uv=11400000, current_now_ua=0, temp_dc=313)
    return dict(ts=1000.0, boot_id="b", ac_online=ac, bats={b: dict(one) for b in present})


class Roles(unittest.TestCase):
    def test_neutral_within_deadband(self):
        roles, _ = policy.decide_roles({"BAT0": 1.0, "BAT1": 1.3}, 0.5)
        self.assertEqual(roles, {"BAT0": "neutral", "BAT1": "neutral"})

    def test_leader_protected(self):
        roles, _ = policy.decide_roles({"BAT0": 1.0, "BAT1": 2.0}, 0.5)
        self.assertEqual(roles, {"BAT1": "protect", "BAT0": "work"})

    def test_single_slot_is_none(self):
        roles, why = policy.decide_roles({"BAT0": 1.0}, 0.5)
        self.assertIsNone(roles)


class Resolve(unittest.TestCase):
    def setUp(self):
        self.cfg = config.default_config()
        self.state = st.new_state()
        self.state["slots"] = {"BAT0": slot(1.0), "BAT1": slot(1.0)}
        self.state["profile_switched_ts"] = 0.0

    def test_daily_neutral(self):
        r = policy.resolve(self.cfg, self.state, sample(), now=100.0)
        self.assertEqual(r.profile, "daily")
        self.assertEqual(r.bands, {"BAT0": (50, 80), "BAT1": (50, 80)})
        self.assertIsNone(r.revert)

    def test_daily_diverged(self):
        self.state["slots"]["BAT1"] = slot(2.0)
        r = policy.resolve(self.cfg, self.state, sample(), now=100.0)
        self.assertEqual(r.bands, {"BAT1": (50, 60), "BAT0": (80, 90)})
        self.assertEqual(r.roles, {"BAT1": "protect", "BAT0": "work"})

    def test_fixed_profile(self):
        self.cfg["general"]["active_profile"] = "storage"
        r = policy.resolve(self.cfg, self.state, sample(), now=100.0)
        self.assertEqual(r.bands, {"BAT0": (50, 55), "BAT1": (50, 55)})
        self.assertEqual(r.profile_type, "fixed")

    def test_single_slot_gets_neutral(self):
        r = policy.resolve(self.cfg, self.state, sample(present=("BAT1",)), now=100.0)
        self.assertEqual(r.bands, {"BAT1": (50, 80)})

    def test_pin_role_overrides(self):
        self.cfg["profiles"]["daily"]["pins"] = {"BAT0": {"role": "protect"}}
        r = policy.resolve(self.cfg, self.state, sample(), now=100.0)
        self.assertEqual(r.bands["BAT0"], (50, 60))
        self.assertEqual(r.bands["BAT1"], (50, 80))

    def test_pin_band_overrides_even_fixed(self):
        self.cfg["general"]["active_profile"] = "field"
        self.cfg["profiles"]["field"]["pins"] = {"BAT1": {"start": 55, "stop": 70}}
        r = policy.resolve(self.cfg, self.state, sample(), now=100.0)
        self.assertEqual(r.bands, {"BAT0": (90, 100), "BAT1": (55, 70)})

    def test_deadband_from_config(self):
        self.cfg["general"]["deadband_efc"] = 2.0
        self.state["slots"]["BAT1"] = slot(2.5)
        r = policy.resolve(self.cfg, self.state, sample(), now=100.0)
        self.assertEqual(r.roles, {"BAT0": "neutral", "BAT1": "neutral"})


class Revert(unittest.TestCase):
    def setUp(self):
        self.cfg = config.default_config()
        self.state = st.new_state()
        self.state["slots"] = {"BAT0": slot(1.0), "BAT1": slot(1.0)}
        policy.switch_profile(self.cfg, self.state, "field", now=0.0, reason="test")

    def test_switch_records(self):
        self.assertEqual(self.cfg["general"]["active_profile"], "field")
        self.assertEqual(self.cfg["general"]["previous_profile"], "daily")
        self.assertEqual(self.state["profile_switched_ts"], 0.0)
        self.assertEqual(self.state["events"][-1]["kind"], "profile")

    def test_not_due_early(self):
        self.assertIsNone(policy.revert_due(self.cfg["profiles"]["field"], self.state, now=3600.0))

    def test_after_hours(self):
        self.assertEqual(policy.revert_due(self.cfg["profiles"]["field"], self.state,
                                           now=72 * 3600.0 + 1), "after_hours")

    def test_on_ac_hours(self):
        self.state["ac_run_start_ts"] = 100.0
        self.assertEqual(policy.revert_due(self.cfg["profiles"]["field"], self.state,
                                           now=100.0 + 12 * 3600.0), "on_ac_hours")

    def test_on_ac_not_when_run_is_none(self):
        self.state["ac_run_start_ts"] = None
        self.assertIsNone(policy.revert_due(self.cfg["profiles"]["field"], self.state,
                                            now=100 * 3600.0 - 1))

    def test_one_off_override_shortens(self):
        self.state["one_off_revert_hours"] = 8
        self.assertEqual(policy.revert_due(self.cfg["profiles"]["field"], self.state,
                                           now=8 * 3600.0 + 1), "after_hours")

    def test_target_previous(self):
        self.assertEqual(policy.resolve_revert_target(self.cfg, "field"), "daily")

    def test_target_falls_back_when_previous_reverts(self):
        self.cfg["general"]["previous_profile"] = "field"
        self.assertEqual(policy.resolve_revert_target(self.cfg, "field"), "daily")

    def test_resolve_reports_revert_and_uses_new_profile(self):
        r = policy.resolve(self.cfg, self.state, sample(), now=72 * 3600.0 + 1)
        self.assertEqual(r.revert, {"to": "daily", "reason": "after_hours"})
        self.assertEqual(r.profile, "daily")
        self.assertEqual(r.bands, {"BAT0": (50, 80), "BAT1": (50, 80)})

    def test_switch_clears_one_off(self):
        self.state["one_off_revert_hours"] = 8
        policy.switch_profile(self.cfg, self.state, "daily", now=5.0, reason="test")
        self.assertIsNone(self.state["one_off_revert_hours"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest tests.test_policy -v`
Expected: AttributeError on `policy.resolve` etc.

- [ ] **Step 3: Rewrite `dbb/policy.py`**

```python
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
```

Update `dbb/render.py`: replace uses of `decide_roles(state, DEFAULT_DEADBAND)` with `policy.decide_roles(policy.efc_by_slot(state), deadband)` where `deadband` comes from a `cfg` argument — change signatures to `state_json(state, sample, cfg)` and `fmt_status(state, sample, cfg)`, and replace `DEFAULT_BANDS[r]` lookups with `cfg["profiles"][cfg["general"]["active_profile"]]["bands"]`. Update `dbb/cli.py` callers to pass `config.load()` (wrapped: on `ConfigError` fall back to `config.default_config()` and print the error to stderr). Delete `DEFAULT_BANDS`/`DEFAULT_DEADBAND`.

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest discover -s tests -v`
Expected: all pass. Then `DBB_STATE_DIR=/tmp/dbb-t4 DBB_CONFIG_DIR=/tmp/dbb-t4 ./dell-battery-balance status` prints without error.

- [ ] **Step 5: Commit**

```bash
git add dbb/policy.py dbb/render.py dbb/cli.py tests/test_policy.py
git commit -m "Policy engine: resolve() with profiles, revert triggers, pins, single slot

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Apply layer — write on change, read back, record mismatch

**Files:**
- Create: `dbb/apply.py`, `tests/test_apply.py`

**Interfaces:**
- Produces: `dbb.apply.apply_bands(bands: dict[str, tuple], state, password=None) -> dict` returning `{"written": [slots], "skipped": [slots], "errors": {slot: msg}, "mismatch": [slots]}`; records `state["firmware"][slot] = {"requested": [s,e], "observed": [s,e]|None, "ts": iso, "error": str|None}`; adds an event on mismatch; returns `mismatch` non-empty so the caller sets `general.firmware_write_needs_reboot`.
- `dbb.apply.read_password(cfg) -> str|None` (reads `general.bios_password_file` if non-empty)

- [ ] **Step 1: Write failing tests**

`tests/test_apply.py`:

```python
import os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))


class ApplyBands(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DBB_SYSFS_ROOT"] = self.tmp.name
        for m in list(sys.modules):
            if m.startswith("dbb"):
                del sys.modules[m]
        from tests.fakesys import FakeSys
        from dbb import apply, state as st, sysfs
        self.apply, self.st, self.sysfs = apply, st, sysfs
        self.fs = FakeSys(self.tmp.name)
        self.fs.bat("BAT0", charge_types="Trickle Fast Standard [Adaptive] Custom", start=50, stop=90)
        self.fs.bat("BAT1")
        for slot in ("BAT0", "BAT1"):
            mode, a, b = sysfs.SYSMAN_ATTRS[slot]
            self.fs.sysman_attr(mode, "Adaptive")
            self.fs.sysman_attr(a, "50", possible=None)
            self.fs.sysman_attr(b, "90", possible=None)
        self.state = st.new_state()

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("DBB_SYSFS_ROOT", None)

    def test_writes_and_reads_back(self):
        r = self.apply.apply_bands({"BAT0": (50, 80), "BAT1": (50, 80)}, self.state)
        self.assertEqual(sorted(r["written"]), ["BAT0", "BAT1"])
        self.assertEqual(r["mismatch"], [])
        self.assertEqual(self.fs.read("class/firmware-attributes/dell-wmi-sysman/attributes/SliceBattChargeCfg/current_value"), "Custom")
        self.assertEqual(self.fs.read("class/firmware-attributes/dell-wmi-sysman/attributes/SliceBattCustomChargeStop/current_value"), "80")
        self.assertEqual(self.state["firmware"]["BAT1"]["observed"], [50, 80])

    def test_skips_when_already_observed(self):
        self.apply.apply_bands({"BAT0": (50, 80)}, self.state)
        r = self.apply.apply_bands({"BAT0": (50, 80)}, self.state)
        self.assertEqual(r["skipped"], ["BAT0"])
        self.assertEqual(r["written"], [])

    def test_mismatch_recorded(self):
        # Make the stop attribute silently refuse: writing leaves the old value.
        p = os.path.join(self.tmp.name, "class/firmware-attributes/dell-wmi-sysman/attributes/SliceBattCustomChargeStop/current_value")
        os.chmod(p, 0o444)
        r = self.apply.apply_bands({"BAT1": (50, 70)}, self.state)
        self.assertIn("BAT1", r["errors"])
        self.assertEqual(self.state["firmware"]["BAT1"]["requested"], [50, 70])
        self.assertNotEqual(self.state["firmware"]["BAT1"]["observed"], [50, 70])
        self.assertIn("BAT1", r["mismatch"])
        self.assertEqual(self.state["events"][-1]["kind"], "firmware")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest tests.test_apply -v`
Expected: ImportError `dbb.apply`.

- [ ] **Step 3: Implement `dbb/apply.py`**

```python
"""Write resolved bands to firmware, only when they differ, and read back."""
from pathlib import Path

from dbb.state import add_event, now_iso
from dbb.sysfs import apply_band, read_applied_band


def read_password(cfg):
    p = cfg["general"].get("bios_password_file") or ""
    if not p:
        return None
    try:
        return Path(p).read_text().strip() or None
    except OSError:
        return None


def apply_bands(bands, state, password=None):
    result = {"written": [], "skipped": [], "errors": {}, "mismatch": []}
    fw = state.setdefault("firmware", {})
    for slot, band in bands.items():
        if band is None:
            continue
        want = [int(band[0]), int(band[1])]
        last = fw.get(slot)
        if last and last.get("observed") == want and not last.get("error"):
            result["skipped"].append(slot)
            continue
        msg, err = apply_band(slot, want[0], want[1], password, dry_run=False)
        observed = read_applied_band(slot)
        rec = {"requested": want, "observed": list(observed) if observed else None,
               "ts": now_iso(), "error": err}
        fw[slot] = rec
        if err:
            result["errors"][slot] = err
        else:
            result["written"].append(slot)
        if rec["observed"] != want:
            result["mismatch"].append(slot)
            add_event(state, "firmware",
                      f"{slot}: requested {want} observed {rec['observed']}"
                      + (f" ({err})" if err else ""))
    return result
```

Note: `apply_band` in `sysfs.py` returns an error when a sysman write raises; the test relies on `chmod 0o444` producing `PermissionError` there. If the test runs as root (it should not), the chmod trick does not work — the plan's Global Constraints forbid running tests as root.

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest discover -s tests -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add dbb/apply.py tests/test_apply.py
git commit -m "Apply layer: write bands only on change, read back, record mismatches

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: `tick`, profile/config CLI, status JSON extensions, polkit-class enforcement

**Files:**
- Rewrite: `dbb/cli.py`
- Modify: `dbb/render.py` (`state_json` gains `profile`, `profiles`, `revert`, `firmware`, `config_error`, `events`)
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything from Tasks 2–5.
- Produces: subcommands `tick`, `sample`, `status [--json]`, `report`, `balance [--apply]`, `profile list|show|set [--for DUR]|create --from|edit k=v…|delete`, `config get [key]|set k=v…|apply PATH|validate PATH`, `field`, `restore`, `reset [--slot S|--all]`; hidden global `--polkit-class {control,configure}`.
- `dbb.cli.load_config_or_snapshot(state) -> cfg` (records `state["config_error"]`, uses `state["config_snapshot"]` on error, saves snapshot on success).
- `dbb.cli.CONFIGURE_CLASS = {"config", "profile-create", "profile-edit", "profile-delete", "reset"}` — the subcommands refused under `--polkit-class control`.
- `dbb.cli.parse_duration("8h"|"3d"|"90m") -> float hours`
- JSON additions (consumed by the applet in Task 8):
  ```
  profile:  {name, label, type, description, previous}
  profiles: [{name, label, type, active}]   sorted by name
  revert:   null | {to, after_hours_left, on_ac_hours_left}   (floats or null)
  firmware: {slot: {requested, observed, ts, error}}
  config_error: null | str
  events:   last 10
  ```

- [ ] **Step 1: Write failing tests**

`tests/test_cli.py`:

```python
import io, json, os, sys, tempfile, unittest
from contextlib import redirect_stdout, redirect_stderr
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))


class CliBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DBB_SYSFS_ROOT"] = os.path.join(self.tmp.name, "sys")
        os.environ["DBB_STATE_DIR"] = os.path.join(self.tmp.name, "state")
        os.environ["DBB_CONFIG_DIR"] = os.path.join(self.tmp.name, "etc")
        os.environ["DBB_BOOT_ID"] = "boot-1"
        for m in list(sys.modules):
            if m.startswith("dbb") or m == "tests.fakesys":
                del sys.modules[m]
        from tests.fakesys import FakeSys
        from dbb import cli, sysfs
        self.cli, self.sysfs = cli, sysfs
        self.fs = FakeSys(os.environ["DBB_SYSFS_ROOT"])
        self.fs.bat("BAT0", charge_types="Trickle Fast Standard [Adaptive] Custom", start=50, stop=90, capacity=100, charge_now=4600000, status="Full")
        self.fs.bat("BAT1", capacity=60, charge_now=2760000, status="Charging")
        for slot in ("BAT0", "BAT1"):
            mode, a, b = sysfs.SYSMAN_ATTRS[slot]
            self.fs.sysman_attr(mode, "Adaptive")
            self.fs.sysman_attr(a, "50", possible=None)
            self.fs.sysman_attr(b, "90", possible=None)

    def tearDown(self):
        self.tmp.cleanup()
        for k in ("DBB_SYSFS_ROOT", "DBB_STATE_DIR", "DBB_CONFIG_DIR", "DBB_BOOT_ID"):
            os.environ.pop(k, None)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with redirect_stdout(out), redirect_stderr(err):
            try:
                self.cli.main(list(argv))
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
        return code, out.getvalue(), err.getvalue()

    def status(self):
        code, out, _ = self.run_cli("status", "--json")
        self.assertEqual(code, 0)
        return json.loads(out)


class Tick(CliBase):
    def test_tick_applies_active_profile(self):
        code, _, err = self.run_cli("tick")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.fs.read("class/firmware-attributes/dell-wmi-sysman/attributes/SliceBattCustomChargeStop/current_value"), "80")
        j = self.status()
        self.assertEqual(j["profile"]["name"], "daily")
        self.assertEqual(j["firmware"]["BAT1"]["observed"], [50, 80])

    def test_tick_respects_auto_balance_off(self):
        self.run_cli("config", "set", "general.auto_balance=false")
        self.run_cli("tick")
        self.assertEqual(self.fs.read("class/firmware-attributes/dell-wmi-sysman/attributes/SliceBattCustomChargeStop/current_value"), "90")

    def test_bad_config_uses_snapshot_and_reports(self):
        self.run_cli("tick")
        p = os.path.join(os.environ["DBB_CONFIG_DIR"], "config.toml")
        open(p, "w").write("[general]\nactive_profile = 'nope'\n")
        code, _, _ = self.run_cli("tick")
        self.assertEqual(code, 0)
        j = self.status()
        self.assertIn("nope", j["config_error"])
        self.assertEqual(j["profile"]["name"], "daily")


class Profiles(CliBase):
    def test_set_applies_immediately_even_with_auto_balance_off(self):
        self.run_cli("config", "set", "general.auto_balance=false")
        code, _, err = self.run_cli("profile", "set", "storage")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.fs.read("class/firmware-attributes/dell-wmi-sysman/attributes/SliceBattCustomChargeStop/current_value"), "55")
        j = self.status()
        self.assertEqual(j["profile"]["name"], "storage")
        self.assertEqual(j["profile"]["previous"], "daily")

    def test_field_then_restore(self):
        self.run_cli("field")
        self.assertEqual(self.status()["profile"]["name"], "field")
        self.assertIsNotNone(self.status()["revert"])
        self.run_cli("restore")
        self.assertEqual(self.status()["profile"]["name"], "daily")

    def test_set_for_duration_sets_one_off(self):
        self.run_cli("profile", "set", "field", "--for", "8h")
        j = self.status()
        self.assertAlmostEqual(j["revert"]["after_hours_left"], 8.0, places=1)

    def test_create_edit_delete(self):
        code, _, err = self.run_cli("profile", "create", "demo", "--from", "daily")
        self.assertEqual(code, 0, err)
        code, _, err = self.run_cli("profile", "edit", "demo", "bands.neutral=60,85", "label=Demo")
        self.assertEqual(code, 0, err)
        code, out, _ = self.run_cli("profile", "show", "demo")
        self.assertIn("60, 85", out)
        code, _, err = self.run_cli("profile", "delete", "demo")
        self.assertEqual(code, 0, err)
        names = [p["name"] for p in self.status()["profiles"]]
        self.assertNotIn("demo", names)

    def test_cannot_delete_daily_or_active(self):
        self.assertNotEqual(self.run_cli("profile", "delete", "daily")[0], 0)
        self.run_cli("profile", "set", "travel")
        self.assertNotEqual(self.run_cli("profile", "delete", "travel")[0], 0)

    def test_invalid_edit_rejected_and_config_unchanged(self):
        code, _, err = self.run_cli("profile", "edit", "daily", "bands.neutral=10,80")
        self.assertNotEqual(code, 0)
        self.assertIn("profiles.daily.bands.neutral", err)
        self.assertEqual(self.status()["profiles"][0]["name"], "daily")


class ConfigCmd(CliBase):
    def test_get_set_roundtrip(self):
        self.run_cli("config", "set", "general.deadband_efc=0.3")
        code, out, _ = self.run_cli("config", "get", "general.deadband_efc")
        self.assertEqual(out.strip(), "0.3")

    def test_apply_validates(self):
        p = os.path.join(self.tmp.name, "new.toml")
        open(p, "w").write("[general]\ndeadband_efc = -3\n")
        code, _, err = self.run_cli("config", "apply", p)
        self.assertNotEqual(code, 0)
        self.assertIn("deadband_efc", err)


class PolkitClass(CliBase):
    def test_control_class_refuses_configure_commands(self):
        code, _, err = self.run_cli("--polkit-class", "control", "config", "set", "general.deadband_efc=0.1")
        self.assertEqual(code, 3)
        self.assertIn("configure", err)

    def test_control_class_allows_profile_set(self):
        code, _, err = self.run_cli("--polkit-class", "control", "profile", "set", "travel")
        self.assertEqual(code, 0, err)


class Duration(unittest.TestCase):
    def test_parse(self):
        from dbb.cli import parse_duration
        self.assertEqual(parse_duration("8h"), 8.0)
        self.assertEqual(parse_duration("3d"), 72.0)
        self.assertEqual(parse_duration("90m"), 1.5)
        with self.assertRaises(ValueError):
            parse_duration("soon")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest tests.test_cli -v`
Expected: failures on `tick`, `profile`, `config`, `--polkit-class`, `parse_duration`.

- [ ] **Step 3: Rewrite `dbb/cli.py`**

```python
"""Command-line surface. Every command reads config, state and a sample the same way."""
import argparse
import json
import sys
import time
from pathlib import Path

from dbb import VERSION, apply as apply_mod, config as cfg_mod, policy, render
from dbb.state import add_event, append_log, load_state, save_state
from dbb.sysfs import BATS, sample_all
from dbb.wear import integrate

CONFIGURE_CLASS = {"config", "profile-create", "profile-edit", "profile-delete", "reset"}


def parse_duration(text):
    units = {"m": 1 / 60.0, "h": 1.0, "d": 24.0}
    t = text.strip().lower()
    if not t or t[-1] not in units:
        raise ValueError(f"bad duration {text!r}; use e.g. 90m, 8h, 3d")
    return float(t[:-1]) * units[t[-1]]


def load_config_or_snapshot(state):
    try:
        cfg = cfg_mod.load()
    except cfg_mod.ConfigError as e:
        state["config_error"] = str(e)
        if state.get("config_snapshot"):
            return state["config_snapshot"]
        return cfg_mod.default_config()
    state["config_error"] = None
    state["config_snapshot"] = cfg
    return cfg


def _save_config(cfg, state, what):
    try:
        cfg_mod.save(cfg)
    except cfg_mod.ConfigError as e:
        sys.exit(f"error: {what}: {e}")
    state["config_snapshot"] = cfg
    state["config_error"] = None


def _apply(cfg, state, sample, res):
    pw = apply_mod.read_password(cfg)
    r = apply_mod.apply_bands(res.bands, state, pw)
    if r["mismatch"] and not cfg["general"]["firmware_write_needs_reboot"]:
        cfg["general"]["firmware_write_needs_reboot"] = True
        _save_config(cfg, state, "recording firmware mismatch")
    state["policy"] = {"ts": time.time(), "profile": res.profile, "roles": res.roles,
                       "mode": res.profile_type}
    return r


def _switch_and_apply(cfg, state, name, reason, one_off_hours=None):
    now = time.time()
    policy.switch_profile(cfg, state, name, now, reason)
    if one_off_hours is not None:
        state["one_off_revert_hours"] = one_off_hours
    _save_config(cfg, state, "switching profile")
    sample = sample_all()
    res = policy.resolve(cfg, state, sample, now)
    r = _apply(cfg, state, sample, res)
    save_state(state)
    print(f"profile -> {name}: " + ", ".join(f"{s}={b[0]}/{b[1]}" for s, b in sorted(res.bands.items())))
    for s, e in r["errors"].items():
        print(f"  {s}: {e}", file=sys.stderr)
    return 0 if not r["errors"] else 2


# ----------------------------------------------------------------- commands

def cmd_tick(args):
    state = load_state()
    sample = sample_all()
    if not sample["bats"]:
        save_state(state)
        return
    integrate(state, sample)
    append_log(sample)
    cfg = load_config_or_snapshot(state)
    now = time.time()
    res = policy.resolve(cfg, state, sample, now)
    if res.revert:
        policy.switch_profile(cfg, state, res.revert["to"], now, f"revert: {res.revert['reason']}")
        _save_config(cfg, state, "auto-revert")
    if cfg["general"]["auto_balance"] or res.revert:
        _apply(cfg, state, sample, res)
    save_state(state)


def cmd_sample(args):
    state = load_state()
    s = sample_all()
    if not s["bats"]:
        sys.exit("error: no batteries present")
    integrate(state, s)
    save_state(state)
    if not args.no_log:
        append_log(s)


def _view():
    state = load_state()
    cfg = load_config_or_snapshot(state)
    return state, cfg, sample_all()


def cmd_status(args):
    state, cfg, s = _view()
    if args.json:
        print(json.dumps(render.state_json(state, s, cfg), indent=2, sort_keys=True))
    else:
        print(render.fmt_status(state, s, cfg))


def cmd_report(args):
    state, cfg, s = _view()
    print(render.fmt_status(state, s, cfg))
    print()
    print("events:")
    for e in state.get("events", [])[-20:]:
        print(f"  {e['ts']}  {e['kind']:9} {e['detail']}")
    print()
    print("firmware read-back:")
    for slot, rec in sorted(state.get("firmware", {}).items()):
        print(f"  {slot}: requested={rec['requested']} observed={rec['observed']} at {rec['ts']}"
              + (f"  ERROR {rec['error']}" if rec.get("error") else ""))


def cmd_balance(args):
    state, cfg, s = _view()
    res = policy.resolve(cfg, state, s, time.time())
    print(res.why)
    for slot, b in sorted(res.bands.items()):
        print(f"  {'[dry-run] ' if not args.apply else ''}{slot} -> {b[0]}/{b[1]}")
    if args.apply:
        r = _apply(cfg, state, s, res)
        save_state(state)
        if r["errors"]:
            sys.exit("error: " + "; ".join(f"{k}: {v}" for k, v in r["errors"].items()))


def cmd_profile_list(args):
    state, cfg, _ = _view()
    active = cfg["general"]["active_profile"]
    for name in sorted(cfg["profiles"]):
        p = cfg["profiles"][name]
        mark = "*" if name == active else " "
        print(f"{mark} {name:12} {cfg_mod.profile_type(p):9} {p['label']}")


def cmd_profile_show(args):
    state, cfg, _ = _view()
    if args.name not in cfg["profiles"]:
        sys.exit(f"error: no profile {args.name!r}")
    one = {"general": cfg["general"], "profiles": {args.name: cfg["profiles"][args.name]}}
    text = cfg_mod.emit(one)
    print(text[text.index(f"[profiles.{args.name}]"):], end="")


def cmd_profile_set(args):
    state, cfg, _ = _view()
    if args.name not in cfg["profiles"]:
        sys.exit(f"error: no profile {args.name!r}")
    hours = parse_duration(args.for_) if args.for_ else None
    sys.exit(_switch_and_apply(cfg, state, args.name, "cli", hours))


def cmd_profile_create(args):
    state, cfg, _ = _view()
    if args.name in cfg["profiles"]:
        sys.exit(f"error: profile {args.name!r} exists")
    if args.from_ not in cfg["profiles"]:
        sys.exit(f"error: no profile {args.from_!r}")
    cfg["profiles"][args.name] = json.loads(json.dumps(cfg["profiles"][args.from_]))
    cfg["profiles"][args.name]["label"] = args.name
    _save_config(cfg, state, "creating profile")
    save_state(state)


def cmd_profile_edit(args):
    state, cfg, _ = _view()
    if args.name not in cfg["profiles"]:
        sys.exit(f"error: no profile {args.name!r}")
    for kv in args.assignments:
        k, _, v = kv.partition("=")
        cfg_mod.set_dotted(cfg, f"profiles.{args.name}.{k}", v)
    _save_config(cfg, state, "editing profile")
    save_state(state)


def cmd_profile_delete(args):
    state, cfg, _ = _view()
    if args.name == "daily":
        sys.exit("error: daily cannot be deleted")
    if args.name == cfg["general"]["active_profile"]:
        sys.exit("error: cannot delete the active profile")
    if args.name not in cfg["profiles"]:
        sys.exit(f"error: no profile {args.name!r}")
    del cfg["profiles"][args.name]
    if cfg["general"]["previous_profile"] == args.name:
        cfg["general"]["previous_profile"] = "daily"
    _save_config(cfg, state, "deleting profile")
    save_state(state)


def cmd_config_get(args):
    state, cfg, _ = _view()
    node = cfg
    if args.key:
        for p in args.key.split("."):
            if not isinstance(node, dict) or p not in node:
                sys.exit(f"error: no key {args.key!r}")
            node = node[p]
    if isinstance(node, dict):
        print(cfg_mod.emit(node) if "general" in node else json.dumps(node, indent=2))
    else:
        print(node if not isinstance(node, bool) else str(node).lower())


def cmd_config_set(args):
    state, cfg, _ = _view()
    for kv in args.assignments:
        k, _, v = kv.partition("=")
        cfg_mod.set_dotted(cfg, k, v)
    _save_config(cfg, state, "config set")
    save_state(state)


def cmd_config_validate(args):
    try:
        cfg_mod.load(args.path)
    except cfg_mod.ConfigError as e:
        sys.exit(f"error: {e}")
    print("ok")


def cmd_config_apply(args):
    state = load_state()
    try:
        cfg = cfg_mod.load(args.path)
    except cfg_mod.ConfigError as e:
        sys.exit(f"error: {e}")
    _save_config(cfg, state, "config apply")
    add_event(state, "config", f"applied from {Path(args.path).name}")
    save_state(state)
    print("ok")


def cmd_field(args):
    state, cfg, _ = _view()
    sys.exit(_switch_and_apply(cfg, state, "field", "cli"))


def cmd_restore(args):
    state, cfg, _ = _view()
    sys.exit(_switch_and_apply(cfg, state, cfg["general"]["previous_profile"], "cli restore"))


def cmd_reset(args):
    state = load_state()
    if args.slot:
        state.get("slots", {}).pop(args.slot, None)
        state["last"] = None
        add_event(state, "reset", args.slot)
    elif args.all:
        from dbb.state import new_state
        state = new_state()
    else:
        sys.exit("error: give --slot SLOT or --all")
    save_state(state)


# --------------------------------------------------------------------- main

def build_parser():
    p = argparse.ArgumentParser(prog="dell-battery-balance",
                                description="Wear tracking and charge-ceiling balancing for a Dell Rugged's packs.")
    p.add_argument("--version", action="version", version=VERSION)
    p.add_argument("--polkit-class", choices=("control", "configure"), help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("tick", help="sample, evaluate reverts, apply if auto_balance").set_defaults(func=cmd_tick, cls="control")
    sp = sub.add_parser("sample", help="one measurement, no policy")
    sp.add_argument("--no-log", action="store_true")
    sp.set_defaults(func=cmd_sample, cls="control")
    sp = sub.add_parser("status", help="wear summary")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_status, cls="control")
    sub.add_parser("report", help="detailed analysis").set_defaults(func=cmd_report, cls="control")
    sp = sub.add_parser("balance", help="resolve and optionally apply")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_balance, cls="control")

    pr = sub.add_parser("profile", help="manage profiles").add_subparsers(dest="pcmd", required=True)
    pr.add_parser("list").set_defaults(func=cmd_profile_list, cls="control")
    sp = pr.add_parser("show"); sp.add_argument("name"); sp.set_defaults(func=cmd_profile_show, cls="control")
    sp = pr.add_parser("set"); sp.add_argument("name"); sp.add_argument("--for", dest="for_", metavar="DURATION")
    sp.set_defaults(func=cmd_profile_set, cls="control")
    sp = pr.add_parser("create"); sp.add_argument("name"); sp.add_argument("--from", dest="from_", required=True)
    sp.set_defaults(func=cmd_profile_create, cls="profile-create")
    sp = pr.add_parser("edit"); sp.add_argument("name"); sp.add_argument("assignments", nargs="+", metavar="key=value")
    sp.set_defaults(func=cmd_profile_edit, cls="profile-edit")
    sp = pr.add_parser("delete"); sp.add_argument("name"); sp.set_defaults(func=cmd_profile_delete, cls="profile-delete")

    cf = sub.add_parser("config", help="general settings").add_subparsers(dest="ccmd", required=True)
    sp = cf.add_parser("get"); sp.add_argument("key", nargs="?"); sp.set_defaults(func=cmd_config_get, cls="control")
    sp = cf.add_parser("set"); sp.add_argument("assignments", nargs="+", metavar="key=value"); sp.set_defaults(func=cmd_config_set, cls="config")
    sp = cf.add_parser("validate"); sp.add_argument("path"); sp.set_defaults(func=cmd_config_validate, cls="control")
    sp = cf.add_parser("apply"); sp.add_argument("path"); sp.set_defaults(func=cmd_config_apply, cls="config")

    sub.add_parser("field", help="alias: profile set field").set_defaults(func=cmd_field, cls="control")
    sub.add_parser("restore", help="alias: profile set <previous>").set_defaults(func=cmd_restore, cls="control")
    sp = sub.add_parser("reset", help="clear counters")
    sp.add_argument("--slot", choices=BATS); sp.add_argument("--all", action="store_true")
    sp.set_defaults(func=cmd_reset, cls="reset")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.polkit_class == "control" and args.cls in CONFIGURE_CLASS:
        print("error: this command needs the configure action (dbb-configure), not control",
              file=sys.stderr)
        sys.exit(3)
    args.func(args)
```

Update `dbb/render.py` `state_json(state, s, cfg)` to add:

```python
    g = cfg["general"]
    name = g["active_profile"]
    prof = cfg["profiles"][name]
    out["profile"] = {"name": name, "label": prof["label"], "type": profile_type(prof),
                      "description": prof.get("description", ""), "previous": g["previous_profile"]}
    out["profiles"] = [{"name": n, "label": p["label"], "type": profile_type(p), "active": n == name}
                       for n, p in sorted(cfg["profiles"].items())]
    now = time.time()
    rv = prof.get("revert")
    if rv:
        switched = state.get("profile_switched_ts")
        after = state.get("one_off_revert_hours") or rv.get("after_hours")
        run = state.get("ac_run_start_ts")
        out["revert"] = {
            "to": policy.resolve_revert_target(cfg, name),
            "after_hours_left": (after - (now - switched) / 3600.0) if (after and switched is not None) else None,
            "on_ac_hours_left": (rv["on_ac_hours"] - (now - run) / 3600.0) if (rv.get("on_ac_hours") and run is not None) else None,
        }
    else:
        out["revert"] = None
    out["firmware"] = state.get("firmware", {})
    out["config_error"] = state.get("config_error")
    out["events"] = state.get("events", [])[-10:]
    out["field_mode"] = profile_type(prof) == "fixed" and prof["bands"]["all"][1] >= 95
```

and `fmt_status` prints `profile: <label> (<name>, <type>)` plus a `revert in …` line when applicable, and `CONFIG ERROR: …` first if set.

- [ ] **Step 4: Run tests and a manual sanity check**

Run: `python3 -m unittest discover -s tests -v`
Expected: all pass.

Run: `DBB_STATE_DIR=/tmp/dbb-t6 DBB_CONFIG_DIR=/tmp/dbb-t6 ./dell-battery-balance profile list` on the real machine.
Expected: four profiles, `daily` starred.

- [ ] **Step 5: Commit**

```bash
git add dbb/cli.py dbb/render.py tests/test_cli.py
git commit -m "tick, profile/config commands, status JSON for the applet, polkit-class gate

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Scoped service account, grant script, wrappers, polkit, udev, units, installer

**Files:**
- Create: `libexec/dell-battery-balance-grant`, `libexec/dbb-control`, `libexec/dbb-configure`, `polkit/com.chiefgyk3d.dellbatterybalance.control.policy`, `polkit/com.chiefgyk3d.dellbatterybalance.configure.policy`, `udev/90-dell-battery-balance.rules`, `uninstall.sh`
- Rewrite: `systemd/dell-battery-balance.service`, `systemd/dell-battery-balance.timer`, `install.sh`
- Delete: `systemd/dell-battery-balance-sample.service`, `systemd/dell-battery-balance-sample.timer`, `polkit/com.chiefgyk3d.dellbatterybalance.policy`
- Modify: `dbb/cli.py` (remove `require_root` if still present), `README.md`

**Interfaces:**
- Produces: system account `dell-battery-balance`; executables `/usr/local/libexec/dbb-control` and `/usr/local/libexec/dbb-configure` (used by the applet in Task 8); polkit action ids `com.chiefgyk3d.dellbatterybalance.control` and `.configure`.

- [ ] **Step 1: Grant script**

`libexec/dell-battery-balance-grant`:

```sh
#!/bin/sh
# The only code in dell-battery-balance that runs as root. Grants the service
# group read/write on exactly the firmware files the tool needs. Idempotent.
set -u
GROUP=dell-battery-balance
SYSMAN=/sys/class/firmware-attributes/dell-wmi-sysman/attributes
PS=/sys/class/power_supply

grant() { # path mode
    [ -e "$1" ] || return 0
    chgrp "$GROUP" "$1" 2>/dev/null && chmod "$2" "$1" 2>/dev/null
}

for a in PrimaryBattChargeCfg CustomChargeStart CustomChargeStop \
         SliceBattChargeCfg SliceBattCustomChargeStart SliceBattCustomChargeStop; do
    grant "$SYSMAN/$a/current_value" 0660
done
grant "$PS/BAT0/charge_control_start_threshold" 0664
grant "$PS/BAT0/charge_control_end_threshold" 0664

# Only when a BIOS admin password is configured for the tool.
if [ -s /etc/dell-battery-balance/config.toml ] \
   && grep -Eq '^bios_password_file *= *"[^"]+"' /etc/dell-battery-balance/config.toml; then
    grant /sys/class/firmware-attributes/dell-wmi-sysman/authentication/Admin/current_password 0620
fi
exit 0
```

- [ ] **Step 2: Wrappers**

`libexec/dbb-control`:

```sh
#!/bin/sh
exec /usr/local/bin/dell-battery-balance --polkit-class control "$@"
```

`libexec/dbb-configure`:

```sh
#!/bin/sh
exec /usr/local/bin/dell-battery-balance --polkit-class configure "$@"
```

- [ ] **Step 3: Polkit actions**

`polkit/com.chiefgyk3d.dellbatterybalance.control.policy`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC
 "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/PolicyKit/1.0/policyconfig.dtd">
<policyconfig>
  <vendor>dell-battery-balance</vendor>
  <vendor_url>https://github.com/ChiefGyk3D/dell-battery-balance</vendor_url>
  <action id="com.chiefgyk3d.dellbatterybalance.control">
    <description>Switch battery profile or apply charge ceilings</description>
    <message>Authentication is required to change the battery profile</message>
    <icon_name>battery</icon_name>
    <defaults>
      <allow_any>auth_admin</allow_any>
      <allow_inactive>auth_admin</allow_inactive>
      <allow_active>auth_self_keep</allow_active>
    </defaults>
    <annotate key="org.freedesktop.policykit.exec.path">/usr/local/libexec/dbb-control</annotate>
    <annotate key="org.freedesktop.policykit.exec.allow_gui">true</annotate>
  </action>
</policyconfig>
```

`polkit/com.chiefgyk3d.dellbatterybalance.configure.policy`: identical except `id="com.chiefgyk3d.dellbatterybalance.configure"`, description `Edit battery balancing configuration`, message `Authentication is required to edit the battery balancing configuration`, `allow_active` = `auth_admin_keep`, exec path `/usr/local/libexec/dbb-configure`.

- [ ] **Step 4: udev rule**

`udev/90-dell-battery-balance.rules`:

```
SUBSYSTEM=="firmware-attributes", KERNEL=="dell-wmi-sysman", ACTION=="add", RUN+="/usr/local/libexec/dell-battery-balance-grant"
SUBSYSTEM=="power_supply", KERNEL=="BAT0", ACTION=="add", RUN+="/usr/local/libexec/dell-battery-balance-grant"
```

- [ ] **Step 5: Units**

`systemd/dell-battery-balance.service`:

```ini
[Unit]
Description=Dell dual-battery wear tracking and charge-ceiling balancing (tick)
Documentation=https://github.com/ChiefGyk3D/dell-battery-balance
ConditionPathExists=/sys/class/power_supply/BAT0

[Service]
Type=oneshot
User=dell-battery-balance
Group=dell-battery-balance
ExecStartPre=+/usr/local/libexec/dell-battery-balance-grant
ExecStart=/usr/local/bin/dell-battery-balance tick
Nice=10
IOSchedulingClass=idle
ProtectSystem=strict
ReadWritePaths=/var/lib/dell-battery-balance /etc/dell-battery-balance
ProtectHome=yes
PrivateTmp=yes
NoNewPrivileges=yes
CapabilityBoundingSet=
RestrictAddressFamilies=AF_UNIX
SystemCallFilter=@system-service
SystemCallArchitectures=native
LockPersonality=yes
MemoryDenyWriteExecute=yes
RestrictRealtime=yes
RestrictNamespaces=yes
# Deliberately NOT ProtectKernelTunables: it would remount /sys read-only.
```

`systemd/dell-battery-balance.timer`:

```ini
[Unit]
Description=Run the dell-battery-balance tick every 2 minutes

[Timer]
OnBootSec=1min
OnUnitActiveSec=2min
AccuracySec=15s

[Install]
WantedBy=timers.target
```

- [ ] **Step 6: install.sh / uninstall.sh**

`install.sh`:

```sh
#!/usr/bin/env bash
# Install dell-battery-balance with a scoped service account. Run as root.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run as root: sudo ./install.sh" >&2; exit 1; }
src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SVC=dell-battery-balance

# Stop anything from the previous layout.
systemctl disable --now dell-battery-balance-sample.timer 2>/dev/null || true
systemctl disable --now dell-battery-balance.timer 2>/dev/null || true
rm -f /etc/systemd/system/dell-battery-balance-sample.{service,timer} \
      /usr/share/polkit-1/actions/com.chiefgyk3d.dellbatterybalance.policy

getent group "$SVC" >/dev/null || groupadd --system "$SVC"
getent passwd "$SVC" >/dev/null || \
    useradd --system --gid "$SVC" --home-dir /var/lib/$SVC --shell /usr/sbin/nologin \
            --comment "dell-battery-balance service" "$SVC"

install -d -m755 /usr/local/lib/$SVC
cp -r "$src/dbb" /usr/local/lib/$SVC/
find /usr/local/lib/$SVC -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
install -Dm755 "$src/dell-battery-balance" /usr/local/bin/dell-battery-balance
install -Dm755 "$src/libexec/dell-battery-balance-grant" /usr/local/libexec/dell-battery-balance-grant
install -Dm755 "$src/libexec/dbb-control"   /usr/local/libexec/dbb-control
install -Dm755 "$src/libexec/dbb-configure" /usr/local/libexec/dbb-configure
install -Dm644 "$src/README.md" /usr/local/share/doc/$SVC/README.md

install -d -m755 -o "$SVC" -g "$SVC" /var/lib/$SVC
chown -R "$SVC:$SVC" /var/lib/$SVC
install -d -m2775 -o root -g "$SVC" /etc/$SVC
[[ -e /etc/$SVC/config.toml ]] || install -m664 -o root -g "$SVC" "$src/config/config.toml.default" /etc/$SVC/config.toml

install -Dm644 "$src/polkit/com.chiefgyk3d.dellbatterybalance.control.policy"   /usr/share/polkit-1/actions/com.chiefgyk3d.dellbatterybalance.control.policy
install -Dm644 "$src/polkit/com.chiefgyk3d.dellbatterybalance.configure.policy" /usr/share/polkit-1/actions/com.chiefgyk3d.dellbatterybalance.configure.policy
install -Dm644 "$src/udev/90-dell-battery-balance.rules" /etc/udev/rules.d/90-dell-battery-balance.rules
install -Dm644 "$src/systemd/dell-battery-balance.service" /etc/systemd/system/dell-battery-balance.service
install -Dm644 "$src/systemd/dell-battery-balance.timer"   /etc/systemd/system/dell-battery-balance.timer

udevadm control --reload
/usr/local/libexec/dell-battery-balance-grant
systemctl daemon-reload
systemctl enable --now dell-battery-balance.timer

if [[ -n "${SUDO_USER:-}" ]] && command -v kpackagetool6 >/dev/null; then
    sudo -u "$SUDO_USER" kpackagetool6 --type Plasma/Applet --upgrade "$src/plasmoid/package" 2>/dev/null \
    || sudo -u "$SUDO_USER" kpackagetool6 --type Plasma/Applet --install "$src/plasmoid/package"
fi

echo
echo "Installed. Service account: $SVC. Timer: dell-battery-balance.timer (tick every 2 min)."
echo "Config: /etc/$SVC/config.toml   State: /var/lib/$SVC"
echo "Verify:  sudo -u $SVC /usr/local/bin/dell-battery-balance status"
```

`uninstall.sh` reverses every line above (disable timer, remove units/rules/policies/libexec/bin/lib, `userdel`/`groupdel`, leaves `/etc` and `/var/lib` unless `--purge`).

- [ ] **Step 7: Delete the old units and policy, remove `require_root`, update README**

`git rm systemd/dell-battery-balance-sample.service systemd/dell-battery-balance-sample.timer polkit/com.chiefgyk3d.dellbatterybalance.policy`. Remove `require_root` from `dbb/cli.py` and any caller. README: replace the Install/Usage/Policy/State sections with the new commands, the profiles table from the spec §1.2, and a "Privilege model" section summarising §10 (account, eight files, grant script, two polkit actions and how to loosen `control` to `yes`).

- [ ] **Step 8: Verify on the machine (requires the user to run with sudo)**

```
sudo ./install.sh
id dell-battery-balance
stat -c '%A %U:%G %n' /sys/class/firmware-attributes/dell-wmi-sysman/attributes/SliceBattCustomChargeStop/current_value
systemctl status dell-battery-balance.timer
sudo -u dell-battery-balance /usr/local/bin/dell-battery-balance status
pkexec --user dell-battery-balance /usr/local/libexec/dbb-control profile set travel
pkexec --user dell-battery-balance /usr/local/libexec/dbb-control config set general.deadband_efc=0.4   # must fail with exit 3
sudo journalctl -u dell-battery-balance.service -n 5
```

Expected: `-rw-rw---- root:dell-battery-balance` on the sysman file; status works as the service user; profile switch succeeds after a password prompt; the config write through the control wrapper is refused; the service log shows tick runs with no permission errors.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "Scoped service account: grant script, wrappers, two polkit actions, hardened tick unit

Root now runs fifteen lines that chgrp eight files. Everything that parses
config or writes firmware runs as dell-battery-balance.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Applet — profile switcher, revert countdown, config error, scoped pkexec

**Files:**
- Modify: `plasmoid/package/contents/ui/main.qml`, `FullRepresentation.qml`, `CompactRepresentation.qml`

**Interfaces:**
- Consumes: JSON from Task 6 (`profile`, `profiles`, `revert`, `config_error`, `field_mode`, `firmware`), wrappers from Task 7.

- [ ] **Step 1: main.qml — route actions through the wrappers**

Replace `act(subcommand)` with:

```qml
    readonly property string controlExe: "/usr/local/libexec/dbb-control"
    readonly property string configureExe: "/usr/local/libexec/dbb-configure"

    function act(subcommand, configure) {
        if (acting) return;
        acting = true;
        const exe = configure ? configureExe : controlExe;
        exec.run("pkexec --user dell-battery-balance " + exe + " " + subcommand);
    }
```

and `fieldMode` becomes `info && info.field_mode === true` (unchanged) — it is now computed server-side from the profile.

- [ ] **Step 2: FullRepresentation.qml — profile switcher and countdown**

Replace the footer's two buttons with a `Flow` of one `PlasmaComponents.Button` per entry in `root.info.profiles`:

```qml
    footer: PlasmaExtras.PlasmoidHeading {
        position: PlasmaComponents.ToolBar.Footer
        contentItem: ColumnLayout {
            spacing: Kirigami.Units.smallSpacing
            PlasmaComponents.Label {
                visible: root.info && root.info.revert
                font: Kirigami.Theme.smallFont
                text: {
                    if (!root.info || !root.info.revert) return "";
                    const r = root.info.revert;
                    const parts = [];
                    if (r.after_hours_left !== null) parts.push(i18n("%1 h", Math.max(0, r.after_hours_left).toFixed(1)));
                    if (r.on_ac_hours_left !== null) parts.push(i18n("%1 h on AC", Math.max(0, r.on_ac_hours_left).toFixed(1)));
                    return i18n("Reverts to %1 in %2", r.to, parts.join(i18n(" or ")));
                }
            }
            Flow {
                Layout.fillWidth: true
                spacing: Kirigami.Units.smallSpacing
                Repeater {
                    model: root.info ? root.info.profiles : []
                    delegate: PlasmaComponents.Button {
                        required property var modelData
                        text: modelData.label
                        checkable: true
                        checked: modelData.active
                        enabled: !root.acting
                        icon.name: modelData.type === "fixed" ? "battery-profile-performance" : "battery-profile-powersave"
                        onClicked: root.act("profile set " + modelData.name, false)
                    }
                }
            }
        }
    }
```

Add above the per-pack rows a `Kirigami.InlineMessage` of type `Error`, visible when `root.info && root.info.config_error`, text `i18n("Config error: %1", root.info.config_error)`. Add to each pack's grid a "Firmware:" row showing `info.firmware[slot].observed` joined with "/" and a "mismatch" suffix when `observed` differs from `requested`.

- [ ] **Step 3: CompactRepresentation.qml — unchanged red-dot rule** (it reads `root.fieldMode`, which is now profile-derived). No edit needed; verify.

- [ ] **Step 4: Verify with plasmawindowed**

Run: `kpackagetool6 --type Plasma/Applet --upgrade plasmoid/package && timeout 10 plasmawindowed com.chiefgyk3d.dellbatterybalance 2>&1 | grep -iE 'error|TypeError|ReferenceError|Binding loop' ; echo "(nothing above = clean)"`
Expected: no lines. Visually: four profile buttons, `Daily` checked, no red dot. Click `Field / Conference`: password prompt, then the button state and red dot update within a refresh.

- [ ] **Step 5: Commit**

```bash
git add plasmoid
git commit -m "Applet: profile switcher, revert countdown, config error, scoped pkexec

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Follow-on plans (not in this document)

- **Plan B — pack registry, tenures, swap detection, migration, bench estimate** (spec §2, §5 version bump, `pack` CLI, applet identity questions).
- **Plan C — applet detail popup, config dialog, notifications** (spec §6.1 remainder, §6.2, §6.3).

## Self-review notes

- Spec §1.2: every rule has a test in Task 2 (`Validation` class), including unknown keys, name regex, required `daily`, band limits, `balancing` agreement, revert triggers, pin exclusivity.
- Spec §3: revert → fixed/balancing → single slot → pins → write-on-change with read-back — Tasks 4, 5, 6. `profile set` applies immediately regardless of `auto_balance` — tested in Task 6 `Profiles.test_set_applies_immediately_even_with_auto_balance_off`.
- Spec §7: invalid config keeps the last-good snapshot and surfaces `config_error` — Task 6 `Tick.test_bad_config_uses_snapshot_and_reports`. Firmware failure recorded per slot — Task 5. pkexec 126/127 treated as cancel — unchanged applet code from the current release.
- Spec §10: account, grant script, `ExecStartPre=+`, udev add rule, hardened unit without `ProtectKernelTunables`, two actions, wrappers enforcing class (Task 6 `PolkitClass` tests + Task 7 step 8 manual check), `bios_password_file` read from a file only.
- One spec deviation, deliberate: §10.2 said the applet passes `--polkit-action`; pkexec chooses the action by executable path, so two wrapper executables are the correct mechanism. `--polkit-class` is the internal flag they set. Spec §10.2 should be updated to say so when Plan A lands.
- Type consistency: `Resolution.bands` values are tuples; `apply_bands` accepts tuples and stores lists in state; `state_json` emits the stored lists. `decide_roles(efc_map, deadband)` signature is used identically in Tasks 4 and 6 and by `render`.

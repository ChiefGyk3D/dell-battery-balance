# Plan C: Applet Detail Popup, Config Dialog and Notifications — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the applet side of the design: the per-pack detail fields the popup still lacks, a Plasma config dialog (Display / Profiles / Packs / General) that edits the system config through the existing configure-class polkit action, and desktop notifications raised once per state event.

**Architecture:** Three small Python additions feed the applet: events gain monotonic ids (so the applet can notify once per event and keep a high-water mark in KConfig), `status --json` gains the detail fields §6.1 lists, and `config get/apply/validate` gain `--json` so the dialog submits a JSON document with the TOML's exact shape and only Python ever emits TOML. On the applet side, `contents/config/main.xml` + `config.qml` add the four pages; a shared `ConfigBackend.qml` runs the CLI and the pkexec wrappers and stages the candidate config through a `mktemp` file in `/tmp` (the spec's `$XDG_RUNTIME_DIR` path is mode 700 and unreadable by the service account — see "Deviations from the spec" below). Notifications come from `org.kde.notification` with a notifyrc that `install.sh` places under `/usr/share/knotifications6/`.

**Tech Stack:** Python 3.13 stdlib only; `unittest`; Plasma 6.3 QML (`org.kde.kcmutils`, `org.kde.notification`, `org.kde.plasma.plasma5support`); PyQt6 (system package, already installed) for a *developer-only* headless page loader under `plasmoid/tools/` — never imported by `dbb/` or `tests/`.

**Spec:** `docs/superpowers/specs/2026-09-12-profiles-packs-config-design.md` — §1.3 (applet editing surface), §4 (`config` subcommands, "one JSON model"), §5 (events), §6.1 (remaining popup fields, tray text), §6.2 (config dialog), §6.3 (notifications), §7 (pkexec dismissed = cancel), §8 (applet testing). Plans A and B are merged on `main` at `afc67eb`; installed version on the laptop is 0.2.0.

## Deviations from the spec (decided here, documented in Task 9)

1. **Staging path.** §1.3 says the applet writes the candidate to `$XDG_RUNTIME_DIR/dell-battery-balance/config-<pid>.toml`. That directory is mode `700` and owned by the desktop user (measured: `stat -c %a $XDG_RUNTIME_DIR` → `700`), so the `dell-battery-balance` service account that `pkexec --user` drops to cannot read anything under it. The candidate instead goes through `mktemp /tmp/dbb-config-XXXXXX.json`, `chmod 644`, applied, then removed — all in one shell command so the file lives for seconds. It carries no secrets: the config holds bands and the *path* to a BIOS password file, never a password. `/tmp` is sticky, so another local user cannot replace the file between stage and apply.
2. **JSON, not TOML, from the applet.** §1.3 says "a complete TOML document". Writing a second TOML emitter in JavaScript that must agree byte-for-byte with `config.emit` is a bug farm; §4's own rule is "one JSON model". The dialog submits `JSON.stringify(cfg)` and the CLI gains `config apply --json` / `config validate --json`, which run the same `validate()` and then `save()` emits TOML as today. `config get --json` supplies the working copy.
3. **"Event ids seen are kept in KConfig"** (§6.3) is implemented as a single high-water mark (`lastNotifiedEventId`), which is equivalent because ids are monotonic and bounded in size.

## Global Constraints

- Python stdlib only; no third-party imports anywhere in `dbb/` or `tests/`. The PyQt6 loader lives in `plasmoid/tools/` and is not part of the unit suite.
- Tests: `python3 -W error::ResourceWarning -m unittest discover -s tests` must pass with zero warnings at the end of every task (147 tests today; only grows). Tests never touch `/sys`, `/etc`, `/var`; they use `DBB_SYSFS_ROOT`, `DBB_STATE_DIR`, `DBB_CONFIG_DIR`, `DBB_BOOT_ID` and `tests/fakesys.FakeSys`.
- Nothing in `dbb/` calls `sudo`, `os.setuid`, or checks `geteuid()`. No CLI test may run `tick`, `profile set`, `field`, `restore`, `balance --apply`, `config set/apply`, or any `pack` write without the env overrides (the `CliBase` harness sets them).
- The applet never writes `/etc` itself; every privileged action goes through `pkexec --user dell-battery-balance /usr/local/libexec/dbb-control|dbb-configure` (spec §10). `config apply` is configure-class; `pack assign|new|same` are control-class; `pack rename|retire|unretire` are configure-class.
- pkexec exit 126/127 is a cancel, never an error banner (spec §7).
- Profile names match `^[a-z0-9_-]{1,32}$`; pack names match `^[A-Za-z0-9_-]{1,16}$`; bands are `[start, stop]` with start 50–95, stop 55–100, start < stop (spec §1.2, §2.1). The dialog limits inputs to these ranges; the CLI re-validates regardless.
- Every QML file starts with the GPL-3.0-or-later SPDX header the existing files use.
- QML verification per task: `kpackagetool6 --type Plasma/Applet --upgrade plasmoid/package && timeout 12 plasmawindowed com.chiefgyk3d.dellbatterybalance > /tmp/pw.log 2>&1; grep -iE 'error|TypeError|ReferenceError|Binding loop|is not a type|non-existent' /tmp/pw.log` → nothing. Config pages additionally load through `python3 plasmoid/tools/load-page.py <page.qml>` (Task 3) → `ok`. (`kf.kirigami.platform: Failed to find a Kirigami platform plugin for style "Fusion"` from the loader is noise, not an error.)
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Work on a branch `plan-c-applet` in a worktree; do not push and do not merge to `main` — that is the user's call.
- Never write hostnames, machine serials, or the user's callsign into any file.

## File Structure

```
dbb/state.py               add_event() assigns ids from state["next_event_id"]; load_state() numbers old events
dbb/render.py              state_json(): version, per-bat power_w / voltage_v / health_pct / in_slot_hours
dbb/config.py              load_json(path) — JSON candidate with the TOML's shape, same validate()
dbb/cli.py                 config get --json; config apply/validate --json
dbb/__init__.py            VERSION = "0.3.0" (Task 9)
tests/test_state.py        EventIds
tests/test_cli.py          StatusDetail, ConfigCmd additions
plasmoid/package/contents/config/main.xml         trayText, pollInterval, expandDetails, notify, lastNotifiedEventId
plasmoid/package/contents/config/config.qml       four ConfigCategory entries
plasmoid/package/contents/ui/configDisplay.qml    KConfig page (cfg_ properties only)
plasmoid/package/contents/ui/ConfigBackend.qml    shared: load / loadStatus / act / apply via mktemp+pkexec
plasmoid/package/contents/ui/configGeneral.qml    deadband, auto-balance, bench temperature
plasmoid/package/contents/ui/configProfiles.qml   profile picker, add/delete, editor (label, type, bands, revert, pins)
plasmoid/package/contents/ui/configPacks.qml      pack list with rename/retire/unretire; pending questions
plasmoid/package/contents/ui/main.qml             poll interval from config; ageText(); notifications
plasmoid/package/contents/ui/CompactRepresentation.qml   optional text beside the icon
plasmoid/package/contents/ui/FullRepresentation.qml      expandable details, ceiling match + age, recent events
plasmoid/package/metadata.json                    Version 1.1
plasmoid/notifyrc/dell_battery_balance.notifyrc   four events
plasmoid/tools/load-page.py                       developer-only headless QML loader (PyQt6)
install.sh / uninstall.sh                         notifyrc install/remove
README.md, docs/superpowers/specs/…design.md      Task 9
```

---

### Task 1: Event ids and the remaining `status --json` detail fields

**Files:**
- Modify: `dbb/state.py` (`new_state`, `add_event`, `load_state`), `dbb/render.py` (`state_json`)
- Test: `tests/test_state.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `dbb.sysfs` sample fields `current_now_ua`, `voltage_now_uv`, `charge_full_uah`, `charge_full_design_uah`, `status`; `registry.open_tenure(state, slot)["start_ts"]`; `dbb.VERSION`.
- Produces:
  - `state["next_event_id"]: int` (starts at 1); every event is `{"id": int, "ts": str, "kind": str, "detail": str}`; ids strictly increase and are never reused, including across the 500-event trim.
  - `status --json` top level gains `"version": str`; each present bat gains `"power_w": float|None` (negative while `status == "Discharging"`), `"voltage_v": float|None`, `"health_pct": float|None`, `"in_slot_hours": float|None`. Events in `out["events"]` carry `id`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_state.py`:

```python
class EventIds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DBB_STATE_DIR"] = self.tmp.name
        for m in list(sys.modules):
            if m.startswith("dbb"):
                del sys.modules[m]

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("DBB_STATE_DIR", None)
        for m in list(sys.modules):
            if m.startswith("dbb"):
                del sys.modules[m]

    def test_ids_increase_and_survive_the_trim(self):
        from dbb import state as st
        s = st.new_state()
        for i in range(503):
            st.add_event(s, "test", str(i))
        ids = [e["id"] for e in s["events"]]
        self.assertEqual(len(ids), 500)
        self.assertEqual(ids[0], 4)          # the first three were trimmed
        self.assertEqual(ids[-1], 503)
        self.assertEqual(s["next_event_id"], 504)

    def test_old_events_are_numbered_once_on_load(self):
        from dbb import state as st
        s = st.new_state()
        s["events"] = [{"ts": "t1", "kind": "pack", "detail": "a"},
                       {"ts": "t2", "kind": "profile", "detail": "b"}]
        s.pop("next_event_id")
        st.save_state(s)
        loaded = st.load_state()
        self.assertEqual([e["id"] for e in loaded["events"]], [1, 2])
        self.assertEqual(loaded["next_event_id"], 3)
        st.add_event(loaded, "x", "c")
        self.assertEqual(loaded["events"][-1]["id"], 3)
```

And append to `tests/test_cli.py` (after class `ConfigCmd`):

```python
class StatusDetail(CliBase):
    def test_json_has_the_detail_fields(self):
        # 1.5 A at 11.4 V discharging -> -17.1 W; BAT0 is Full at 0 A -> 0.0 W
        self.fs.bat("BAT1", capacity=60, charge_now=2760000, status="Discharging",
                    current_now=1500000)
        self.run_cli("sample")
        j = self.status()
        b1, b0 = j["bats"]["BAT1"], j["bats"]["BAT0"]
        self.assertAlmostEqual(b1["power_w"], -17.1, places=2)
        self.assertAlmostEqual(b0["power_w"], 0.0, places=2)
        self.assertAlmostEqual(b1["voltage_v"], 11.4, places=2)
        self.assertAlmostEqual(b0["health_pct"], 100.0, places=1)
        self.assertIsNotNone(b0["in_slot_hours"])
        self.assertGreaterEqual(b0["in_slot_hours"], 0.0)
        self.assertEqual(j["version"], self.cli.VERSION)

    def test_events_carry_ids(self):
        self.run_cli("profile", "set", "travel")
        j = self.status()
        self.assertTrue(j["events"])
        last = j["events"][-1]
        self.assertEqual(last["kind"], "profile")
        self.assertIsInstance(last["id"], int)
```

- [ ] **Step 2: Run to verify failure** — `python3 -m unittest tests.test_state.EventIds tests.test_cli.StatusDetail -v` → `KeyError: 'id'` / `KeyError: 'power_w'` / `KeyError: 'version'`.

- [ ] **Step 3: Implement** — `dbb/state.py`:

```python
def new_state():
    return {
        "version": 2, "created": now_iso(),
        "packs": {}, "tenures": [], "slots": {b: None for b in BATS}, "pending": {},
        "next_tenure_id": 1, "next_event_id": 1, "last": None,
        "discharge_first": {b: 0 for b in BATS}, "sessions": 0, "policy": None,
        "ac_run_start_ts": None, "profile_switched_ts": None,
        "one_off_revert_hours": None, "firmware": {}, "events": [],
        "config_error": None, "config_snapshot": None,
    }


def add_event(state, kind, detail):
    # Ids are monotonic and never reused, so the applet can notify once per
    # event with a single high-water mark even after the list is trimmed.
    eid = state.get("next_event_id", 1)
    state.setdefault("events", []).append(
        {"id": eid, "ts": now_iso(), "kind": kind, "detail": detail})
    state["next_event_id"] = eid + 1
    del state["events"][:-500]


def _number_events(state):
    """Events gained ids in 0.3. Number any older ones once, in order."""
    evs = state.get("events", [])
    if any("id" not in e for e in evs):
        for i, e in enumerate(evs, 1):
            e["id"] = i
        state["next_event_id"] = len(evs) + 1
```

In `load_state()`, after the `for k, v in base.items(): state.setdefault(k, v)` loop and before `return state`, add `_number_events(state)`.

`dbb/render.py`: add `from dbb import VERSION` to the imports, then in `state_json`:

```python
    out = {
        "ts": now_iso(),
        "version": VERSION,
        "ac_online": s["ac_online"],
        ...
```

and inside the per-bat loop, after `"discharged_wh": None,` in the `entry` dict, add:

```python
            "power_w": _power_w(v),
            "voltage_v": (round(v["voltage_now_uv"] / 1e6, 2)
                          if v["voltage_now_uv"] is not None else None),
            "health_pct": (round(100.0 * v["charge_full_uah"] / v["charge_full_design_uah"], 1)
                           if v["charge_full_uah"] and v["charge_full_design_uah"] else None),
            "in_slot_hours": round((now - t["start_ts"]) / 3600.0, 1) if t else None,
```

and a module-level helper next to `wh()`:

```python
def _power_w(v):
    """Signed watts: negative while discharging. Dell reports current as a
    magnitude, so the sign comes from the status string."""
    if v["current_now_ua"] is None or v["voltage_now_uv"] is None:
        return None
    w = abs(v["current_now_ua"]) * v["voltage_now_uv"] / 1e12
    return round(-w if v["status"] == "Discharging" else w, 2)
```

- [ ] **Step 4: Run** — `python3 -W error::ResourceWarning -m unittest discover -s tests -v`; all pass. Also check the import is not circular: `python3 -c "import dbb.render"` prints nothing.

- [ ] **Step 5: Commit**

```bash
git add dbb/state.py dbb/render.py tests/test_state.py tests/test_cli.py
git commit -m "Events carry monotonic ids; status JSON adds power, voltage, health, time in slot, version

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `config get --json`, `config apply --json`, `config validate --json`

**Files:**
- Modify: `dbb/config.py` (new `load_json`), `dbb/cli.py` (`cmd_config_get`, `cmd_config_validate`, `cmd_config_apply`, parser)
- Test: `tests/test_cli.py` (class `ConfigCmd`)

**Interfaces:**
- Produces: `config.load_json(path) -> dict` (raises `ConfigError` naming the path for unreadable/garbage input, and the same key-named errors as `load()` for schema violations). CLI: `config get [key] [--json]`, `config validate [--json] <path>`, `config apply [--json] <path>`. Polkit classes unchanged (`get` control, `validate`/`apply` config).

- [ ] **Step 1: Write the failing tests** — append inside class `ConfigCmd` in `tests/test_cli.py`:

```python
    def test_get_json_is_the_whole_config(self):
        code, out, err = self.run_cli("config", "get", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), self.config.default_config())

    def test_get_json_one_key(self):
        code, out, _ = self.run_cli("config", "get", "profiles.field.bands", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"all": [90, 100]})

    def _json_candidate(self, mutate):
        cfg = self.config.default_config()
        mutate(cfg)
        p = os.path.join(self.tmp.name, "new.json")
        with open(p, "w") as fh:
            json.dump(cfg, fh)
        return p, cfg

    def test_apply_json_roundtrip(self):
        def m(c):
            c["general"]["deadband_efc"] = 0.7
            c["profiles"]["daily"]["label"] = "Desk"
        p, cfg = self._json_candidate(m)
        code, _, err = self.run_cli("config", "apply", "--json", p)
        self.assertEqual(code, 0, err)
        code, out, _ = self.run_cli("config", "get", "--json")
        self.assertEqual(json.loads(out), cfg)

    def test_validate_json_ok(self):
        p, _ = self._json_candidate(lambda c: None)
        code, out, _ = self.run_cli("config", "validate", "--json", p)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "ok")

    def test_apply_json_rejects_with_the_key_named(self):
        p, _ = self._json_candidate(lambda c: c["profiles"]["travel"]["bands"].__setitem__("work", [96, 95]))
        code, _, err = self.run_cli("config", "apply", "--json", p)
        self.assertNotEqual(code, 0)
        self.assertIn("travel", err)
        self.assertIn("work", err)
        code, out, _ = self.run_cli("config", "get", "profiles.travel.bands.work", "--json")
        self.assertEqual(json.loads(out), [80, 95])

    def test_apply_json_garbage_names_the_path(self):
        p = os.path.join(self.tmp.name, "bad.json")
        with open(p, "w") as fh:
            fh.write("{not json")
        code, _, err = self.run_cli("config", "apply", "--json", p)
        self.assertNotEqual(code, 0)
        self.assertIn("bad.json", err)

    def test_apply_json_missing_path(self):
        code, _, err = self.run_cli("config", "apply", "--json", "/nonexistent/x.json")
        self.assertNotEqual(code, 0)
        self.assertIn("not found", err)
```

- [ ] **Step 2: Run to verify failure** — `python3 -m unittest tests.test_cli.ConfigCmd -v` → argparse `unrecognized arguments: --json`.

- [ ] **Step 3: Implement** — `dbb/config.py`, after `load()`:

```python
def load_json(path):
    """A candidate config as a JSON document with the TOML's exact shape.

    The applet's config dialog submits these so that only Python ever emits
    TOML. Same strict validation as load(); the path is named on any I/O or
    parse error so the dialog can show it."""
    path = Path(path)
    try:
        with path.open("rb") as fh:
            cfg = json.load(fh)
    except OSError as e:
        raise ConfigError(f"{path}: not found or unreadable") from e
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ConfigError(f"{path}: {e}") from e
    if not isinstance(cfg, dict):
        raise ConfigError(f"{path}: top level must be an object")
    validate(cfg)
    return cfg
```

(`json` is already imported by `dbb/config.py` for `_val`.)

`dbb/cli.py` — replace `cmd_config_get`, `cmd_config_validate`, `cmd_config_apply`:

```python
def cmd_config_get(args):
    state, cfg, _ = _view()
    node = cfg
    if args.key:
        for p in args.key.split("."):
            if not isinstance(node, dict) or p not in node:
                die(f"error: no key {args.key!r}")
            node = node[p]
    if args.json:
        print(json.dumps(node, indent=2, sort_keys=True))
    elif not args.key:
        print(cfg_mod.emit(cfg), end="")
    elif isinstance(node, dict):
        print(cfg_mod.emit(node) if "general" in node else json.dumps(node, indent=2))
    else:
        print(node if not isinstance(node, bool) else str(node).lower())


def _load_candidate(args):
    """The file named on the command line, as TOML or (--json) JSON. Never
    falls back to defaults: a missing path must not replace the real config."""
    try:
        if args.json:
            return cfg_mod.load_json(args.path)
        return cfg_mod.load(args.path, must_exist=True)
    except cfg_mod.ConfigError as e:
        die(f"error: {e}")


def cmd_config_validate(args):
    _load_candidate(args)
    print("ok")


def cmd_config_apply(args):
    # The applied file replaces the whole config -- same strict, full-schema
    # semantics as config.load()/config validate, not a partial overlay.
    state = load_state()
    cfg = _load_candidate(args)
    _save_config(cfg, state, "config apply")
    add_event(state, "config", f"applied from {Path(args.path).name}")
    save_state(state)
    print("ok")
```

Parser (in `build_parser`/wherever the `cf` subparsers are built):

```python
    cf = sub.add_parser("config", help="general settings").add_subparsers(dest="ccmd", required=True)
    sp = cf.add_parser("get"); sp.add_argument("key", nargs="?"); sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_config_get, cls="control")
    sp = cf.add_parser("set"); sp.add_argument("assignments", nargs="+", metavar="key=value"); sp.set_defaults(func=cmd_config_set, cls="config")
    sp = cf.add_parser("validate"); sp.add_argument("--json", action="store_true", help="the file is JSON with the TOML's shape"); sp.add_argument("path")
    sp.set_defaults(func=cmd_config_validate, cls="config")
    sp = cf.add_parser("apply"); sp.add_argument("--json", action="store_true", help="the file is JSON with the TOML's shape"); sp.add_argument("path")
    sp.set_defaults(func=cmd_config_apply, cls="config")
```

- [ ] **Step 4: Run** — full suite with `-W error::ResourceWarning`; all pass. On the machine, read-only smoke: `DBB_STATE_DIR=/tmp/dbb-c2 DBB_CONFIG_DIR=/tmp/dbb-c2 ./dell-battery-balance config get --json | python3 -m json.tool > /dev/null` (temp dirs only).

- [ ] **Step 5: Commit**

```bash
git add dbb/config.py dbb/cli.py tests/test_cli.py
git commit -m "CLI: config get/validate/apply accept and emit JSON for the applet's config dialog

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Config scaffolding — `main.xml`, `config.qml`, Display page, poll interval, tray text, headless page loader

**Files:**
- Create: `plasmoid/package/contents/config/main.xml`, `plasmoid/package/contents/config/config.qml`, `plasmoid/package/contents/ui/configDisplay.qml`, `plasmoid/tools/load-page.py`
- Modify: `plasmoid/package/contents/ui/main.qml` (Timer), `plasmoid/package/contents/ui/CompactRepresentation.qml` (whole file)

**Interfaces:**
- Produces KConfig keys read elsewhere: `Plasmoid.configuration.trayText: int` (0 none, 1 profile label, 2 EFC divergence), `pollInterval: int` seconds, `expandDetails: bool`, `notify: bool`, `lastNotifiedEventId: int` (−1 = never initialised). `config.qml` references pages `configProfiles.qml`, `configPacks.qml`, `configGeneral.qml` that Tasks 5–7 create; until then the dialog shows those categories as empty — acceptable within the branch, never on `main`.
- Produces the developer tool `python3 plasmoid/tools/load-page.py FILE...` → prints `ok FILE` per file, exit 1 on any component error, creation failure or QML warning.

- [ ] **Step 1: `main.xml`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<kcfg xmlns="http://www.kde.org/standards/kcfg/1.0"
      xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
      xsi:schemaLocation="http://www.kde.org/standards/kcfg/1.0
      http://www.kde.org/standards/kcfg/1.0/kcfg.xsd">
  <kcfgfile name=""/>

  <group name="General">
    <!-- 0 = none, 1 = profile label, 2 = EFC divergence -->
    <entry name="trayText" type="Int">
      <default>0</default>
    </entry>
    <!-- seconds between `status --json` polls -->
    <entry name="pollInterval" type="Int">
      <default>30</default>
    </entry>
    <entry name="expandDetails" type="Bool">
      <default>false</default>
    </entry>
    <entry name="notify" type="Bool">
      <default>true</default>
    </entry>
    <!-- highest state event id already notified; -1 = adopt the backlog silently -->
    <entry name="lastNotifiedEventId" type="Int">
      <default>-1</default>
    </entry>
  </group>
</kcfg>
```

- [ ] **Step 2: `config.qml`**

```qml
/*
 * Copyright (C) 2026 ChiefGyk3D
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
import QtQuick
import org.kde.plasma.configuration

ConfigModel {
    ConfigCategory {
        name: i18n("Display")
        icon: "preferences-desktop-display"
        source: "configDisplay.qml"
    }
    ConfigCategory {
        name: i18n("Profiles")
        icon: "battery-profile-powersave"
        source: "configProfiles.qml"
    }
    ConfigCategory {
        name: i18n("Packs")
        icon: "battery"
        source: "configPacks.qml"
    }
    ConfigCategory {
        name: i18n("General")
        icon: "configure"
        source: "configGeneral.qml"
    }
}
```

- [ ] **Step 3: `configDisplay.qml`** — the only page that writes KConfig; the dialog's Apply/OK copies every `cfg_` property back.

```qml
/*
 * Copyright (C) 2026 ChiefGyk3D
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM

KCM.SimpleKCM {
    id: page

    property int cfg_trayText
    property int cfg_pollInterval
    property bool cfg_expandDetails
    property bool cfg_notify
    // Declared so the dialog's generic cfg_ round-trip keeps it; never edited here.
    property int cfg_lastNotifiedEventId

    Kirigami.FormLayout {
        QQC2.ComboBox {
            Kirigami.FormData.label: i18n("Text beside the icon:")
            model: [i18n("None"), i18n("Profile label"), i18n("EFC divergence")]
            currentIndex: page.cfg_trayText
            onActivated: page.cfg_trayText = currentIndex
        }
        QQC2.Label {
            text: i18n("Shown when the widget sits in a panel; the system tray keeps it icon-only.")
            font: Kirigami.Theme.smallFont
            opacity: 0.7
        }
        QQC2.SpinBox {
            Kirigami.FormData.label: i18n("Poll interval:")
            from: 10
            to: 600
            stepSize: 5
            value: page.cfg_pollInterval
            onValueModified: page.cfg_pollInterval = value
            textFromValue: (v, locale) => i18n("%1 s", v)
            valueFromText: (t, locale) => parseInt(t) || 30
        }
        QQC2.CheckBox {
            Kirigami.FormData.label: i18n("Pack details:")
            text: i18n("Expanded by default")
            checked: page.cfg_expandDetails
            onToggled: page.cfg_expandDetails = checked
        }
        QQC2.CheckBox {
            Kirigami.FormData.label: i18n("Notifications:")
            text: i18n("Identity questions, reverts, firmware mismatch, pack removed above 70%")
            checked: page.cfg_notify
            onToggled: page.cfg_notify = checked
        }
    }
}
```

- [ ] **Step 4: `main.qml`** — replace the poll `Timer`:

```qml
    Timer {
        interval: Math.max(10, Plasmoid.configuration.pollInterval) * 1000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: root.refresh()
    }
```

- [ ] **Step 5: `CompactRepresentation.qml`** — whole file (adds the optional label; the two dots now anchor to the icon box rather than the whole item):

```qml
/*
 * Copyright (C) 2026 ChiefGyk3D
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami

MouseArea {
    id: compact

    // Optional text beside the icon (Display page). The system tray gives
    // every item a square, so this only shows when the widget is placed
    // directly in a panel.
    readonly property string sideText: {
        const mode = Plasmoid.configuration.trayText;
        if (!root.info || mode === 0) return "";
        if (mode === 1) return root.info.profile ? root.info.profile.label : "";
        const d = root.info.divergence_efc;
        return (d === null || d === undefined) ? "" : i18n("Δ%1", d.toFixed(2));
    }

    Layout.minimumWidth: Kirigami.Units.iconSizes.small
        + (label.visible ? label.implicitWidth + Kirigami.Units.smallSpacing : 0)
    Layout.minimumHeight: Kirigami.Units.iconSizes.small
    Layout.preferredWidth: Layout.minimumWidth

    hoverEnabled: true
    onClicked: plasmoid.expanded = !plasmoid.expanded

    RowLayout {
        anchors.fill: parent
        spacing: Kirigami.Units.smallSpacing

        Item {
            id: iconBox
            Layout.fillHeight: true
            Layout.preferredWidth: height

            Kirigami.Icon {
                anchors.fill: parent
                source: Plasmoid.icon
                active: compact.containsMouse
            }

            // Field mode disables the wear protection entirely, and it is
            // easy to leave on by accident. Mark it in the tray, not just
            // in the popup.
            Rectangle {
                visible: root.fieldMode
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                width: Math.round(parent.width / 3)
                height: width
                radius: width / 2
                color: Kirigami.Theme.negativeTextColor
                border.width: 1
                border.color: Kirigami.Theme.backgroundColor
            }

            // A pack swap left an identity question unanswered. Field
            // mode's red dot takes precedence as the more urgent condition.
            Rectangle {
                visible: root.hasPending && !root.fieldMode
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                width: Math.round(parent.width / 3)
                height: width
                radius: width / 2
                color: Kirigami.Theme.neutralTextColor
                border.width: 1
                border.color: Kirigami.Theme.backgroundColor
            }
        }

        PlasmaComponents.Label {
            id: label
            visible: compact.sideText !== ""
            text: compact.sideText
            font: Kirigami.Theme.smallFont
            elide: Text.ElideRight
        }
    }
}
```

- [ ] **Step 6: `plasmoid/tools/load-page.py`** — developer tool; documents itself:

```python
#!/usr/bin/env python3
# Copyright (C) 2026 ChiefGyk3D
# SPDX-License-Identifier: GPL-3.0-or-later
"""Load applet config pages headlessly and fail on any QML error or warning.

The pages never reference the `plasmoid` context object, so they load
outside plasmashell; `i18n` is stubbed on the JS global object. Needs the
distro's python3-pyqt6 (uses the system Qt and its KDE QML modules). Not
part of the unit suite -- `tests/` stays stdlib-only.

    python3 plasmoid/tools/load-page.py plasmoid/package/contents/ui/config*.qml
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QTimer, QUrl                      # noqa: E402
from PyQt6.QtGui import QGuiApplication                    # noqa: E402
from PyQt6.QtQml import QQmlComponent, QQmlEngine          # noqa: E402

I18N_STUB = ("(function(s){var a=arguments;return String(s)"
             ".replace(/%(\\d)/g,function(m,n){return a[+n];});})")
NOISE = ("Failed to find a Kirigami platform plugin",)


def load(engine, path):
    bad = []
    engine.warnings.connect(lambda errs: bad.extend(str(e) for e in errs))
    comp = QQmlComponent(engine, QUrl.fromLocalFile(os.path.abspath(path)))
    if comp.isError():
        return [str(e) for e in comp.errors()]
    obj = comp.create()
    app = QGuiApplication.instance()
    QTimer.singleShot(400, app.quit)
    app.exec()
    if obj is None:
        bad.append("create() returned null")
    else:
        obj.deleteLater()
    return [b for b in bad if not any(n in b for n in NOISE)]


def main(paths):
    app = QGuiApplication(sys.argv[:1])   # noqa: F841 (must outlive the engine)
    engine = QQmlEngine()
    for name in ("i18n", "i18nc", "i18np", "i18nd"):
        engine.globalObject().setProperty(name, engine.evaluate(I18N_STUB))
    rc = 0
    for p in paths:
        problems = load(engine, p)
        if problems:
            rc = 1
            print(f"FAIL {p}")
            for line in problems:
                print("   ", line)
        else:
            print(f"ok {p}")
    return rc


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1:]))
```

Note `i18nc`'s first argument is the context, so the stub returns the context string for it; that is fine for a load check.

- [ ] **Step 7: Verify** — `python3 plasmoid/tools/load-page.py plasmoid/package/contents/ui/configDisplay.qml` → `ok …configDisplay.qml`. Then the standard applet check: `kpackagetool6 --type Plasma/Applet --upgrade plasmoid/package && timeout 12 plasmawindowed com.chiefgyk3d.dellbatterybalance > /tmp/pw.log 2>&1; grep -iE 'error|TypeError|ReferenceError|Binding loop|is not a type|non-existent' /tmp/pw.log` → nothing. Unit suite unchanged; run once as a guard.

- [ ] **Step 8: Commit**

```bash
git add plasmoid
git commit -m "Applet: config schema, Display page, poll interval and tray text from config; headless page loader

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Popup — expandable per-pack details, ceiling match with read-back age, recent events

**Files:**
- Modify: `plasmoid/package/contents/ui/main.qml` (add `ageText`, `fmtNum`), `plasmoid/package/contents/ui/FullRepresentation.qml`

**Interfaces:**
- Consumes: Task 1 JSON fields (`power_w`, `voltage_v`, `health_pct`, `in_slot_hours`, `temp_c`, `mean_soc`, `pct_ge90`, `discharged_wh`, `firmware[slot].{requested,observed,ts}`, `events[].{ts,kind,detail}`), `Plasmoid.configuration.expandDetails` (Task 3).
- Produces: `root.ageText(iso: string) -> string` and `root.fmtNum(v, dp: int, unit: string) -> string` in `main.qml`, reused by Task 8's notifications and by the pages.

- [ ] **Step 1: `main.qml` helpers** — add below `validPackName`:

```qml
    // "3 min ago" for an ISO-8601 timestamp; "" when it will not parse.
    function ageText(iso) {
        if (!iso) return "";
        const t = Date.parse(iso);
        if (isNaN(t)) return "";
        const m = Math.round((Date.now() - t) / 60000);
        if (m < 1) return i18n("just now");
        if (m < 60) return i18n("%1 min ago", m);
        const h = Math.round(m / 60);
        if (h < 48) return i18n("%1 h ago", h);
        return i18n("%1 d ago", Math.round(h / 24));
    }

    // A number with a unit, or the shared "no data yet" placeholder.
    function fmtNum(v, dp, unit) {
        if (v === null || v === undefined) return i18n("no data yet");
        return i18n("%1%2", Number(v).toFixed(dp), unit);
    }
```

- [ ] **Step 2: `FullRepresentation.qml` — pack row header gets a toggle.** In the per-pack delegate, add a property and change the header `RowLayout`. Replace the block from `RowLayout {` (the one containing `PlasmaExtras.Heading { level: 5 …"BAT0 - primary"`) through its closing brace with:

```qml
                    property bool expanded: Plasmoid.configuration.expandDetails

                    RowLayout {
                        Layout.fillWidth: true
                        PlasmaExtras.Heading {
                            level: 5
                            text: row.modelData === "BAT0"
                                ? i18n("BAT0 - primary") : i18n("BAT1 - slice")
                        }
                        PlasmaComponents.Label {
                            text: row.have && row.bat.pack ? row.bat.pack : i18n("unidentified")
                            opacity: 0.8
                        }
                        Item { Layout.fillWidth: true }
                        PlasmaComponents.Label {
                            text: row.have
                                ? i18n("%1%  %2", row.bat.capacity, row.bat.status)
                                : ""
                            font.weight: Font.Bold
                        }
                        PlasmaComponents.ToolButton {
                            icon.name: row.expanded ? "arrow-up" : "arrow-down"
                            display: QQC2.AbstractButton.IconOnly
                            text: row.expanded ? i18n("Fewer details") : i18n("More details")
                            onClicked: row.expanded = !row.expanded
                            PlasmaComponents.ToolTip { text: parent.text }
                        }
                    }
```

- [ ] **Step 3: Ceiling row replaces the two "Charge ceiling" / "Firmware" rows.** In the existing 2-column `GridLayout`, delete the four `PlasmaComponents.Label` items for "Charge ceiling:" and "Firmware:" (label + value each) and insert:

```qml
                        PlasmaComponents.Label {
                            text: i18n("Ceiling:")
                            font: Kirigami.Theme.smallFont
                            opacity: 0.8
                        }
                        PlasmaComponents.Label {
                            Layout.fillWidth: true
                            font: Kirigami.Theme.smallFont
                            wrapMode: Text.WordWrap
                            // What the tool asked for, whether firmware agrees, and how
                            // stale the read-back is. Firmware mode itself is only
                            // readable as the service account, so the band is enough.
                            text: {
                                if (!row.have) return "";
                                const fw = (root.info && root.info.firmware)
                                    ? root.info.firmware[row.modelData] : null;
                                let req;
                                if (row.bat.start && row.bat.stop) {
                                    req = i18n("%1 - %2%", row.bat.start, row.bat.stop);
                                } else if (fw && fw.requested) {
                                    req = i18n("%1 - %2%", fw.requested[0], fw.requested[1]);
                                } else {
                                    return i18n("not applied yet");
                                }
                                if (!fw || !fw.observed) return req;
                                const o = fw.observed, r = fw.requested;
                                const ok = r && o[0] === r[0] && o[1] === r[1];
                                return ok
                                    ? i18n("%1 - firmware agrees (%2)", req, root.ageText(fw.ts))
                                    : i18n("%1 - MISMATCH, firmware has %2 - %3% (%4)", req, o[0], o[1], root.ageText(fw.ts));
                            }
                        }
```

- [ ] **Step 4: Details grid** — immediately after that `GridLayout`'s closing brace (still inside the delegate `ColumnLayout`, before the `pendingMsg` `InlineMessage`), add:

```qml
                    GridLayout {
                        Layout.fillWidth: true
                        visible: row.expanded && row.have
                        columns: 2
                        rowSpacing: 0
                        columnSpacing: Kirigami.Units.largeSpacing

                        Repeater {
                            model: !row.have ? [] : [
                                [i18n("Power:"), root.fmtNum(row.bat.power_w, 1, i18n(" W"))],
                                [i18n("Voltage:"), root.fmtNum(row.bat.voltage_v, 2, i18n(" V"))],
                                [i18n("Temperature:"), root.fmtNum(row.bat.temp_c, 1, i18n(" °C"))],
                                [i18n("Health:"), row.bat.health_pct === null
                                    ? i18n("no data yet")
                                    : i18n("%1% of design (as reported, updates rarely)", row.bat.health_pct.toFixed(1))],
                                [i18n("Mean SoC:"), root.fmtNum(row.bat.mean_soc, 1, "%")],
                                [i18n("Time at 90%+:"), root.fmtNum(row.bat.pct_ge90, 1, "%")],
                                [i18n("Discharged:"), root.fmtNum(row.bat.discharged_wh, 1, i18n(" Wh"))],
                                [i18n("In slot:"), row.bat.in_slot_hours === null
                                    ? i18n("no data yet")
                                    : (row.bat.in_slot_hours >= 48
                                        ? i18n("%1 d", (row.bat.in_slot_hours / 24).toFixed(1))
                                        : i18n("%1 h", row.bat.in_slot_hours.toFixed(1)))],
                            ]
                            delegate: Repeater {
                                required property var modelData
                                model: modelData
                                delegate: PlasmaComponents.Label {
                                    required property string modelData
                                    required property int index
                                    Layout.fillWidth: index === 1
                                    font: Kirigami.Theme.smallFont
                                    opacity: index === 0 ? 0.8 : 1.0
                                    wrapMode: Text.WordWrap
                                    text: modelData
                                }
                            }
                        }
                    }
```

- [ ] **Step 5: Recent events** — after the last summary `PlasmaComponents.Label` (the one showing `recommendation.why`) and before the `ColumnLayout` closes, add:

```qml
            PlasmaExtras.Heading {
                level: 5
                Layout.topMargin: Kirigami.Units.smallSpacing
                visible: !!(root.info && root.info.events && root.info.events.length > 0)
                text: i18n("Recent")
            }
            Repeater {
                model: root.info && root.info.events ? root.info.events.slice(-3).reverse() : []
                delegate: PlasmaComponents.Label {
                    required property var modelData
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    font: Kirigami.Theme.smallFont
                    opacity: 0.8
                    text: i18n("%1 - %2: %3", root.ageText(modelData.ts), modelData.kind, modelData.detail)
                }
            }
```

- [ ] **Step 6: Verify** — `kpackagetool6 --type Plasma/Applet --upgrade plasmoid/package && timeout 12 plasmawindowed com.chiefgyk3d.dellbatterybalance > /tmp/pw.log 2>&1; grep -iE 'error|TypeError|ReferenceError|Binding loop|is not a type|non-existent' /tmp/pw.log` → nothing. If the nested `Repeater` inside a `GridLayout` misplaces cells (labels not in two columns), replace the nested Repeater with sixteen explicit `PlasmaComponents.Label` pairs using the same texts — correctness over cleverness. Unit suite unchanged; run once.

- [ ] **Step 7: Commit**

```bash
git add plasmoid
git commit -m "Applet: expandable pack details, ceiling read-back age and match, last three events

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: `ConfigBackend.qml` and the General page

**Files:**
- Create: `plasmoid/package/contents/ui/ConfigBackend.qml`, `plasmoid/package/contents/ui/configGeneral.qml`

**Interfaces:**
- Produces `ConfigBackend` (an `Item`):
  - properties `cli: string`, `controlExe: string`, `configureExe: string`, `privPrefix: string` (default `"pkexec --user dell-battery-balance "`), `applyVerb: string` (default `"config apply --json"`), `busy: bool`.
  - functions `load()` → signal `loaded(var cfg)`; `loadStatus()` → `statusLoaded(var info)`; `act(subcommand: string, configure: bool)` → `actionDone(string source)`; `apply(cfg: var)` → `applied()`.
  - signals `failed(string message)` and `cancelled()` for every call.
  - The three overridable strings exist so the loader tool and a manual test can run the pipeline unprivileged (`privPrefix: ""`, `applyVerb: "config validate --json"`).
- Consumes: Task 2 CLI flags.

- [ ] **Step 1: `ConfigBackend.qml`**

```qml
/*
 * Copyright (C) 2026 ChiefGyk3D
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
import QtQuick
import org.kde.plasma.plasma5support as Plasma5Support

// Shared by the config pages: read the live config or status as JSON, run a
// privileged subcommand, or apply a replacement config through the
// configure-class polkit action. Each call is one shell command through the
// executable engine, so the pages stay declarative.
Item {
    id: backend

    property string cli: "/usr/local/bin/dell-battery-balance"
    property string controlExe: "/usr/local/libexec/dbb-control"
    property string configureExe: "/usr/local/libexec/dbb-configure"
    // Overridable so the pipeline can be exercised unprivileged (see tools/).
    property string privPrefix: "pkexec --user dell-battery-balance "
    property string applyVerb: "config apply --json"
    property bool busy: false

    signal loaded(var cfg)
    signal statusLoaded(var info)
    signal applied()
    signal actionDone(string source)
    signal failed(string message)
    signal cancelled()

    Plasma5Support.DataSource {
        id: exec
        engine: "executable"
        connectedSources: []
        onNewData: (source, payload) => {
            disconnectSource(source);
            backend.busy = false;
            backend.handle(source, payload);
        }
    }

    function run(cmd) {
        busy = true;
        exec.connectSource(cmd);
    }

    function load() { run(cli + " config get --json"); }
    function loadStatus() { run(cli + " status --json"); }

    function act(subcommand, configure) {
        run(privPrefix + (configure ? configureExe : controlExe) + " " + subcommand);
    }

    // The service account cannot read $XDG_RUNTIME_DIR (mode 700), so the
    // candidate goes through a mktemp file in /tmp, world-readable for the
    // seconds it exists. It carries no secrets: the config holds bands and
    // the *path* to a BIOS password file, never a password. base64 keeps
    // the JSON out of shell quoting entirely (Qt.btoa encodes UTF-8).
    function apply(cfg) {
        const b64 = Qt.btoa(JSON.stringify(cfg));
        run("sh -c 'f=$(mktemp /tmp/dbb-config-XXXXXX.json) && printf %s " + b64
            + " | base64 -d > \"$f\" && chmod 644 \"$f\" && "
            + privPrefix + configureExe + " " + applyVerb + " \"$f\"; "
            + "rc=$?; rm -f -- \"$f\"; exit $rc'");
    }

    function handle(source, payload) {
        const stdout = (payload["stdout"] || "").trim();
        const stderr = (payload["stderr"] || "").trim();
        const code = payload["exit code"];
        const isGet = source.indexOf(" config get --json") !== -1;
        const isStatus = source.indexOf(" status --json") !== -1;
        if (isGet || isStatus) {
            if (code !== 0) {
                failed(stderr !== "" ? stderr : i18n("could not read the tool's output"));
                return;
            }
            try {
                const parsed = JSON.parse(stdout);
                if (isStatus) statusLoaded(parsed); else loaded(parsed);
            } catch (e) {
                failed(i18n("could not parse the tool's output"));
            }
            return;
        }
        // pkexec exits 126 when the prompt is dismissed and 127 when
        // authorisation is refused; neither is an error to show.
        if (code === 126 || code === 127) { cancelled(); return; }
        if (code !== 0) {
            failed(stderr !== "" ? stderr : i18n("action failed (exit %1)", code));
            return;
        }
        if (source.indexOf("dbb-config-") !== -1) applied(); else actionDone(source);
    }
}
```

- [ ] **Step 2: `configGeneral.qml`**

```qml
/*
 * Copyright (C) 2026 ChiefGyk3D
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM

KCM.SimpleKCM {
    id: page

    // Working copy of the whole config: `config apply` replaces everything,
    // so even a one-field change submits the full document.
    property var cfg: null
    property string message: ""
    property int messageType: Kirigami.MessageType.Information

    // The dialog connects this to its Apply button.
    signal configurationChanged()

    // Called by the dialog on Apply / OK, after it wrote KConfig. The
    // result arrives asynchronously; use Apply (not OK) to see a
    // validation error, since OK closes the dialog.
    function saveConfig() {
        if (cfg) backend.apply(cfg);
    }

    function touch() {
        page.cfgChanged();
        page.configurationChanged();
    }

    ConfigBackend {
        id: backend
        onLoaded: c => { page.cfg = c; page.message = ""; }
        onApplied: {
            page.message = i18n("Applied.");
            page.messageType = Kirigami.MessageType.Positive;
        }
        onFailed: m => {
            page.message = m;
            page.messageType = Kirigami.MessageType.Error;
        }
        onCancelled: {
            page.message = i18n("Not applied: authorisation was cancelled.");
            page.messageType = Kirigami.MessageType.Information;
        }
    }
    Component.onCompleted: backend.load()

    ColumnLayout {
        Kirigami.InlineMessage {
            Layout.fillWidth: true
            visible: page.message !== ""
            type: page.messageType
            text: page.message
        }

        Kirigami.FormLayout {
            enabled: !!page.cfg && !backend.busy

            QQC2.SpinBox {
                Kirigami.FormData.label: i18n("Balance deadband:")
                // hundredths of an EFC; SpinBox is integer-only
                from: 0
                to: 500
                stepSize: 5
                value: page.cfg ? Math.round(page.cfg.general.deadband_efc * 100) : 50
                textFromValue: (v, locale) => i18n("%1 EFC", (v / 100).toFixed(2))
                valueFromText: (t, locale) => Math.round(parseFloat(t) * 100) || 0
                onValueModified: { page.cfg.general.deadband_efc = value / 100; page.touch(); }
            }
            QQC2.Label {
                text: i18n("Below this EFC gap the packs are treated as even and both get the neutral band.")
                font: Kirigami.Theme.smallFont
                opacity: 0.7
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            QQC2.CheckBox {
                Kirigami.FormData.label: i18n("Automatic balancing:")
                text: i18n("Apply the recommended ceilings on every tick")
                checked: page.cfg ? page.cfg.general.auto_balance : true
                onToggled: { page.cfg.general.auto_balance = checked; page.touch(); }
            }
            QQC2.SpinBox {
                Kirigami.FormData.label: i18n("Bench temperature:")
                from: -20
                to: 60
                value: page.cfg ? Math.round(page.cfg.general.bench_temp_c) : 25
                textFromValue: (v, locale) => i18n("%1 °C", v)
                valueFromText: (t, locale) => parseInt(t) || 25
                onValueModified: { page.cfg.general.bench_temp_c = value; page.touch(); }
            }
            QQC2.Label {
                text: i18n("Assumed temperature for packs on the shelf; it drives the bench calendar-wear estimate.")
                font: Kirigami.Theme.smallFont
                opacity: 0.7
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }
    }
}
```

- [ ] **Step 3: Verify (load + unprivileged pipeline)** — `python3 plasmoid/tools/load-page.py plasmoid/package/contents/ui/ConfigBackend.qml plasmoid/package/contents/ui/configGeneral.qml` → two `ok` lines (the General page's `backend.load()` runs the real read-only `config get --json` against the installed 0.2.0 CLI; that command has no `--json` yet on the installed copy, so expect the page to load fine and simply carry `message` = the CLI's usage error — the loader only fails on QML errors).

  Then exercise the staging pipeline without pkexec, from the scratchpad directory, with a harness that points the backend at the repo's CLI in validate mode:

```qml
// scratch/pipeline.qml  (do not commit)
import QtQuick
import "/home/chiefgyk3d/src/dell-battery-balance/plasmoid/package/contents/ui" as Ui
Item {
    property string result: "pending"
    Ui.ConfigBackend {
        id: b
        cli: "/home/chiefgyk3d/src/dell-battery-balance/dell-battery-balance"
        configureExe: "/home/chiefgyk3d/src/dell-battery-balance/dell-battery-balance"
        privPrefix: ""
        applyVerb: "config validate --json"
        onLoaded: c => { c.general.deadband_efc = 0.42; b.apply(c); }
        onApplied: result = "applied"
        onFailed: m => result = "failed: " + m
    }
    Component.onCompleted: b.load()
}
```

  Run it with the same PyQt6 pattern as `load-page.py` but printing `obj.property("result")` after ~2 s, with `DBB_CONFIG_DIR=/tmp/dbb-c5 DBB_STATE_DIR=/tmp/dbb-c5` exported so nothing touches `/etc`. Expected: `applied`. Then change `applyVerb` to `"config validate --json /nonexistent/"` (so the staged path becomes a second positional) and expect `failed: …` — proves errors propagate. (If the worktree path differs from `/home/chiefgyk3d/src/dell-battery-balance`, substitute it.)

- [ ] **Step 4: Commit**

```bash
git add plasmoid
git commit -m "Applet: shared config backend (staged JSON apply via pkexec) and General page

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Profiles page

**Files:**
- Create: `plasmoid/package/contents/ui/configProfiles.qml`

**Interfaces:**
- Consumes: `ConfigBackend` (Task 5). Config shape from spec §1.2: `cfg.profiles[name] = {label, description?, balancing, bands: {all} | {neutral, protect, work}, revert?: {after_hours?, on_ac_hours?, to}, pins?: {BAT0?: {role}|{start,stop}, BAT1?: …}}`.
- Rules mirrored client-side (the CLI still validates): `daily` and the active profile cannot be deleted; a fixed profile has only `bands.all`; `balancing` follows the type; a `revert` needs at least one trigger, so the page forces `after_hours ≥ 1` whenever `on_ac_hours` is 0.

- [ ] **Step 1: `configProfiles.qml`**

```qml
/*
 * Copyright (C) 2026 ChiefGyk3D
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM

KCM.SimpleKCM {
    id: page

    property var cfg: null                 // working copy of the whole config
    property string sel: ""                // selected profile name
    readonly property var names: cfg ? Object.keys(cfg.profiles).sort() : []
    readonly property var p: (cfg && sel && cfg.profiles[sel]) ? cfg.profiles[sel] : null
    readonly property bool fixed: !!(p && p.bands && ("all" in p.bands))
    property string message: ""
    property int messageType: Kirigami.MessageType.Information

    signal configurationChanged()

    function saveConfig() {
        if (cfg) backend.apply(cfg);
    }

    // Edits mutate the plain JS object; re-emit so bindings on page.cfg /
    // page.p re-evaluate, and arm the dialog's Apply button.
    function touch() {
        page.cfgChanged();
        page.configurationChanged();
    }

    function validNewName(s) {
        return /^[a-z0-9_-]{1,32}$/.test(s || "") && !(cfg && (s in cfg.profiles));
    }

    // The picker's currentIndex binding breaks on the first user selection
    // (standard ComboBox behaviour), so keep it in sync by hand.
    onSelChanged: picker.currentIndex = names.indexOf(sel)
    onNamesChanged: picker.currentIndex = names.indexOf(sel)

    // The editor is re-created per selection so every control starts from a
    // fresh binding to the newly selected profile.
    onPChanged: { editor.active = false; editor.active = !!p; }

    ConfigBackend {
        id: backend
        onLoaded: c => { page.cfg = c; page.sel = c.general.active_profile; page.message = ""; }
        onApplied: {
            page.message = i18n("Profiles applied.");
            page.messageType = Kirigami.MessageType.Positive;
        }
        onFailed: m => {
            page.message = m;
            page.messageType = Kirigami.MessageType.Error;
        }
        onCancelled: {
            page.message = i18n("Not applied: authorisation was cancelled.");
            page.messageType = Kirigami.MessageType.Information;
        }
    }
    Component.onCompleted: backend.load()

    // One [start, stop] editor. key is "all" | "neutral" | "protect" | "work".
    // The from/to limits are bindings (not broken by interaction), so the
    // user cannot cross start over stop.
    component BandRow: RowLayout {
        id: band
        required property string key
        readonly property var v: (page.p && page.p.bands && page.p.bands[band.key])
            ? page.p.bands[band.key] : [50, 80]
        QQC2.SpinBox {
            from: 50
            to: Math.min(95, band.v[1] - 1)
            value: band.v[0]
            onValueModified: { page.p.bands[band.key] = [value, band.v[1]]; page.touch(); }
        }
        QQC2.Label { text: i18n("to") }
        QQC2.SpinBox {
            from: Math.max(55, band.v[0] + 1)
            to: 100
            value: band.v[1]
            onValueModified: { page.p.bands[band.key] = [band.v[0], value]; page.touch(); }
        }
        QQC2.Label { text: "%" }
    }

    // Per-slot pin: follow the wear logic, pin a role, or pin a fixed band.
    component PinRow: RowLayout {
        id: pin
        required property string slot
        readonly property var cur: (page.p && page.p.pins && page.p.pins[pin.slot]) ? page.p.pins[pin.slot] : null
        readonly property int mode: !cur ? 0
            : (cur.role ? ["neutral", "protect", "work"].indexOf(cur.role) + 1 : 4)
        QQC2.ComboBox {
            model: [i18n("Follow the wear logic"), i18n("Pin role: neutral"),
                    i18n("Pin role: protect"), i18n("Pin role: work"), i18n("Pin a fixed band")]
            currentIndex: pin.mode
            onActivated: idx => {
                if (idx === 0) {
                    if (page.p.pins) {
                        delete page.p.pins[pin.slot];
                        if (Object.keys(page.p.pins).length === 0) delete page.p.pins;
                    }
                } else {
                    if (!page.p.pins) page.p.pins = {};
                    page.p.pins[pin.slot] = idx === 4
                        ? { start: 55, stop: 70 }
                        : { role: ["neutral", "protect", "work"][idx - 1] };
                }
                page.touch();
            }
        }
        QQC2.SpinBox {
            visible: pin.mode === 4
            from: 50
            to: pin.cur && pin.cur.stop !== undefined ? Math.min(95, pin.cur.stop - 1) : 95
            value: pin.cur && pin.cur.start !== undefined ? pin.cur.start : 55
            onValueModified: { page.p.pins[pin.slot] = { start: value, stop: pin.cur.stop }; page.touch(); }
        }
        QQC2.Label { visible: pin.mode === 4; text: i18n("to") }
        QQC2.SpinBox {
            visible: pin.mode === 4
            from: pin.cur && pin.cur.start !== undefined ? Math.max(55, pin.cur.start + 1) : 55
            to: 100
            value: pin.cur && pin.cur.stop !== undefined ? pin.cur.stop : 70
            onValueModified: { page.p.pins[pin.slot] = { start: pin.cur.start, stop: value }; page.touch(); }
        }
        QQC2.Label { visible: pin.mode === 4; text: "%" }
    }

    ColumnLayout {
        Kirigami.InlineMessage {
            Layout.fillWidth: true
            visible: page.message !== ""
            type: page.messageType
            text: page.message
        }

        Kirigami.FormLayout {
            id: top
            enabled: !!page.cfg && !backend.busy

            QQC2.ComboBox {
                id: picker
                Kirigami.FormData.label: i18n("Profile:")
                model: page.names
                onActivated: idx => page.sel = page.names[idx]
            }
            RowLayout {
                Kirigami.FormData.label: i18n("New profile:")
                QQC2.TextField {
                    id: newName
                    Layout.preferredWidth: Kirigami.Units.gridUnit * 8
                    placeholderText: i18n("name: a-z 0-9 _ -")
                }
                QQC2.Button {
                    text: i18n("Add as a copy of %1", page.sel)
                    icon.name: "list-add"
                    enabled: !!page.p && page.validNewName(newName.text)
                    onClicked: {
                        const copy = JSON.parse(JSON.stringify(page.p));
                        copy.label = newName.text;
                        page.cfg.profiles[newName.text] = copy;
                        const n = newName.text;
                        newName.text = "";
                        page.touch();
                        page.sel = n;
                    }
                }
            }
            QQC2.Button {
                text: i18n("Delete %1", page.sel)
                icon.name: "edit-delete"
                // daily is the fallback and the active profile is in use (spec §1.2, §4)
                enabled: !!page.p && page.sel !== "daily"
                    && page.sel !== page.cfg.general.active_profile
                onClicked: {
                    delete page.cfg.profiles[page.sel];
                    page.sel = "daily";
                    page.touch();
                }
            }
        }

        Loader {
            id: editor
            Layout.fillWidth: true
            active: false
            sourceComponent: editorForm
        }
    }

    Component {
        id: editorForm

        Kirigami.FormLayout {
            enabled: !!page.p && !backend.busy

            Kirigami.Separator { Kirigami.FormData.isSection: true; Kirigami.FormData.label: page.sel }

            QQC2.TextField {
                Kirigami.FormData.label: i18n("Label:")
                text: page.p ? page.p.label : ""
                onTextEdited: { page.p.label = text; page.touch(); }
            }
            QQC2.TextField {
                Kirigami.FormData.label: i18n("Description:")
                text: page.p ? (page.p.description || "") : ""
                onTextEdited: { page.p.description = text; page.touch(); }
            }
            QQC2.ComboBox {
                Kirigami.FormData.label: i18n("Type:")
                model: [i18n("Balancing - the wear logic picks each pack's band"),
                        i18n("Fixed - one band for both packs")]
                currentIndex: page.fixed ? 1 : 0
                onActivated: idx => {
                    if ((idx === 1) === page.fixed) return;
                    if (idx === 1) {
                        page.p.bands = { all: [50, 80] };
                        page.p.balancing = false;
                        delete page.p.pins;
                    } else {
                        page.p.bands = { neutral: [50, 80], protect: [50, 60], work: [80, 90] };
                        page.p.balancing = true;
                    }
                    page.touch();
                }
            }

            BandRow { Kirigami.FormData.label: i18n("Both packs:"); visible: page.fixed; key: "all" }
            BandRow { Kirigami.FormData.label: i18n("Neutral band:"); visible: !page.fixed; key: "neutral" }
            BandRow { Kirigami.FormData.label: i18n("Protect band:"); visible: !page.fixed; key: "protect" }
            BandRow { Kirigami.FormData.label: i18n("Work band:"); visible: !page.fixed; key: "work" }

            Kirigami.Separator { Kirigami.FormData.isSection: true; Kirigami.FormData.label: i18n("Auto-revert") }

            QQC2.CheckBox {
                id: revertBox
                Kirigami.FormData.label: i18n("Revert:")
                text: i18n("Switch back automatically")
                checked: !!(page.p && page.p.revert)
                onToggled: {
                    if (checked) page.p.revert = { after_hours: 72, to: "previous" };
                    else delete page.p.revert;
                    page.touch();
                }
            }
            QQC2.SpinBox {
                visible: revertBox.checked
                Kirigami.FormData.label: i18n("After:")
                from: (page.p && page.p.revert && page.p.revert.on_ac_hours) ? 0 : 1
                to: 720
                value: (page.p && page.p.revert && page.p.revert.after_hours) ? page.p.revert.after_hours : 72
                textFromValue: (v, locale) => v === 0 ? i18n("never") : i18n("%1 h", v)
                valueFromText: (t, locale) => parseInt(t) || 0
                onValueModified: {
                    if (value > 0) page.p.revert.after_hours = value;
                    else delete page.p.revert.after_hours;
                    page.touch();
                }
            }
            QQC2.SpinBox {
                visible: revertBox.checked
                Kirigami.FormData.label: i18n("After on AC for:")
                from: 0
                to: 168
                value: (page.p && page.p.revert && page.p.revert.on_ac_hours) ? page.p.revert.on_ac_hours : 0
                textFromValue: (v, locale) => v === 0 ? i18n("never") : i18n("%1 h", v)
                valueFromText: (t, locale) => parseInt(t) || 0
                onValueModified: {
                    if (value > 0) {
                        page.p.revert.on_ac_hours = value;
                    } else {
                        delete page.p.revert.on_ac_hours;
                        // a revert with no trigger is rejected by the tool
                        if (!page.p.revert.after_hours) page.p.revert.after_hours = 72;
                    }
                    page.touch();
                }
            }
            QQC2.ComboBox {
                id: revertTo
                visible: revertBox.checked
                Kirigami.FormData.label: i18n("Revert to:")
                model: [i18n("the previous profile")].concat(page.names.filter(n => n !== page.sel))
                currentIndex: {
                    const to = (page.p && page.p.revert) ? page.p.revert.to : "previous";
                    if (to === "previous") return 0;
                    const i = page.names.filter(n => n !== page.sel).indexOf(to);
                    return i < 0 ? 0 : i + 1;
                }
                onActivated: idx => {
                    page.p.revert.to = idx === 0 ? "previous" : page.names.filter(n => n !== page.sel)[idx - 1];
                    page.touch();
                }
            }

            Kirigami.Separator {
                visible: !page.fixed
                Kirigami.FormData.isSection: true
                Kirigami.FormData.label: i18n("Pins (override the wear logic)")
            }
            PinRow { Kirigami.FormData.label: "BAT0:"; visible: !page.fixed; slot: "BAT0" }
            PinRow { Kirigami.FormData.label: "BAT1:"; visible: !page.fixed; slot: "BAT1" }
        }
    }
}
```

- [ ] **Step 2: Verify** — `python3 plasmoid/tools/load-page.py plasmoid/package/contents/ui/configProfiles.qml` → `ok`. If the loader reports that `Kirigami.FormData.label` cannot be set on the inline-component instances (`BandRow { Kirigami.FormData.label: … }`), move the label into a wrapping `RowLayout { Kirigami.FormData.label: …; BandRow { … } }` for each of the six uses. Then the standard `plasmawindowed` check from the Global Constraints → nothing.

  Manual walkthrough (spec §8) — the user does this after Task 9 since sessions here cannot click: in `plasmawindowed com.chiefgyk3d.dellbatterybalance`, right-click → Configure…, Profiles page: change the travel work band to 80–92, Apply → polkit prompt → "Profiles applied."; `dell-battery-balance profile show travel` prints `bands.work = [80, 92]`.

- [ ] **Step 3: Commit**

```bash
git add plasmoid
git commit -m "Applet: Profiles config page (add/delete, label, type, bands, revert, pins)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Packs page

**Files:**
- Create: `plasmoid/package/contents/ui/configPacks.qml`

**Interfaces:**
- Consumes: `ConfigBackend.loadStatus()/act()` (Task 5); `status --json` keys `packs[] = {name, efc, calendar_score, bench_hours, in_slot, retired, removed_at_soc?}` and `pending[slot] = {guess, reason, previous_pack, …}` (Plan B).
- Polkit classes: `pack same|assign|new` → control (`configure=false`); `pack rename|retire|unretire` → configure (`configure=true`).

- [ ] **Step 1: `configPacks.qml`**

```qml
/*
 * Copyright (C) 2026 ChiefGyk3D
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM

KCM.SimpleKCM {
    id: page

    property var info: null
    property string message: ""
    property int messageType: Kirigami.MessageType.Information

    function validPackName(s) {
        return /^[A-Za-z0-9_-]{1,16}$/.test(s || "");
    }

    function where(pk) {
        if (pk.in_slot) return i18n("in %1", pk.in_slot);
        if (pk.retired) return i18n("retired");
        return i18n("on the bench, %1 h", Math.round(pk.bench_hours || 0));
    }

    ConfigBackend {
        id: backend
        onStatusLoaded: i => { page.info = i; }
        onActionDone: { page.message = ""; backend.loadStatus(); }
        onFailed: m => {
            page.message = m;
            page.messageType = Kirigami.MessageType.Error;
        }
        onCancelled: {
            page.message = i18n("Cancelled.");
            page.messageType = Kirigami.MessageType.Information;
        }
    }
    Component.onCompleted: backend.loadStatus()

    ColumnLayout {
        spacing: Kirigami.Units.smallSpacing

        Kirigami.InlineMessage {
            Layout.fillWidth: true
            visible: page.message !== ""
            type: page.messageType
            text: page.message
        }
        QQC2.Label {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            opacity: 0.7
            font: Kirigami.Theme.smallFont
            text: i18n("Changes on this page take effect immediately; each one asks for authorisation. The two packs are physically identical, so identity is only ever what you confirm here or in the popup.")
        }

        // ---- pending identity questions -------------------------------
        Repeater {
            model: (page.info && page.info.pending)
                ? ["BAT0", "BAT1"].filter(s => !!page.info.pending[s]) : []
            delegate: ColumnLayout {
                id: ask
                required property string modelData
                readonly property var q: page.info.pending[modelData]
                Layout.fillWidth: true

                Kirigami.InlineMessage {
                    Layout.fillWidth: true
                    visible: true
                    type: Kirigami.MessageType.Warning
                    text: {
                        const why = ask.q.reason === "insert" ? i18n("A pack was inserted") : i18n("The reading jumped");
                        const prev = ask.q.previous_pack ? i18n(" (was %1)", ask.q.previous_pack) : "";
                        const g = ask.q.guess === "same" ? i18n("probably the same pack") : i18n("not sure which pack");
                        return i18n("%1 in %2%3 - %4. Which pack is this?", why, ask.modelData, prev, g);
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    QQC2.Button {
                        visible: !!ask.q.previous_pack
                        text: i18n("Same (%1)", ask.q.previous_pack || "")
                        icon.name: "dialog-ok"
                        enabled: !backend.busy
                        onClicked: backend.act("pack same " + ask.modelData, false)
                    }
                    QQC2.ComboBox {
                        id: known
                        Layout.fillWidth: true
                        model: page.info.packs
                            .filter(pk => !pk.retired && (!pk.in_slot || pk.in_slot === ask.modelData))
                            .map(pk => pk.name)
                        enabled: !backend.busy && count > 0
                    }
                    QQC2.Button {
                        text: i18n("This one")
                        enabled: !backend.busy && known.count > 0
                        onClicked: backend.act("pack assign " + ask.modelData + " " + known.currentText, false)
                    }
                    QQC2.TextField {
                        id: newName
                        Layout.preferredWidth: Kirigami.Units.gridUnit * 6
                        placeholderText: i18n("new name")
                    }
                    QQC2.Button {
                        text: i18n("New")
                        enabled: !backend.busy && page.validPackName(newName.text)
                        onClicked: { backend.act("pack new " + ask.modelData + " " + newName.text, false); newName.text = ""; }
                    }
                }
            }
        }

        Kirigami.Separator { Layout.fillWidth: true }

        // ---- known packs ----------------------------------------------
        QQC2.Label {
            visible: !!(page.info && page.info.packs && page.info.packs.length === 0)
            text: i18n("No packs registered yet. Answer an identity question first.")
            opacity: 0.7
        }
        Repeater {
            model: (page.info && page.info.packs) ? page.info.packs : []
            delegate: RowLayout {
                id: row
                required property var modelData
                Layout.fillWidth: true

                QQC2.Label {
                    text: row.modelData.name
                    font.weight: Font.Bold
                    opacity: row.modelData.retired ? 0.6 : 1.0
                }
                QQC2.Label {
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                    opacity: 0.8
                    text: i18n("%1 EFC, calendar %2, %3",
                               Number(row.modelData.efc).toFixed(2),
                               Number(row.modelData.calendar_score).toFixed(1),
                               page.where(row.modelData))
                }
                QQC2.TextField {
                    id: rename
                    Layout.preferredWidth: Kirigami.Units.gridUnit * 6
                    placeholderText: i18n("rename to")
                }
                QQC2.Button {
                    text: i18n("Rename")
                    icon.name: "edit-rename"
                    enabled: !backend.busy && page.validPackName(rename.text) && rename.text !== row.modelData.name
                    onClicked: { backend.act("pack rename " + row.modelData.name + " " + rename.text, true); rename.text = ""; }
                }
                QQC2.Button {
                    text: row.modelData.retired ? i18n("Unretire") : i18n("Retire")
                    icon.name: row.modelData.retired ? "edit-undo" : "archive-remove"
                    // a pack in a slot cannot be retired (spec §4)
                    enabled: !backend.busy && (row.modelData.retired || !row.modelData.in_slot)
                    onClicked: backend.act((row.modelData.retired ? "pack unretire " : "pack retire ") + row.modelData.name, true)
                }
            }
        }

        QQC2.Button {
            text: i18n("Refresh")
            icon.name: "view-refresh"
            enabled: !backend.busy
            onClicked: backend.loadStatus()
        }
    }
}
```

- [ ] **Step 2: Verify** — `python3 plasmoid/tools/load-page.py plasmoid/package/contents/ui/configPacks.qml` → `ok`; standard `plasmawindowed` check → nothing.

- [ ] **Step 3: Commit**

```bash
git add plasmoid
git commit -m "Applet: Packs config page (rename, retire/unretire, pending identity questions)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Notifications

**Files:**
- Create: `plasmoid/notifyrc/dell_battery_balance.notifyrc`
- Modify: `plasmoid/package/contents/ui/main.qml`, `install.sh`, `uninstall.sh`

**Interfaces:**
- Consumes: event ids (Task 1); `Plasmoid.configuration.notify` / `lastNotifiedEventId` (Task 3); event kinds and detail strings as written by `dbb`: `pack` + `"occupancy change"` (registry.observe), `profile` + `"(revert:"` (cli.cmd_tick), `firmware` (apply.apply_bands), `warning` (registry, "removed at N% - discharge…").
- Produces: notifyrc component `dell_battery_balance` with events `identityPending`, `revertFired`, `firmwareMismatch`, `packRemovedHigh`; `root.raiseNotifications()` called after every successful status parse.

- [ ] **Step 1: notifyrc** — `plasmoid/notifyrc/dell_battery_balance.notifyrc`:

```ini
[Global]
Name=Dell Battery Balance
Comment=Wear tracking and charge ceilings for dual-battery Dell laptops
IconName=battery-profile-powersave

[Event/identityPending]
Name=Pack identity question
Comment=A pack was inserted or swapped and needs to be identified
Action=Popup

[Event/revertFired]
Name=Profile reverted
Comment=A temporary profile switched back automatically
Action=Popup

[Event/firmwareMismatch]
Name=Firmware disagrees
Comment=The charge ceiling read back from firmware does not match what was requested
Action=Popup

[Event/packRemovedHigh]
Name=Pack removed at high charge
Comment=A pack was removed above 70% and should be discharged before storage
Action=Popup
```

- [ ] **Step 2: `install.sh`** — after the two `install -Dm644 … .policy` lines add:

```bash
install -Dm644 "$src/plasmoid/notifyrc/dell_battery_balance.notifyrc" /usr/share/knotifications6/dell_battery_balance.notifyrc
```

`uninstall.sh` — next to the `rm -f /usr/share/polkit-1/actions/…` lines add:

```bash
rm -f /usr/share/knotifications6/dell_battery_balance.notifyrc
```

- [ ] **Step 3: `main.qml`** — add the import `import org.kde.notification as KNotification` after the plasma5support import, then add inside `PlasmoidItem`, after `fmtNum`:

```qml
    // One Notification object per raised event; autoDelete frees it once
    // shown. The component name matches /usr/share/knotifications6/
    // dell_battery_balance.notifyrc, which install.sh places.
    Component {
        id: notifier
        KNotification.Notification {
            componentName: "dell_battery_balance"
            iconName: "battery-profile-powersave"
            autoDelete: true
        }
    }

    // Map a state event to [notifyrc event id, title]; null when it is
    // not one of the four conditions we notify for (spec §6.3).
    function notifyEventFor(ev) {
        const d = ev.detail || "";
        if (ev.kind === "pack" && d.indexOf("occupancy change") !== -1)
            return ["identityPending", i18n("Which pack is this?")];
        if (ev.kind === "profile" && d.indexOf("(revert:") !== -1)
            return ["revertFired", i18n("Profile reverted")];
        if (ev.kind === "firmware")
            return ["firmwareMismatch", i18n("Firmware disagrees with the requested ceiling")];
        if (ev.kind === "warning")
            return ["packRemovedHigh", i18n("Pack removed at high charge")];
        return null;
    }

    // Root has no session bus, so the applet raises notifications from the
    // events it sees in status. Once per event id: the high-water mark
    // lives in KConfig, so a restart does not re-notify. -1 means this
    // applet instance has never run; adopt the backlog silently.
    function raiseNotifications() {
        if (!info || !info.events) return;
        const evs = info.events.filter(e => typeof e.id === "number");
        if (evs.length === 0) return;
        const seen = plasmoid.configuration.lastNotifiedEventId;
        let high = seen;
        for (const ev of evs) {
            if (ev.id <= seen) continue;
            if (ev.id > high) high = ev.id;
            if (seen < 0 || !plasmoid.configuration.notify) continue;
            const m = notifyEventFor(ev);
            if (!m) continue;
            const n = notifier.createObject(root, { eventId: m[0], title: m[1], text: ev.detail });
            n.sendEvent();
        }
        if (high !== seen) plasmoid.configuration.lastNotifiedEventId = high;
    }
```

In `handleResult`, inside the `status --json` branch, change the `try` block to:

```qml
            try {
                info = JSON.parse(stdout);
                lastError = "";
                raiseNotifications();
            } catch (e) {
                lastError = i18n("Could not parse status output");
            }
```

- [ ] **Step 4: Verify** — `bash -n install.sh uninstall.sh`; standard `plasmawindowed` check → nothing. The notifyrc is not installed until the user runs `sudo ./install.sh` (sudo is denied in these sessions), so `sendEvent()` shows nothing yet; the code path is exercised by the loop itself — confirm no QML errors with a fresh `lastNotifiedEventId` (the `plasmawindowed` instance uses its own config, so `-1` → adopts the backlog; the log must be clean). To see a real notification after install: `sudo ./install.sh`, restart plasmashell (`systemctl --user restart plasma-plasmashell.service`), then `pkexec --user dell-battery-balance /usr/local/libexec/dbb-control profile set travel --for 1m`; within a poll after the revert fires, "Profile reverted" pops.

- [ ] **Step 5: Commit**

```bash
git add plasmoid install.sh uninstall.sh
git commit -m "Applet: desktop notifications once per event id; notifyrc installed system-wide

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Version 0.3.0, README, spec amendment, applet metadata

**Files:**
- Modify: `dbb/__init__.py`, `plasmoid/package/metadata.json`, `README.md` (CLI reference, Plasma applet, State, Roadmap, Known limits, Verify), `docs/superpowers/specs/2026-09-12-profiles-packs-config-design.md` (§1.3, §6.3)

- [ ] **Step 1: Versions** — `dbb/__init__.py`: `VERSION = "0.3.0"`. `plasmoid/package/metadata.json`: `"Version": "1.1"`. `grep -rn '0\.2\.0' tests README.md` → update any pin found (none expected).

- [ ] **Step 2: Spec amendment** — in §1.3 replace the applet bullet with:

```markdown
- Applet config dialog: submits a complete config as a JSON document with the
  TOML's exact shape (so only Python ever emits TOML) via
  `pkexec --user dell-battery-balance dbb-configure config apply --json <path>`.
  The candidate is staged through `mktemp /tmp/dbb-config-XXXXXX.json`,
  mode 644, and removed after the call — `$XDG_RUNTIME_DIR` is mode 700 and
  unreadable by the service account. The file carries no secrets (bands and
  a *path* to the BIOS password file). Root validates before installing and
  refuses on any error, printing the reason for the dialog to display. The
  applet never writes `/etc` itself.
```

In §6.3 replace "Event ids seen are kept in KConfig so a restart does not re-notify." with "Events carry monotonic ids; the applet keeps the highest id it has notified in KConfig (`lastNotifiedEventId`), so a restart does not re-notify and a fresh install adopts the backlog silently."

- [ ] **Step 3: README** —

  CLI reference rows: change `config get [key]` → `` `config get [key] [--json]` `` "print the whole config or one dotted key; `--json` is what the applet's config dialog reads"; `config validate <path>` → `` `config validate [--json] <path>` ``; `config apply <path>` → `` `config apply [--json] <path>` `` "replace the whole config from a TOML file, or with `--json` a JSON document of the same shape (must be complete and valid)".

  Plasma applet section: after the "Field mode is flagged…" paragraph add:

```markdown
### Configuring from the applet

Right-click the widget → Configure. Four pages:

- **Display** — text beside the icon (none / profile label / EFC
  divergence; only visible when the widget sits in a panel, the system tray
  keeps items icon-only), poll interval, whether pack details start
  expanded, and whether to raise notifications. Saved in the applet's own
  KConfig.
- **Profiles** — pick a profile, add one as a copy, delete one (`daily` and
  the active profile are protected), and edit its label, type (balancing or
  fixed), bands, auto-revert triggers and per-slot pins.
- **Packs** — rename, retire or un-retire packs, and answer pending identity
  questions.
- **General** — balance deadband, automatic balancing, bench temperature.

Profiles and General edit a working copy and submit the *whole* config on
Apply/OK through the configure-class polkit action, so one polkit prompt
per apply. The candidate is staged as a JSON file under `/tmp` (mode 644,
removed straight after; it carries no secrets) because the service account
cannot read the user's runtime directory. The tool validates before
installing and refuses on any error; use **Apply** rather than OK to see
the error message, since OK closes the dialog. Packs actions run
immediately, one prompt each.

### Notifications

The applet raises a desktop notification once per state event for: a
pending pack identity question, an auto-revert firing, a firmware
read-back mismatch, and a pack removed above 70%. They come from the
applet (root has no session bus), so they need the widget running and lag
by at most one poll interval. The event definitions live in
`/usr/share/knotifications6/dell_battery_balance.notifyrc`, which
`install.sh` places — re-run `sudo ./install.sh` when upgrading from 0.2,
then restart the shell so the applet package reloads:

```sh
systemctl --user restart plasma-plasmashell.service
```

Notification sounds/popups per event are configurable in System Settings
→ Notifications → Application-specific → Dell Battery Balance.
```

  State section: add "Events carry an `id` (monotonic, never reused) since 0.3; a 0.2 state file gets its existing events numbered once on first load."

  Verify block: add after the `dell-battery-balance status` lines:

```sh
dell-battery-balance --version
    # 0.3.0
ls /usr/share/knotifications6/dell_battery_balance.notifyrc
dell-battery-balance config get --json | python3 -m json.tool > /dev/null
```

  Roadmap: replace the "What remains…" sentence with "The applet's config dialog and notifications landed in 0.3, completing the spec. Open follow-ups are tracked in the issues (`pack swap`, `--for` clipping, sysfs-hiccup pending)."

  Known limits: add three bullets — tray text only outside the system tray; notifications need the applet and lag one poll; a config error after OK is not shown (use Apply).

  Tests section: mention `plasmoid/tools/load-page.py` as the headless check for config pages (needs `python3-pyqt6`; not part of the unit suite).

- [ ] **Step 4: Verify** — full suite with `-W error::ResourceWarning`; `./dell-battery-balance --version` → `0.3.0`; `python3 plasmoid/tools/load-page.py plasmoid/package/contents/ui/config*.qml plasmoid/package/contents/ui/ConfigBackend.qml` → all `ok`; standard `plasmawindowed` check → nothing; `bash -n install.sh uninstall.sh`; `git grep -nE 'callsign|serial' -- README.md docs | grep -vi 'nvme\|never'` → nothing new.

- [ ] **Step 5: Commit**

```bash
git add dbb/__init__.py plasmoid/package/metadata.json README.md docs/superpowers/specs
git commit -m "0.3.0: README for the config dialog and notifications; spec §1.3/§6.3 amended to the shipped mechanism

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Profile shortcut, full revert control, sudo-safe files, documented command surface

*(Added mid-execution, after Task 2, at the user's request: `sudo dell-battery-balance profile field` answered with argparse's "invalid choice"; the field profile's auto-revert must be tweakable and disable-able with the reasons documented; and tests must keep the documented command surface honest so this cannot recur. Executed right after Task 2, before the applet tasks.)*

**Files:**
- Modify: `dbb/cli.py` (`expand_profile_shortcut`, `_one_off_hours`, `_require_profile`, `cmd_profile_set/show/edit/delete`, `cmd_field`, parser, `main`), `dbb/policy.py` (`revert_due`), `dbb/render.py` (`_revert_info`, `fmt_status` revert line), `dbb/config.py` (`set_dotted`, `save`), `dbb/state.py` (`save_state`, `append_log` modes), `install.sh` (state dir mode), `plasmoid/package/contents/ui/FullRepresentation.qml` (footer revert label), `README.md`, spec §4
- Create: `tests/test_cli_surface.py`
- Test: `tests/test_cli.py` (class `Profiles`, class `ConfigCmd`), `tests/test_policy.py`

**Interfaces:**
- Produces: `cli.expand_profile_shortcut(argv: list[str]) -> list[str]` (pure; `profile <name>` → `profile set <name>` at the command position only); `cli.PROFILE_SUBCOMMANDS`; `profile set <name> [--for D | --stay]`, `field [--for D | --stay]`; `state["one_off_revert_hours"]` semantics: `None` = use the profile's own `[revert]`, `0.0` = `--stay` (no automatic revert this switch), `> 0` = revert after that many hours *replacing* the profile's triggers; `status --json` `revert` gains `"stay": bool`; `config.set_dotted` accepts `profiles.X.revert=none` and `profiles.X.revert.after_hours|on_ac_hours=none|0`.
- Consumes: nothing from Tasks 3–9. Task 4 later edits other regions of `FullRepresentation.qml`.

- [ ] **Step 1: Failing tests.** Append inside class `Profiles` in `tests/test_cli.py`:

```python
    def test_profile_shortcut_is_profile_set(self):
        code, _, err = self.run_cli("profile", "field")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.status()["profile"]["name"], "field")

    def test_profile_shortcut_survives_the_wrapper_prefix(self):
        code, _, err = self.run_cli("--polkit-class", "control", "--", "profile", "travel")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.status()["profile"]["name"], "travel")

    def test_profile_shortcut_leaves_later_positionals_alone(self):
        # a pack literally named "profile" must not be rewritten into "profile set"
        code, _, err = self.run_cli("pack", "rename", "profile", "x")
        self.assertNotEqual(code, 0)
        self.assertNotIn("invalid choice", err)
        self.assertNotIn("usage:", err)

    def test_unknown_profile_lists_the_choices(self):
        code, _, err = self.run_cli("profile", "nosuch")
        self.assertNotEqual(code, 0)
        self.assertIn("daily", err)
        self.assertIn("field", err)
        self.assertNotIn("invalid choice", err)

    def test_for_longer_than_the_profile_after_hours_is_honoured(self):
        # issue #1: --for is an override of the profile's triggers, not a floor
        self.run_cli("profile", "set", "field", "--for", "96h")
        j = self.status()
        self.assertAlmostEqual(j["revert"]["after_hours_left"], 96.0, places=1)
        self.assertIsNone(j["revert"]["on_ac_hours_left"])
        self.assertFalse(j["revert"]["stay"])

    def test_stay_disables_revert_for_this_switch(self):
        code, _, err = self.run_cli("profile", "set", "field", "--stay")
        self.assertEqual(code, 0, err)
        j = self.status()
        self.assertTrue(j["revert"]["stay"])
        self.assertIsNone(j["revert"]["after_hours_left"])
        self.assertIsNone(j["revert"]["on_ac_hours_left"])
        code, out, _ = self.run_cli("status")
        self.assertIn("stays on field", out)

    def test_for_and_stay_are_exclusive(self):
        code, _, err = self.run_cli("profile", "set", "field", "--for", "8h", "--stay")
        self.assertNotEqual(code, 0)

    def test_field_alias_takes_for_and_stay(self):
        self.run_cli("field", "--stay")
        self.assertTrue(self.status()["revert"]["stay"])
        self.run_cli("restore")
        self.run_cli("field", "--for", "3d")
        self.assertAlmostEqual(self.status()["revert"]["after_hours_left"], 72.0, places=1)

    def test_revert_none_removes_the_table(self):
        code, _, err = self.run_cli("profile", "edit", "field", "revert=none")
        self.assertEqual(code, 0, err)
        code, out, _ = self.run_cli("profile", "show", "field")
        self.assertNotIn("revert.", out)
        self.run_cli("profile", "set", "field")
        self.assertIsNone(self.status()["revert"])

    def test_dropping_the_last_trigger_drops_the_table(self):
        code, _, err = self.run_cli("profile", "edit", "field", "revert.on_ac_hours=none")
        self.assertEqual(code, 0, err)
        code, out, _ = self.run_cli("profile", "show", "field")
        self.assertIn("revert.after_hours = 72", out)
        self.assertNotIn("on_ac_hours", out)
        code, _, err = self.run_cli("profile", "edit", "field", "revert.after_hours=0")
        self.assertEqual(code, 0, err)
        code, out, _ = self.run_cli("profile", "show", "field")
        self.assertNotIn("revert.", out)

    def test_trigger_can_be_added_to_a_profile_without_revert(self):
        code, _, err = self.run_cli("profile", "edit", "travel", "revert.after_hours=24")
        self.assertEqual(code, 0, err)
        code, out, _ = self.run_cli("profile", "show", "travel")
        self.assertIn("revert.after_hours = 24", out)
        self.run_cli("profile", "set", "travel")
        self.assertAlmostEqual(self.status()["revert"]["after_hours_left"], 24.0, places=1)
```

Append inside class `ConfigCmd`:

```python
    def test_backup_is_replaced_not_rewritten(self):
        # A .bak left root-owned by a stray `sudo` run must not lock the
        # service account out: the backup is renamed into place, never
        # opened for writing.
        self.run_cli("config", "set", "general.deadband_efc=0.3")
        self.run_cli("config", "set", "general.deadband_efc=0.35")
        bak = os.path.join(os.environ["DBB_CONFIG_DIR"], "config.toml.bak")
        self.assertTrue(os.path.exists(bak))
        os.chmod(bak, 0o444)
        code, _, err = self.run_cli("config", "set", "general.deadband_efc=0.4")
        self.assertEqual(code, 0, err)
        with open(bak) as fh:
            self.assertIn("deadband_efc = 0.35", fh.read())

    def test_state_and_sample_log_are_group_writable(self):
        self.run_cli("sample")
        sd = os.environ["DBB_STATE_DIR"]
        self.assertEqual(os.stat(os.path.join(sd, "state.json")).st_mode & 0o777, 0o664)
        logs = [f for f in os.listdir(sd) if f.startswith("samples-")]
        self.assertEqual(len(logs), 1)
        self.assertEqual(os.stat(os.path.join(sd, logs[0])).st_mode & 0o777, 0o664)
```

Append to `tests/test_policy.py` (check its imports/harness first and match them; the assertions are what matter):

```python
class OneOffRevert(unittest.TestCase):
    def setUp(self):
        for m in list(sys.modules):
            if m.startswith("dbb"):
                del sys.modules[m]
        from dbb import policy, config
        self.policy = policy
        self.field = config.default_config()["profiles"]["field"]   # after 72 h, on AC 12 h

    def test_for_replaces_both_profile_triggers(self):
        st = {"profile_switched_ts": 0.0, "one_off_revert_hours": 96.0, "ac_run_start_ts": 0.0}
        self.assertIsNone(self.policy.revert_due(self.field, st, 80 * 3600))    # profile's 72 h / 12 h AC do NOT fire
        self.assertEqual(self.policy.revert_due(self.field, st, 97 * 3600), "after_hours")

    def test_stay_never_reverts(self):
        st = {"profile_switched_ts": 0.0, "one_off_revert_hours": 0.0, "ac_run_start_ts": 0.0}
        self.assertIsNone(self.policy.revert_due(self.field, st, 1000 * 3600))

    def test_profile_triggers_apply_when_no_one_off(self):
        st = {"profile_switched_ts": 0.0, "one_off_revert_hours": None, "ac_run_start_ts": None}
        self.assertEqual(self.policy.revert_due(self.field, st, 73 * 3600), "after_hours")
        st["ac_run_start_ts"] = 0.0
        self.assertEqual(self.policy.revert_due(self.field, st, 13 * 3600), "on_ac_hours")
```

Create `tests/test_cli_surface.py` — the guard that keeps the documented surface and the parser in step:

```python
"""Every command the README documents must parse, and every command the
parser knows must be documented. This is what makes `profile field`-style
surprises a test failure instead of a bug report."""
import argparse
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

README = os.path.join(os.path.dirname(__file__), os.pardir, "README.md")

# One entry per documented form, placeholders filled with plausible values.
DOCUMENTED = [
    ["tick"], ["sample"], ["status"], ["status", "--json"], ["report"],
    ["balance"], ["balance", "--apply"],
    ["profile", "list"], ["profile", "show", "daily"],
    ["profile", "set", "field"], ["profile", "set", "field", "--for", "8h"],
    ["profile", "set", "field", "--stay"], ["profile", "field"],
    ["profile", "create", "trip", "--from", "travel"],
    ["profile", "edit", "field", "revert.after_hours=120"], ["profile", "edit", "field", "revert=none"],
    ["profile", "delete", "trip"],
    ["config", "get"], ["config", "get", "general.deadband_efc"], ["config", "get", "--json"],
    ["config", "set", "general.deadband_efc=0.4"],
    ["config", "validate", "x.toml"], ["config", "validate", "--json", "x.json"],
    ["config", "apply", "x.toml"], ["config", "apply", "--json", "x.json"],
    ["field"], ["field", "--for", "3d"], ["field", "--stay"], ["restore"],
    ["pack", "list"], ["pack", "assign", "BAT0", "A"], ["pack", "new", "BAT0", "A"],
    ["pack", "same", "BAT0"], ["pack", "reassign", "3", "A"], ["pack", "rename", "A", "B"],
    ["pack", "retire", "A"], ["pack", "unretire", "A"],
    ["reset", "--slot", "BAT0"], ["reset", "--pack", "A"], ["reset", "--all"],
    ["--polkit-class", "control", "--", "profile", "field"],
]

ROW = re.compile(r"^\| `([^`]+)`")
GROUPS = ("profile", "config", "pack")


def _subparser_choices(parser):
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            return a.choices
    return {}


def parser_commands(parser):
    """{('tick',), ('profile', 'set'), ...} straight from argparse."""
    out = set()
    for name, sub in _subparser_choices(parser).items():
        inner = _subparser_choices(sub)
        if inner:
            out.update((name, k) for k in inner)
        else:
            out.add((name,))
    return out


def readme_commands():
    with open(README) as fh:
        text = fh.read()
    section = text.split("### CLI reference", 1)[1].split("\n## ", 1)[0]
    out = set()
    for line in section.splitlines():
        m = ROW.match(line)
        if not m:
            continue
        words = []
        for w in m.group(1).split():
            if w[0] in "<[-\\|" or "=" in w:
                break
            words.append(w)
        out.add(tuple(words[:2]) if words[0] in GROUPS else (words[0],))
    return out


class CommandSurface(unittest.TestCase):
    def setUp(self):
        for m in list(sys.modules):
            if m.startswith("dbb"):
                del sys.modules[m]
        from dbb import cli
        self.cli = cli
        self.parser = cli.build_parser()

    def test_every_documented_form_parses(self):
        for argv in DOCUMENTED:
            with self.subTest(argv=" ".join(argv)):
                try:
                    self.parser.parse_args(self.cli.expand_profile_shortcut(argv))
                except SystemExit as e:
                    self.fail(f"{' '.join(argv)!r} does not parse (exit {e.code})")

    def test_readme_table_matches_the_parser(self):
        documented = readme_commands()
        known = parser_commands(self.parser)
        # `profile <name>` is the shortcut row; it is not a subparser
        self.assertEqual(documented - known - {("profile",)}, set(),
                         "README documents commands the parser does not have")
        self.assertEqual(known - documented, set(),
                         "parser has commands the README does not document")
```

- [ ] **Step 2: Run to verify failure** — `python3 -m unittest tests.test_cli.Profiles tests.test_cli.ConfigCmd tests.test_policy tests.test_cli_surface -v` → the shortcut/`--stay`/`revert=none` tests fail on argparse or `ConfigError`, the surface test fails on `expand_profile_shortcut` missing, the `.bak` test fails with `PermissionError`, the mode test with `0o644 != 0o664`.

- [ ] **Step 3: Implement.**

`dbb/cli.py` — near the top, after `CONFIGURE_CLASS`:

```python
PROFILE_SUBCOMMANDS = ("list", "show", "set", "create", "edit", "delete")


def expand_profile_shortcut(argv):
    """`profile <name>` means `profile set <name>`: it is the first thing
    people type, and argparse's "invalid choice" reply taught nobody the
    real spelling. Only the command position is rewritten (after the
    wrappers' `--polkit-class X --` prefix), never a later positional, so a
    pack or profile literally named "profile" is untouched."""
    out = list(argv)
    i = 0
    while i < len(out):
        tok = out[i]
        if tok == "--":
            i += 1
            break
        if tok == "--polkit-class":
            i += 2
            continue
        if tok.startswith("--polkit-class="):
            i += 1
            continue
        break
    if (i + 1 < len(out) and out[i] == "profile"
            and out[i + 1] not in PROFILE_SUBCOMMANDS
            and not out[i + 1].startswith("-")):
        out.insert(i + 1, "set")
    return out


def _one_off_hours(args):
    """--stay -> 0.0 (no automatic revert this switch); --for -> hours;
    neither -> None (the profile's own [revert] table applies)."""
    if getattr(args, "stay", False):
        return 0.0
    if getattr(args, "for_", None):
        try:
            return parse_duration(args.for_)
        except ValueError as e:
            die(f"error: {e}")
    return None


def _require_profile(cfg, name):
    if name not in cfg["profiles"]:
        die(f"error: no profile {name!r}. Profiles: {', '.join(sorted(cfg['profiles']))}")
```

Replace `cmd_profile_set` and `cmd_field`:

```python
def cmd_profile_set(args):
    state, cfg, _ = _view()
    _require_profile(cfg, args.name)
    sys.exit(_switch_and_apply(cfg, state, args.name, "cli", _one_off_hours(args)))


def cmd_field(args):
    state, cfg, _ = _view()
    sys.exit(_switch_and_apply(cfg, state, "field", "cli", _one_off_hours(args)))
```

In `cmd_profile_show`, `cmd_profile_edit`, `cmd_profile_delete` replace the `if args.name not in cfg["profiles"]: die(...)` lines with `_require_profile(cfg, args.name)` (in `delete`, keep the `daily`/active checks before it).

Parser: replace the `profile set` line and the `field` line:

```python
    sp = pr.add_parser("set"); sp.add_argument("name")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--for", dest="for_", metavar="DURATION",
                   help="revert after this long (90m, 8h, 3d), replacing the profile's own triggers for this switch")
    g.add_argument("--stay", action="store_true", help="no automatic revert for this switch")
    sp.set_defaults(func=cmd_profile_set, cls="control")
    ...
    sp = sub.add_parser("field", help="alias: profile set field")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--for", dest="for_", metavar="DURATION")
    g.add_argument("--stay", action="store_true")
    sp.set_defaults(func=cmd_field, cls="control")
```

(Look at how `field` is currently registered and keep its `cls`.) In `main()`, change `args = build_parser().parse_args(argv)` to `args = build_parser().parse_args(expand_profile_shortcut(raw))`.

`dbb/policy.py` — `revert_due` becomes:

```python
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
    if on_ac and run is not None and (now - run) / 3600.0 >= on_ac:
        return "on_ac_hours"
    return None
```

`dbb/render.py` — `_revert_info` becomes:

```python
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
    return {
        "to": policy.resolve_revert_target(cfg, name),
        "stay": stay,
        "after_hours_left": (after - (now - switched) / 3600.0) if (after and switched is not None) else None,
        "on_ac_hours_left": (on_ac - (now - run) / 3600.0) if (on_ac and run is not None) else None,
    }
```

In `fmt_status`, where the revert line is printed from this dict, print `f"revert: none - stays on {name} until you change it"` when `stay` is true (keep the existing countdown line otherwise).

`dbb/config.py` — at the top of `set_dotted`:

```python
OFF_WORDS = ("none", "off", "false", "")


def set_dotted(cfg, key, raw):
    parts = key.split(".")
    # Removing auto-revert is a first-class edit, not a validation trap:
    #   profiles.X.revert=none                      drops the whole table
    #   profiles.X.revert.after_hours=none (or 0)   drops that trigger; when no
    #   trigger is left the table goes too, since a revert with no trigger is
    #   meaningless (validate() rejects it).
    word = raw.strip().lower()
    if len(parts) == 3 and parts[0] == "profiles" and parts[2] == "revert" and word in OFF_WORDS:
        cfg.get("profiles", {}).get(parts[1], {}).pop("revert", None)
        return
    if (len(parts) == 4 and parts[0] == "profiles" and parts[2] == "revert"
            and parts[3] in ("after_hours", "on_ac_hours") and word in OFF_WORDS + ("0", "0.0")):
        prof = cfg.get("profiles", {}).get(parts[1], {})
        rv = prof.get("revert")
        if rv:
            rv.pop(parts[3], None)
            if not rv.get("after_hours") and not rv.get("on_ac_hours"):
                del prof["revert"]
        return
    ... (existing body unchanged)
```

Check `validate()` (the `revert` block around line 145): if it requires `to`, then when the existing body creates a fresh `revert` table (adding a trigger to a profile that had none), default it — after the `for p in parts[:-1]` walk, if `parts[:3] == ["profiles", name, "revert"]` and `"to" not in node`, set `node.setdefault("to", "previous")`. If `validate()` already treats `to` as optional (`resolve_revert_target` defaults to `"previous"`), leave it alone; say which in the report.

`save()` — replace the backup block:

```python
    if path.exists():
        # Rename the backup into place rather than opening it for writing: a
        # .bak left root-owned by a stray `sudo` run would otherwise raise
        # PermissionError for the service account on every later save.
        bak = path.with_name(path.name + ".bak")
        bak_tmp = path.with_name(path.name + ".bak.tmp")
        bak_tmp.write_bytes(path.read_bytes())
        os.replace(bak_tmp, bak)
        try:
            os.chmod(bak, 0o664)
        except OSError:
            pass
```

`dbb/state.py` — `save_state`: `_make_readable(STATE_FILE, 0o664)`; `append_log`: `_make_readable(log, 0o664)`; update the comment above them: "The service account writes; root may too (a `sudo` run), and with the state directory setgid to the service group a root-created file stays group-writable, so one stray root run never locks the service account out. Any user may read."

`install.sh` — change the state-dir line to `install -d -m2775 -o "$SVC" -g "$SVC" /var/lib/$SVC` and after the `chown -R` add `find /var/lib/$SVC -type f -exec chmod 664 {} +` (repairs files an earlier root run left 0644).

`plasmoid/package/contents/ui/FullRepresentation.qml` — footer revert `Label`: replace its `visible`/`text` with

```qml
                visible: text !== ""
                font: Kirigami.Theme.smallFont
                text: {
                    const r = root.info ? root.info.revert : null;
                    if (!r) return "";
                    if (r.stay) return i18n("No automatic revert - stays on %1 until you change it", root.info.profile.label);
                    return parts.length > 0 ? i18n("Reverts to %1 in %2", r.to, parts.join(i18n(" or "))) : "";
                }
```

(keep the `parts` property as is).

- [ ] **Step 4: Docs.** `README.md`:
  - Profiles table, `field` row: "Auto-reverts after 72 h, or after 12 h back on AC — both adjustable or removable, see below."
  - New subsection after the Profiles paragraph:

```markdown
### Field mode, auto-revert, and why

Field holds both packs at 90–100%. That is exactly the state the rest of
this tool exists to avoid — high state of charge is the dominant
calendar-wear input, and a rugged laptop in a bag is usually warm too — so
the failure mode of field mode is forgetting to leave it. The default
revert is therefore on: 72 h covers a conference or a long weekend in the
field, and 12 h of continuous AC means you are back at a desk. You are not
locked into either:

```sh
dell-battery-balance profile field                    # same as: profile set field
dell-battery-balance profile field --for 5d           # this switch reverts after 5 days, nothing else
dell-battery-balance profile field --stay             # this switch never reverts; you change it yourself
dell-battery-balance profile edit field revert.after_hours=120
dell-battery-balance profile edit field revert.on_ac_hours=none
dell-battery-balance profile edit field revert.to=daily
dell-battery-balance profile edit field revert=none   # never revert, permanently
dell-battery-balance profile edit travel revert.after_hours=24   # any profile can revert
```

`--for` and `--stay` belong to the switch: they replace the profile's own
triggers for that switch and are forgotten on the next one. Editing
`revert.*` changes the profile for good; `none`/`off`/`0` removes a
trigger, and a table with no trigger left is removed with it. Every
privileged form above goes through `dbb-control` (`--for`/`--stay`) or
`dbb-configure` (`profile edit`), as in Usage.
```

  - Usage: after the pkexec block, add: "Plain `sudo dell-battery-balance …` also works — root can do everything — but leaves files it creates root-owned. Since 0.3 the state directory is setgid and files are group-writable, so a stray root run no longer locks the service account out; prefer `sudo -u dell-battery-balance dell-battery-balance …` or the wrappers all the same."
  - CLI reference rows: `profile set <name> [--for <duration> \| --stay]` — "switch profiles and apply immediately; `--for` reverts after that long and `--stay` never, either one replacing the profile's own triggers for this switch"; add row `` `profile <name>` `` — "shortcut for `profile set <name>`"; `profile edit <name> key=value ...` — add "`revert=none` or `revert.after_hours=none` remove auto-revert"; `field [--for <duration> \| --stay]`.
  - Spec `§4` block: `profile set <name> [--for <duration> | --stay]`, `field [--for | --stay]`, and the line "`profile <name>` is accepted as `profile set <name>`." Under §1.2 rules add: "`revert` may be removed with `revert=none` (CLI); a trigger set to `none`/`0` is removed, and the table with it when no trigger remains."

- [ ] **Step 5: Run** — full suite with `-W error::ResourceWarning`; `bash -n install.sh`; the standard `plasmawindowed` check from the Global Constraints → nothing. On the machine, read-only: `DBB_STATE_DIR=/tmp/dbb-c10 DBB_CONFIG_DIR=/tmp/dbb-c10 ./dell-battery-balance profile nosuch` prints the profile list in the error.

- [ ] **Step 6: Commit**

```bash
git add dbb tests install.sh plasmoid README.md docs/superpowers/specs
git commit -m "CLI: profile <name> shortcut, --stay, --for as a true override (issue #1), revert=none; sudo-safe file modes; documented-surface tests

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review notes

- **Spec §1.3** editing surface: Task 5 (`ConfigBackend.apply`) + Task 2 (`config apply --json`), deviation documented in Task 9. **§4** "one JSON model": `config get --json` reuses the config dict; `state_json` carries the new fields (Task 1). **§5** events: ids (Task 1). **§6.1** remaining fields — power, voltage, temperature, ceiling in force with match indicator and read-back age, health "as reported, updates rarely", mean SoC, % time ≥ 90%, time in slot, last three events, expandable per pack: Task 4; tray text none/profile/divergence: Task 3; profile-specific icon, red dot, amber dot already shipped in Plans A/B. **§6.2** four pages with the listed contents: Tasks 3, 5, 6, 7; "Display page saves to KConfig only": Task 3. **§6.3** four notifications, once per id, kept in KConfig: Task 8 (Task 1 supplies the ids). **§7** pkexec 126/127 = cancel: `ConfigBackend.handle` (Task 5) and the existing `main.qml`. **§8** applet tests: `plasmawindowed` load per task; config pages load headlessly (Task 3 tool); the manual walkthrough of one profile edit and one identity confirmation is called out in Task 6 for the user, since sessions here cannot click.
- **Placeholder scan:** none of the forbidden patterns. Two explicit fallbacks are given with their concrete alternative (Task 4 nested Repeater → explicit label pairs; Task 6 attached `FormData.label` on inline components → wrapping RowLayout).
- **Type consistency:** `ConfigBackend` signal names (`loaded`, `statusLoaded`, `applied`, `actionDone`, `failed`, `cancelled`) and function names (`load`, `loadStatus`, `act(subcommand, configure)`, `apply(cfg)`) match their uses in Tasks 5–7. KConfig keys `trayText`, `pollInterval`, `expandDetails`, `notify`, `lastNotifiedEventId` match `main.xml`, `configDisplay.qml`, `main.qml`, `CompactRepresentation.qml`, `FullRepresentation.qml`. JSON keys `power_w`, `voltage_v`, `health_pct`, `in_slot_hours`, `version`, `events[].id` match render (Task 1) and QML (Tasks 4, 8). `notifyEventFor` ids match the notifyrc's four `[Event/…]` sections. The `-1` sentinel for `lastNotifiedEventId` is the same in `main.xml` and `raiseNotifications`.
- **Not in this plan:** issue #1 (`--for` clipping, `must_exist` OSError) and issue #2 (`pack swap`, sysfs-hiccup pending) stay parked; the README Roadmap points at them.

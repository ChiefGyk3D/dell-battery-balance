# Conference Profile, Overnight Top-off and Revert Flexibility — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship 0.4.0: a `conference` profile that charges fully by day, holds both packs at a lower band overnight and tops them off in time for a departure time (or, at the user's choice, leaves them at 100%), two quick actions for the evenings that differ, an RTC wake so the top-off happens through a closed lid, and three revert changes for every reverting profile (a still-going-out guard, a warning with `profile extend`, and `--until`).

**Architecture:** One new module, `dbb/overnight.py`, owns the night window, the departure time, the top-off estimate, a four-phase state machine that the tick advances once per sample (`step`), a pure band override the policy engine applies (`apply_phase`), and the wake-request file. Config gains an `[overnight]` table on fixed profiles and `general.topoff_resuspend`. A second root-only helper next to the grant script turns the wake request into an RTC alarm and re-suspends after a top-off wake. The revert changes live in `dbb/wear.py` (battery-stint tracking) and `dbb/policy.py` (guard, ETA, extend). Status, JSON, metrics and the applet read the new state; the applet gains the quick actions, a leave-at field, an Extend button and the Conference profile type.

**Tech Stack:** Python 3.13 stdlib only; `unittest`; POSIX `sh` for the wake helper; Plasma 6 QML; systemd `ExecStartPost=+`.

**Spec:** `docs/superpowers/specs/2026-09-14-conference-overnight-design.md` (this plan cites it as "spec §N"). Base design: `docs/superpowers/specs/2026-09-12-profiles-packs-config-design.md`.

## Global Constraints

- Python stdlib only; no third-party imports in `dbb/` or `tests/`.
- Tests: `python3 -W error::ResourceWarning -m unittest discover -s tests` passes with zero failures at the end of every task (211 tests at the start; only grows). Tests never touch `/sys`, `/etc`, `/var`, `/proc`; they use `DBB_SYSFS_ROOT`, `DBB_STATE_DIR`, `DBB_CONFIG_DIR`, `DBB_BOOT_ID` and `tests/fakesys.FakeSys`. New env overrides for the wake helper: `DBB_RTC_PATH`, `DBB_LID_GLOB`, `DBB_CONFIG_FILE`, `DBB_SUSPEND_CMD`.
- Local-clock arithmetic goes through `time.localtime`/`time.mktime` so DST is handled by libc. Tests that depend on the clock set `TZ` and call `time.tzset()` in `setUp`, restore in `tearDown`.
- Adding a general config key means adding it to BOTH `GENERAL_KEYS` and `GENERAL_OPTIONAL` with a default, or every installed `config.toml` stops validating. Validation of old configs is otherwise unchanged; the built-in `conference` profile is NOT injected into an existing config on load (spec §1.1).
- `tests/test_cli_surface.py` compares the parser with the README's `### CLI reference` table. Every task that adds a command adds its README row in the same task, and adds a `DOCUMENTED` entry.
- Nothing in `dbb/` calls `sudo`, `systemctl`, or writes outside `STATE_DIR`/`CONFIG_DIR`. The only root code is `libexec/dell-battery-balance-grant` and the new `libexec/dell-battery-balance-wake`.
- `topoff now`, `night`, `leave-at`, `profile extend`, `--until` are control class. `profile create --template` and `profile edit overnight.*` are configure class.
- Every QML file keeps the GPL-3.0-or-later SPDX header. QML verification: `kpackagetool6 --type Plasma/Applet --upgrade plasmoid/package && timeout 12 plasmawindowed com.chiefgyk3d.dellbatterybalance > /tmp/pw.log 2>&1; grep -iE 'error|TypeError|ReferenceError|Binding loop|is not a type|non-existent' /tmp/pw.log` prints nothing. Config pages additionally load through `python3 plasmoid/tools/load-page.py plasmoid/package/contents/ui/configProfiles.qml` → `ok`.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Work on branch `worktree-conference-overnight` in the worktree at `.claude/worktrees/conference-overnight`; do not push and do not merge to `main` — that is the user's call. Commit with `-c user.email=19499446+ChiefGyk3D@users.noreply.github.com -c user.name=ChiefGyk3D` if the worktree's git identity is not already that.
- Never write hostnames, machine serials, or the user's callsign into any file. Employer name never appears.
- Version bump to `0.4.0` and the README test count happen in the last task only.

## File Structure

```
dbb/config.py            [overnight] table schema + validation + emit + set_dotted; conference default profile; general.topoff_resuspend; profile_type() -> "conference"
dbb/overnight.py         NEW: parse_hhmm, next_occurrence, in_window, fmt_local; blank/ensure; charge-current ring + estimate_topoff_s; is_topoff_profile, leave_ts, set_manual, set_leave_at, step, apply_phase, describe; wakealarm_text, write_wakealarm
dbb/policy.py            resolve() applies overnight.apply_phase; switch_profile clears leave-at override + warning; active_day, revert guard, revert_eta, revert_soon, extend, PolicyError
dbb/wear.py              ACTIVE_DAY_STINT_MIN / ACTIVE_DAY_WINDOW_H; _track_ac_run tracks battery stints
dbb/state.py             new_state keys: battery_run_start_ts, last_battery_stint_end_ts, revert_warned_ts
dbb/cli.py               profile create --template; topoff now / night / leave-at; profile extend; --until; tick calls step + writes wake file + revert warning; _step_resolve_apply
dbb/render.py            overnight line + revert warning/guard text; state_json overnight object + revert flags; field_mode includes conference
dbb/metrics.py           dbb_overnight_phase, dbb_topoff_start_timestamp_seconds, dbb_leave_timestamp_seconds, dbb_revert_warning
dbb/__init__.py          VERSION = "0.4.0" (Task 11)
libexec/dell-battery-balance-wake   NEW root helper: RTC alarm program/clear, guarded re-suspend
systemd/dell-battery-balance.service   ExecStartPost=+ wake helper
install.sh / uninstall.sh          install/remove the helper; uninstall clears an owned alarm
config/config.toml.default         conference profile + topoff_resuspend
tests/test_config.py, tests/test_overnight.py (NEW), tests/test_wake_helper.py (NEW), tests/test_policy.py, tests/test_wear_model.py, tests/test_cli.py, tests/test_cli_surface.py, tests/test_metrics.py
plasmoid/notifyrc/dell_battery_balance.notifyrc   revertWarning, topoffStarted
plasmoid/package/contents/ui/main.qml             notifyEventFor for the two new events
plasmoid/package/contents/ui/FullRepresentation.qml   conference banner, overnight block, quick actions, leave-at, Extend
plasmoid/package/contents/ui/configProfiles.qml   Conference type + overnight fields
plasmoid/package/metadata.json                    Version 1.2
README.md, media/*.png, docs/superpowers/specs/2026-09-14-conference-overnight-design.md   Task 11
```

---

### Task 1: Config schema — overnight table, conference profile, topoff_resuspend

**Files:**
- Modify: `dbb/config.py`
- Modify: `config/config.toml.default`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `config.profile_type(profile)` returns `"conference"` when the profile has an `overnight` table, else `"fixed"`/`"balancing"` as before. `config.OVERNIGHT_KEYS`, `config.OVERNIGHT_MODES`, `config.DEFAULT_OVERNIGHT` (dict). `default_config()["profiles"]["conference"]` exists. `default_config()["general"]["topoff_resuspend"] is True`. `set_dotted(cfg, "profiles.X.overnight", "none")` removes the table; `"default"` installs `DEFAULT_OVERNIGHT`; `set_dotted(cfg, "profiles.X.overnight.leave_at", "06:30")` sets one key (creating the table from `DEFAULT_OVERNIGHT` first when absent).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py` (and change the existing `test_default_has_four_profiles` expectation):

```python
# in DefaultsAndRoundTrip, replace test_default_has_four_profiles with:
    def test_default_has_five_profiles(self):
        self.assertEqual(sorted(config.default_config()["profiles"]),
                         ["conference", "daily", "field", "storage", "travel"])

    def test_profile_type_conference(self):
        cfg = config.default_config()
        self.assertEqual(config.profile_type(cfg["profiles"]["conference"]), "conference")

    def test_conference_default_shape(self):
        p = config.default_config()["profiles"]["conference"]
        self.assertEqual(p["bands"], {"all": [90, 100]})
        self.assertEqual(p["revert"], {"after_hours": 168, "on_ac_hours": 12, "to": "previous"})
        self.assertEqual(p["overnight"], {"mode": "topoff", "leave_at": "07:00", "night_from": "23:00",
                                          "hold": [70, 80], "margin_min": 30})
        self.assertEqual(config.DEFAULT_OVERNIGHT, p["overnight"])

    def test_topoff_resuspend_is_optional_and_defaults_true(self):
        cfg = config.default_config()
        self.assertIs(cfg["general"]["topoff_resuspend"], True)
        text = config.emit(cfg).replace("topoff_resuspend = true\n", "")
        self.assertNotIn("topoff_resuspend", text)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.toml")
            with open(p, "w") as fh:
                fh.write(text)
            loaded = config.load(p)
        self.assertIs(loaded["general"]["topoff_resuspend"], True)


class OvernightValidation(unittest.TestCase):
    def setUp(self):
        self.cfg = config.default_config()
        self.ov = self.cfg["profiles"]["conference"]["overnight"]

    def assertRejects(self, fragment):
        with self.assertRaises(config.ConfigError) as cm:
            config.validate(self.cfg)
        self.assertIn(fragment, str(cm.exception))

    def test_unknown_key(self):
        self.ov["snooze"] = 1
        self.assertRejects("profiles.conference.overnight.snooze: unknown key")

    def test_missing_key(self):
        del self.ov["margin_min"]
        self.assertRejects("profiles.conference.overnight.margin_min: missing")

    def test_bad_mode(self):
        self.ov["mode"] = "nap"
        self.assertRejects("profiles.conference.overnight.mode")

    def test_bad_time(self):
        self.ov["leave_at"] = "7:00"
        self.assertRejects("profiles.conference.overnight.leave_at: must be HH:MM")

    def test_times_must_differ(self):
        self.ov["night_from"] = self.ov["leave_at"]
        self.assertRejects("profiles.conference.overnight.leave_at: must differ from night_from")

    def test_hold_is_a_band(self):
        self.ov["hold"] = [80, 70]
        self.assertRejects("profiles.conference.overnight.hold: start must be below stop")

    def test_margin_non_negative(self):
        self.ov["margin_min"] = -1
        self.assertRejects("profiles.conference.overnight.margin_min: must be >= 0")

    def test_overnight_only_on_fixed_profiles(self):
        self.cfg["profiles"]["daily"]["overnight"] = dict(self.ov)
        self.assertRejects("profiles.daily.overnight: only a fixed profile")

    def test_full_mode_still_validates_other_keys(self):
        self.ov["mode"] = "full"
        self.ov["leave_at"] = "nope"
        self.assertRejects("profiles.conference.overnight.leave_at")


class OvernightEditing(unittest.TestCase):
    def setUp(self):
        self.cfg = config.default_config()

    def test_emit_round_trips_overnight(self):
        self.assertEqual(tomllib.loads(config.emit(self.cfg)), self.cfg)
        self.assertIn('overnight.leave_at = "07:00"', config.emit(self.cfg))

    def test_set_one_key(self):
        config.set_dotted(self.cfg, "profiles.conference.overnight.leave_at", "06:30")
        self.assertEqual(self.cfg["profiles"]["conference"]["overnight"]["leave_at"], "06:30")
        config.set_dotted(self.cfg, "profiles.conference.overnight.hold", "60,75")
        self.assertEqual(self.cfg["profiles"]["conference"]["overnight"]["hold"], [60, 75])
        config.set_dotted(self.cfg, "profiles.conference.overnight.margin_min", "45")
        self.assertEqual(self.cfg["profiles"]["conference"]["overnight"]["margin_min"], 45)
        config.validate(self.cfg)

    def test_none_removes_table(self):
        config.set_dotted(self.cfg, "profiles.conference.overnight", "none")
        self.assertNotIn("overnight", self.cfg["profiles"]["conference"])
        self.assertEqual(config.profile_type(self.cfg["profiles"]["conference"]), "fixed")
        config.validate(self.cfg)

    def test_default_adds_table_to_a_fixed_profile(self):
        config.set_dotted(self.cfg, "profiles.field.overnight", "default")
        self.assertEqual(self.cfg["profiles"]["field"]["overnight"], config.DEFAULT_OVERNIGHT)
        config.validate(self.cfg)

    def test_setting_a_key_on_a_profile_without_the_table_fills_defaults(self):
        config.set_dotted(self.cfg, "profiles.field.overnight.night_from", "19:00")
        ov = self.cfg["profiles"]["field"]["overnight"]
        self.assertEqual(ov["night_from"], "19:00")
        self.assertEqual(ov["leave_at"], "07:00")
        config.validate(self.cfg)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_config -v 2>&1 | tail -20`
Expected: the new tests FAIL (`KeyError: 'conference'`, `AttributeError: DEFAULT_OVERNIGHT`, `AssertionError`).

- [ ] **Step 3: Implement the schema in `dbb/config.py`**

Replace the constants block and `default_config`/`profile_type`:

```python
GENERAL_KEYS = {
    "active_profile": str, "previous_profile": str, "deadband_efc": float,
    "auto_balance": bool, "sample_interval_s": int, "bench_temp_c": float,
    "bios_password_file": str, "firmware_write_needs_reboot": bool,
    "sample_log_years": int, "topoff_resuspend": bool,
}
# Keys added after 0.3.0: a config.toml written before they existed must
# still load, so they are optional on read and filled in by load()/load_json().
GENERAL_OPTIONAL = {"sample_log_years": 3, "topoff_resuspend": True}
PROFILE_KEYS = {"label", "description", "balancing", "bands", "revert", "pins", "overnight"}
REVERT_KEYS = {"after_hours", "on_ac_hours", "to"}
OVERNIGHT_KEYS = {"mode", "leave_at", "night_from", "hold", "margin_min"}
OVERNIGHT_MODES = ("topoff", "full")
HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DEFAULT_OVERNIGHT = {"mode": "topoff", "leave_at": "07:00", "night_from": "23:00",
                     "hold": [70, 80], "margin_min": 30}
```

In `default_config()` add `"topoff_resuspend": True,` after `"sample_log_years": 3,` in `general`, and add this profile after `"field"`:

```python
            "conference": {"label": "Conference",
                           "description": "Con week. Full by day, held overnight, topped off before you leave.",
                           "balancing": False, "bands": {"all": [90, 100]},
                           "revert": {"after_hours": 168, "on_ac_hours": 12, "to": "previous"},
                           "overnight": copy.deepcopy(DEFAULT_OVERNIGHT)},
```

```python
def profile_type(profile):
    if "overnight" in profile:
        return "conference"
    return "fixed" if "all" in profile.get("bands", {}) else "balancing"
```

In `validate()`, after the `pins` loop and before the final `for k in ("active_profile", "previous_profile")` loop, inside the per-profile loop:

```python
        ov = p.get("overnight")
        if ov is not None:
            ob = f"{base}.overnight"
            if not isinstance(ov, dict):
                raise ConfigError(f"{ob}: must be a table")
            if "all" not in bands:
                raise ConfigError(f"{ob}: only a fixed profile (bands.all) can have an overnight table")
            for k in ov:
                if k not in OVERNIGHT_KEYS:
                    raise ConfigError(f"{ob}.{k}: unknown key")
            for k in sorted(OVERNIGHT_KEYS):
                if k not in ov:
                    raise ConfigError(f"{ob}.{k}: missing")
            if ov["mode"] not in OVERNIGHT_MODES:
                raise ConfigError(f"{ob}.mode: must be one of {', '.join(OVERNIGHT_MODES)}")
            for k in ("leave_at", "night_from"):
                if not isinstance(ov[k], str) or not HHMM_RE.match(ov[k]):
                    raise ConfigError(f"{ob}.{k}: must be HH:MM (24-hour)")
            if ov["leave_at"] == ov["night_from"]:
                raise ConfigError(f"{ob}.leave_at: must differ from night_from")
            _band(f"{ob}.hold", ov["hold"])
            _typed(f"{ob}.margin_min", ov["margin_min"], int)
            if ov["margin_min"] < 0:
                raise ConfigError(f"{ob}.margin_min: must be >= 0")
```

In `emit()`, after the `revert` lines and before the `pins` lines:

```python
        for k in ("mode", "leave_at", "night_from", "hold", "margin_min"):
            if k in p.get("overnight", {}):
                out.append(f"overnight.{k} = {_val(p['overnight'][k])}")
```

In `_resolve_type()`, add a branch after the `revert` one:

```python
        elif len(rest) == 2 and rest[0] == "overnight":
            return {"mode": str, "leave_at": str, "night_from": str,
                    "hold": list, "margin_min": int}.get(rest[1])
```

In `set_dotted()`, after the existing two `revert` special cases and before `node = cfg`:

```python
    if len(parts) == 3 and parts[0] == "profiles" and parts[2] == "overnight":
        if parts[1] not in cfg.get("profiles", {}):
            raise ConfigError(f"{key}: no profile {parts[1]!r}")
        prof = cfg["profiles"][parts[1]]
        if word in OFF_WORDS:
            prof.pop("overnight", None)
            return
        if word == "default":
            prof["overnight"] = copy.deepcopy(DEFAULT_OVERNIGHT)
            return
        raise ConfigError(f"{key}: use 'default' to add the table or 'none' to remove it")
    if len(parts) == 4 and parts[0] == "profiles" and parts[2] == "overnight":
        if parts[1] not in cfg.get("profiles", {}):
            raise ConfigError(f"{key}: no profile {parts[1]!r}")
        prof = cfg["profiles"][parts[1]]
        if "overnight" not in prof:
            prof["overnight"] = copy.deepcopy(DEFAULT_OVERNIGHT)
```

(The generic path below then sets the single key with the resolved type.)

- [ ] **Step 4: Update `config/config.toml.default`**

Add `topoff_resuspend = true` after `sample_log_years = 3`, and add after the `[profiles.field]` block:

```toml
[profiles.conference]
label = "Conference"
description = "Con week. Full by day, held overnight, topped off before you leave."
balancing = false
bands.all = [90, 100]
revert.after_hours = 168
revert.on_ac_hours = 12
revert.to = "previous"
overnight.mode = "topoff"
overnight.leave_at = "07:00"
overnight.night_from = "23:00"
overnight.hold = [70, 80]
overnight.margin_min = 30
```

- [ ] **Step 5: Run the whole suite**

Run: `python3 -W error::ResourceWarning -m unittest discover -s tests 2>&1 | tail -5`
Expected: OK. If any existing test enumerates the four profiles or `profile list` output (grep `tests/` for `"storage", "travel"` and `travel` in list assertions), update it to include `conference`. Also add a test that the shipped default file validates if none exists:

```python
    def test_shipped_default_file_validates(self):
        here = os.path.dirname(__file__)
        cfg = config.load(os.path.join(here, os.pardir, "config", "config.toml.default"), must_exist=True)
        self.assertIn("conference", cfg["profiles"])
```

- [ ] **Step 6: Commit**

```bash
git add dbb/config.py config/config.toml.default tests/test_config.py
git commit -m "Config: [overnight] table, built-in conference profile, general.topoff_resuspend

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `profile create --template <builtin>`

**Files:**
- Modify: `dbb/cli.py` (`cmd_profile_create`, parser)
- Modify: `README.md` (`### CLI reference` row for `profile create`)
- Test: `tests/test_cli.py`, `tests/test_cli_surface.py`

**Interfaces:**
- Produces: `profile create <name> (--from <existing> | --template <builtin>)`; the two flags are a required mutually exclusive group.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
class ProfileTemplate(CliBase):
    def test_template_adds_a_builtin_missing_from_the_installed_config(self):
        code, _, err = self.run_cli("profile", "delete", "conference")
        self.assertEqual(code, 0, err)
        code, _, err = self.run_cli("profile", "create", "defcon", "--template", "conference")
        self.assertEqual(code, 0, err)
        code, out, _ = self.run_cli("profile", "show", "defcon")
        self.assertIn('overnight.mode = "topoff"', out)
        self.assertIn('label = "defcon"', out)

    def test_unknown_template_is_an_error(self):
        code, _, err = self.run_cli("profile", "create", "x", "--template", "nope")
        self.assertEqual(code, 1)
        self.assertIn("no built-in profile 'nope'", err)
        self.assertIn("conference", err)

    def test_from_and_template_are_exclusive(self):
        code, _, _ = self.run_cli("profile", "create", "x", "--from", "field", "--template", "conference")
        self.assertEqual(code, 2)

    def test_template_needs_configure_class(self):
        code, _, err = self.run_cli("--polkit-class", "control", "--", "profile", "create", "x", "--template", "conference")
        self.assertEqual(code, 3)
```

In `tests/test_cli_surface.py` `DOCUMENTED`, after the existing `profile create` entry add:

```python
    ["profile", "create", "defcon", "--template", "conference"],
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests.test_cli.ProfileTemplate tests.test_cli_surface -v 2>&1 | tail -8`
Expected: FAIL (unrecognized arguments `--template`).

- [ ] **Step 3: Implement**

In `dbb/cli.py` replace `cmd_profile_create`:

```python
def cmd_profile_create(args):
    state, cfg, _ = _view()
    if args.name in cfg["profiles"]:
        die(f"error: profile {args.name!r} exists")
    if args.template:
        builtins = cfg_mod.default_config()["profiles"]
        if args.template not in builtins:
            die(f"error: no built-in profile {args.template!r}. Built-ins: {', '.join(sorted(builtins))}")
        src = builtins[args.template]
    else:
        if args.from_ not in cfg["profiles"]:
            die(f"error: no profile {args.from_!r}")
        src = cfg["profiles"][args.from_]
    cfg["profiles"][args.name] = json.loads(json.dumps(src))
    cfg["profiles"][args.name]["label"] = args.name
    _save_config(cfg, state, "creating profile")
    save_state(state)
```

In `build_parser()` replace the `create` line:

```python
    sp = pr.add_parser("create"); sp.add_argument("name")
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--from", dest="from_", metavar="NAME", help="clone an existing profile")
    g.add_argument("--template", metavar="BUILTIN",
                   help="start from a shipped default (conference, field, ...), even if the installed config lacks it")
    sp.set_defaults(func=cmd_profile_create, cls="profile-create")
```

README `### CLI reference`: replace the `profile create` row with:

```
| `profile create <name> --from <name> \| --template <builtin>` | clone an existing profile, or start from a shipped default (`conference`, `field`, `travel`, `storage`, `daily`) that an older config.toml may not have |
```

- [ ] **Step 4: Run the suite**

Run: `python3 -W error::ResourceWarning -m unittest discover -s tests 2>&1 | tail -4`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add dbb/cli.py README.md tests/test_cli.py tests/test_cli_surface.py
git commit -m "profile create --template: start from a shipped default the installed config lacks

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `dbb/overnight.py` — local-clock helpers

**Files:**
- Create: `dbb/overnight.py`
- Create: `tests/test_overnight.py`

**Interfaces:**
- Produces: `overnight.parse_hhmm(text) -> (h, m)` raising `ValueError`; `overnight.next_occurrence(now_ts, hhmm, tomorrow=False) -> float` (epoch of the next local occurrence strictly after `now_ts`, or tomorrow's when `tomorrow`); `overnight.in_window(now_ts, night_from, leave_at) -> bool` (half-open `[night_from, leave_at)` on the local clock, wrapping midnight when `leave_at < night_from`); `overnight.fmt_local(ts) -> "HH:MM"` (`"?"` for None).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_overnight.py`:

```python
import os, sys, time, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))


def local_ts(y, mo, d, h, mi):
    return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))


class TZBase(unittest.TestCase):
    TZ = "America/New_York"

    def setUp(self):
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = self.TZ
        time.tzset()
        for m in list(sys.modules):
            if m.startswith("dbb"):
                del sys.modules[m]
        from dbb import overnight
        self.ov = overnight

    def tearDown(self):
        if self._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._tz
        time.tzset()


class ParseHHMM(TZBase):
    def test_valid(self):
        self.assertEqual(self.ov.parse_hhmm("07:05"), (7, 5))
        self.assertEqual(self.ov.parse_hhmm("23:59"), (23, 59))

    def test_invalid(self):
        for bad in ("7", "7:00:00", "24:00", "12:60", "", None, "noon"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.ov.parse_hhmm(bad)


class NextOccurrence(TZBase):
    def test_later_today(self):
        now = local_ts(2026, 8, 5, 20, 0)
        self.assertEqual(self.ov.next_occurrence(now, "23:00"), local_ts(2026, 8, 5, 23, 0))

    def test_tomorrow_when_passed(self):
        now = local_ts(2026, 8, 5, 20, 0)
        self.assertEqual(self.ov.next_occurrence(now, "07:00"), local_ts(2026, 8, 6, 7, 0))

    def test_exactly_now_means_tomorrow(self):
        now = local_ts(2026, 8, 5, 7, 0)
        self.assertEqual(self.ov.next_occurrence(now, "07:00"), local_ts(2026, 8, 6, 7, 0))

    def test_tomorrow_flag_skips_today(self):
        now = local_ts(2026, 8, 5, 3, 0)
        self.assertEqual(self.ov.next_occurrence(now, "07:00", tomorrow=True), local_ts(2026, 8, 6, 7, 0))

    def test_spring_forward_night_is_an_hour_shorter(self):
        # 2026-03-08 02:00 EST -> 03:00 EDT. 23:30 to 07:00 across it is 6.5 h.
        now = local_ts(2026, 3, 7, 23, 30)
        self.assertAlmostEqual(self.ov.next_occurrence(now, "07:00") - now, 6.5 * 3600)

    def test_fall_back_night_is_an_hour_longer(self):
        # 2026-11-01 02:00 EDT -> 01:00 EST. 23:30 to 07:00 across it is 8.5 h.
        now = local_ts(2026, 10, 31, 23, 30)
        self.assertAlmostEqual(self.ov.next_occurrence(now, "07:00") - now, 8.5 * 3600)


class InWindow(TZBase):
    def test_wrapping_window(self):
        for hhmm, inside in (((23, 0), True), ((23, 30), True), ((0, 10), True), ((6, 59), True),
                             ((7, 0), False), ((12, 0), False), ((22, 59), False)):
            with self.subTest(at=hhmm):
                now = local_ts(2026, 8, 5, *hhmm)
                self.assertEqual(self.ov.in_window(now, "23:00", "07:00"), inside)

    def test_non_wrapping_window(self):
        for hhmm, inside in (((0, 30), False), ((1, 0), True), ((2, 0), True), ((6, 59), True), ((7, 0), False)):
            with self.subTest(at=hhmm):
                now = local_ts(2026, 8, 5, *hhmm)
                self.assertEqual(self.ov.in_window(now, "01:00", "07:00"), inside)

    def test_fmt_local(self):
        self.assertEqual(self.ov.fmt_local(local_ts(2026, 8, 5, 5, 31)), "05:31")
        self.assertEqual(self.ov.fmt_local(None), "?")


class InWindowAuckland(InWindow):
    TZ = "Pacific/Auckland"
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests.test_overnight -v 2>&1 | tail -5`
Expected: FAIL with `ModuleNotFoundError: No module named 'dbb.overnight'`.

- [ ] **Step 3: Create the module**

Create `dbb/overnight.py`:

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
"""Conference profile: the night window, the departure time, the timed
top-off and the RTC wake request.

Everything here is on the LOCAL clock (time.localtime / time.mktime), so a
night that crosses a DST change is as long as the wall clock says it is.
The firmware can only cap charging -- it cannot lower a full pack -- so the
whole point of the state machine is to stop the charge at the hold band the
moment the laptop is plugged in for the night, and to lift the cap just
early enough that both packs are full at the departure time.
"""
import os
import time

from dbb.state import STATE_DIR, _make_readable, add_event
from dbb.sysfs import BATS, clamp_band

PHASES = ("off", "charging_full", "holding", "topping")
CV_TAIL_S = 20 * 60           # constant-voltage tail per pack, on top of the CC estimate
FALLBACK_CHARGE_UA = 2_000_000
CHARGE_UA_KEEP = 48           # charging currents remembered per slot (~1.6 h of ticks)
WAKEALARM_FILE = STATE_DIR / "wakealarm"


# ------------------------------------------------------------ local clock

def parse_hhmm(text):
    try:
        h, m = text.split(":")
        h, m = int(h), int(m)
    except (ValueError, AttributeError, TypeError):
        raise ValueError(f"bad time {text!r}; use HH:MM")
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"bad time {text!r}; use HH:MM")
    return h, m


def _local_at(now_ts, h, m, day_offset=0):
    lt = time.localtime(now_ts)
    # tm_mday overflow is normalised by mktime; tm_isdst=-1 lets libc decide.
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + day_offset, h, m, 0, 0, 0, -1))


def next_occurrence(now_ts, hhmm, tomorrow=False):
    """Epoch of the next local HH:MM strictly after now_ts (tomorrow's when
    `tomorrow`)."""
    h, m = parse_hhmm(hhmm)
    if tomorrow:
        return _local_at(now_ts, h, m, 1)
    cand = _local_at(now_ts, h, m)
    return cand if cand > now_ts else _local_at(now_ts, h, m, 1)


def in_window(now_ts, night_from, leave_at):
    """[night_from, leave_at) on the local clock; wraps midnight when
    leave_at is earlier in the day than night_from."""
    lt = time.localtime(now_ts)
    now_m = lt.tm_hour * 60 + lt.tm_min
    nf_h, nf_m = parse_hhmm(night_from)
    la_h, la_m = parse_hhmm(leave_at)
    nf, la = nf_h * 60 + nf_m, la_h * 60 + la_m
    if nf < la:
        return nf <= now_m < la
    return now_m >= nf or now_m < la


def fmt_local(ts):
    return time.strftime("%H:%M", time.localtime(ts)) if ts is not None else "?"
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest tests.test_overnight -v 2>&1 | tail -5`
Expected: OK (both TZ variants).

- [ ] **Step 5: Commit**

```bash
git add dbb/overnight.py tests/test_overnight.py
git commit -m "overnight: local-clock helpers (HH:MM, next occurrence, night window)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Charge-current ring and the top-off estimate

**Files:**
- Modify: `dbb/overnight.py`
- Test: `tests/test_overnight.py`

**Interfaces:**
- Produces: `overnight.blank() -> dict` (the `state["overnight"]` default: `phase, since_ts, topoff_start_ts, leave_ts, manual, leave_at_override_ts, charge_ua`); `overnight.ensure(state) -> dict` (returns `state["overnight"]`, filling missing keys); `overnight.record_charge_current(state, sample)`; `overnight.median_charge_ua(state, slot) -> int`; `overnight.estimate_topoff_s(state, sample, margin_min) -> float` (spec §3).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_overnight.py`:

```python
DESIGN = 4600000


def bat(status="Discharging", capacity=50, charge_now=2300000, current=0, full=DESIGN):
    return dict(status=status, capacity=capacity, charge_now_uah=charge_now, charge_full_uah=full,
                charge_full_design_uah=DESIGN, voltage_now_uv=11400000, voltage_min_design_uv=11400000,
                current_now_ua=current, temp_dc=313)


def sample(ts, ac=1, **bats):
    return dict(ts=ts, boot_id="b", ac_online=ac, bats=bats)


class Estimate(TZBase):
    def setUp(self):
        super().setUp()
        from dbb import state as st
        self.state = st.new_state()

    def test_blank_and_ensure(self):
        ov = self.ov.ensure(self.state)
        self.assertEqual(ov["phase"], "off")
        self.assertIsNone(ov["manual"])
        self.assertEqual(ov["charge_ua"], {"BAT0": [], "BAT1": []})
        self.state["overnight"] = {"phase": "holding"}           # a partial dict from an older state
        ov = self.ov.ensure(self.state)
        self.assertEqual(ov["phase"], "holding")
        self.assertIn("charge_ua", ov)

    def test_fallback_current_when_no_charging_seen(self):
        self.assertEqual(self.ov.median_charge_ua(self.state, "BAT0"), 2_000_000)

    def test_ring_keeps_last_48_and_medians(self):
        for i in range(60):
            s = sample(1000 + i, BAT0=bat(status="Charging", current=1_000_000 + i * 10_000))
            self.ov.record_charge_current(self.state, s)
        ring = self.state["overnight"]["charge_ua"]["BAT0"]
        self.assertEqual(len(ring), 48)
        self.assertEqual(ring[0], 1_120_000)                     # samples 0..11 dropped
        self.assertEqual(self.ov.median_charge_ua(self.state, "BAT0"), sorted(ring)[24])

    def test_non_charging_samples_are_ignored(self):
        self.ov.record_charge_current(self.state, sample(1, BAT0=bat(status="Discharging", current=900_000)))
        self.ov.record_charge_current(self.state, sample(2, BAT0=bat(status="Charging", current=0)))
        self.ov.record_charge_current(self.state, sample(3, BAT1=bat(status="Charging", current=-2_280_000)))
        self.assertEqual(self.state["overnight"]["charge_ua"]["BAT0"], [])
        self.assertEqual(self.state["overnight"]["charge_ua"]["BAT1"], [2_280_000])

    def test_estimate_sums_both_packs_sequentially(self):
        # 80% -> 100% is 920000 uAh per pack; at the 2 A fallback that is 1656 s
        # + 1200 s tail each; margin 30 min = 1800 s.
        s = sample(0, BAT0=bat(charge_now=3680000), BAT1=bat(charge_now=3680000))
        self.assertAlmostEqual(self.ov.estimate_topoff_s(self.state, s, 30), 2 * (1656 + 1200) + 1800, places=0)

    def test_full_packs_and_missing_readings_are_skipped(self):
        s = sample(0, BAT0=bat(charge_now=DESIGN), BAT1=bat(charge_now=None))
        self.assertEqual(self.ov.estimate_topoff_s(self.state, s, 10), 600)

    def test_estimate_uses_the_measured_median(self):
        for _ in range(5):
            self.ov.record_charge_current(self.state, sample(0, BAT0=bat(status="Charging", current=920_000)))
        s = sample(0, BAT0=bat(charge_now=3680000))
        self.assertAlmostEqual(self.ov.estimate_topoff_s(self.state, s, 0), 3600 + 1200)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests.test_overnight.Estimate -v 2>&1 | tail -5`
Expected: FAIL (`AttributeError: module 'dbb.overnight' has no attribute 'ensure'`).

- [ ] **Step 3: Implement**

Append to `dbb/overnight.py`:

```python
# ---------------------------------------------------------------- state

def blank():
    return {"phase": "off", "since_ts": None, "topoff_start_ts": None, "leave_ts": None,
            "manual": None, "leave_at_override_ts": None,
            "charge_ua": {b: [] for b in BATS}}


def ensure(state):
    ov = state.get("overnight")
    if not isinstance(ov, dict):
        ov = state["overnight"] = blank()
    for k, v in blank().items():
        ov.setdefault(k, v)
    for b in BATS:
        ov["charge_ua"].setdefault(b, [])
    return ov


# ------------------------------------------------------------- estimate

def record_charge_current(state, sample):
    ov = ensure(state)
    for b, v in sample["bats"].items():
        if v.get("status") == "Charging" and v.get("current_now_ua"):
            lst = ov["charge_ua"].setdefault(b, [])
            lst.append(int(abs(v["current_now_ua"])))
            del lst[:-CHARGE_UA_KEEP]


def median_charge_ua(state, slot):
    lst = sorted(ensure(state)["charge_ua"].get(slot) or [])
    if not lst:
        return FALLBACK_CHARGE_UA
    return lst[len(lst) // 2]


def estimate_topoff_s(state, sample, margin_min):
    """Seconds to bring every present pack to full: the EC charges the packs
    one after the other, so the per-pack times ADD (spec §3)."""
    total = float(margin_min) * 60.0
    for b, v in sample["bats"].items():
        full, now_uah = v.get("charge_full_uah"), v.get("charge_now_uah")
        if not full or now_uah is None:
            continue
        deficit = full - now_uah
        if deficit <= 0:
            continue
        total += deficit / median_charge_ua(state, b) * 3600.0 + CV_TAIL_S
    return total
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest tests.test_overnight -v 2>&1 | tail -4`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add dbb/overnight.py tests/test_overnight.py
git commit -m "overnight: charging-current ring and the sequential top-off estimate

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: The overnight state machine, policy override, wake file, tick integration

**Files:**
- Modify: `dbb/overnight.py`
- Modify: `dbb/policy.py` (`resolve`, `switch_profile`)
- Modify: `dbb/cli.py` (`cmd_tick`, `_switch_and_apply`, new `_step_resolve_apply`, `_write_wakealarm`)
- Test: `tests/test_overnight.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 3/4 helpers; `config.profile_type`.
- Produces: `overnight.is_topoff_profile(profile) -> bool`; `overnight.leave_ts(state, profile, now_ts) -> float`; `overnight.set_manual(state, which)` with `which in ("topoff", "night")`; `overnight.set_leave_at(state, hhmm_or_None, now_ts, tomorrow=False) -> float|None`; `overnight.step(cfg, state, sample, now_ts) -> phase` (advances `state["overnight"]`, adds `overnight` events); `overnight.apply_phase(profile, state, bands) -> bands` (pure); `overnight.describe(profile, state, sample, now_ts) -> str|None`; `overnight.wakealarm_text(state) -> str`; `overnight.write_wakealarm(state, path=None)`. In `cli`: `_step_resolve_apply(cfg, state, sample, now=None) -> (Resolution, apply_result)`.
- Event kind `"overnight"`; the topping entry detail starts with `"top-off started"` (the applet keys on it in Task 10).

- [ ] **Step 1: Write the failing unit tests**

Append to `tests/test_overnight.py`:

```python
class Machine(TZBase):
    """Local clock America/New_York; dates in August 2026 (EDT, no DST edge)."""

    def setUp(self):
        super().setUp()
        from dbb import config, policy, state as st
        self.config, self.policy, self.st = config, policy, st
        self.cfg = config.default_config()
        self.cfg["general"]["active_profile"] = "conference"
        self.state = st.new_state()

    def at(self, h, mi, day=5):
        return local_ts(2026, 8, day, h, mi)

    def both(self, cap=60, charge=2760000, ac=1, ts=None):
        return sample(ts if ts is not None else self.at(20, 0), ac=ac,
                      BAT0=bat(capacity=cap, charge_now=charge), BAT1=bat(capacity=cap, charge_now=charge))

    def resolve(self, s, now):
        return self.policy.resolve(self.cfg, self.state, s, now)

    def test_plug_in_by_day_charges_full(self):
        now = self.at(19, 0)
        self.assertEqual(self.ov.step(self.cfg, self.state, self.both(), now), "charging_full")
        self.assertEqual(self.resolve(self.both(), now).bands, {"BAT0": (90, 100), "BAT1": (90, 100)})
        self.assertIn("charging to 100% until 23:00, then hold", self.state["events"][-1]["detail"])

    def test_plug_in_at_night_holds(self):
        now = self.at(23, 30)
        self.assertEqual(self.ov.step(self.cfg, self.state, self.both(), now), "holding")
        self.assertEqual(self.resolve(self.both(), now).bands, {"BAT0": (70, 80), "BAT1": (70, 80)})
        ov = self.state["overnight"]
        self.assertEqual(ov["leave_ts"], self.at(7, 0, day=6))
        est = self.ov.estimate_topoff_s(self.state, self.both(), 30)
        self.assertAlmostEqual(ov["topoff_start_ts"], ov["leave_ts"] - est)
        self.assertIn("holding 70/80, top-off", self.state["events"][-1]["detail"])
        self.assertIn("for 07:00", self.state["events"][-1]["detail"])

    def test_window_opening_moves_charging_full_to_holding(self):
        self.ov.step(self.cfg, self.state, self.both(), self.at(22, 58))
        self.assertEqual(self.ov.step(self.cfg, self.state, self.both(), self.at(23, 0)), "holding")

    def test_holding_to_topping_when_due(self):
        self.ov.step(self.cfg, self.state, self.both(), self.at(23, 30))
        start = self.state["overnight"]["topoff_start_ts"]
        self.assertEqual(self.ov.step(self.cfg, self.state, self.both(ts=start - 60), start - 60), "holding")
        self.assertEqual(self.ov.step(self.cfg, self.state, self.both(ts=start), start), "topping")
        self.assertEqual(self.resolve(self.both(), start).bands, {"BAT0": (90, 100), "BAT1": (90, 100)})
        self.assertTrue(self.state["events"][-1]["detail"].startswith("top-off started"))
        self.assertIn("ready by 07:00", self.state["events"][-1]["detail"])
        self.assertIsNone(self.state["overnight"]["topoff_start_ts"])

    def test_immediate_topping_when_plugged_in_close_to_departure(self):
        now = self.at(6, 30)
        self.assertEqual(self.ov.step(self.cfg, self.state, self.both(), now), "topping")

    def test_topping_stays_past_departure_until_unplugged(self):
        self.ov.step(self.cfg, self.state, self.both(), self.at(6, 30))
        self.assertEqual(self.ov.step(self.cfg, self.state, self.both(), self.at(9, 0)), "topping")
        self.assertEqual(self.ov.step(self.cfg, self.state, self.both(ac=0), self.at(9, 2)), "off")

    def test_manual_topoff_from_holding(self):
        self.ov.step(self.cfg, self.state, self.both(), self.at(23, 30))
        self.ov.set_manual(self.state, "topoff")
        self.assertEqual(self.ov.step(self.cfg, self.state, self.both(), self.at(23, 32)), "topping")
        self.assertIn("top-off started now", self.state["events"][-1]["detail"])

    def test_manual_night_from_charging_full_notes_packs_above_hold(self):
        s = sample(self.at(20, 0), BAT0=bat(capacity=96, charge_now=4416000), BAT1=bat(capacity=40, charge_now=1840000))
        self.ov.step(self.cfg, self.state, s, self.at(20, 0))
        self.ov.set_manual(self.state, "night")
        self.assertEqual(self.ov.step(self.cfg, self.state, s, self.at(20, 2)), "holding")
        d = self.state["events"][-1]["detail"]
        self.assertIn("BAT0 is at 96%, above the 80% hold; the firmware cannot lower it", d)
        self.assertNotIn("BAT1 is at", d)

    def test_unplug_resets_and_clears_manual(self):
        self.ov.step(self.cfg, self.state, self.both(), self.at(20, 0))
        self.ov.set_manual(self.state, "night")
        self.assertEqual(self.ov.step(self.cfg, self.state, self.both(ac=0), self.at(20, 2)), "off")
        ov = self.state["overnight"]
        self.assertIsNone(ov["manual"])
        self.assertIsNone(ov["topoff_start_ts"])
        self.assertIn("unplugged", self.state["events"][-1]["detail"])

    def test_pins_win_over_the_hold(self):
        self.cfg["profiles"]["conference"]["pins"] = {"BAT1": {"start": 55, "stop": 70}}
        now = self.at(23, 30)
        self.ov.step(self.cfg, self.state, self.both(), now)
        self.assertEqual(self.resolve(self.both(), now).bands, {"BAT0": (70, 80), "BAT1": (55, 70)})

    def test_full_mode_is_inert(self):
        self.cfg["profiles"]["conference"]["overnight"]["mode"] = "full"
        now = self.at(23, 30)
        self.assertEqual(self.ov.step(self.cfg, self.state, self.both(), now), "off")
        self.assertEqual(self.resolve(self.both(), now).bands, {"BAT0": (90, 100), "BAT1": (90, 100)})
        self.assertFalse(self.ov.is_topoff_profile(self.cfg["profiles"]["conference"]))

    def test_other_profiles_are_untouched(self):
        self.cfg["general"]["active_profile"] = "field"
        now = self.at(23, 30)
        self.assertEqual(self.ov.step(self.cfg, self.state, self.both(), now), "off")
        self.assertEqual(self.resolve(self.both(), now).bands, {"BAT0": (90, 100), "BAT1": (90, 100)})

    def test_leave_at_override_is_used_then_consumed(self):
        now = self.at(23, 30)
        self.assertEqual(self.ov.set_leave_at(self.state, "06:00", now), self.at(6, 0, day=6))
        self.ov.step(self.cfg, self.state, self.both(), now)
        self.assertEqual(self.state["overnight"]["leave_ts"], self.at(6, 0, day=6))
        # the override has passed: back to the profile's 07:00, with an event
        later = self.at(6, 1, day=6)
        self.ov.step(self.cfg, self.state, self.both(ac=0), later)
        self.ov.step(self.cfg, self.state, self.both(), self.at(23, 30, day=6))
        self.assertIsNone(self.state["overnight"]["leave_at_override_ts"])
        self.assertEqual(self.state["overnight"]["leave_ts"], self.at(7, 0, day=7))
        self.assertTrue(any("override 06:00 has passed" in e["detail"] for e in self.state["events"]))

    def test_set_leave_at_none_clears(self):
        self.ov.set_leave_at(self.state, "06:00", self.at(20, 0))
        self.assertIsNone(self.ov.set_leave_at(self.state, None, self.at(20, 0)))
        self.assertIsNone(self.state["overnight"]["leave_at_override_ts"])

    def test_switch_profile_clears_the_override(self):
        self.ov.set_leave_at(self.state, "06:00", self.at(20, 0))
        self.policy.switch_profile(self.cfg, self.state, "daily", self.at(20, 1), "test")
        self.assertIsNone(self.state["overnight"]["leave_at_override_ts"])

    def test_wakealarm_text_only_while_holding(self):
        self.assertEqual(self.ov.wakealarm_text(self.state), "")
        self.ov.step(self.cfg, self.state, self.both(), self.at(23, 30))
        start = self.state["overnight"]["topoff_start_ts"]
        self.assertEqual(self.ov.wakealarm_text(self.state), f"{int(start)}\n")
        self.ov.step(self.cfg, self.state, self.both(ts=start), start)
        self.assertEqual(self.ov.wakealarm_text(self.state), "")

    def test_write_wakealarm(self):
        import tempfile
        from pathlib import Path
        self.ov.step(self.cfg, self.state, self.both(), self.at(23, 30))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "wakealarm"
            self.ov.write_wakealarm(self.state, p)
            self.assertEqual(p.read_text(), self.ov.wakealarm_text(self.state))
            self.ov.step(self.cfg, self.state, self.both(ac=0), self.at(23, 32))
            self.ov.write_wakealarm(self.state, p)
            self.assertEqual(p.read_text(), "")

    def test_describe(self):
        prof = self.cfg["profiles"]["conference"]
        self.assertIsNone(self.ov.describe(self.cfg["profiles"]["field"], self.state, self.both(), self.at(20, 0)))
        self.assertIn("on battery", self.ov.describe(prof, self.state, self.both(ac=0), self.at(20, 0)))
        self.ov.step(self.cfg, self.state, self.both(), self.at(20, 0))
        self.assertEqual(self.ov.describe(prof, self.state, self.both(), self.at(20, 0)),
                         "overnight: charging to 100% until 23:00, then hold")
        self.ov.step(self.cfg, self.state, self.both(), self.at(23, 30))
        text = self.ov.describe(prof, self.state, self.both(), self.at(23, 30))
        self.assertTrue(text.startswith("overnight: holding 70/80, top-off "), text)
        self.assertIn(") for 07:00", text)
        self.ov.set_manual(self.state, "topoff")
        self.ov.step(self.cfg, self.state, self.both(), self.at(23, 32))
        self.assertEqual(self.ov.describe(prof, self.state, self.both(), self.at(23, 32)),
                         "overnight: topping off, ready by 07:00")
        prof["overnight"]["mode"] = "full"
        self.assertEqual(self.ov.describe(prof, self.state, self.both(), self.at(23, 32)),
                         "overnight: mode full, packs stay at 100% on AC")
```

- [ ] **Step 2: Write the failing CLI tests**

Append to `tests/test_cli.py` (imports at the top of the file already include `os, json`; add `import time` and `from unittest import mock` to the import line):

```python
class Overnight(CliBase):
    """The tick drives the machine on the real clock; pin it with mock."""

    def setUp(self):
        super().setUp()
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = "America/New_York"
        time.tzset()
        # Pin the clock for the switch too: at 12:00 the machine lands in
        # charging_full, so every test starts from the same phase whatever
        # the wall clock says.
        with mock.patch("time.time", return_value=self.at(12, 0)):
            code, _, err = self.run_cli("profile", "set", "conference", "--stay")
        self.assertEqual(code, 0, err)

    def tearDown(self):
        if self._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._tz
        time.tzset()
        super().tearDown()

    @staticmethod
    def at(h, mi, day=5):
        return time.mktime((2026, 8, day, h, mi, 0, 0, 0, -1))

    def stop_value(self, slot):
        attr = self.sysfs.SYSMAN_ATTRS[slot][2]
        return self.fs.read(f"class/firmware-attributes/dell-wmi-sysman/attributes/{attr}/current_value")

    def wakealarm(self):
        p = os.path.join(os.environ["DBB_STATE_DIR"], "wakealarm")
        return open(p).read() if os.path.exists(p) else None

    def test_tick_holds_at_night_and_writes_the_wake_request(self):
        with mock.patch("time.time", return_value=self.at(23, 30)):
            code, _, err = self.run_cli("tick")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.stop_value("BAT1"), "80")
        self.assertEqual(self.stop_value("BAT0"), "80")
        j = self.status()
        self.assertEqual(j["overnight"]["phase"], "holding")
        self.assertEqual(self.wakealarm(), f"{int(j['overnight']['topoff_start_ts'])}\n")

    def test_tick_applies_overnight_even_with_auto_balance_off(self):
        self.run_cli("config", "set", "general.auto_balance=false")
        with mock.patch("time.time", return_value=self.at(23, 30)):
            self.run_cli("tick")
        self.assertEqual(self.stop_value("BAT1"), "80")

    def test_tick_by_day_charges_full_and_leaves_no_wake_request(self):
        with mock.patch("time.time", return_value=self.at(19, 0)):
            self.run_cli("tick")
        self.assertEqual(self.stop_value("BAT1"), "100")
        self.assertEqual(self.wakealarm(), "")

    def test_profile_set_applies_the_phase_immediately(self):
        self.run_cli("profile", "set", "daily")
        with mock.patch("time.time", return_value=self.at(23, 45)):
            code, out, err = self.run_cli("profile", "set", "conference", "--stay")
        self.assertEqual(code, 0, err)
        self.assertIn("BAT1=70/80", out)
```

`status --json` gains the `overnight` object in Task 9; for THIS task make the two `j["overnight"]` assertions pass by adding the minimal key in `render.state_json`: `out["overnight"] = {"phase": overnight.ensure(state)["phase"], "topoff_start_ts": overnight.ensure(state)["topoff_start_ts"]}` (Task 9 replaces it with the full object).

- [ ] **Step 3: Run to verify they fail**

Run: `python3 -m unittest tests.test_overnight.Machine tests.test_cli.Overnight 2>&1 | tail -5`
Expected: FAIL (`AttributeError: ... 'step'`, missing `overnight` in JSON).

- [ ] **Step 4: Implement the machine in `dbb/overnight.py`**

Append:

```python
# ------------------------------------------------------- state machine

def is_topoff_profile(profile):
    ov = profile.get("overnight")
    return bool(ov) and ov.get("mode") == "topoff"


def leave_ts(state, profile, now_ts):
    """The departure the estimate targets: a one-off override while it is
    still ahead, else the next occurrence of the profile's leave_at."""
    ov = ensure(state)
    o = ov["leave_at_override_ts"]
    if o is not None:
        if o > now_ts:
            return o
        ov["leave_at_override_ts"] = None
        add_event(state, "overnight",
                  f"leave-at override {fmt_local(o)} has passed; back to {profile['overnight']['leave_at']}")
    return next_occurrence(now_ts, profile["overnight"]["leave_at"])


def set_manual(state, which):
    if which not in ("topoff", "night"):
        raise ValueError(which)
    ensure(state)["manual"] = which


def set_leave_at(state, hhmm, now_ts, tomorrow=False):
    ov = ensure(state)
    ov["leave_at_override_ts"] = None if hhmm is None else next_occurrence(now_ts, hhmm, tomorrow)
    return ov["leave_at_override_ts"]


def _reset(ov):
    ov.update(phase="off", since_ts=None, topoff_start_ts=None, leave_ts=None, manual=None)


def _enter(state, ov, phase, now_ts, detail):
    ov["phase"] = phase
    ov["since_ts"] = now_ts
    if phase != "holding":
        ov["topoff_start_ts"] = None      # empties the wake request
    add_event(state, "overnight", detail)


def _plan_hold(state, ov, prof, sample, now_ts):
    o = prof["overnight"]
    ov["leave_ts"] = leave_ts(state, prof, now_ts)
    ov["topoff_start_ts"] = ov["leave_ts"] - estimate_topoff_s(state, sample, o["margin_min"])


def step(cfg, state, sample, now_ts):
    """Advance the machine one tick. Returns the phase. Only the tick and the
    control-class commands call this; status never does."""
    ov = ensure(state)
    record_charge_current(state, sample)
    prof = cfg["profiles"][cfg["general"]["active_profile"]]
    if not is_topoff_profile(prof) or sample["ac_online"] != 1:
        if ov["phase"] != "off":
            why = "unplugged" if is_topoff_profile(prof) else "profile changed"
            add_event(state, "overnight", f"{why}; overnight off")
        _reset(ov)
        return "off"
    o = prof["overnight"]
    hold = o["hold"]
    if ov["manual"] == "topoff" and ov["phase"] != "topping":
        ov["leave_ts"] = leave_ts(state, prof, now_ts)
        _enter(state, ov, "topping", now_ts, f"top-off started now; ready by {fmt_local(ov['leave_ts'])}")
        return ov["phase"]
    if ov["phase"] in ("off", "charging_full"):
        if ov["manual"] == "night" or in_window(now_ts, o["night_from"], o["leave_at"]):
            above = [f"{b} is at {v['capacity']}%, above the {hold[1]}% hold; the firmware cannot lower it"
                     for b, v in sorted(sample["bats"].items())
                     if v.get("capacity") is not None and v["capacity"] > hold[1]]
            _plan_hold(state, ov, prof, sample, now_ts)
            _enter(state, ov, "holding", now_ts,
                   f"holding {hold[0]}/{hold[1]}, top-off {fmt_local(ov['topoff_start_ts'])} "
                   f"for {fmt_local(ov['leave_ts'])}" + "".join("; " + a for a in above))
        elif ov["phase"] == "off":
            _enter(state, ov, "charging_full", now_ts,
                   f"charging to {prof['bands']['all'][1]}% until {o['night_from']}, then hold")
    if ov["phase"] == "holding":
        _plan_hold(state, ov, prof, sample, now_ts)
        if now_ts >= ov["topoff_start_ts"]:
            _enter(state, ov, "topping", now_ts, f"top-off started; ready by {fmt_local(ov['leave_ts'])}")
    return ov["phase"]


def apply_phase(profile, state, bands):
    """Pure: while holding, every present slot that is not pinned takes the
    hold band. Everything else is the profile's own band."""
    if not is_topoff_profile(profile):
        return bands
    ov = state.get("overnight") or {}
    if ov.get("phase") != "holding":
        return bands
    hold = clamp_band(*profile["overnight"]["hold"])
    pins = profile.get("pins", {})
    return {s: (hold if s not in pins else b) for s, b in bands.items()}


def describe(profile, state, sample, now_ts):
    """The one-line status of the overnight machinery, or None for a
    profile without an overnight table. Shared by status, the CLI actions
    and the applet."""
    o = profile.get("overnight")
    if not o:
        return None
    if o["mode"] == "full":
        return "overnight: mode full, packs stay at 100% on AC"
    ov = ensure(state)
    note = ""
    if ov["leave_at_override_ts"] is not None and ov["leave_at_override_ts"] > now_ts:
        note = f" (leaving at {fmt_local(ov['leave_at_override_ts'])} set)"
    if sample.get("ac_online") != 1:
        return (f"overnight: on battery; on AC it holds {o['hold'][0]}/{o['hold'][1]} from "
                f"{o['night_from']} and tops off for {o['leave_at']}{note}")
    ph = ov["phase"]
    if ph == "charging_full":
        return f"overnight: charging to {profile['bands']['all'][1]}% until {o['night_from']}, then hold{note}"
    if ph == "holding":
        mins = max(0, int((ov["topoff_start_ts"] - now_ts) // 60))
        return (f"overnight: holding {o['hold'][0]}/{o['hold'][1]}, top-off {fmt_local(ov['topoff_start_ts'])} "
                f"({mins // 60}h{mins % 60:02d}m) for {fmt_local(ov['leave_ts'])}{note}")
    if ph == "topping":
        return f"overnight: topping off, ready by {fmt_local(ov['leave_ts'])}{note}"
    return f"overnight: off{note}"


# ------------------------------------------------------- wake request

def wakealarm_text(state):
    ov = state.get("overnight") or {}
    ts = ov.get("topoff_start_ts") if ov.get("phase") == "holding" else None
    return f"{int(ts)}\n" if ts else ""


def write_wakealarm(state, path=None):
    """Rewritten every tick: the epoch the root wake helper should program
    into the RTC while holding, empty otherwise."""
    path = path or WAKEALARM_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(wakealarm_text(state))
    os.replace(tmp, path)
    _make_readable(path, 0o664)
```

- [ ] **Step 5: Wire the policy engine**

In `dbb/policy.py` add `from dbb import overnight, registry` (replace the existing `from dbb import registry`). In `resolve()`, replace the tail from `bands = _bands_for_profile(...)`:

```python
    bands = _bands_for_profile(profile, roles, present)
    if ptype == "conference":
        bands = overnight.apply_phase(profile, state, bands)
        why = f"{name}: overnight {(state.get('overnight') or {}).get('phase', 'off')}"
    return Resolution(bands=bands, profile=name, profile_type=ptype,
                      roles=roles, why=why, revert=revert)
```

`ptype == "balancing"` branch is unchanged; `"conference"` falls through it like `"fixed"`. In `switch_profile()` add before `add_event`:

```python
    overnight.ensure(state)["leave_at_override_ts"] = None
```

- [ ] **Step 6: Wire the CLI**

In `dbb/cli.py` import: `from dbb import VERSION, apply as apply_mod, config as cfg_mod, metrics, overnight, policy, registry, render`. Add after `_apply`:

```python
def _write_wakealarm(state):
    """Best effort like metrics: the wake request must never fail a tick."""
    try:
        overnight.write_wakealarm(state)
    except OSError as e:
        print(f"warning: wake request not written: {e}", file=sys.stderr)


def _step_resolve_apply(cfg, state, sample, now=None):
    """Advance the overnight machine, resolve bands for the active profile
    (after any switch the caller made), write them, record the wake request."""
    now = now or time.time()
    overnight.step(cfg, state, sample, now)
    res = policy.resolve(cfg, state, sample, now)
    r = _apply(cfg, state, sample, res)
    _write_wakealarm(state)
    return res, r
```

Replace the body of `_switch_and_apply` from `sample = sample_all()`:

```python
    sample = sample_all()
    res, r = _step_resolve_apply(cfg, state, sample, now)
    save_state(state)
    print(f"profile -> {name}: " + ", ".join(f"{s}={b[0]}/{b[1]}" for s, b in sorted(res.bands.items())))
    for s, e in r["errors"].items():
        print(f"  {s}: {e}", file=sys.stderr)
    return 0 if not r["errors"] else 2
```

In `cmd_tick`, replace from `now = time.time()`:

```python
    now = time.time()
    res = policy.resolve(cfg, state, sample, now)
    reverted = bool(res.revert)
    if reverted:
        policy.switch_profile(cfg, state, res.revert["to"], now, f"revert: {res.revert['reason']}")
        _save_config(cfg, state, "auto-revert")
    before = (state.get("overnight") or {}).get("phase", "off")
    phase = overnight.step(cfg, state, sample, now)
    res = policy.resolve(cfg, state, sample, now)     # bands now reflect the overnight phase
    if cfg["general"]["auto_balance"] or reverted or phase != "off" or before != "off":
        _apply(cfg, state, sample, res)
    _write_wakealarm(state)
    save_state(state)
    _write_metrics(state, sample, cfg)
```

In `dbb/render.py` `state_json`, add the temporary line (Task 9 expands it) after `out["revert"] = ...`:

```python
    ov = overnight.ensure(state)
    out["overnight"] = {"phase": ov["phase"], "topoff_start_ts": ov["topoff_start_ts"]}
```

with `from dbb import VERSION, overnight, policy, registry` at the top.

- [ ] **Step 7: Run the suite**

Run: `python3 -W error::ResourceWarning -m unittest discover -s tests 2>&1 | tail -4`
Expected: OK. If `test_end_to_end_auto_revert_on_tick` or other tick tests changed behaviour, the cause is the `before != "off"` apply condition — it must only apply when the machine was active; check `state["overnight"]` defaults to phase `off`.

- [ ] **Step 8: Commit**

```bash
git add dbb/overnight.py dbb/policy.py dbb/cli.py dbb/render.py tests/test_overnight.py tests/test_cli.py
git commit -m "overnight: four-phase state machine, policy override, wake request, tick wiring

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Quick actions — `topoff now`, `night`, `leave-at`

**Files:**
- Modify: `dbb/cli.py`
- Modify: `README.md` (`### CLI reference` rows)
- Test: `tests/test_cli.py`, `tests/test_cli_surface.py`

**Interfaces:**
- Consumes: `overnight.set_manual`, `overnight.set_leave_at`, `overnight.describe`, `_step_resolve_apply`.
- Produces: commands `topoff now`, `night`, `leave-at <HH:MM|none> [--tomorrow]`, all control class; exit 2 with a printed reason when the active profile is not a topoff conference profile, or (for the first two) when AC is off (spec §6).

- [ ] **Step 1: Write the failing tests**

Append to the `Overnight` class in `tests/test_cli.py`:

```python
    def test_topoff_now_from_holding(self):
        with mock.patch("time.time", return_value=self.at(23, 30)):
            self.run_cli("tick")
            code, out, err = self.run_cli("topoff", "now")
        self.assertEqual(code, 0, err)
        self.assertIn("topping off, ready by 07:00", out)
        self.assertEqual(self.stop_value("BAT1"), "100")
        self.assertEqual(self.wakealarm(), "")

    def test_night_from_charging_full(self):
        with mock.patch("time.time", return_value=self.at(20, 0)):
            self.run_cli("tick")
            code, out, err = self.run_cli("night")
        self.assertEqual(code, 0, err)
        self.assertIn("holding 70/80", out)
        self.assertEqual(self.stop_value("BAT1"), "80")

    def test_quick_actions_refuse_on_battery(self):
        self.fs.ac(0)
        for argv in (["topoff", "now"], ["night"]):
            with self.subTest(argv=argv):
                code, _, err = self.run_cli(*argv)
                self.assertEqual(code, 2)
                self.assertIn("not on AC", err)

    def test_quick_actions_refuse_outside_a_topoff_profile(self):
        self.run_cli("profile", "set", "field")
        for argv in (["topoff", "now"], ["night"], ["leave-at", "06:00"]):
            with self.subTest(argv=argv):
                code, _, err = self.run_cli(*argv)
                self.assertEqual(code, 2)
                self.assertIn("not a conference profile", err)

    def test_leave_at_sets_clears_and_replans(self):
        with mock.patch("time.time", return_value=self.at(23, 30)):
            self.run_cli("tick")
            code, out, err = self.run_cli("leave-at", "06:00")
            self.assertEqual(code, 0, err)
            self.assertIn("for 06:00", out)
            self.assertIn("(leaving at 06:00 set)", out)
            j = self.status()
            self.assertEqual(j["overnight"]["topoff_start_ts"] < self.at(6, 0, day=6), True)
            code, out, _ = self.run_cli("leave-at", "none")
            self.assertEqual(code, 0)
            self.assertIn("for 07:00", out)
            self.assertNotIn("leaving at", out)

    def test_leave_at_tomorrow_and_bad_time(self):
        with mock.patch("time.time", return_value=self.at(3, 0)):
            code, out, _ = self.run_cli("leave-at", "07:30", "--tomorrow")
            self.assertEqual(code, 0)
            code, _, err = self.run_cli("leave-at", "7am")
            self.assertEqual(code, 1)
            self.assertIn("bad time", err)

    def test_quick_actions_are_control_class(self):
        with mock.patch("time.time", return_value=self.at(20, 0)):
            code, _, _ = self.run_cli("--polkit-class", "control", "--", "night")
        self.assertEqual(code, 0)
```

In `tests/test_cli_surface.py`: change `GROUPS = ("profile", "config", "pack", "topoff")` and add to `DOCUMENTED`:

```python
    ["topoff", "now"], ["night"], ["leave-at", "06:00"], ["leave-at", "06:00", "--tomorrow"], ["leave-at", "none"],
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests.test_cli.Overnight tests.test_cli_surface 2>&1 | tail -5`
Expected: FAIL (`invalid choice: 'topoff'`).

- [ ] **Step 3: Implement**

In `dbb/cli.py` add after `cmd_restore`:

```python
def _require_topoff_profile(cfg):
    prof = cfg["profiles"][cfg["general"]["active_profile"]]
    if not overnight.is_topoff_profile(prof):
        die("error: the active profile is not a conference profile in topoff mode "
            "(profile create defcon --template conference; profile set defcon)", 2)
    return prof


def _quick(which):
    state, cfg, s = _view()
    prof = _require_topoff_profile(cfg)
    if s["ac_online"] != 1:
        die("error: not on AC; nothing to hold or top off", 2)
    overnight.set_manual(state, which)
    now = time.time()
    res, r = _step_resolve_apply(cfg, state, s, now)
    save_state(state)
    print(overnight.describe(prof, state, s, now))
    for slot, e in r["errors"].items():
        print(f"  {slot}: {e}", file=sys.stderr)
    sys.exit(0 if not r["errors"] else 2)


def cmd_topoff_now(args):
    _quick("topoff")


def cmd_night(args):
    _quick("night")


def cmd_leave_at(args):
    state, cfg, s = _view()
    prof = _require_topoff_profile(cfg)
    now = time.time()
    if args.when.strip().lower() in ("none", "off", "clear"):
        overnight.set_leave_at(state, None, now)
        add_event(state, "overnight", "leave-at override cleared")
    else:
        try:
            ts = overnight.set_leave_at(state, args.when, now, tomorrow=args.tomorrow)
        except ValueError as e:
            die(f"error: {e}")
        add_event(state, "overnight", f"leaving at {time.strftime('%a %H:%M', time.localtime(ts))}")
    if s["ac_online"] == 1:
        _step_resolve_apply(cfg, state, s, now)
    save_state(state)
    print(overnight.describe(prof, state, s, now))
```

In `build_parser()` after the `restore` line:

```python
    tp = sub.add_parser("topoff", help="conference profile: top off now").add_subparsers(dest="tcmd", required=True)
    tp.add_parser("now", help="charge both packs to 100% now and stay there until unplugged").set_defaults(func=cmd_topoff_now, cls="control")
    sub.add_parser("night", help="conference profile: in for the night, start the hold now").set_defaults(func=cmd_night, cls="control")
    sp = sub.add_parser("leave-at", help="conference profile: one-off departure time for the next top-off")
    sp.add_argument("when", metavar="HH:MM|none")
    sp.add_argument("--tomorrow", action="store_true", help="tomorrow's HH:MM even if today's is still ahead")
    sp.set_defaults(func=cmd_leave_at, cls="control")
```

README `### CLI reference`: add after the `restore` row:

```
| `topoff now` | conference profile: lift the overnight hold now, charge both packs to 100% and stay there until unplugged — for going back out to the CTF |
| `night` | conference profile: in for the night — start the hold now instead of waiting for `night_from` |
| `leave-at <HH:MM \| none> [--tomorrow]` | conference profile: one-off departure time for the next top-off (the next occurrence of HH:MM; `--tomorrow` forces tomorrow's); `none` goes back to the profile's `leave_at` |
```

- [ ] **Step 4: Run the suite**

Run: `python3 -W error::ResourceWarning -m unittest discover -s tests 2>&1 | tail -4`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add dbb/cli.py README.md tests/test_cli.py tests/test_cli_surface.py
git commit -m "topoff now / night / leave-at: the conference profile's quick actions

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: The root wake helper — RTC alarm and guarded re-suspend

**Files:**
- Create: `libexec/dell-battery-balance-wake`
- Modify: `systemd/dell-battery-balance.service`, `install.sh`, `uninstall.sh`
- Create: `tests/test_wake_helper.py`

**Interfaces:**
- Consumes: `STATE_DIR/wakealarm` (Task 5).
- Produces: `STATE_DIR/wakealarm.set` marker (root-owned). Env overrides for tests: `DBB_STATE_DIR`, `DBB_RTC_PATH`, `DBB_LID_GLOB`, `DBB_CONFIG_FILE`, `DBB_SUSPEND_CMD`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_wake_helper.py`:

```python
"""The root-only wake helper, run against temp files instead of the RTC."""
import os, subprocess, tempfile, time, unittest

HELPER = os.path.join(os.path.dirname(__file__), os.pardir, "libexec", "dell-battery-balance-wake")


class WakeHelper(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.state = os.path.join(d, "state"); os.makedirs(self.state)
        self.rtc = os.path.join(d, "wakealarm"); open(self.rtc, "w").close()
        self.lid = os.path.join(d, "lid", "LID0", "state"); os.makedirs(os.path.dirname(self.lid))
        self.config = os.path.join(d, "config.toml")
        self.suspended = os.path.join(d, "suspended")
        self.env = dict(os.environ, DBB_STATE_DIR=self.state, DBB_RTC_PATH=self.rtc,
                        DBB_LID_GLOB=os.path.join(d, "lid", "*", "state"), DBB_CONFIG_FILE=self.config,
                        DBB_SUSPEND_CMD=f"touch {self.suspended}")
        self.now = int(time.time())

    def tearDown(self):
        self.tmp.cleanup()

    def run_helper(self):
        r = subprocess.run(["sh", HELPER], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def req(self, text):
        with open(os.path.join(self.state, "wakealarm"), "w") as fh:
            fh.write(text)

    def mark(self, value=None):
        p = os.path.join(self.state, "wakealarm.set")
        if value is None:
            return open(p).read().strip() if os.path.exists(p) else None
        with open(p, "w") as fh:
            fh.write(f"{value}\n")

    def rtc_text(self):
        return open(self.rtc).read().strip()

    def lid_state(self, s):
        with open(self.lid, "w") as fh:
            fh.write(f"state:      {s}\n")

    def test_programs_a_future_alarm_and_records_it(self):
        want = self.now + 3600
        self.req(f"{want}\n")
        self.run_helper()
        self.assertEqual(self.rtc_text(), str(want))
        self.assertEqual(self.mark(), str(want))

    def test_rewrites_only_when_the_value_changes(self):
        want = self.now + 3600
        self.req(f"{want}\n")
        self.run_helper()
        with open(self.rtc, "w") as fh:
            fh.write("sentinel")
        self.run_helper()
        self.assertEqual(self.rtc_text(), "sentinel")
        self.req(f"{want + 60}\n")
        self.run_helper()
        self.assertEqual(self.rtc_text(), str(want + 60))

    def test_refuses_past_far_and_garbage(self):
        for bad in (f"{self.now - 10}\n", f"{self.now + 90000}\n", "soon\n", "", "12 34\n"):
            with self.subTest(bad=bad):
                self.req(bad)
                self.run_helper()
                self.assertEqual(self.rtc_text(), "")
                self.assertIsNone(self.mark())

    def test_clears_an_alarm_it_set_when_the_request_goes_away(self):
        self.mark(self.now + 3600)
        self.req("")
        self.run_helper()
        self.assertEqual(self.rtc_text(), "0")
        self.assertIsNone(self.mark())

    def test_never_touches_an_alarm_it_did_not_set(self):
        with open(self.rtc, "w") as fh:
            fh.write("1234567890")
        self.req("")
        self.run_helper()
        self.assertEqual(self.rtc_text(), "1234567890")

    def test_resuspends_after_its_own_wake_with_the_lid_closed(self):
        self.mark(self.now - 60)
        self.req("")
        self.lid_state("closed")
        self.run_helper()
        self.assertTrue(os.path.exists(self.suspended))
        self.assertIsNone(self.mark())
        self.assertEqual(self.rtc_text(), "")          # a fired alarm needs no clearing

    def test_no_resuspend_with_the_lid_open(self):
        self.mark(self.now - 60)
        self.req("")
        self.lid_state("open")
        self.run_helper()
        self.assertFalse(os.path.exists(self.suspended))
        self.assertIsNone(self.mark())

    def test_no_resuspend_when_config_says_so(self):
        self.mark(self.now - 60)
        self.req("")
        self.lid_state("closed")
        with open(self.config, "w") as fh:
            fh.write("[general]\ntopoff_resuspend = false\n")
        self.run_helper()
        self.assertFalse(os.path.exists(self.suspended))

    def test_stale_marker_is_a_clear_not_a_suspend(self):
        self.mark(self.now - 600)
        self.req("")
        self.lid_state("closed")
        self.run_helper()
        self.assertFalse(os.path.exists(self.suspended))
        self.assertEqual(self.rtc_text(), "0")
        self.assertIsNone(self.mark())

    def test_wake_still_holding_reprograms_instead_of_suspending(self):
        # The estimate moved later after the wake: the request is still there.
        self.mark(self.now - 60)
        self.req(f"{self.now + 900}\n")
        self.lid_state("closed")
        self.run_helper()
        self.assertFalse(os.path.exists(self.suspended))
        self.assertEqual(self.rtc_text(), str(self.now + 900))
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests.test_wake_helper 2>&1 | tail -4`
Expected: FAIL (`sh: ... No such file`).

- [ ] **Step 3: Create the helper**

Create `libexec/dell-battery-balance-wake` (mode 755):

```sh
#!/bin/sh
# The second and last piece of root code in dell-battery-balance, run as
# ExecStartPost=+ after every tick. It turns the service's wake request
# (STATE_DIR/wakealarm: an epoch while the conference profile is holding,
# empty otherwise) into an RTC alarm, clears only alarms it set itself
# (STATE_DIR/wakealarm.set is the proof of ownership), and after a wake it
# caused -- lid still closed, config not opting out -- puts the machine back
# to sleep. Every path is bounded: 32 bytes read, integer only, at most 24 h
# ahead, never in the past.
set -u
STATE=${DBB_STATE_DIR:-/var/lib/dell-battery-balance}
RTC=${DBB_RTC_PATH:-/sys/class/rtc/rtc0/wakealarm}
LID_GLOB=${DBB_LID_GLOB:-/proc/acpi/button/lid/*/state}
CONFIG=${DBB_CONFIG_FILE:-/etc/dell-battery-balance/config.toml}
SUSPEND=${DBB_SUSPEND_CMD:-systemctl suspend --no-block}
REQ=$STATE/wakealarm
MARK=$STATE/wakealarm.set
RESUSPEND_WINDOW=180

now=$(date +%s)

want=""
if [ -f "$REQ" ]; then
    v=$(head -c 32 "$REQ" | tr -d '[:space:]')
    case "$v" in
        ''|*[!0-9]*) v="" ;;
    esac
    if [ -n "$v" ] && [ "$v" -gt "$now" ] && [ $((v - now)) -le 86400 ]; then
        want=$v
    fi
fi

have=""
if [ -f "$MARK" ]; then
    have=$(head -c 32 "$MARK" | tr -d '[:space:]')
    case "$have" in
        ''|*[!0-9]*) have="" ;;
    esac
fi

if [ -n "$want" ]; then
    if [ "$want" != "$have" ] && [ -w "$RTC" ]; then
        # A pending alarm must be cleared before a new one is accepted.
        echo 0 > "$RTC" 2>/dev/null
        if echo "$want" > "$RTC" 2>/dev/null; then
            printf '%s\n' "$want" > "$MARK"
        fi
    fi
    exit 0
fi

[ -n "$have" ] || exit 0

if [ "$now" -ge "$have" ] && [ $((now - have)) -le "$RESUSPEND_WINDOW" ]; then
    # Our alarm just fired and the tick has moved on (no request left).
    rm -f "$MARK"
    lid_closed=0
    for f in $LID_GLOB; do
        [ -f "$f" ] && grep -q closed "$f" && lid_closed=1
    done
    resuspend=1
    if [ -s "$CONFIG" ] && head -c 65536 "$CONFIG" | grep -Eq '^topoff_resuspend *= *false'; then
        resuspend=0
    fi
    if [ "$lid_closed" -eq 1 ] && [ "$resuspend" -eq 1 ]; then
        $SUSPEND
    fi
    exit 0
fi

# The request went away before the alarm fired (unplugged, topped off by
# hand, profile changed): clear the alarm we set.
[ -w "$RTC" ] && echo 0 > "$RTC" 2>/dev/null
rm -f "$MARK"
exit 0
```

Run `chmod 755 libexec/dell-battery-balance-wake`.

- [ ] **Step 4: Service, install, uninstall**

`systemd/dell-battery-balance.service`: add after the `ExecStart=` line:

```ini
ExecStartPost=+/usr/local/libexec/dell-battery-balance-wake
```

`install.sh`: after the grant helper install line add:

```bash
install -Dm755 "$src/libexec/dell-battery-balance-wake"  /usr/local/libexec/dell-battery-balance-wake
```

`uninstall.sh`: before the `rm -f /usr/local/libexec/dell-battery-balance-grant` block add:

```bash
# Clear an RTC alarm the tool set (the marker is its proof of ownership).
if [[ -f /var/lib/$SVC/wakealarm.set && -w /sys/class/rtc/rtc0/wakealarm ]]; then
    echo 0 > /sys/class/rtc/rtc0/wakealarm 2>/dev/null || true
    rm -f /var/lib/$SVC/wakealarm.set
fi
```

and add `/usr/local/libexec/dell-battery-balance-wake \` to the `rm -f` list of helpers.

- [ ] **Step 5: Run the suite**

Run: `python3 -W error::ResourceWarning -m unittest discover -s tests 2>&1 | tail -4`
Expected: OK. Also `sh -n libexec/dell-battery-balance-wake` prints nothing.

- [ ] **Step 6: Commit**

```bash
git add libexec/dell-battery-balance-wake systemd/dell-battery-balance.service install.sh uninstall.sh tests/test_wake_helper.py
git commit -m "Wake helper: RTC alarm for the top-off, owned-alarm clearing, guarded re-suspend

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Revert flexibility — still-going-out guard, warning + `profile extend`, `--until`

**Files:**
- Modify: `dbb/wear.py`, `dbb/state.py`, `dbb/policy.py`, `dbb/cli.py`, `dbb/render.py`
- Modify: `README.md` (`### CLI reference` rows)
- Test: `tests/test_wear_model.py`, `tests/test_policy.py`, `tests/test_cli.py`, `tests/test_cli_surface.py`

**Interfaces:**
- Produces: `wear.ACTIVE_DAY_STINT_MIN = 30`, `wear.ACTIVE_DAY_WINDOW_H = 24`; state keys `battery_run_start_ts`, `last_battery_stint_end_ts`, `revert_warned_ts`; `policy.active_day(state, now) -> bool`; `policy.revert_due` honours the guard for `on_ac_hours`; `policy.revert_eta(profile, state, now) -> hours|None`; `policy.revert_soon(profile, state, now, within_h=1.0) -> bool`; `policy.extend(cfg, state, hours, now)` raising `policy.PolicyError`; `cli.parse_until(text, now_ts) -> hours`; `profile set --until`, `field --until`, `profile extend <duration>`; `render._revert_info` gains `eta_hours`, `warning`, `guard_blocking`, `last_battery_stint_end_ts`; tick adds one `revert-warning` event per switch.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_wear_model.py` (the file's `AcRunTracking` class shows the sample shape; reuse its helpers if any, else this is self-contained):

```python
class BatteryStintTracking(unittest.TestCase):
    def setUp(self):
        for m in list(sys.modules):
            if m.startswith("dbb"):
                del sys.modules[m]
        from dbb import wear, state as st
        self.wear, self.state = wear, st.new_state()

    def s(self, ts, ac):
        return {"ts": ts, "ac_online": ac, "bats": {}}

    def test_short_battery_run_does_not_count(self):
        self.wear._track_ac_run(self.state, None, self.s(0, 1))
        self.wear._track_ac_run(self.state, self.s(0, 1), self.s(100, 0))
        self.wear._track_ac_run(self.state, self.s(100, 0), self.s(100 + 20 * 60, 1))
        self.assertIsNone(self.state["last_battery_stint_end_ts"])
        self.assertIsNone(self.state["battery_run_start_ts"])

    def test_thirty_minute_stint_is_recorded_when_ac_returns(self):
        self.wear._track_ac_run(self.state, None, self.s(0, 1))
        self.wear._track_ac_run(self.state, self.s(0, 1), self.s(100, 0))
        self.assertEqual(self.state["battery_run_start_ts"], 100)
        end = 100 + 30 * 60
        self.wear._track_ac_run(self.state, self.s(100, 0), self.s(end, 1))
        self.assertEqual(self.state["last_battery_stint_end_ts"], end)
        self.assertIsNone(self.state["battery_run_start_ts"])
        self.assertEqual(self.state["ac_run_start_ts"], end)
```

Append to `tests/test_policy.py` inside class `Revert` (it switches to `field` at `now=0.0`):

```python
    def test_on_ac_guarded_by_a_recent_battery_stint(self):
        self.state["ac_run_start_ts"] = 100.0
        self.state["last_battery_stint_end_ts"] = 100.0
        now = 100.0 + 12 * 3600.0
        self.assertIsNone(policy.revert_due(self.cfg["profiles"]["field"], self.state, now=now))
        self.assertTrue(policy.active_day(self.state, now))
        self.state["last_battery_stint_end_ts"] = now - 25 * 3600.0
        self.assertEqual(policy.revert_due(self.cfg["profiles"]["field"], self.state, now=now), "on_ac_hours")

    def test_guard_does_not_touch_after_hours(self):
        self.state["last_battery_stint_end_ts"] = 72 * 3600.0
        self.assertEqual(policy.revert_due(self.cfg["profiles"]["field"], self.state, now=72 * 3600.0 + 1), "after_hours")

    def test_eta_and_soon(self):
        f = self.cfg["profiles"]["field"]
        self.assertAlmostEqual(policy.revert_eta(f, self.state, now=70 * 3600.0), 2.0)
        self.assertFalse(policy.revert_soon(f, self.state, now=70 * 3600.0))
        self.assertTrue(policy.revert_soon(f, self.state, now=71.5 * 3600.0))
        self.assertFalse(policy.revert_soon(f, self.state, now=72.5 * 3600.0))   # already due, not "soon"
        self.state["ac_run_start_ts"] = 60 * 3600.0
        self.assertAlmostEqual(policy.revert_eta(f, self.state, now=71.5 * 3600.0), 0.5)   # on-AC is sooner
        self.state["last_battery_stint_end_ts"] = 71 * 3600.0
        self.assertAlmostEqual(policy.revert_eta(f, self.state, now=71.5 * 3600.0), 0.5)   # guard drops on-AC: after_hours left

    def test_eta_for_one_off_and_stay(self):
        f = self.cfg["profiles"]["field"]
        self.state["one_off_revert_hours"] = 8
        self.assertAlmostEqual(policy.revert_eta(f, self.state, now=6 * 3600.0), 2.0)
        self.state["one_off_revert_hours"] = 0
        self.assertIsNone(policy.revert_eta(f, self.state, now=6 * 3600.0))

    def test_extend_moves_the_switch_forward_and_restarts_the_ac_clock(self):
        self.state["ac_run_start_ts"] = 100.0
        self.state["revert_warned_ts"] = 0.0
        policy.extend(self.cfg, self.state, 24.0, now=1000.0)
        self.assertEqual(self.state["profile_switched_ts"], 24 * 3600.0)
        self.assertEqual(self.state["ac_run_start_ts"], 1000.0)
        self.assertIsNone(self.state["revert_warned_ts"])
        self.assertIn("extended by 24h", self.state["events"][-1]["detail"])
        self.state["ac_run_start_ts"] = None
        policy.extend(self.cfg, self.state, 1.0, now=2000.0)
        self.assertIsNone(self.state["ac_run_start_ts"])

    def test_extend_refuses_when_nothing_reverts(self):
        policy.switch_profile(self.cfg, self.state, "daily", now=5.0, reason="test")
        with self.assertRaises(policy.PolicyError):
            policy.extend(self.cfg, self.state, 1.0, now=10.0)
        policy.switch_profile(self.cfg, self.state, "field", now=20.0, reason="test")
        self.state["one_off_revert_hours"] = 0.0
        with self.assertRaises(policy.PolicyError):
            policy.extend(self.cfg, self.state, 1.0, now=30.0)

    def test_switch_clears_warning_marker(self):
        self.state["revert_warned_ts"] = 1.0
        policy.switch_profile(self.cfg, self.state, "daily", now=5.0, reason="test")
        self.assertIsNone(self.state["revert_warned_ts"])
```

Append to `tests/test_cli.py`:

```python
class Until(unittest.TestCase):
    def setUp(self):
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = "America/New_York"
        time.tzset()
        for m in list(sys.modules):
            if m.startswith("dbb"):
                del sys.modules[m]
        from dbb import cli
        self.cli = cli
        self.now = time.mktime((2026, 8, 5, 20, 0, 0, 0, 0, -1))

    def tearDown(self):
        if self._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._tz
        time.tzset()

    def test_date_means_end_of_that_day(self):
        self.assertAlmostEqual(self.cli.parse_until("2026-08-10", self.now), (5 * 24 + 3) + 59 / 60, places=3)

    def test_date_and_time(self):
        self.assertAlmostEqual(self.cli.parse_until("2026-08-06 06:30", self.now), 10.5)
        self.assertAlmostEqual(self.cli.parse_until("2026-08-06T06:30", self.now), 10.5)

    def test_time_only_is_next_occurrence(self):
        self.assertAlmostEqual(self.cli.parse_until("21:00", self.now), 1.0)
        self.assertAlmostEqual(self.cli.parse_until("07:00", self.now), 11.0)

    def test_past_and_garbage(self):
        for bad in ("2026-08-01", "2026-08-05 19:00", "next week", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.cli.parse_until(bad, self.now)


class RevertFlex(CliBase):
    def state_json(self):
        with open(os.path.join(os.environ["DBB_STATE_DIR"], "state.json")) as fh:
            return json.load(fh)

    def write_state(self, st):
        with open(os.path.join(os.environ["DBB_STATE_DIR"], "state.json"), "w") as fh:
            json.dump(st, fh)

    def test_until_arms_a_one_off(self):
        code, out, err = self.run_cli("field", "--until", "2099-01-01")
        self.assertEqual(code, 0, err)
        st = self.state_json()
        self.assertGreater(st["one_off_revert_hours"], 24 * 365 * 50)
        code, _, err = self.run_cli("profile", "set", "field", "--until", "2000-01-01")
        self.assertEqual(code, 1)
        self.assertIn("in the past", err)

    def test_until_for_stay_are_exclusive(self):
        code, _, _ = self.run_cli("field", "--until", "2099-01-01", "--for", "1h")
        self.assertEqual(code, 2)

    def test_warning_event_once_per_switch_and_extend_clears_it(self):
        code, _, err = self.run_cli("field", "--for", "2h")
        self.assertEqual(code, 0, err)
        st = self.state_json()
        st["profile_switched_ts"] -= 1.5 * 3600
        self.write_state(st)
        self.run_cli("tick")
        self.run_cli("tick")
        evs = [e for e in self.state_json()["events"] if e["kind"] == "revert-warning"]
        self.assertEqual(len(evs), 1)
        self.assertIn("reverts in about 30 min", evs[0]["detail"])
        j = self.status()
        self.assertTrue(j["revert"]["warning"])
        code, out, err = self.run_cli("profile", "extend", "24h")
        self.assertEqual(code, 0, err)
        self.assertIn("extended", out)
        j = self.status()
        self.assertFalse(j["revert"]["warning"])
        self.assertGreater(j["revert"]["after_hours_left"], 24)
        self.run_cli("tick")
        evs = [e for e in self.state_json()["events"] if e["kind"] == "revert-warning"]
        self.assertEqual(len(evs), 1)

    def test_extend_refuses_with_nothing_to_extend(self):
        code, _, err = self.run_cli("profile", "extend", "1h")
        self.assertEqual(code, 1)
        self.assertIn("nothing to extend", err)

    def test_guard_shows_in_status(self):
        self.run_cli("field")
        st = self.state_json()
        st["ac_run_start_ts"] = time.time() - 13 * 3600
        st["last_battery_stint_end_ts"] = time.time() - 3600
        self.write_state(st)
        code, out, _ = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("on-AC revert held: on battery 1h ago", out)
        j = self.status()
        self.assertTrue(j["revert"]["guard_blocking"])
        self.assertEqual(j["profile"]["name"], "field")
```

In `tests/test_cli_surface.py` `DOCUMENTED` add:

```python
    ["profile", "set", "field", "--until", "2026-08-10"], ["field", "--until", "2026-08-10 07:00"],
    ["profile", "extend", "24h"],
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests.test_wear_model.BatteryStintTracking tests.test_policy.Revert tests.test_cli.Until tests.test_cli.RevertFlex tests.test_cli_surface 2>&1 | tail -5`
Expected: FAIL.

- [ ] **Step 3: Implement wear + state**

`dbb/wear.py`: add constants after `GAP_FLAG_SECONDS`:

```python
# A battery stint at least this long within this window means "still going
# out daily": the on-AC revert trigger does not fire during it (spec §5).
ACTIVE_DAY_STINT_MIN = 30
ACTIVE_DAY_WINDOW_H = 24
```

Replace `_track_ac_run`:

```python
def _track_ac_run(state, last, s):
    ac = s["ac_online"]
    if ac != 1:
        state["ac_run_start_ts"] = None
        if state.get("battery_run_start_ts") is None:
            state["battery_run_start_ts"] = s["ts"]
        return
    start = state.get("battery_run_start_ts")
    if start is not None:
        if s["ts"] - start >= ACTIVE_DAY_STINT_MIN * 60:
            state["last_battery_stint_end_ts"] = s["ts"]
        state["battery_run_start_ts"] = None
    if last is None or last["ac_online"] != 1 or state.get("ac_run_start_ts") is None:
        # A gap with AC on both sides is still one run: the charger held SoC
        # the whole time, so the pack was floating throughout.
        state["ac_run_start_ts"] = s["ts"]
```

`dbb/state.py` `new_state()`: add `"battery_run_start_ts": None, "last_battery_stint_end_ts": None, "revert_warned_ts": None,` after `"one_off_revert_hours": None,`.

- [ ] **Step 4: Implement policy**

`dbb/policy.py`: import `from dbb.wear import ACTIVE_DAY_STINT_MIN, ACTIVE_DAY_WINDOW_H` (wear does not import policy, so no cycle). Add:

```python
class PolicyError(ValueError):
    pass


def active_day(state, now):
    end = state.get("last_battery_stint_end_ts")
    return end is not None and (now - end) < ACTIVE_DAY_WINDOW_H * 3600.0
```

In `revert_due`, replace the `on_ac` block:

```python
    on_ac = rv.get("on_ac_hours")
    run = state.get("ac_run_start_ts")
    if on_ac and run is not None and (now - run) / 3600.0 >= on_ac and not active_day(state, now):
        return "on_ac_hours"
    return None
```

Add after `revert_due`:

```python
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
```

In `switch_profile` add `state["revert_warned_ts"] = None` next to `state["one_off_revert_hours"] = None`.

- [ ] **Step 5: Implement CLI**

`dbb/cli.py`: `PROFILE_SUBCOMMANDS = ("list", "show", "set", "create", "edit", "delete", "extend")`. Add after `parse_duration`:

```python
def parse_until(text, now_ts):
    """`YYYY-MM-DD` (end of that local day), `YYYY-MM-DD HH:MM` /
    `YYYY-MM-DDTHH:MM`, or `HH:MM` (next occurrence) -> hours from now_ts."""
    t = (text or "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d", "%H:%M"):
        try:
            d = datetime.strptime(t, fmt)
        except ValueError:
            continue
        if fmt == "%H:%M":
            lt = time.localtime(now_ts)
            target = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, d.hour, d.minute, 0, 0, 0, -1))
            if target <= now_ts:
                target = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + 1, d.hour, d.minute, 0, 0, 0, -1))
        else:
            h, m = (23, 59) if fmt == "%Y-%m-%d" else (d.hour, d.minute)
            target = time.mktime((d.year, d.month, d.day, h, m, 0, 0, 0, -1))
        hours = (target - now_ts) / 3600.0
        if hours <= 0:
            raise ValueError(f"{text!r} is in the past")
        return hours
    raise ValueError(f"bad time {text!r}; use YYYY-MM-DD, 'YYYY-MM-DD HH:MM' or HH:MM")
```

Replace `_one_off_hours`:

```python
def _one_off_hours(args):
    """--stay -> 0.0 (no automatic revert this switch); --for/--until ->
    hours; none -> None (the profile's own [revert] table applies)."""
    if getattr(args, "stay", False):
        return 0.0
    try:
        if getattr(args, "for_", None):
            return parse_duration(args.for_)
        if getattr(args, "until", None):
            return parse_until(args.until, time.time())
    except ValueError as e:
        die(f"error: {e}")
    return None
```

Add command:

```python
def cmd_profile_extend(args):
    state, cfg, _ = _view()
    try:
        hours = parse_duration(args.duration)
        policy.extend(cfg, state, hours, time.time())
    except (ValueError, policy.PolicyError) as e:
        die(f"error: {e}")
    save_state(state)
    print(f"{cfg['general']['active_profile']}: revert extended by {args.duration}")
```

Parser: in both the `profile set` and `field` mutually exclusive groups add

```python
    g.add_argument("--until", metavar="WHEN",
                   help="revert at this local time: YYYY-MM-DD (end of day), 'YYYY-MM-DD HH:MM', or HH:MM (next)")
```

and after the `profile delete` line:

```python
    sp = pr.add_parser("extend"); sp.add_argument("duration", metavar="DURATION")
    sp.set_defaults(func=cmd_profile_extend, cls="control")
```

In `cmd_tick`, after the second `res = policy.resolve(...)` line and before the apply condition:

```python
    active = cfg["profiles"][cfg["general"]["active_profile"]]
    if (policy.revert_soon(active, state, now)
            and state.get("revert_warned_ts") != state.get("profile_switched_ts")):
        state["revert_warned_ts"] = state.get("profile_switched_ts")
        mins = int(round(policy.revert_eta(active, state, now) * 60))
        add_event(state, "revert-warning",
                  f"{cfg['general']['active_profile']} reverts in about {mins} min; "
                  f"'profile extend 24h' keeps it")
```

README `### CLI reference`: change the `profile set` row and the `field` row to mention `--until`, and add the extend row:

```
| `profile set <name> [--for <duration> \| --until <when> \| --stay]` | switch profiles and apply immediately; `--for` reverts after that long, `--until` at a local date/time (`2026-08-10`, `2026-08-10 07:00`, `07:00`), `--stay` never — any of them replacing the profile's own triggers for this switch |
| `profile extend <duration>` | push the active profile's auto-revert out by that long (the on-AC clock restarts too); refused when nothing would revert |
| `field [--for <duration> \| --until <when> \| --stay]` | alias: `profile set field` |
```

- [ ] **Step 6: Implement render**

In `dbb/render.py` replace `_revert_info`'s return with:

```python
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
```

In `fmt_status`, replace the `revert` block:

```python
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
```

- [ ] **Step 7: Run the suite**

Run: `python3 -W error::ResourceWarning -m unittest discover -s tests 2>&1 | tail -4`
Expected: OK. If `test_guard_shows_in_status` prints `0h ago`, the `ago` computation is right but the fixture is `3600` s → `1h`; check `f"{ago:.0f}"`.

- [ ] **Step 8: Commit**

```bash
git add dbb/wear.py dbb/state.py dbb/policy.py dbb/cli.py dbb/render.py README.md tests/
git commit -m "Revert flexibility: still-going-out guard, hour-out warning with profile extend, --until

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Status, JSON and metrics surface

**Files:**
- Modify: `dbb/render.py`, `dbb/metrics.py`
- Test: `tests/test_cli.py`, `tests/test_metrics.py`

**Interfaces:**
- Produces: `status` prints the `overnight:` line from `overnight.describe` for a conference profile; `state_json()["overnight"]` = `{active, mode, phase, hold, leave_at, night_from, leave_ts, topoff_start_ts, estimate_s, manual, leave_at_override_ts, text, actions}` (for a profile without the table: `{"active": False, "mode": None, "phase": "off", "text": None, "actions": False}` plus the other keys as None); `state_json()["field_mode"]` is true for conference profiles too; metrics `dbb_overnight_phase{phase}`, `dbb_topoff_start_timestamp_seconds`, `dbb_leave_timestamp_seconds`, `dbb_revert_warning`.

- [ ] **Step 1: Write the failing tests**

Append to the `Overnight` class in `tests/test_cli.py`:

```python
    def test_status_json_and_text_carry_the_overnight_view(self):
        with mock.patch("time.time", return_value=self.at(23, 30)):
            self.run_cli("tick")
            j = self.status()
            code, out, _ = self.run_cli("status")
        self.assertEqual(code, 0)
        ov = j["overnight"]
        self.assertTrue(ov["active"])
        self.assertEqual(ov["mode"], "topoff")
        self.assertEqual(ov["phase"], "holding")
        self.assertEqual(ov["hold"], [70, 80])
        self.assertEqual(ov["leave_at"], "07:00")
        self.assertEqual(ov["night_from"], "23:00")
        self.assertTrue(ov["actions"])
        self.assertIsNone(ov["manual"])
        self.assertGreater(ov["estimate_s"], 1800)
        self.assertTrue(ov["text"].startswith("overnight: holding 70/80"))
        self.assertIn(ov["text"], out)
        self.assertTrue(j["field_mode"])
        self.assertEqual(j["profile"]["type"], "conference")

    def test_status_json_for_a_plain_profile(self):
        self.run_cli("profile", "set", "daily")
        j = self.status()
        self.assertEqual(j["overnight"], {"active": False, "mode": None, "phase": "off", "text": None,
                                          "actions": False, "hold": None, "leave_at": None, "night_from": None,
                                          "leave_ts": None, "topoff_start_ts": None, "estimate_s": None,
                                          "manual": None, "leave_at_override_ts": None})
        self.assertFalse(j["field_mode"])
```

Append to `tests/test_metrics.py` (the `view()` helper returns a dict; add keys to a copy):

```python
class OvernightSeries(unittest.TestCase):
    def test_phase_one_hot_and_timestamps(self):
        j = view()
        j["overnight"] = {"active": True, "mode": "topoff", "phase": "holding", "topoff_start_ts": 1789400000,
                          "leave_ts": 1789405400}
        j["revert"] = {"warning": True}
        text = metrics.render_prometheus(j)
        self.assertIn('dbb_overnight_phase{phase="holding"} 1', text)
        self.assertIn('dbb_overnight_phase{phase="off"} 0', text)
        self.assertIn("dbb_topoff_start_timestamp_seconds 1789400000", text)
        self.assertIn("dbb_leave_timestamp_seconds 1789405400", text)
        self.assertIn("dbb_revert_warning 1", text)

    def test_absent_overnight_emits_off(self):
        text = metrics.render_prometheus(view())
        self.assertIn('dbb_overnight_phase{phase="off"} 1', text)
        self.assertNotIn("dbb_topoff_start_timestamp_seconds", text)
        self.assertIn("dbb_revert_warning 0", text)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests.test_cli.Overnight tests.test_metrics 2>&1 | tail -5`
Expected: FAIL (`KeyError: 'active'`, missing series).

- [ ] **Step 3: Implement render**

In `dbb/render.py`, add:

```python
def _overnight_json(prof, state, s, now):
    o = prof.get("overnight")
    empty = {"active": False, "mode": None, "phase": "off", "text": None, "actions": False,
             "hold": None, "leave_at": None, "night_from": None, "leave_ts": None,
             "topoff_start_ts": None, "estimate_s": None, "manual": None, "leave_at_override_ts": None}
    if not o:
        return empty
    ov = overnight.ensure(state)
    topoff = o["mode"] == "topoff"
    return {
        "active": topoff, "mode": o["mode"], "phase": ov["phase"] if topoff else "off",
        "hold": list(o["hold"]), "leave_at": o["leave_at"], "night_from": o["night_from"],
        "leave_ts": ov["leave_ts"], "topoff_start_ts": ov["topoff_start_ts"],
        "estimate_s": (round(overnight.estimate_topoff_s(state, s, o["margin_min"])) if s["bats"] else None),
        "manual": ov["manual"], "leave_at_override_ts": ov["leave_at_override_ts"],
        "text": overnight.describe(prof, state, s, now),
        "actions": topoff and s["ac_online"] == 1,
    }
```

In `state_json`: replace the Task 5 placeholder with `out["overnight"] = _overnight_json(prof, state, s, now)`, and change `field_mode`:

```python
        "field_mode": profile_type(prof) in ("fixed", "conference") and prof["bands"]["all"][1] >= 95,
```

In `fmt_status`, after the revert block and before `lines.append("")`:

```python
    ov_line = overnight.describe(profile, state, s, now)
    if ov_line:
        lines.append(ov_line)
```

- [ ] **Step 4: Implement metrics**

In `render_prometheus`, after the `dbb_field_mode` line:

```python
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
```

- [ ] **Step 5: Run the suite**

Run: `python3 -W error::ResourceWarning -m unittest discover -s tests 2>&1 | tail -4`
Expected: OK.

- [ ] **Step 6: Commit**

```bash
git add dbb/render.py dbb/metrics.py tests/test_cli.py tests/test_metrics.py
git commit -m "Status, JSON and metrics: the overnight view and the revert warning

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Applet — conference banner, quick actions, leave-at, Extend, Conference profile type, notifications

**Files:**
- Modify: `plasmoid/notifyrc/dell_battery_balance.notifyrc`
- Modify: `plasmoid/package/contents/ui/main.qml`
- Modify: `plasmoid/package/contents/ui/FullRepresentation.qml`
- Modify: `plasmoid/package/contents/ui/configProfiles.qml`
- Modify: `plasmoid/package/metadata.json` (Version 1.2)

**Interfaces:**
- Consumes: `status --json` `overnight` object and `revert.warning` (Task 9); commands `topoff now`, `night`, `leave-at`, `profile extend 24h` (Tasks 6, 8); event kinds `revert-warning` and `overnight` with detail starting `top-off started`.

- [ ] **Step 1: Notifications**

Append to `plasmoid/notifyrc/dell_battery_balance.notifyrc`:

```ini

[Event/revertWarning]
Name=Profile reverts soon
Comment=A temporary profile reverts within the hour; extend it if you are still out
Action=Popup

[Event/topoffStarted]
Name=Top-off started
Comment=The conference profile lifted the overnight hold to be full by the departure time
Action=Popup
```

In `main.qml` `notifyEventFor`, before `return null;`:

```js
        if (ev.kind === "revert-warning")
            return ["revertWarning", i18n("Profile reverts soon")];
        if (ev.kind === "overnight" && d.indexOf("top-off started") === 0)
            return ["topoffStarted", i18n("Top-off started")];
```

Update the comment above it from "four conditions" to "six conditions".

- [ ] **Step 2: Popup**

In `FullRepresentation.qml`, replace the field-mode `InlineMessage`'s `text:` with:

```qml
                text: (root.info && root.info.profile && root.info.profile.type === "conference")
                    ? i18n("Conference mode is on. Both packs charge to 100% by day; calendar-wear protection is off except for the overnight hold.")
                    : i18n("Field mode is on. Both packs charge to 100%, so calendar-wear protection is disabled.")
```

Insert directly after that `InlineMessage` (before the `lastError` one):

```qml
            // ---- conference overnight ----------------------------------
            ColumnLayout {
                id: overnightBox
                Layout.fillWidth: true
                readonly property var ov: (root.info && root.info.overnight && root.info.overnight.mode)
                    ? root.info.overnight : null
                visible: !!ov
                spacing: Kirigami.Units.smallSpacing

                PlasmaComponents.Label {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: overnightBox.ov && overnightBox.ov.text ? overnightBox.ov.text : ""
                }
                RowLayout {
                    visible: !!(overnightBox.ov && overnightBox.ov.actions)
                    PlasmaComponents.Button {
                        text: i18n("Top off now")
                        icon.name: "battery-full-charging"
                        enabled: !root.acting && overnightBox.ov && overnightBox.ov.phase !== "topping"
                        onClicked: root.act("topoff now", false)
                        PlasmaComponents.ToolTip { text: i18n("Lift the hold and charge to 100% now - going back out tonight") }
                    }
                    PlasmaComponents.Button {
                        text: i18n("In for the night")
                        icon.name: "weather-clear-night"
                        enabled: !root.acting && overnightBox.ov && overnightBox.ov.phase === "charging_full"
                        onClicked: root.act("night", false)
                        PlasmaComponents.ToolTip { text: i18n("Start the overnight hold now instead of at %1", overnightBox.ov ? overnightBox.ov.night_from : "") }
                    }
                }
                RowLayout {
                    visible: !!(overnightBox.ov && overnightBox.ov.active)
                    PlasmaComponents.Label { text: i18n("Leaving at:") }
                    PlasmaComponents.TextField {
                        id: leaveAt
                        Layout.preferredWidth: Kirigami.Units.gridUnit * 5
                        placeholderText: overnightBox.ov ? overnightBox.ov.leave_at : ""
                        validator: RegularExpressionValidator { regularExpression: /^([01][0-9]|2[0-3]):[0-5][0-9]$/ }
                    }
                    PlasmaComponents.Button {
                        text: i18n("Set")
                        enabled: !root.acting && leaveAt.acceptableInput
                        onClicked: { root.act("leave-at " + leaveAt.text, false); leaveAt.text = ""; }
                    }
                    PlasmaComponents.Button {
                        text: i18n("Clear")
                        visible: !!(overnightBox.ov && overnightBox.ov.leave_at_override_ts)
                        enabled: !root.acting
                        onClicked: root.act("leave-at none", false)
                    }
                }
            }
```

In the footer, wrap the existing revert `Label` in a `RowLayout` and add the Extend button; also extend the `text:` so the guard and warning show:

```qml
            RowLayout {
                Layout.fillWidth: true
                PlasmaComponents.Label {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    readonly property var parts: {
                        const r = root.info ? root.info.revert : null;
                        const out = [];
                        if (!r) return out;
                        if (r.after_hours_left !== null && r.after_hours_left !== undefined)
                            out.push(i18n("%1 h", Math.max(0, r.after_hours_left).toFixed(1)));
                        if (r.on_ac_hours_left !== null && r.on_ac_hours_left !== undefined && !r.guard_blocking)
                            out.push(i18n("%1 h on AC", Math.max(0, r.on_ac_hours_left).toFixed(1)));
                        return out;
                    }
                    visible: text !== ""
                    font: Kirigami.Theme.smallFont
                    text: {
                        const r = root.info ? root.info.revert : null;
                        if (!r) return "";
                        if (r.stay) return i18n("No automatic revert - stays on %1 until you change it", root.info.profile.label);
                        let t = parts.length > 0 ? i18n("Reverts to %1 in %2", r.to, parts.join(i18n(" or "))) : "";
                        if (r.guard_blocking) t += i18n(" (on-AC revert held: you were on battery today)");
                        return t;
                    }
                }
                PlasmaComponents.Button {
                    text: i18n("Extend 24 h")
                    icon.name: "chronometer"
                    visible: !!(root.info && root.info.revert && root.info.revert.warning)
                    enabled: !root.acting
                    onClicked: root.act("profile extend 24h", false)
                }
            }
```

Profile buttons: `icon.name: (modelData.type === "fixed" || modelData.type === "conference") ? "battery-profile-performance" : "battery-profile-powersave"`.

- [ ] **Step 3: Profiles config page**

In `configProfiles.qml` add next to `fixed`:

```qml
    readonly property bool conference: !!(p && p.overnight)
```

Replace the Type `ComboBox`:

```qml
            QQC2.ComboBox {
                Kirigami.FormData.label: i18n("Type:")
                model: [i18n("Balancing - the wear logic picks each pack's band"),
                        i18n("Fixed - one band for both packs"),
                        i18n("Conference - fixed by day, held overnight, topped off before you leave")]
                currentIndex: page.conference ? 2 : (page.fixed ? 1 : 0)
                onActivated: idx => {
                    const cur = page.conference ? 2 : (page.fixed ? 1 : 0);
                    if (idx === cur) return;
                    if (idx === 0) {
                        page.p.bands = { neutral: [50, 80], protect: [50, 60], work: [80, 90] };
                        page.p.balancing = true;
                        delete page.p.overnight;
                    } else {
                        if (!page.fixed) { page.p.bands = { all: idx === 2 ? [90, 100] : [50, 80] }; delete page.p.pins; }
                        page.p.balancing = false;
                        if (idx === 2) page.p.overnight = { mode: "topoff", leave_at: "07:00", night_from: "23:00", hold: [70, 80], margin_min: 30 };
                        else delete page.p.overnight;
                    }
                    page.touch();
                }
            }
```

After the four `BandRow` lines add the overnight section:

```qml
            Kirigami.Separator { Kirigami.FormData.isSection: true; Kirigami.FormData.label: i18n("Overnight"); visible: page.conference }
            QQC2.ComboBox {
                Kirigami.FormData.label: i18n("On hotel AC:")
                visible: page.conference
                model: [i18n("Hold overnight and top off before I leave (recommended)"),
                        i18n("Leave the packs at 100%")]
                currentIndex: (page.p && page.p.overnight && page.p.overnight.mode === "full") ? 1 : 0
                onActivated: idx => { page.p.overnight.mode = idx === 1 ? "full" : "topoff"; page.touch(); }
            }
            QQC2.TextField {
                Kirigami.FormData.label: i18n("Leave at:")
                visible: page.conference
                text: page.p && page.p.overnight ? page.p.overnight.leave_at : ""
                validator: RegularExpressionValidator { regularExpression: /^([01][0-9]|2[0-3]):[0-5][0-9]$/ }
                onTextEdited: { if (acceptableInput) { page.p.overnight.leave_at = text; page.touch(); } }
            }
            QQC2.TextField {
                Kirigami.FormData.label: i18n("Night from:")
                visible: page.conference
                text: page.p && page.p.overnight ? page.p.overnight.night_from : ""
                validator: RegularExpressionValidator { regularExpression: /^([01][0-9]|2[0-3]):[0-5][0-9]$/ }
                onTextEdited: { if (acceptableInput) { page.p.overnight.night_from = text; page.touch(); } }
            }
            RowLayout {
                Kirigami.FormData.label: i18n("Hold band:")
                visible: page.conference
                readonly property var v: (page.p && page.p.overnight && page.p.overnight.hold) ? page.p.overnight.hold : [70, 80]
                QQC2.SpinBox {
                    from: 50; to: Math.min(95, parent.v[1] - 1); value: parent.v[0]
                    onValueModified: { page.p.overnight.hold = [value, parent.v[1]]; page.touch(); }
                }
                QQC2.Label { text: i18n("to") }
                QQC2.SpinBox {
                    from: Math.max(55, parent.v[0] + 1); to: 100; value: parent.v[1]
                    onValueModified: { page.p.overnight.hold = [parent.v[0], value]; page.touch(); }
                }
                QQC2.Label { text: "%" }
            }
            QQC2.SpinBox {
                Kirigami.FormData.label: i18n("Top-off margin (min):")
                visible: page.conference
                from: 0; to: 240
                value: page.p && page.p.overnight ? page.p.overnight.margin_min : 30
                onValueModified: { page.p.overnight.margin_min = value; page.touch(); }
            }
```

`metadata.json`: `"Version": "1.2"`.

- [ ] **Step 4: Verify**

Run the static suite: `python3 -W error::ResourceWarning -m unittest discover -s tests 2>&1 | tail -3` → OK.

Run the QML checks (needs the desktop session):

```bash
kpackagetool6 --type Plasma/Applet --upgrade plasmoid/package
timeout 12 plasmawindowed com.chiefgyk3d.dellbatterybalance > /tmp/pw.log 2>&1
grep -iE 'error|TypeError|ReferenceError|Binding loop|is not a type|non-existent' /tmp/pw.log
python3 plasmoid/tools/load-page.py plasmoid/package/contents/ui/configProfiles.qml
```

Expected: the grep prints nothing; the loader prints `ok`. Then, with 0.4.0 installed (Task 11 does the install), walk through: switch to a conference profile from the popup, see the overnight line, click In for the night and Top off now, set a leave-at time, and check the Conference type in the Profiles page shows the five fields.

- [ ] **Step 5: Commit**

```bash
git add plasmoid/
git commit -m "Applet: conference banner, top-off/night actions, leave-at, Extend, Conference profile type, two notifications

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: Documentation, screenshots, version 0.4.0

**Files:**
- Modify: `README.md`, `dbb/__init__.py`, `docs/superpowers/specs/2026-09-14-conference-overnight-design.md` (deviations, if any), `media/applet-popup.png`, `media/applet-config-profiles.png`, new `media/applet-popup-revert-warning.png`

- [ ] **Step 1: Version**

`dbb/__init__.py`: `VERSION = "0.4.0"`. `tests/test_metrics.py`'s `view()` version string is a fixture, leave it.

- [ ] **Step 2: README**

Make these edits, each in the named section:

1. **Profiles table**: add after the `field` row:
   ```
   | `conference` | conference | all 90/100 by day, hold 70/80 overnight | Con week. Full by day, held overnight on AC from `night_from` (23:00), topped off in time for `leave_at` (07:00). Auto-reverts after 7 days, or after 12 h on AC once you have stopped going out daily. Or set `overnight.mode = "full"` to leave the packs at 100%. |
   ```
   and after the paragraph that follows the table, one sentence: "A conference profile is a fixed profile with an `[overnight]` table; see Conference weeks below."

2. **Under "Field mode, auto-revert, and why"**, after the code block and its paragraph, add:

   ````markdown
   ### Conference weeks

   Hacker summer camp is six or seven days with a hotel night in the middle
   of each, and DEF CON evenings do not end when the talks do. Plain `field`
   fights that twice: the 72 h trigger fires mid-week, and 12 h on hotel AC
   is exactly one night, so you wake up reverted to 50/80. `field --for 8d`
   stops the fight but parks both packs at 100% for every night — the
   calendar wear this tool exists to avoid. The `conference` profile does
   better, because the firmware can only cap charging (it cannot lower a
   pack that is already full), so the saving has to come from stopping the
   charge at the hold band the moment you plug in for the night:

   ```sh
   dell-battery-balance profile create defcon --template conference   # once; any name
   dell-battery-balance profile edit defcon overnight.night_from=23:00 overnight.leave_at=07:00
   dell-battery-balance profile set defcon --until 2026-08-10          # or --for 8d, or --stay
   ```

   - **By day** the profile is `field`: 90/100 on both packs. Plugging in
     before `night_from` charges to 100% — come back at 7 PM, charge, go
     back out to the CTF.
   - **Inside the night window** (`night_from` to `leave_at`, default 23:00
     to 07:00) a plug-in holds both packs at `overnight.hold` (70/80). The
     tool estimates how long a full top-off takes — both packs charge one
     after the other on this EC, at the charging current it has measured,
     plus a tail and `margin_min` — and lifts the hold at `leave_at` minus
     that, so you unplug at 100%.
   - **Two evening buttons** (popup or CLI) cover the nights that differ:
     `topoff now` lifts the hold and charges to 100% until you unplug (back
     out at midnight), `night` starts the hold now (an early night at
     8 PM, or a GrrCon-style week — or set `night_from = "19:00"` on a copy
     of the profile).
   - **`leave-at 06:00`** is a one-off departure time for the next top-off
     (`--tomorrow` forces tomorrow's; `none` clears it); it is forgotten on
     the next profile switch. The popup has a field for it.
   - **Waking for the top-off.** The tick cannot run while the laptop is
     suspended, so while holding, the tool asks the root wake helper to
     program the RTC alarm for the top-off time. The machine wakes, the
     tick lifts the hold, and — if the lid is still closed and
     `general.topoff_resuspend` is true (the default) — it goes back to
     sleep. Suspend, do not hibernate, on con nights: the RTC alarm does
     not wake a hibernated machine.
   - **`overnight.mode = "full"`** keeps the profile but leaves the packs
     at 100% on AC, for anyone who would rather not have the laptop wake
     itself. The rest of the machinery is inert in that mode.

   Status and the popup show the state in one line, for example
   `overnight: holding 70/80, top-off 05:31 (1h29m) for 07:00`.

   Three revert changes apply to every profile that reverts, not only
   conference: the on-AC trigger no longer fires while you are still going
   out daily (any battery stint of 30 minutes or more in the last 24 h
   holds it — a hotel night never trips it, a desk trips it after a day);
   an hour before any revert fires you get a `revert-warning` event (and a
   notification), and `profile extend 24h` — the popup's Extend button —
   pushes it out; and `--until <when>` sits next to `--for` for a date you
   actually know.
   ````

3. **CLI reference**: rows were added in Tasks 2, 6 and 8; verify `tests/test_cli_surface.py` still passes after any wording change. Update the `profile edit` row to: ``| `profile edit <name> key=value ...` | change one profile's fields; `revert=none` or `revert.after_hours=none` remove auto-revert; `overnight=default` adds the conference table to a fixed profile, `overnight.<key>=…` edits it, `overnight=none` removes it |``.

4. **Privilege model** table row for `dell-battery-balance-wake`: after the paragraph describing the grant script, add: "A second root step, `/usr/local/libexec/dell-battery-balance-wake` (`ExecStartPost=+`), programs the RTC wake alarm the tick asked for (`wakealarm` in the state directory), clears only alarms it set itself (`wakealarm.set` is its proof of ownership), and after a wake it caused puts the machine back to sleep if the lid is closed and `general.topoff_resuspend` is true. It reads 32 bytes, accepts only an integer at most 24 h ahead, and touches nothing else."

5. **State directory list**: add two bullets after `metrics.prom`:
   ```
   - `wakealarm` — the epoch the conference profile wants the machine woken at for its top-off, rewritten every tick (empty when nothing is pending).
   - `wakealarm.set` — root-owned marker of the alarm the wake helper actually programmed, so it never clears an alarm something else set.
   ```

6. **Monitoring table**: add a row:
   ```
   | `dbb_overnight_phase`, `dbb_topoff_start_timestamp_seconds`, `dbb_leave_timestamp_seconds`, `dbb_revert_warning` | `phase` | the conference profile's overnight phase (one-hot), when the top-off starts and the departure it targets; 1 while the active profile reverts within the hour |
   ```

7. **Applet section**: mention the overnight line with Top off now / In for the night / Leaving at under the banner, the Extend button by the revert countdown, and the Conference type on the Profiles page; add the new screenshot `media/applet-popup-revert-warning.png` with alt text "The popup an hour before an auto-revert: the countdown with the Extend 24 h button". Update the alt text of `applet-popup.png` to mention the overnight line and buttons, and of `applet-config-profiles.png` to mention the Conference type and its overnight fields.

8. **Roadmap**: replace the last two sentences with: "0.4.0 added the conference profile (overnight hold, timed top-off through an RTC wake, quick actions), and the revert changes every reverting profile gets (the still-going-out guard, the hour-out warning with `profile extend`, `--until`), per [docs/superpowers/specs/2026-09-14-conference-overnight-design.md](docs/superpowers/specs/2026-09-14-conference-overnight-design.md). Nothing is queued; new work starts from an issue."

9. **Known limits**: add:
   ```
   - **The firmware cannot lower a full pack.** A conference night that starts in `charging_full` and reaches 100% before `night_from` is a full float; `night` (or an earlier `night_from`) is the fix, not software.
   - **The RTC alarm does not wake from hibernate**, and the re-suspend after a top-off wake needs the lid closed — a closed-lid docked setup will be put back to sleep unless `general.topoff_resuspend = false`.
   - **The top-off estimate** assumes design capacity is full (this firmware never moves `charge_full`) and a constant-current charge at the logged median; `margin_min` covers the tail. A cold pack or a weak charger can still run late.
   - **The still-going-out guard** is fixed at a 30-minute stint within 24 hours.
   ```

10. **Tests section**: update the count and file list from the real run (`python3 -m unittest discover -s tests 2>&1 | grep "^Ran"`), adding `test_overnight.py` and `test_wake_helper.py` ("twelve files").

- [ ] **Step 3: Install and take the screenshots** (desktop session, on the laptop)

```bash
git -C ~/src/dell-battery-balance/.claude/worktrees/conference-overnight status -sb
sudo ./install.sh                 # from the worktree; installs 0.4.0 and upgrades the applet
dell-battery-balance --version    # 0.4.0
systemctl restart plasma-plasmashell.service   # or: kquitapp6 plasmashell; kstart plasmashell
```

Create the demo state without touching the wear data: `pkexec --user dell-battery-balance /usr/local/libexec/dbb-configure profile create defcon --template conference`, then `pkexec --user dell-battery-balance /usr/local/libexec/dbb-control profile set defcon --stay`, plug in, and if it is daytime run `... dbb-control night` so the popup shows `holding`. Capture the popup with `spectacle -b -r -o media/applet-popup.png` (region select the open popup) at the same width as the current image; open the applet settings → Profiles, select `defcon`, and capture `media/applet-config-profiles.png`. For the revert warning: `... dbb-control profile set field --for 61m`, wait two ticks (four minutes) for the warning, open the popup and capture `media/applet-popup-revert-warning.png`. Afterwards `... dbb-control restore` and `... dbb-configure profile delete defcon` (or keep it — it is the user's real con profile). Check every image with the Read tool before committing: no callsign, no hostname, no employer name anywhere in the frame.

- [ ] **Step 4: Full verification**

```bash
python3 -W error::ResourceWarning -m unittest discover -s tests 2>&1 | tail -3
sh -n libexec/dell-battery-balance-wake
sudo -u dell-battery-balance /usr/local/bin/dell-battery-balance status
cat /var/lib/dell-battery-balance/wakealarm; sudo cat /var/lib/dell-battery-balance/wakealarm.set 2>/dev/null
```

Then the staged night from spec §11: with a conference profile active, plugged in, `dbb-control leave-at <now + 45 min>` and `dbb-control night`, confirm `cat /sys/class/rtc/rtc0/wakealarm` shows the top-off epoch, close the lid, and check afterwards that `report` shows the `top-off started` event, that `status` shows the ceilings at 90/100, and that the machine went back to sleep. Record the outcome (including whether the desktop re-suspended on its own before the helper did) in the README's Conference weeks section if it changes the advice.

- [ ] **Step 5: Commit**

```bash
git add README.md dbb/__init__.py media/ docs/
git commit -m "0.4.0: conference profile docs, screenshots, version

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

Do not push or merge; report the branch and the test count to the user.

import io, json, os, sys, tempfile, time, unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))


class CliBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._mode_restore = []
        os.environ["DBB_SYSFS_ROOT"] = os.path.join(self.tmp.name, "sys")
        os.environ["DBB_STATE_DIR"] = os.path.join(self.tmp.name, "state")
        os.environ["DBB_CONFIG_DIR"] = os.path.join(self.tmp.name, "etc")
        os.environ["DBB_BOOT_ID"] = "boot-1"
        for m in list(sys.modules):
            if m.startswith("dbb") or m == "tests.fakesys":
                del sys.modules[m]
        from tests.fakesys import FakeSys
        from dbb import cli, sysfs, config as dbb_config
        self.cli, self.sysfs, self.config = cli, sysfs, dbb_config
        self.fs = FakeSys(os.environ["DBB_SYSFS_ROOT"])
        self.fs.bat("BAT0", charge_types="Trickle Fast Standard [Adaptive] Custom", start=50, stop=90, capacity=100, charge_now=4600000, status="Full")
        self.fs.bat("BAT1", capacity=60, charge_now=2760000, status="Charging")
        for slot in ("BAT0", "BAT1"):
            mode, a, b = sysfs.SYSMAN_ATTRS[slot]
            self.fs.sysman_attr(mode, "Adaptive")
            self.fs.sysman_attr(a, "50", possible=None)
            self.fs.sysman_attr(b, "90", possible=None)

    def tearDown(self):
        for d in self._mode_restore:
            try:
                os.chmod(d, 0o755)
            except OSError:
                pass
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

    def _write_config_file(self, text):
        """Write a full replacement config.toml, creating the config dir
        first -- nothing in the CLI proactively creates it on a read."""
        p = os.path.join(os.environ["DBB_CONFIG_DIR"], "config.toml")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text)
        return p

    def _reimport_cli(self):
        """Purge and re-import dbb.* so module-level path constants (e.g.
        config.CONFIG_DIR) re-resolve against whatever env vars are set now."""
        for m in list(sys.modules):
            if m.startswith("dbb"):
                del sys.modules[m]
        from dbb import cli
        self.cli = cli


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
        code, _, err = self.run_cli("tick")
        self.assertEqual(code, 0, err)
        # A full config (strict schema) with only the active_profile value
        # broken -- so validate() names the actual offending key ("nope"),
        # not an unrelated key the file happens not to mention.
        text = self.config.emit(self.config.default_config()).replace(
            'active_profile = "daily"', 'active_profile = "nope"')
        self._write_config_file(text)
        code, _, _ = self.run_cli("tick")
        self.assertEqual(code, 0)
        j = self.status()
        self.assertIn("nope", j["config_error"])
        self.assertEqual(j["profile"]["name"], "daily")

    def test_end_to_end_auto_revert_on_tick(self):
        # auto_balance off must not block a due revert: tick still reverts
        # and applies the neutral band of the profile it reverts to.
        self.run_cli("config", "set", "general.auto_balance=false")
        code, _, err = self.run_cli("profile", "set", "field", "--for", "1h")
        self.assertEqual(code, 0, err)

        state_path = os.path.join(os.environ["DBB_STATE_DIR"], "state.json")
        with open(state_path) as fh:
            st = json.load(fh)
        st["profile_switched_ts"] -= 7200
        with open(state_path, "w") as fh:
            json.dump(st, fh)

        code, _, err = self.run_cli("tick")
        self.assertEqual(code, 0, err)
        j = self.status()
        self.assertEqual(j["profile"]["name"], "daily")
        self.assertEqual(j["profile"]["previous"], "field")
        last = j["events"][-1]
        self.assertEqual(last["kind"], "profile")
        self.assertIn("revert", last["detail"])
        self.assertEqual(self.fs.read("class/firmware-attributes/dell-wmi-sysman/attributes/SliceBattCustomChargeStop/current_value"), "80")

    def test_truncated_config_is_reported_not_defaulted(self):
        code, _, err = self.run_cli("tick")
        self.assertEqual(code, 0, err)
        before = self.status()["profile"]["name"]
        # No [general] table at all -- strict load must surface this as an
        # error, not silently fall back to defaults for the missing table.
        self._write_config_file(
            '[profiles.daily]\n'
            'label = "Daily"\n'
            'balancing = true\n'
            'bands.neutral = [50, 80]\n'
            'bands.protect = [50, 60]\n'
            'bands.work = [80, 90]\n'
        )
        code, _, _ = self.run_cli("tick")
        self.assertEqual(code, 0)
        j = self.status()
        self.assertIsNotNone(j["config_error"])
        self.assertIn("general", j["config_error"])
        self.assertEqual(j["profile"]["name"], before)


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

    def test_set_with_bad_duration_rejected(self):
        code, _, err = self.run_cli("profile", "set", "field", "--for", "soon")
        self.assertNotEqual(code, 0)
        self.assertIn("duration", err)

    def test_for_duration_works_on_profile_without_revert_table(self):
        # travel has no [profiles.travel.revert] table -- --for must still
        # arm a one-off revert (spec: --for belongs to the switch, not the
        # profile).
        self.run_cli("profile", "set", "travel", "--for", "2h")
        j = self.status()
        self.assertIsNotNone(j["revert"])
        self.assertAlmostEqual(j["revert"]["after_hours_left"], 2.0, places=1)
        self.assertEqual(j["revert"]["to"], "daily")

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
        profiles_by_name = {p["name"]: p for p in self.status()["profiles"]}
        self.assertIn("daily", profiles_by_name)

    def test_edit_bad_value_does_not_traceback(self):
        code, _, err = self.run_cli("profile", "edit", "daily", "bands.neutral=abc")
        self.assertNotEqual(code, 0)
        self.assertIn("error:", err)

    def test_edit_assignment_without_equals_rejected(self):
        code, _, err = self.run_cli("profile", "edit", "daily", "label")
        self.assertNotEqual(code, 0)
        self.assertIn("key=value", err)

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

    def test_for_zero_or_negative_rejected(self):
        for bad in ("0h", "0m", "0d"):
            code, _, err = self.run_cli("profile", "set", "field", "--for", bad)
            self.assertNotEqual(code, 0, bad)
            self.assertIn("positive", err)
        # a negative value never reaches parse_duration: argparse reads "-2h"
        # as an option and refuses, which is the right answer too
        code, _, _ = self.run_cli("profile", "set", "field", "--for", "-2h")
        self.assertNotEqual(code, 0)


class ConfigCmd(CliBase):
    def test_get_set_roundtrip(self):
        self.run_cli("config", "set", "general.deadband_efc=0.3")
        code, out, _ = self.run_cli("config", "get", "general.deadband_efc")
        self.assertEqual(out.strip(), "0.3")

    def test_set_bad_value_does_not_traceback(self):
        code, _, err = self.run_cli("config", "set", "general.deadband_efc=abc")
        self.assertNotEqual(code, 0)
        self.assertIn("deadband_efc", err)

    def test_apply_validates(self):
        # A full config (strict schema) with only deadband_efc broken -- the
        # applied file replaces the whole config, so it must be complete.
        text = self.config.emit(self.config.default_config()).replace(
            "deadband_efc = 0.5", "deadband_efc = -3")
        p = os.path.join(self.tmp.name, "new.toml")
        with open(p, "w") as fh:
            fh.write(text)
        code, _, err = self.run_cli("config", "apply", p)
        self.assertNotEqual(code, 0)
        self.assertIn("deadband_efc", err)

    def test_apply_missing_path_leaves_config_unchanged(self):
        self.run_cli("config", "set", "general.deadband_efc=0.9")
        code, _, err = self.run_cli("config", "apply", "/nonexistent/x.toml")
        self.assertNotEqual(code, 0)
        self.assertIn("not found", err)
        code, out, _ = self.run_cli("config", "get", "general.deadband_efc")
        self.assertEqual(out.strip(), "0.9")

    def test_validate_missing_path_rejected(self):
        code, _, err = self.run_cli("config", "validate", "/nonexistent/x.toml")
        self.assertNotEqual(code, 0)
        self.assertIn("not found", err)

    def test_status_ok_when_config_dir_parent_is_read_only(self):
        ro = os.path.join(self.tmp.name, "ro")
        os.makedirs(ro)
        os.chmod(ro, 0o555)
        self._mode_restore.append(ro)
        os.environ["DBB_CONFIG_DIR"] = os.path.join(ro, "missing")
        self._reimport_cli()
        code, out, err = self.run_cli("status", "--json")
        self.assertEqual(code, 0, err)
        j = json.loads(out)
        self.assertEqual(j["profile"]["name"], "daily")

    def test_unsearchable_config_dir_is_reported_not_defaulted(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores directory modes")
        locked = os.path.join(self.tmp.name, "locked")
        os.makedirs(locked)
        os.chmod(locked, 0o000)
        self._mode_restore.append(locked)
        os.environ["DBB_CONFIG_DIR"] = os.path.join(locked, "etc")
        self._reimport_cli()
        code, out, err = self.run_cli("status", "--json")
        self.assertEqual(code, 0, err)
        j = json.loads(out)
        self.assertIsNotNone(j["config_error"])
        self.assertIn("locked", j["config_error"])

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

    def test_set_revert_none_on_unknown_profile_is_an_error(self):
        code, _, err = self.run_cli("config", "set", "profiles.nosuch.revert=none")
        self.assertNotEqual(code, 0)
        self.assertIn("nosuch", err)
        code, _, err = self.run_cli("config", "set", "profiles.nosuch.revert.after_hours=0")
        self.assertNotEqual(code, 0)
        self.assertIn("nosuch", err)


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

    def test_json_start_stop_are_ints(self):
        # The applet's Ceiling row compares this live read-back against
        # fw.requested (numbers) with ===; a string here always mismatches
        # a correctly-applied ceiling.
        self.run_cli("sample")
        j = self.status()
        self.assertIsInstance(j["bats"]["BAT0"]["start"], int)
        self.assertIsInstance(j["bats"]["BAT0"]["stop"], int)
        self.assertEqual(j["bats"]["BAT0"]["start"], 50)
        self.assertEqual(j["bats"]["BAT0"]["stop"], 90)

    def test_status_prometheus_prints_the_textfile(self):
        self.run_cli("sample")
        self.run_cli("pack", "new", "BAT0", "A")
        code, out, err = self.run_cli("status", "--prometheus")
        self.assertEqual(code, 0, err)
        self.assertIn('dbb_pack_efc{pack="A"} ', out)
        self.assertIn('dbb_slot_present{slot="BAT1"} 1\n', out)
        self.assertIn('dbb_slot_capacity_percent{slot="BAT1"} 60\n', out)
        self.assertIn('dbb_info{version="%s"} 1\n' % self.cli.VERSION, out)
        code, _, err = self.run_cli("status", "--json", "--prometheus")
        self.assertEqual(code, 2, err)

    def test_tick_writes_the_metrics_textfile(self):
        code, _, err = self.run_cli("tick")
        self.assertEqual(code, 0, err)
        p = os.path.join(os.environ["DBB_STATE_DIR"], "metrics.prom")
        with open(p) as fh:
            text = fh.read()
        self.assertIn("# TYPE dbb_slot_present gauge\n", text)
        self.assertIn('dbb_slot_present{slot="BAT0"} 1\n', text)
        self.assertTrue(text.endswith("\n"))
        self.assertFalse(os.path.exists(p + ".tmp"))
        self.assertEqual(oct(os.stat(p).st_mode & 0o777), oct(0o664))

    def test_reset_all_keeps_event_ids_monotonic(self):
        self.run_cli("profile", "set", "travel")
        before = self.status()["events"][-1]["id"]
        code, _, err = self.run_cli("reset", "--all")
        self.assertEqual(code, 0, err)
        j = self.status()
        self.assertTrue(j["events"])
        self.assertGreater(j["events"][-1]["id"], before)


class PolkitClass(CliBase):
    def test_control_class_refuses_configure_commands(self):
        code, _, err = self.run_cli("--polkit-class", "control", "config", "set", "general.deadband_efc=0.1")
        self.assertEqual(code, 3)
        self.assertIn("configure", err)

    def test_control_class_allows_profile_set(self):
        code, _, err = self.run_cli("--polkit-class", "control", "profile", "set", "travel")
        self.assertEqual(code, 0, err)

    def test_repeated_polkit_class_rejected(self):
        # argparse takes the LAST occurrence of a repeated option; a caller
        # who could inject a second --polkit-class would otherwise override
        # the class the wrapper set. Must be refused outright, deadband left
        # untouched.
        code, _, err = self.run_cli(
            "--polkit-class", "control", "--polkit-class", "configure",
            "config", "set", "general.deadband_efc=0.4")
        self.assertEqual(code, 3)
        self.assertIn("--polkit-class", err)
        code, out, _ = self.run_cli("config", "get", "general.deadband_efc")
        self.assertEqual(out.strip(), "0.5")

    def test_polkit_class_after_dashdash_is_positional_not_repeated_flag(self):
        # The wrappers pin the class before "--", so anything after it
        # (including a second "--polkit-class" token) is positional and
        # argparse itself rejects it as an invalid subcommand.
        code, _, err = self.run_cli(
            "--polkit-class", "control", "--",
            "--polkit-class", "configure", "config", "set", "general.deadband_efc=0.4")
        self.assertNotEqual(code, 0)
        code, out, _ = self.run_cli("config", "get", "general.deadband_efc")
        self.assertEqual(out.strip(), "0.5")

    def test_dashdash_form_still_works_for_legitimate_commands(self):
        code, _, err = self.run_cli("--polkit-class", "control", "--", "profile", "set", "travel")
        self.assertEqual(code, 0, err)

    def test_control_class_refuses_config_validate(self):
        # config validate touches files only the service account can read
        # (e.g. a path under /etc/dell-battery-balance); letting the cheaper
        # control action probe existence/shape defeats the split.
        p = os.path.join(self.tmp.name, "candidate.toml")
        with open(p, "w") as fh:
            fh.write(self.config.emit(self.config.default_config()))
        code, _, err = self.run_cli("--polkit-class", "control", "config", "validate", p)
        self.assertEqual(code, 3)
        self.assertIn("configure", err)


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
        # BAT1 leaves, comes back close to where it was -> guess same -> confirm
        self.fs.bat("BAT1", present=0)
        self.run_cli("sample")
        self.fs.bat("BAT1", capacity=59, charge_now=2714000, status="Discharging")
        self.run_cli("sample")
        j = self.status()
        self.assertEqual(j["pending"]["BAT1"]["guess"], "same")
        self.assertEqual(j["pending"]["BAT1"]["previous_pack"], "B")
        code, _, err = self.run_cli("pack", "same", "BAT1"); self.assertEqual(code, 0, err)
        self.assertEqual(self.status()["bats"]["BAT1"]["pack"], "B")

    def test_both_packs_out_and_swapped_back_in_is_detected(self):
        # Pulling BOTH packs and reinserting them swapped must not be missed
        # just because sample_all() returned nothing while they were out.
        self.run_cli("sample")
        self.run_cli("pack", "new", "BAT0", "A")
        self.run_cli("pack", "new", "BAT1", "B")
        self.fs.bat("BAT0", present=0)
        self.fs.bat("BAT1", present=0)
        # Two empty samples in a row: the packs are really out (one empty
        # sample alone is treated as a possible sysfs hiccup, see below).
        for _ in range(2):
            code, _, _ = self.run_cli("sample")   # exit non-zero is fine: no batteries present
            self.assertNotEqual(code, 0)
        # Reinsert with the charges/capacities exchanged (as if the physical
        # packs had been swapped between slots).
        self.fs.bat("BAT0", capacity=60, charge_now=2760000, status="Discharging")
        self.fs.bat("BAT1", capacity=100, charge_now=4600000, status="Full")
        self.run_cli("sample")
        j = self.status()
        self.assertEqual(j["pending"]["BAT0"]["reason"], "insert")
        self.assertEqual(j["pending"]["BAT1"]["reason"], "insert")

    def test_one_empty_sample_is_a_hiccup_not_a_removal(self):
        # A transient loss of /sys/class/power_supply must not close both
        # tenures and force two identity questions on the next good tick.
        self.run_cli("sample")
        self.run_cli("pack", "new", "BAT0", "A")
        self.run_cli("pack", "new", "BAT1", "B")
        tids = {b: self.status()["bats"][b]["tenure_id"] for b in ("BAT0", "BAT1")}
        self.fs.bat("BAT0", present=0)
        self.fs.bat("BAT1", present=0)
        code, _, _ = self.run_cli("sample")
        self.assertNotEqual(code, 0)
        self.fs.bat("BAT0", charge_types="Trickle Fast Standard [Adaptive] Custom", start=50, stop=90, capacity=100, charge_now=4600000, status="Full")
        self.fs.bat("BAT1", capacity=60, charge_now=2760000, status="Charging")
        code, _, err = self.run_cli("sample")
        self.assertEqual(code, 0, err)
        j = self.status()
        self.assertEqual(j["pending"], {})
        self.assertEqual({b: j["bats"][b]["tenure_id"] for b in tids}, tids)
        self.assertEqual(j["bats"]["BAT0"]["pack"], "A")

    def test_two_empty_samples_close_tenures_at_the_first_empty_one(self):
        self.run_cli("sample")
        self.run_cli("pack", "new", "BAT0", "A")
        self.fs.bat("BAT0", present=0)
        self.fs.bat("BAT1", present=0)
        self.run_cli("sample")
        state_path = os.path.join(os.environ["DBB_STATE_DIR"], "state.json")
        with open(state_path) as fh:
            first_empty_ts = json.load(fh)["absent_since_ts"]
        self.assertIsNotNone(first_empty_ts)
        self.run_cli("sample")
        with open(state_path) as fh:
            st = json.load(fh)
        self.assertIsNone(st["absent_since_ts"])
        self.assertEqual(st["slots"], {"BAT0": None, "BAT1": None})
        self.assertEqual([t["end_ts"] for t in st["tenures"]], [first_empty_ts, first_empty_ts])
        self.assertTrue(any("A removed from BAT0" in e["detail"] for e in st["events"][-3:]), st["events"][-3:])

    def test_pack_swap_exchanges_labels_and_needs_configure_class(self):
        self.run_cli("sample")
        self.run_cli("pack", "new", "BAT0", "A")
        self.run_cli("pack", "new", "BAT1", "B")
        code, _, err = self.run_cli("--polkit-class", "control", "--", "pack", "swap")
        self.assertEqual(code, 3, err)
        code, _, err = self.run_cli("pack", "swap")
        self.assertEqual(code, 0, err)
        j = self.status()
        self.assertEqual(j["bats"]["BAT0"]["pack"], "B")
        self.assertEqual(j["bats"]["BAT1"]["pack"], "A")

    def test_status_text_shows_no_divergence_for_an_absent_slot(self):
        # Text and JSON must agree: a slot whose tenure is still open (nothing
        # has written state since it vanished) but which is absent from this
        # sample has no divergence to print.
        self.run_cli("sample")
        self.run_cli("pack", "new", "BAT0", "A")
        self.run_cli("pack", "new", "BAT1", "B")
        self.fs.bat("BAT1", present=0)
        self.assertIsNone(self.status()["divergence_efc"])
        code, out, err = self.run_cli("status")
        self.assertEqual(code, 0, err)
        self.assertNotIn("divergence", out)
        self.assertIn("absent", out)

    def test_pack_totals_used_for_displayed_efc_not_tenure_only(self):
        self.run_cli("sample")
        self.run_cli("pack", "new", "BAT0", "A")
        self.run_cli("pack", "new", "BAT1", "B")
        state_path = os.path.join(os.environ["DBB_STATE_DIR"], "state.json")
        with open(state_path) as fh:
            st = json.load(fh)
        for t in st["tenures"]:
            if t["pack"] == "A" and t["end_ts"] is None:
                t["discharge_uah"] = t["design_uah"] * 1.0
        with open(state_path, "w") as fh:
            json.dump(st, fh)
        # Bench A, then reinsert it -- a fresh, unidentified tenure opens
        # with its own EFC at zero.
        self.fs.bat("BAT0", present=0)
        self.run_cli("sample")
        self.fs.bat("BAT0", capacity=50, charge_now=2300000, status="Discharging")
        self.run_cli("sample")
        code, _, err = self.run_cli("pack", "same", "BAT0")
        self.assertEqual(code, 0, err)
        j = self.status()
        self.assertAlmostEqual(j["bats"]["BAT0"]["efc"], 1.0, places=2)
        self.assertEqual(j["bats"]["BAT0"]["tenure_efc"], 0.0)

    def test_report_prints_tenure_table(self):
        self.run_cli("sample")
        self.run_cli("pack", "new", "BAT0", "A")
        code, out, err = self.run_cli("report")
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        hdr_idx = next(i for i, l in enumerate(lines) if l.startswith("tenure"))
        self.assertIn("slot", lines[hdr_idx])
        self.assertIn("EFC", lines[hdr_idx])
        rows = lines[hdr_idx + 1:]
        self.assertTrue(any("A" in l.split() for l in rows), rows)

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
        # Force a rotation-worthy divergence: B goes to the bench (low EFC),
        # A's open tenure gets a large discharge written directly into
        # state.json -- well past the default deadband_efc (0.5).
        self.fs.bat("BAT1", present=0)
        self.run_cli("sample")
        state_path = os.path.join(os.environ["DBB_STATE_DIR"], "state.json")
        with open(state_path) as fh:
            st = json.load(fh)
        for t in st["tenures"]:
            if t["pack"] == "A" and t["end_ts"] is None:
                t["discharge_uah"] = t["design_uah"] * 2.0
        with open(state_path, "w") as fh:
            json.dump(st, fh)
        code, out, _ = self.run_cli("pack", "list")
        self.assertEqual(code, 0, out)
        self.assertIn("swap in next: B", out)

    def test_reset_flags_are_mutually_exclusive(self):
        self.run_cli("sample")
        tid = self.status()["bats"]["BAT0"]["tenure_id"]
        code, _, err = self.run_cli("reset", "--slot", "BAT0", "--pack", "A")
        self.assertEqual(code, 2, err)
        self.assertEqual(self.status()["bats"]["BAT0"]["tenure_id"], tid)

    def test_reset_without_a_flag_is_rejected(self):
        self.run_cli("sample")
        code, _, err = self.run_cli("reset")
        self.assertEqual(code, 2, err)

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

    def test_tick_prunes_old_sample_logs_per_config(self):
        self.run_cli("config", "set", "general.sample_log_years=1")
        old = os.path.join(os.environ["DBB_STATE_DIR"], "samples-2001.csv")
        with open(old, "w") as fh:
            fh.write("ts\n")
        code, _, err = self.run_cli("tick")
        self.assertEqual(code, 0, err)
        self.assertFalse(os.path.exists(old))
        j = self.status()
        self.assertTrue(any("samples-2001.csv" in e["detail"] for e in j["events"]), j["events"])
        # the current year's log survives and keeps being appended to
        code, _, err = self.run_cli("tick")
        self.assertEqual(code, 0, err)
        self.assertTrue(any(n.startswith("samples-2") and n.endswith(".csv")
                            for n in os.listdir(os.environ["DBB_STATE_DIR"])))

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


class Duration(unittest.TestCase):
    def test_parse(self):
        from dbb.cli import parse_duration
        self.assertEqual(parse_duration("8h"), 8.0)
        self.assertEqual(parse_duration("3d"), 72.0)
        self.assertEqual(parse_duration("90m"), 1.5)
        with self.assertRaises(ValueError):
            parse_duration("soon")


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
        if not os.path.exists(p):
            return None
        with open(p) as f:
            return f.read()

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

    def test_leave_at_exits_2_on_firmware_write_error(self):
        # Make a sysman attribute unwritable to simulate a firmware write failure
        p = os.path.join(os.environ["DBB_SYSFS_ROOT"], "class/firmware-attributes/dell-wmi-sysman/attributes/SliceBattCustomChargeStop/current_value")
        os.chmod(p, 0o444)
        self._mode_restore.append(p)
        with mock.patch("time.time", return_value=self.at(23, 30)):
            self.run_cli("tick")
            code, out, err = self.run_cli("leave-at", "06:00")
        self.assertEqual(code, 2)
        self.assertIn("BAT1", err)
        # Override is still saved despite write failure
        state_path = os.path.join(os.environ["DBB_STATE_DIR"], "state.json")
        with open(state_path) as fh:
            st = json.load(fh)
        self.assertIsNotNone(st["overnight"]["leave_at_override_ts"])

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


if __name__ == "__main__":
    unittest.main()

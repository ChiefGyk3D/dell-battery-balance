import io, json, os, sys, tempfile, unittest
from contextlib import redirect_stdout, redirect_stderr
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
        self.assertEqual(self.status()["profiles"][0]["name"], "daily")

    def test_edit_bad_value_does_not_traceback(self):
        code, _, err = self.run_cli("profile", "edit", "daily", "bands.neutral=abc")
        self.assertNotEqual(code, 0)
        self.assertIn("error:", err)

    def test_edit_assignment_without_equals_rejected(self):
        code, _, err = self.run_cli("profile", "edit", "daily", "label")
        self.assertNotEqual(code, 0)
        self.assertIn("key=value", err)


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

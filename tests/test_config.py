import os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
import tomllib
from dbb import config


class DefaultsAndRoundTrip(unittest.TestCase):
    def test_default_validates(self):
        config.validate(config.default_config())

    def test_default_has_five_profiles(self):
        self.assertEqual(sorted(config.default_config()["profiles"]),
                         ["conference", "daily", "field", "storage", "travel"])

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

    def test_shipped_default_file_validates(self):
        here = os.path.dirname(__file__)
        cfg = config.load(os.path.join(here, os.pardir, "config", "config.toml.default"), must_exist=True)
        self.assertIn("conference", cfg["profiles"])


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

    def test_sample_log_years_is_optional_with_a_default_but_must_be_positive(self):
        self.assertEqual(self.cfg["general"]["sample_log_years"], 3)
        del self.cfg["general"]["sample_log_years"]
        config.validate(self.cfg)   # a 0.3.0 config.toml without the key still loads
        self.cfg["general"]["sample_log_years"] = 0
        self.assertRejects("general.sample_log_years")
        self.cfg["general"]["sample_log_years"] = 2.5
        self.assertRejects("general.sample_log_years")

    def test_required_general_key_still_missing(self):
        del self.cfg["general"]["deadband_efc"]
        self.assertRejects("general.deadband_efc: missing")

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

    def test_unknown_top_level_key(self):
        self.cfg["bogus"] = 1
        self.assertRejects("bogus")

    def test_revert_unknown_key(self):
        self.cfg["profiles"]["field"]["revert"]["typo"] = 1
        self.assertRejects("profiles.field.revert.typo")


class LoadSave(unittest.TestCase):
    def test_load_missing_returns_default(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = config.load(os.path.join(d, "config.toml"))
        self.assertEqual(cfg, config.default_config())

    def test_load_fills_the_optional_key_so_emit_round_trips_it(self):
        text = config.emit(config.default_config())
        text = "\n".join(l for l in text.splitlines() if not l.startswith("sample_log_years")) + "\n"
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.toml")
            with open(p, "w") as fh:
                fh.write(text)
            cfg = config.load(p)
        self.assertEqual(cfg["general"]["sample_log_years"], 3)
        self.assertIn("sample_log_years = 3\n", config.emit(cfg))

    def test_save_writes_bak_and_validates(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.toml")
            cfg = config.default_config()
            config.save(cfg, p)
            cfg["general"]["deadband_efc"] = 0.7
            config.save(cfg, p)
            self.assertTrue(os.path.exists(p + ".bak"))
            self.assertEqual(config.load(p)["general"]["deadband_efc"], 0.7)
            with open(p + ".bak", "rb") as fh:
                self.assertEqual(tomllib.load(fh)["general"]["deadband_efc"], 0.5)
            cfg["general"]["deadband_efc"] = -1
            with self.assertRaises(config.ConfigError):
                config.save(cfg, p)
            self.assertEqual(config.load(p)["general"]["deadband_efc"], 0.7)

    def test_load_bad_toml_raises_configerror(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.toml")
            with open(p, "w") as fh:
                fh.write("[general\nbroken")
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

    def test_all_digit_profile_name_stays_string(self):
        cfg = config.default_config()
        cfg["profiles"]["2024"] = cfg["profiles"]["daily"]
        config.set_dotted(cfg, "general.active_profile", "2024")
        self.assertIsInstance(cfg["general"]["active_profile"], str)
        self.assertEqual(cfg["general"]["active_profile"], "2024")
        config.validate(cfg)

    def test_label_with_comma_stays_one_string(self):
        cfg = config.default_config()
        config.set_dotted(cfg, "profiles.daily.label", "Desk, Home")
        self.assertIsInstance(cfg["profiles"]["daily"]["label"], str)
        self.assertEqual(cfg["profiles"]["daily"]["label"], "Desk, Home")
        config.validate(cfg)

    def test_auto_balance_true_uppercase(self):
        cfg = config.default_config()
        config.set_dotted(cfg, "general.auto_balance", "TRUE")
        self.assertIs(cfg["general"]["auto_balance"], True)
        config.validate(cfg)

    def test_band_with_one_value_raises(self):
        cfg = config.default_config()
        with self.assertRaises(config.ConfigError) as cm:
            config.set_dotted(cfg, "profiles.daily.bands.neutral", "50")
        self.assertIn("profiles.daily.bands.neutral", str(cm.exception))

    def test_deadband_invalid_float_raises(self):
        cfg = config.default_config()
        with self.assertRaises(config.ConfigError) as cm:
            config.set_dotted(cfg, "general.deadband_efc", "abc")
        self.assertIn("general.deadband_efc", str(cm.exception))


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


if __name__ == "__main__":
    unittest.main()

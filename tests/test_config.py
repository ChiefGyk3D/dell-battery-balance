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


if __name__ == "__main__":
    unittest.main()

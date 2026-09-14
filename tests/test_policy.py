import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
from dbb import config, policy, registry, state as st

DESIGN = 4600000


def sample(ac=1, present=("BAT0", "BAT1")):
    one = dict(status="Full", capacity=80, charge_now_uah=3680000, charge_full_uah=DESIGN,
               charge_full_design_uah=DESIGN, voltage_now_uv=11400000,
               voltage_min_design_uv=11400000, current_now_ua=0, temp_dc=313)
    return dict(ts=1000.0, boot_id="b", ac_online=ac, bats={b: dict(one) for b in present})


def seed(state, slot, efc_value, name):
    v = dict(status="Full", capacity=80, charge_now_uah=3680000, charge_full_uah=DESIGN,
             charge_full_design_uah=DESIGN, voltage_now_uv=11400000,
             voltage_min_design_uv=11400000, current_now_ua=0, temp_dc=313)
    t, _ = registry.observe(state, slot, v, sample(present=(slot,)), None)
    if t["pack"] is None:
        registry.assign(state, slot, name, new=True)
    t["discharge_uah"] = efc_value * DESIGN
    return t


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
        seed(self.state, "BAT0", 1.0, "A")
        seed(self.state, "BAT1", 1.0, "B")
        self.state["profile_switched_ts"] = 0.0

    def test_daily_neutral(self):
        r = policy.resolve(self.cfg, self.state, sample(), now=100.0)
        self.assertEqual(r.profile, "daily")
        self.assertEqual(r.bands, {"BAT0": (50, 80), "BAT1": (50, 80)})
        self.assertIsNone(r.revert)

    def test_daily_diverged(self):
        seed(self.state, "BAT1", 2.0, "B")
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
        seed(self.state, "BAT1", 2.5, "B")
        r = policy.resolve(self.cfg, self.state, sample(), now=100.0)
        self.assertEqual(r.roles, {"BAT0": "neutral", "BAT1": "neutral"})


class Revert(unittest.TestCase):
    def setUp(self):
        self.cfg = config.default_config()
        self.state = st.new_state()
        seed(self.state, "BAT0", 1.0, "A")
        seed(self.state, "BAT1", 1.0, "B")
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
        # 20h clears on_ac_hours (12) but stays under after_hours (72), so this
        # isolates the on_ac branch: it must not fire without run tracking.
        self.state["ac_run_start_ts"] = None
        self.assertIsNone(policy.revert_due(self.cfg["profiles"]["field"], self.state,
                                            now=20 * 3600.0 - 1))

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

    def test_one_off_for_works_on_profile_without_revert_table(self):
        # travel has no [profiles.travel.revert] -- --for must still arm a
        # one-off revert regardless (spec: --for belongs to the switch).
        policy.switch_profile(self.cfg, self.state, "travel", now=0.0, reason="test")
        self.state["one_off_revert_hours"] = 2
        self.assertIsNone(policy.revert_due(self.cfg["profiles"]["travel"], self.state,
                                            now=3600.0))
        self.assertEqual(policy.revert_due(self.cfg["profiles"]["travel"], self.state,
                                           now=2 * 3600.0 + 1), "after_hours")
        self.assertEqual(policy.resolve_revert_target(self.cfg, "travel"), "daily")

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

    def test_hour_out_warning_survives_the_still_going_out_guard(self):
        # The guard holds the on-AC trigger while active_day() is true, but
        # revert_eta must still see it coming (and revert_soon must still
        # warn) instead of omitting the on-AC branch entirely.
        f = self.cfg["profiles"]["field"]
        self.state["ac_run_start_ts"] = 0.0
        self.state["last_battery_stint_end_ts"] = 0.0
        self.assertAlmostEqual(policy.revert_eta(f, self.state, now=23.5 * 3600.0), 0.5)
        self.assertTrue(policy.revert_soon(f, self.state, now=23.5 * 3600.0))
        self.assertEqual(policy.revert_due(f, self.state, now=24 * 3600.0 + 1), "on_ac_hours")

    def test_switch_clears_warning_marker(self):
        self.state["revert_warned_ts"] = 1.0
        policy.switch_profile(self.cfg, self.state, "daily", now=5.0, reason="test")
        self.assertIsNone(self.state["revert_warned_ts"])


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


if __name__ == "__main__":
    unittest.main()

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


if __name__ == "__main__":
    unittest.main()

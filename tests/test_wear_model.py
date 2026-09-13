"""Wear model and policy tests: simulated EC behaviour, no real /sys touched."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from dbb import policy, sysfs, wear

DESIGN = 4600000


def mk(ts, ac, b0, b1, s0="Discharging", s1="Discharging", t=320):
    def one(c, st):
        return dict(status=st, capacity=round(100 * c / DESIGN), charge_now_uah=c,
                    charge_full_uah=DESIGN, charge_full_design_uah=DESIGN,
                    voltage_now_uv=11400000, voltage_min_design_uv=11400000,
                    current_now_ua=0, temp_dc=t)
    return dict(ts=ts, boot_id="x", ac_online=ac, bats={"BAT0": one(b0, s0), "BAT1": one(b1, s1)})


class WearModelTests(unittest.TestCase):
    def test_three_unplug_cycles(self):
        """Simulate the EC behaviour we actually observed: BAT1 drains first
        to a floor, then BAT0 takes over. Three full unplug/recharge cycles."""
        st = {"version": 1, "created": "t", "slots": {}, "last": None,
              "discharge_first": {"BAT0": 0, "BAT1": 0}, "sessions": 0, "policy": None}

        t = 0.0
        b0, b1 = DESIGN, DESIGN
        for _cycle in range(3):
            while b1 > DESIGN * 0.05:                      # BAT1 drains, BAT0 holds
                t += 300
                b1 -= DESIGN * 0.05
                wear.integrate(st, mk(t, 0, int(b0), int(max(b1, 0))))
            while b0 > DESIGN * 0.30:                      # BAT0 takes over
                t += 300
                b0 -= DESIGN * 0.05
                wear.integrate(st, mk(t, 0, int(b0), int(max(b1, 0))))
            while b0 < DESIGN or b1 < DESIGN:              # recharge on AC
                t += 300
                if b0 < DESIGN:
                    b0 = min(DESIGN, b0 + DESIGN * 0.05)
                else:
                    b1 = min(DESIGN, b1 + DESIGN * 0.05)
                wear.integrate(st, mk(t, 1, int(b0), int(b1), "Charging", "Charging"))

        e0, e1 = wear.efc(st["slots"]["BAT0"]), wear.efc(st["slots"]["BAT1"])
        self.assertGreater(e1, e0, "draining pack (BAT1) should show more cycle wear")

        self.assertGreater(
            st["slots"]["BAT0"]["calendar_score"], st["slots"]["BAT1"]["calendar_score"],
            "idle pack (BAT0) should show a higher calendar score")

        self.assertGreater(
            st["discharge_first"]["BAT1"], st["discharge_first"]["BAT0"],
            "BAT1 should be credited as draining first")

        roles, _why = policy.decide_roles(st, 0.5)
        self.assertEqual(roles["BAT1"], "protect", "worn pack must be protected")
        self.assertEqual(roles["BAT0"], "work")

    def test_deadband_holds_neutral(self):
        """Near-equal packs must hold neutral rather than flap."""
        st = {"slots": {"BAT0": wear.blank_slot(DESIGN), "BAT1": wear.blank_slot(DESIGN)}}
        st["slots"]["BAT0"]["discharge_uah"] = DESIGN * 2.0
        st["slots"]["BAT1"]["discharge_uah"] = DESIGN * 2.2
        roles, _why = policy.decide_roles(st, 0.5)
        self.assertEqual(set(roles.values()), {"neutral"})

    def test_clamp_band_respects_limits(self):
        """Band clamping against the firmware limits (start 50-95, stop 55-100)."""
        self.assertEqual(sysfs.clamp_band(50, 60), (50, 60))
        self.assertEqual(sysfs.clamp_band(10, 200), (50, 100))
        self.assertLess(sysfs.clamp_band(95, 55)[0], 55)


if __name__ == "__main__":
    unittest.main()

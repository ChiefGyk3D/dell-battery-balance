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
        from dbb import state as dbb_state, registry
        st = dbb_state.new_state()

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

        e0 = wear.efc(registry.slot_counters(st, "BAT0"))
        e1 = wear.efc(registry.slot_counters(st, "BAT1"))
        self.assertGreater(e1, e0, "draining pack (BAT1) should show more cycle wear")

        self.assertGreater(
            registry.slot_counters(st, "BAT0")["calendar_score"],
            registry.slot_counters(st, "BAT1")["calendar_score"],
            "idle pack (BAT0) should show a higher calendar score")

        self.assertGreater(
            st["discharge_first"]["BAT1"], st["discharge_first"]["BAT0"],
            "BAT1 should be credited as draining first")

        roles, _why = policy.decide_roles(policy.efc_by_slot(st), 0.5)
        self.assertEqual(roles["BAT1"], "protect", "worn pack must be protected")
        self.assertEqual(roles["BAT0"], "work")

    def test_deadband_holds_neutral(self):
        """Near-equal packs must hold neutral rather than flap."""
        from dbb import state as dbb_state, registry
        st = dbb_state.new_state()
        s = mk(0, 1, 0, 0)
        t0, _ = registry.observe(st, "BAT0", s["bats"]["BAT0"], s, None)
        t1, _ = registry.observe(st, "BAT1", s["bats"]["BAT1"], s, None)
        t0["discharge_uah"] = DESIGN * 2.0
        t1["discharge_uah"] = DESIGN * 2.2
        roles, _why = policy.decide_roles(policy.efc_by_slot(st), 0.5)
        self.assertEqual(set(roles.values()), {"neutral"})

    def test_clamp_band_respects_limits(self):
        """Band clamping against the firmware limits (start 50-95, stop 55-100)."""
        self.assertEqual(sysfs.clamp_band(50, 60), (50, 60))
        self.assertEqual(sysfs.clamp_band(10, 200), (50, 100))
        self.assertLess(sysfs.clamp_band(95, 55)[0], 55)


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


class GapAccrual(unittest.TestCase):
    def mk(self, ts, ac, capacity, charge_now):
        one = lambda cap, c: dict(status="Full", capacity=cap, charge_now=c,
                                   charge_full_uah=4600000, charge_full_design_uah=4600000,
                                   voltage_now_uv=11400000, voltage_min_design_uv=11400000,
                                   current_now_ua=0, temp_dc=313, charge_now_uah=c)
        # BAT1 stays fully charged and idle throughout, so it never
        # interferes with the BAT0 assertions below.
        return dict(ts=ts, boot_id="b", ac_online=ac,
                    bats={"BAT0": one(capacity, charge_now), "BAT1": one(100, 4600000)})

    def test_ac_bounded_gap_accrues_at_mean_soc(self):
        from dbb import wear, state as st, registry
        s = st.new_state()
        wear.integrate(s, self.mk(0, 1, 100, 4600000))
        wear.integrate(s, self.mk(4 * 3600, 1, 60, 2760000))
        slot = registry.slot_counters(s, "BAT0")
        self.assertAlmostEqual(slot["soc_hours"], 4.0, places=6)
        self.assertAlmostEqual(slot["soc_hours_sum"], 4.0 * 80, places=6)
        self.assertGreater(slot["calendar_score"], 0)

    def test_battery_side_gap_skips_accrual_but_counts_deltas(self):
        from dbb import wear, state as st, registry
        s = st.new_state()
        wear.integrate(s, self.mk(0, 1, 100, 4600000))
        wear.integrate(s, self.mk(4 * 3600, 0, 60, 2760000))
        slot = registry.slot_counters(s, "BAT0")
        self.assertEqual(slot["soc_hours"], 0)
        self.assertEqual(slot["calendar_score"], 0)
        self.assertEqual(slot["discharge_uah"], 4600000 - 2760000)

    def test_zero_previous_soc_is_averaged_not_ignored(self):
        from dbb import wear, state as st, registry
        s = st.new_state()
        wear.integrate(s, self.mk(0, 1, 0, 0))
        wear.integrate(s, self.mk(4 * 3600, 1, 50, 2300000))
        slot = registry.slot_counters(s, "BAT0")
        self.assertAlmostEqual(slot["soc_hours_sum"], 4.0 * 25, places=6)


class SamplesCounter(unittest.TestCase):
    def test_samples_not_incremented_on_the_tick_that_trips_a_change(self):
        from dbb import state as dbb_state, registry
        s = dbb_state.new_state()
        wear.integrate(s, mk(0, 0, DESIGN, DESIGN))              # opens tenures: an "insert" tick, not counted
        wear.integrate(s, mk(120, 0, DESIGN - 1000, DESIGN))     # ordinary tick
        t = registry.slot_counters(s, "BAT0")
        self.assertEqual(t["samples"], 1)
        # A huge, implausible charge_now jump trips a new, unidentified
        # tenure for BAT0 -- that tick's interval belongs to nobody (per
        # observe()'s own contract), so it must not be counted as a sample
        # on the brand-new tenure either.
        wear.integrate(s, mk(240, 0, 0, DESIGN))
        new_t = registry.slot_counters(s, "BAT0")
        self.assertIsNot(new_t, t)
        self.assertEqual(new_t["samples"], 0)
        wear.integrate(s, mk(360, 0, 0, DESIGN))
        self.assertEqual(registry.slot_counters(s, "BAT0")["samples"], 1)


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


class Events(unittest.TestCase):
    def test_bounded(self):
        from dbb import state as st
        s = st.new_state()
        for i in range(600):
            st.add_event(s, "test", str(i))
        self.assertEqual(len(s["events"]), 500)
        self.assertEqual(s["events"][-1]["detail"], "599")


if __name__ == "__main__":
    unittest.main()

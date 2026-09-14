import copy, os, sys, time, unittest
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

    def test_switch_between_two_conference_profiles_resets_the_phase(self):
        # I3: defcon's in-progress topping/manual state must not carry over
        # to grrcon, and grrcon must be free to enter its own hold.
        self.ov.step(self.cfg, self.state, self.both(), self.at(20, 0))
        self.assertEqual(self.state["overnight"]["phase"], "charging_full")
        self.ov.set_manual(self.state, "topoff")
        self.assertEqual(self.ov.step(self.cfg, self.state, self.both(), self.at(20, 1)), "topping")

        self.cfg["profiles"]["grrcon"] = copy.deepcopy(self.cfg["profiles"]["conference"])
        self.cfg["profiles"]["grrcon"]["overnight"]["night_from"] = "19:00"

        self.policy.switch_profile(self.cfg, self.state, "grrcon", self.at(20, 1), "test")
        self.assertEqual(self.state["overnight"]["phase"], "off")
        self.assertIsNone(self.state["overnight"]["manual"])
        self.assertTrue(any(e["kind"] == "overnight" and "overnight off" in e["detail"]
                            for e in self.state["events"]))

        self.assertEqual(self.ov.step(self.cfg, self.state, self.both(), self.at(20, 2)), "holding")

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

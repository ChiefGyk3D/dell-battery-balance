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

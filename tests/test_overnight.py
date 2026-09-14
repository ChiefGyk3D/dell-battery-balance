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

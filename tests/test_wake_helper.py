"""The root-only wake helper, run against temp files instead of the RTC."""
import os, subprocess, tempfile, time, unittest

HELPER = os.path.join(os.path.dirname(__file__), os.pardir, "libexec", "dell-battery-balance-wake")


class WakeHelper(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.state = os.path.join(d, "state"); os.makedirs(self.state)
        self.rtc = os.path.join(d, "wakealarm")
        with open(self.rtc, "w"):
            pass
        self.lid = os.path.join(d, "lid", "LID0", "state"); os.makedirs(os.path.dirname(self.lid))
        self.config = os.path.join(d, "config.toml")
        self.suspended = os.path.join(d, "suspended")
        self.env = dict(os.environ, DBB_STATE_DIR=self.state, DBB_RTC_PATH=self.rtc,
                        DBB_LID_GLOB=os.path.join(d, "lid", "*", "state"), DBB_CONFIG_FILE=self.config,
                        DBB_SUSPEND_CMD=f"touch {self.suspended}")
        self.now = int(time.time())

    def tearDown(self):
        self.tmp.cleanup()

    def run_helper(self):
        r = subprocess.run(["sh", HELPER], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def req(self, text):
        with open(os.path.join(self.state, "wakealarm"), "w") as fh:
            fh.write(text)

    def mark(self, value=None):
        p = os.path.join(self.state, "wakealarm.set")
        if value is None:
            if not os.path.exists(p):
                return None
            with open(p) as fh:
                return fh.read().strip()
        with open(p, "w") as fh:
            fh.write(f"{value}\n")

    def rtc_text(self):
        with open(self.rtc) as fh:
            return fh.read().strip()

    def lid_state(self, s):
        with open(self.lid, "w") as fh:
            fh.write(f"state:      {s}\n")

    def test_programs_a_future_alarm_and_records_it(self):
        want = self.now + 3600
        self.req(f"{want}\n")
        self.run_helper()
        self.assertEqual(self.rtc_text(), str(want))
        self.assertEqual(self.mark(), str(want))

    def test_rewrites_only_when_the_value_changes(self):
        want = self.now + 3600
        self.req(f"{want}\n")
        self.run_helper()
        with open(self.rtc, "w") as fh:
            fh.write("sentinel")
        self.run_helper()
        self.assertEqual(self.rtc_text(), "sentinel")
        self.req(f"{want + 60}\n")
        self.run_helper()
        self.assertEqual(self.rtc_text(), str(want + 60))

    def test_refuses_past_far_and_garbage(self):
        for bad in (f"{self.now - 10}\n", f"{self.now + 90000}\n", "soon\n", "", "12 34\n"):
            with self.subTest(bad=bad):
                self.req(bad)
                self.run_helper()
                self.assertEqual(self.rtc_text(), "")
                self.assertIsNone(self.mark())

    def test_clears_an_alarm_it_set_when_the_request_goes_away(self):
        self.mark(self.now + 3600)
        self.req("")
        self.run_helper()
        self.assertEqual(self.rtc_text(), "0")
        self.assertIsNone(self.mark())

    def test_never_touches_an_alarm_it_did_not_set(self):
        with open(self.rtc, "w") as fh:
            fh.write("1234567890")
        self.req("")
        self.run_helper()
        self.assertEqual(self.rtc_text(), "1234567890")

    def test_resuspends_after_its_own_wake_with_the_lid_closed(self):
        self.mark(self.now - 60)
        self.req("")
        self.lid_state("closed")
        self.run_helper()
        self.assertTrue(os.path.exists(self.suspended))
        self.assertIsNone(self.mark())
        self.assertEqual(self.rtc_text(), "")          # a fired alarm needs no clearing

    def test_no_resuspend_with_the_lid_open(self):
        self.mark(self.now - 60)
        self.req("")
        self.lid_state("open")
        self.run_helper()
        self.assertFalse(os.path.exists(self.suspended))
        self.assertIsNone(self.mark())

    def test_no_resuspend_when_config_says_so(self):
        self.mark(self.now - 60)
        self.req("")
        self.lid_state("closed")
        with open(self.config, "w") as fh:
            fh.write("[general]\ntopoff_resuspend = false\n")
        self.run_helper()
        self.assertFalse(os.path.exists(self.suspended))

    def test_stale_marker_is_a_clear_not_a_suspend(self):
        self.mark(self.now - 600)
        self.req("")
        self.lid_state("closed")
        self.run_helper()
        self.assertFalse(os.path.exists(self.suspended))
        self.assertEqual(self.rtc_text(), "0")
        self.assertIsNone(self.mark())

    def test_wake_still_holding_reprograms_instead_of_suspending(self):
        # The estimate moved later after the wake: the request is still there.
        self.mark(self.now - 60)
        self.req(f"{self.now + 900}\n")
        self.lid_state("closed")
        self.run_helper()
        self.assertFalse(os.path.exists(self.suspended))
        self.assertEqual(self.rtc_text(), str(self.now + 900))

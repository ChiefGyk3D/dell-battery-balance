import os, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))


class ApplyBands(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DBB_SYSFS_ROOT"] = self.tmp.name
        for m in list(sys.modules):
            if m.startswith("dbb"):
                del sys.modules[m]
        from tests.fakesys import FakeSys
        from dbb import apply, state as st, sysfs
        self.apply, self.st, self.sysfs = apply, st, sysfs
        self.fs = FakeSys(self.tmp.name)
        self.fs.bat("BAT0", charge_types="Trickle Fast Standard [Adaptive] Custom", start=50, stop=90)
        self.fs.bat("BAT1")
        for slot in ("BAT0", "BAT1"):
            mode, a, b = sysfs.SYSMAN_ATTRS[slot]
            self.fs.sysman_attr(mode, "Adaptive")
            self.fs.sysman_attr(a, "50", possible=None)
            self.fs.sysman_attr(b, "90", possible=None)
        self.state = st.new_state()

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("DBB_SYSFS_ROOT", None)

    def test_writes_and_reads_back(self):
        r = self.apply.apply_bands({"BAT0": (50, 80), "BAT1": (50, 80)}, self.state)
        self.assertEqual(sorted(r["written"]), ["BAT0", "BAT1"])
        self.assertEqual(r["mismatch"], [])
        self.assertEqual(self.fs.read("class/firmware-attributes/dell-wmi-sysman/attributes/SliceBattChargeCfg/current_value"), "Custom")
        self.assertEqual(self.fs.read("class/firmware-attributes/dell-wmi-sysman/attributes/SliceBattCustomChargeStop/current_value"), "80")
        self.assertEqual(self.state["firmware"]["BAT1"]["observed"], [50, 80])

    def test_skips_when_already_observed(self):
        self.apply.apply_bands({"BAT0": (50, 80)}, self.state)
        r = self.apply.apply_bands({"BAT0": (50, 80)}, self.state)
        self.assertEqual(r["skipped"], ["BAT0"])
        self.assertEqual(r["written"], [])

    def test_mismatch_recorded(self):
        # Make the stop attribute refuse the write, leaving the old value. Done by
        # wrapping the write rather than chmod, because root ignores file modes.
        real = self.sysfs.write_sysman

        def refuse_stop(attr, value, password=None):
            if attr == "SliceBattCustomChargeStop":
                return f"{attr}: [Errno 13] Permission denied"
            return real(attr, value, password)

        with mock.patch.object(self.sysfs, "write_sysman", side_effect=refuse_stop):
            r = self.apply.apply_bands({"BAT1": (50, 70)}, self.state)
        self.assertIn("BAT1", r["errors"])
        self.assertEqual(self.state["firmware"]["BAT1"]["requested"], [50, 70])
        self.assertNotEqual(self.state["firmware"]["BAT1"]["observed"], [50, 70])
        self.assertIn("BAT1", r["mismatch"])
        self.assertEqual(self.state["events"][-1]["kind"], "firmware")


if __name__ == "__main__":
    unittest.main()

"""The pack's own firmware cycle counter, tracked per pack to see whether it
moves and how it compares to the tool's EFC."""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
from dbb import registry, state as st, wear

DESIGN = 4646000
EPPID_X = "CN0XXXXXSLW0000E00AAA01"
EPPID_Y = "CN0XXXXXSLW0000E00ABA01"


def bat(charge=2300000, capacity=50, cycles=0, eppid=EPPID_X, serial="101"):
    return dict(status="Discharging", capacity=capacity, charge_now_uah=charge,
                charge_full_uah=DESIGN, charge_full_design_uah=DESIGN,
                voltage_now_uv=11400000, voltage_min_design_uv=11400000,
                current_now_ua=0, temp_dc=313, present=1, eppid=eppid, serial=serial,
                cycle_count=cycles)


def tick(s, ts, ac=1, **bats):
    wear.integrate(s, dict(ts=float(ts), boot_id="b", ac_online=ac, bats=bats))


class FirmwareCycles(unittest.TestCase):
    def setUp(self):
        self.s = st.new_state()
        tick(self.s, 0, BAT0=bat(), BAT1=bat(eppid=EPPID_Y, serial="102"))
        registry.assign(self.s, "BAT0", "Alpha", new=True)
        registry.assign(self.s, "BAT1", "Bravo", new=True)
        tick(self.s, 120, BAT0=bat(), BAT1=bat(eppid=EPPID_Y, serial="102"))

    def fw(self, name):
        return self.s["packs"][name].get("fw_cycles")

    def events(self, kind):
        return [e["detail"] for e in self.s["events"] if e["kind"] == kind]

    def test_first_reading_is_recorded(self):
        self.assertEqual(self.fw("Alpha")["value"], 0)
        self.assertEqual(self.fw("Alpha")["first_value"], 0)
        self.assertIsNone(self.fw("Alpha")["changed_ts"])

    def test_an_increase_is_an_event_with_the_tools_efc_beside_it(self):
        # Alpha discharges a full design capacity, then its counter ticks.
        tick(self.s, 240, ac=0, BAT0=bat(charge=4646000, capacity=100), BAT1=bat(eppid=EPPID_Y, serial="102"))
        c = 4646000
        ts = 240
        while c > 0:
            c = max(0, c - 400000); ts += 120
            tick(self.s, ts, ac=0, BAT0=bat(charge=c, capacity=int(100 * c / DESIGN)),
                 BAT1=bat(eppid=EPPID_Y, serial="102"))
        for extra in (1, 2):
            tick(self.s, ts + 120 * extra, BAT0=bat(charge=0, capacity=0, cycles=1),
                 BAT1=bat(eppid=EPPID_Y, serial="102"))
        fw = self.fw("Alpha")
        self.assertEqual((fw["first_value"], fw["value"]), (0, 1))
        self.assertIsNotNone(fw["changed_ts"])
        ev = self.events("cycles")
        self.assertEqual(len(ev), 1, ev)
        self.assertIn("Alpha: firmware cycle count 0 -> 1", ev[0])
        self.assertIn("1.00 EFC", ev[0])

    def test_one_sample_is_not_enough(self):
        tick(self.s, 240, BAT0=bat(cycles=5), BAT1=bat(eppid=EPPID_Y, serial="102"))
        self.assertEqual(self.fw("Alpha")["value"], 0)
        tick(self.s, 360, BAT0=bat(cycles=0), BAT1=bat(eppid=EPPID_Y, serial="102"))
        self.assertEqual(self.fw("Alpha")["value"], 0)
        self.assertEqual(self.events("cycles"), [])

    def test_a_mirrored_sample_never_moves_the_other_pack(self):
        # BAT1 mirrors BAT0 for two samples: twins, so no identity, no reading.
        tick(self.s, 240, BAT0=bat(cycles=3), BAT1=bat(cycles=3))
        tick(self.s, 360, BAT0=bat(cycles=3), BAT1=bat(cycles=3))
        self.assertEqual(self.fw("Bravo")["value"], 0)

    def test_waits_for_the_identity_to_be_reconfirmed(self):
        # A one-sample foreign reading restarts the identity count; a count
        # read before the pack's own identity is confirmed again is not taken.
        Z = "CN0XXXXXSLW0000E00ACA01"
        tick(self.s, 240, BAT0=bat(eppid=Z, serial="103", cycles=5), BAT1=bat(eppid=EPPID_Y, serial="102"))
        tick(self.s, 360, BAT0=bat(cycles=5), BAT1=bat(eppid=EPPID_Y, serial="102"))
        self.assertEqual(self.fw("Alpha")["value"], 0)
        tick(self.s, 480, BAT0=bat(cycles=5), BAT1=bat(eppid=EPPID_Y, serial="102"))
        self.assertEqual(self.fw("Alpha")["value"], 5)

    def test_a_decrease_is_a_warning(self):
        tick(self.s, 240, BAT0=bat(cycles=4), BAT1=bat(eppid=EPPID_Y, serial="102"))
        tick(self.s, 360, BAT0=bat(cycles=4), BAT1=bat(eppid=EPPID_Y, serial="102"))
        tick(self.s, 480, BAT0=bat(cycles=1), BAT1=bat(eppid=EPPID_Y, serial="102"))
        tick(self.s, 600, BAT0=bat(cycles=1), BAT1=bat(eppid=EPPID_Y, serial="102"))
        self.assertEqual(self.fw("Alpha")["value"], 1)
        self.assertTrue(any("went down" in w for w in self.events("warning")))

    def test_follows_the_pack_not_the_slot(self):
        tick(self.s, 240)                                   # both out
        tick(self.s, 360, BAT0=bat(eppid=EPPID_Y, serial="102", cycles=7), BAT1=bat(cycles=2))
        tick(self.s, 480, BAT0=bat(eppid=EPPID_Y, serial="102", cycles=7), BAT1=bat(cycles=2))
        self.assertEqual(self.fw("Bravo")["value"], 7)
        self.assertEqual(self.fw("Alpha")["value"], 2)

    def test_unlabelled_slot_records_nothing(self):
        s = st.new_state()
        tick(s, 0, BAT0=bat(cycles=9))
        tick(s, 120, BAT0=bat(cycles=9))
        self.assertEqual(s["packs"], {})

    def test_totals_expose_it(self):
        row = registry.pack_totals(self.s, "Alpha", now=0.0, bench_temp_c=25.0)
        self.assertEqual(row["firmware_cycles"], 0)
        self.assertEqual(row["efc_since_firmware_first"], 0.0)

    def test_sample_log_does_not_change_shape(self):
        self.assertNotIn("cycle_count", st.CSV_FIELDS)


if __name__ == "__main__":
    unittest.main()

"""Pack identity read from the pack itself (ePPID + serial), when it has one."""
import os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
from dbb import registry, state as st, wear

DESIGN = 4646000
# Placeholders shaped like a Dell ePPID; never a real pack's.
EPPID_X = "CN0XXXXXSLW0000E00AAA01"
EPPID_Y = "CN0XXXXXSLW0000E00ABA01"
EPPID_Z = "CN0XXXXXSLW0000E00ACA01"


def bat(charge=2300000, full=DESIGN, capacity=50, eppid=EPPID_X, serial="101", design=DESIGN):
    return dict(status="Discharging", capacity=capacity, charge_now_uah=charge,
                charge_full_uah=full, charge_full_design_uah=design,
                voltage_now_uv=11400000, voltage_min_design_uv=11400000,
                current_now_ua=0, temp_dc=313, present=1, eppid=eppid, serial=serial)


def sample(ts, ac=1, **bats):
    return dict(ts=float(ts), boot_id="b", ac_online=ac, bats=bats)


def tick(s, ts, **bats):
    wear.integrate(s, sample(ts, **bats))


class Fingerprint(unittest.TestCase):
    def test_eppid_and_serial(self):
        self.assertEqual(registry.fingerprint(bat()), f"{EPPID_X}/101")

    def test_no_eppid_is_unreadable(self):
        for bad in (None, "", "   ", "0000000000000000000000", "CN0"):
            self.assertIsNone(registry.fingerprint(bat(eppid=bad)), bad)

    def test_missing_serial_still_reads_eppid(self):
        self.assertEqual(registry.fingerprint(bat(serial=None)), f"{EPPID_X}/")


class Confirmation(unittest.TestCase):
    """A fingerprint counts only once two consecutive samples agree, and
    never when both slots read the same value."""

    def setUp(self):
        self.s = st.new_state()

    def test_one_sample_is_not_enough(self):
        tick(self.s, 0, BAT0=bat())
        self.assertIsNone(registry.slot_identity(self.s, "BAT0"))
        tick(self.s, 120, BAT0=bat())
        self.assertEqual(registry.slot_identity(self.s, "BAT0"), f"{EPPID_X}/101")

    def test_changing_reading_restarts_the_count(self):
        tick(self.s, 0, BAT0=bat())
        tick(self.s, 120, BAT0=bat(eppid=EPPID_Y))
        self.assertIsNone(registry.slot_identity(self.s, "BAT0"))

    def test_both_slots_identical_is_no_identity(self):
        tick(self.s, 0, BAT0=bat(), BAT1=bat())
        tick(self.s, 120, BAT0=bat(), BAT1=bat())
        self.assertIsNone(registry.slot_identity(self.s, "BAT0"))
        self.assertIsNone(registry.slot_identity(self.s, "BAT1"))

    def test_one_mirrored_sample_breaks_the_streak(self):
        # The measured BAT1 glitch: one sample returns BAT0's readings.
        tick(self.s, 0, BAT0=bat(), BAT1=bat(eppid=EPPID_Y))
        tick(self.s, 120, BAT0=bat(), BAT1=bat())
        self.assertIsNone(registry.slot_identity(self.s, "BAT1"))
        tick(self.s, 240, BAT0=bat(), BAT1=bat(eppid=EPPID_Y))
        self.assertIsNone(registry.slot_identity(self.s, "BAT1"))

    def test_removal_forgets_the_slot(self):
        tick(self.s, 0, BAT0=bat(), BAT1=bat(eppid=EPPID_Y))
        tick(self.s, 120, BAT0=bat(), BAT1=bat(eppid=EPPID_Y))
        tick(self.s, 240, BAT0=bat())
        self.assertIsNone(registry.slot_identity(self.s, "BAT1"))


class LearnAndIdentify(unittest.TestCase):
    def setUp(self):
        self.s = st.new_state()
        tick(self.s, 0, BAT0=bat(), BAT1=bat(eppid=EPPID_Y, serial="102"))
        registry.assign(self.s, "BAT0", "Alpha", new=True)
        registry.assign(self.s, "BAT1", "Bravo", new=True)
        tick(self.s, 120, BAT0=bat(), BAT1=bat(eppid=EPPID_Y, serial="102"))

    def fp(self, name):
        return self.s["packs"][name].get("fingerprint")

    def test_named_packs_learn_their_fingerprint(self):
        self.assertEqual(self.fp("Alpha"), f"{EPPID_X}/101")
        self.assertEqual(self.fp("Bravo"), f"{EPPID_Y}/102")
        self.assertEqual(registry.identity_state(self.s["packs"]["Alpha"]), "read")

    def test_a_swap_is_identified_without_asking(self):
        # Both out, then back in the other way round, charge unchanged: the
        # heuristic alone cannot see this.
        tick(self.s, 240)                                   # both out
        tick(self.s, 480, BAT0=bat(eppid=EPPID_Y, serial="102"), BAT1=bat())
        self.assertIn("BAT0", self.s["pending"])            # first sample: still asking
        tick(self.s, 600, BAT0=bat(eppid=EPPID_Y, serial="102"), BAT1=bat())
        self.assertEqual(registry.open_tenure(self.s, "BAT0")["pack"], "Bravo")
        self.assertEqual(registry.open_tenure(self.s, "BAT1")["pack"], "Alpha")
        self.assertEqual(self.s["pending"], {})

    def test_a_swap_the_heuristic_missed_opens_a_tenure(self):
        # No removal seen, charge continuous: only the identity changed.
        tick(self.s, 240, BAT0=bat(eppid=EPPID_Y, serial="102"), BAT1=bat())
        self.assertEqual(registry.open_tenure(self.s, "BAT0")["pack"], "Alpha")   # one sample
        tick(self.s, 360, BAT0=bat(eppid=EPPID_Y, serial="102"), BAT1=bat())
        self.assertEqual(registry.open_tenure(self.s, "BAT0")["pack"], "Bravo")
        self.assertEqual(registry.open_tenure(self.s, "BAT1")["pack"], "Alpha")
        kinds = [e["detail"] for e in self.s["events"]]
        self.assertTrue(any("occupancy change (identity)" in d for d in kinds), kinds)

    def test_an_unknown_pack_is_asked_about(self):
        registry.note_absent(self.s, "BAT1", 240.0)
        tick(self.s, 360, BAT0=bat(), BAT1=bat(eppid=EPPID_Z, serial="103"))
        tick(self.s, 480, BAT0=bat(), BAT1=bat(eppid=EPPID_Z, serial="103"))
        self.assertIn("BAT1", self.s["pending"])
        registry.assign(self.s, "BAT1", "Charlie", new=True)
        tick(self.s, 600, BAT0=bat(), BAT1=bat(eppid=EPPID_Z, serial="103"))
        self.assertEqual(self.fp("Charlie"), f"{EPPID_Z}/103")

    def test_retired_pack_is_not_auto_identified(self):
        registry.note_absent(self.s, "BAT1", 240.0)
        registry.retire_pack(self.s, "Bravo")
        tick(self.s, 360, BAT0=bat(), BAT1=bat(eppid=EPPID_Y, serial="102"))
        tick(self.s, 480, BAT0=bat(), BAT1=bat(eppid=EPPID_Y, serial="102"))
        self.assertIn("BAT1", self.s["pending"])

    def test_hand_label_contradicting_the_pack_forgets_the_fingerprint(self):
        registry.swap(self.s)
        self.assertIsNone(self.fp("Alpha"))
        self.assertIsNone(self.fp("Bravo"))
        self.assertTrue(any("forgotten" in e["detail"] for e in self.s["events"]))
        tick(self.s, 240, BAT0=bat(), BAT1=bat(eppid=EPPID_Y, serial="102"))
        # relearned under the hand labels, no tenure churn
        self.assertEqual(self.fp("Bravo"), f"{EPPID_X}/101")
        self.assertEqual(registry.open_tenure(self.s, "BAT0")["pack"], "Bravo")

    def test_hand_assign_against_the_reading_forgets_that_fingerprint(self):
        registry.note_absent(self.s, "BAT1", 240.0)
        tick(self.s, 360, BAT0=bat(), BAT1=bat(eppid=EPPID_Z, serial="103"))
        tick(self.s, 480, BAT0=bat(), BAT1=bat(eppid=EPPID_Z, serial="103"))
        registry.assign(self.s, "BAT1", "Bravo")        # the operator says so
        self.assertIsNone(self.fp("Bravo"))
        tick(self.s, 600, BAT0=bat(), BAT1=bat(eppid=EPPID_Z, serial="103"))
        self.assertEqual(self.fp("Bravo"), f"{EPPID_Z}/103")
        self.assertEqual(registry.open_tenure(self.s, "BAT1")["pack"], "Bravo")

    def test_identity_change_is_never_guessed_same(self):
        # Measured 2026-09-26: Charlie went in at 27% where Bravo left at 28%;
        # the charge-based guess said "same" while the fingerprint said no.
        near = dict(eppid=EPPID_Z, serial="103", charge=2250000, capacity=48)
        tick(self.s, 240, BAT0=bat(), BAT1=bat(**near))
        tick(self.s, 360, BAT0=bat(), BAT1=bat(**near))
        self.assertEqual(self.s["pending"]["BAT1"]["reason"], "identity")
        self.assertEqual(self.s["pending"]["BAT1"]["guess"], "different")

    def test_unconfirmed_contradicting_reading_is_not_credited_to_the_old_pack(self):
        # The tick that first sees a different pack must neither accrue wear
        # into the labelled pack's tenure nor overwrite where it left off.
        before = registry.open_tenure(self.s, "BAT1")
        discharged = before["discharge_uah"]
        near = dict(eppid=EPPID_Z, serial="103", charge=2250000, capacity=48)
        tick(self.s, 240, BAT0=bat(), BAT1=bat(**near))
        tick(self.s, 360, BAT0=bat(), BAT1=bat(**near))
        self.assertEqual(before["discharge_uah"], discharged)
        self.assertEqual(before["last_capacity"], 50)
        self.assertEqual(self.s["packs"]["Bravo"]["removed_at_soc"], 50)

    def test_a_one_sample_foreign_reading_costs_nothing(self):
        # A single odd reading that goes away again: no tenure, no wear lost
        # beyond the skipped interval, and the pack keeps its label.
        tick(self.s, 240, BAT0=bat(), BAT1=bat(eppid=EPPID_Z, serial="103"))
        tick(self.s, 360, BAT0=bat(), BAT1=bat(eppid=EPPID_Y, serial="102", charge=2200000))
        t = registry.open_tenure(self.s, "BAT1")
        self.assertEqual(t["pack"], "Bravo")
        self.assertEqual(self.s["pending"], {})
        self.assertEqual(t["last_charge_uah"], 2200000)


class Clones(unittest.TestCase):
    """Packs that report one shared identity: the signature of counterfeit or
    third-party packs, and what the tool's first two packs did."""

    def test_two_packs_reading_the_same_become_unreadable(self):
        s = st.new_state()
        tick(s, 0, BAT0=bat())
        registry.assign(s, "BAT0", "A", new=True)
        tick(s, 120, BAT0=bat())
        self.assertEqual(s["packs"]["A"]["fingerprint"], f"{EPPID_X}/101")
        tick(s, 240, BAT0=bat(), BAT1=bat())             # its twin arrives
        registry.assign(s, "BAT1", "B", new=True)
        tick(s, 360, BAT0=bat(), BAT1=bat())
        for name in ("A", "B"):
            self.assertIsNone(s["packs"][name]["fingerprint"], name)
            self.assertTrue(s["packs"][name]["identity_unreadable"], name)
        warn = [e["detail"] for e in s["events"] if e["kind"] == "warning"]
        self.assertTrue(any("counterfeit" in w for w in warn), warn)

    def test_unreadable_pack_never_learns_or_claims(self):
        s = st.new_state()
        tick(s, 0, BAT0=bat())
        registry.assign(s, "BAT0", "A", new=True)
        registry.set_identity(s, "A", "unreadable")
        tick(s, 120, BAT0=bat())
        self.assertIsNone(s["packs"]["A"]["fingerprint"])
        registry.note_absent(s, "BAT0", 200.0)
        tick(s, 240, BAT0=bat())
        tick(s, 360, BAT0=bat())
        self.assertIn("BAT0", s["pending"])                  # asked, never assumed

    def test_forget_relearns(self):
        s = st.new_state()
        tick(s, 0, BAT0=bat())
        registry.assign(s, "BAT0", "A", new=True)
        registry.set_identity(s, "A", "unreadable")
        registry.set_identity(s, "A", "forget")
        self.assertFalse(s["packs"]["A"]["identity_unreadable"])
        tick(s, 120, BAT0=bat())
        self.assertEqual(s["packs"]["A"]["fingerprint"], f"{EPPID_X}/101")

    def test_packs_without_eppid_behave_as_before(self):
        s = st.new_state()
        tick(s, 0, BAT0=bat(eppid=None))
        registry.assign(s, "BAT0", "A", new=True)
        tick(s, 120, BAT0=bat(eppid=None))
        self.assertIsNone(s["packs"]["A"].get("fingerprint"))
        self.assertEqual(registry.identity_state(s["packs"]["A"]), "asked")


class SysfsRead(unittest.TestCase):
    def test_sample_carries_eppid_and_serial(self):
        from tests.fakesys import FakeSys
        with tempfile.TemporaryDirectory() as d:
            fs = FakeSys(d)
            fs.bat("BAT0")
            fs._w("class/power_supply/BAT0/eppid", EPPID_X)
            fs._w("class/power_supply/BAT0/serial_number", "101")
            import importlib
            os.environ["DBB_SYSFS_ROOT"] = d
            try:
                from dbb import sysfs
                importlib.reload(sysfs)
                v = sysfs.sample_all()["bats"]["BAT0"]
            finally:
                del os.environ["DBB_SYSFS_ROOT"]
                importlib.reload(sysfs)
        self.assertEqual((v["eppid"], v["serial"]), (EPPID_X, "101"))

    def test_identity_never_reaches_the_sample_log(self):
        self.assertNotIn("eppid", st.CSV_FIELDS)
        self.assertNotIn("serial", st.CSV_FIELDS)


if __name__ == "__main__":
    unittest.main()

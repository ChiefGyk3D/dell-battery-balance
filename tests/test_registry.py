import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
from dbb import registry, state as st, wear

DESIGN = 4600000


def bat(charge=2300000, full=DESIGN, capacity=50, present=1, status="Discharging"):
    return dict(status=status, capacity=capacity, charge_now_uah=charge, charge_full_uah=full,
                charge_full_design_uah=DESIGN, voltage_now_uv=11400000,
                voltage_min_design_uv=11400000, current_now_ua=0, temp_dc=313, present=present)


def sample(ts, ac=1, **bats):
    return dict(ts=float(ts), boot_id="b", ac_online=ac, bats=bats)


class Tenures(unittest.TestCase):
    def setUp(self):
        self.s = st.new_state()

    def test_new_state_is_v2(self):
        self.assertEqual(self.s["version"], 2)
        self.assertEqual(self.s["slots"], {"BAT0": None, "BAT1": None})
        self.assertEqual(self.s["next_tenure_id"], 1)

    def test_observe_opens_first_tenure(self):
        t, changed = registry.observe(self.s, "BAT0", bat(), sample(0), None)
        self.assertTrue(changed)
        self.assertEqual(t["id"], 1)
        self.assertEqual(t["slot"], "BAT0")
        self.assertIsNone(t["pack"])
        self.assertIsNone(t["end_ts"])
        self.assertIs(registry.open_tenure(self.s, "BAT0"), t)
        self.assertEqual(self.s["next_tenure_id"], 2)

    def test_observe_again_returns_same_tenure(self):
        t1, _ = registry.observe(self.s, "BAT0", bat(), sample(0), None)
        t2, changed = registry.observe(self.s, "BAT0", bat(charge=2200000), sample(120), 120.0)
        self.assertIs(t1, t2)
        self.assertFalse(changed)
        self.assertEqual(t2["last_charge_uah"], 2200000)

    def test_close_records_end_and_clears_slot(self):
        registry.observe(self.s, "BAT1", bat(), sample(0), None)
        t = registry.close_tenure(self.s, "BAT1", 500.0)
        self.assertEqual(t["end_ts"], 500.0)
        self.assertIsNone(self.s["slots"]["BAT1"])
        self.assertIsNone(registry.open_tenure(self.s, "BAT1"))
        self.assertIs(registry.last_closed_tenure(self.s, "BAT1"), t)

    def test_note_absent_closes(self):
        registry.observe(self.s, "BAT1", bat(), sample(0), None)
        registry.note_absent(self.s, "BAT1", 300.0)
        self.assertIsNone(registry.open_tenure(self.s, "BAT1"))
        self.assertEqual(registry.tenure_by_id(self.s, 1)["end_ts"], 300.0)

    def test_ids_never_reused(self):
        registry.observe(self.s, "BAT0", bat(), sample(0), None)
        registry.close_tenure(self.s, "BAT0", 1.0)
        t, _ = registry.observe(self.s, "BAT0", bat(), sample(2), None)
        self.assertEqual(t["id"], 2)


class IntegrateIntoTenures(unittest.TestCase):
    def test_discharge_accrues_into_open_tenure(self):
        s = st.new_state()
        wear.integrate(s, sample(0, ac=0, BAT0=bat(charge=4600000, capacity=100), BAT1=bat(charge=4600000, capacity=100)))
        wear.integrate(s, sample(120, ac=0, BAT0=bat(charge=4600000, capacity=100), BAT1=bat(charge=4370000, capacity=95)))
        t1 = registry.slot_counters(s, "BAT1")
        self.assertEqual(t1["discharge_uah"], 230000)
        self.assertEqual(registry.slot_counters(s, "BAT0")["discharge_uah"], 0)
        self.assertEqual(registry.efc_for_slot(s, "BAT1"), 230000 / DESIGN)

    def test_slot_vanishing_closes_tenure(self):
        s = st.new_state()
        wear.integrate(s, sample(0, BAT0=bat(), BAT1=bat()))
        wear.integrate(s, sample(120, BAT0=bat()))
        self.assertIsNone(registry.open_tenure(s, "BAT1"))
        self.assertEqual(registry.tenure_by_id(s, 2)["end_ts"], 120.0)

    def test_interval_after_change_is_not_accrued(self):
        s = st.new_state()
        wear.integrate(s, sample(0, BAT0=bat(charge=4600000, capacity=100)))
        wear.integrate(s, sample(120, BAT0=bat(charge=4600000, capacity=100), BAT1=bat(charge=2300000)))
        # BAT1 just appeared: its first interval must not accrue anything.
        t = registry.slot_counters(s, "BAT1")
        self.assertEqual(t["discharge_uah"], 0)
        self.assertEqual(t["soc_hours"], 0)


class MigrationV1(unittest.TestCase):
    def v1(self):
        c0 = wear.blank_slot(DESIGN); c0["discharge_uah"] = 1.5 * DESIGN; c0["calendar_score"] = 12.0
        c1 = wear.blank_slot(DESIGN); c1["discharge_uah"] = 2.0 * DESIGN
        return {
            "version": 1, "created": "2026-09-12T00:00:00+00:00",
            "slots": {"BAT0": c0, "BAT1": c1},
            "last": sample(1000, BAT0=bat(charge=4000000, capacity=87), BAT1=bat(charge=3000000, capacity=65)),
            "discharge_first": {"BAT0": 0, "BAT1": 3}, "sessions": 3, "policy": None,
            "ac_run_start_ts": 900.0, "profile_switched_ts": None, "one_off_revert_hours": None,
            "firmware": {}, "events": [], "config_error": None, "config_snapshot": None,
        }

    def test_migrates_slots_to_packs_a_b(self):
        s = registry.migrate_v1(self.v1())
        self.assertEqual(s["version"], 2)
        self.assertEqual(sorted(s["packs"]), ["A", "B"])
        self.assertEqual(s["slots"]["BAT0"], 1)
        self.assertEqual(s["slots"]["BAT1"], 2)
        a = registry.open_tenure(s, "BAT0"); b = registry.open_tenure(s, "BAT1")
        self.assertEqual(a["pack"], "A"); self.assertEqual(b["pack"], "B")
        self.assertEqual(a["discharge_uah"], 1.5 * DESIGN)
        self.assertEqual(a["calendar_score"], 12.0)
        self.assertEqual(b["discharge_uah"], 2.0 * DESIGN)
        self.assertEqual(a["last_charge_uah"], 4000000)
        self.assertEqual(b["last_capacity"], 65)
        self.assertEqual(s["discharge_first"], {"BAT0": 0, "BAT1": 3})
        self.assertEqual(s["sessions"], 3)
        self.assertEqual(s["ac_run_start_ts"], 900.0)
        self.assertEqual(s["next_tenure_id"], 3)
        self.assertEqual(s["events"][-1]["kind"], "migrate")

    def test_idempotent(self):
        s = registry.migrate_v1(self.v1())
        again = registry.migrate_v1(s)
        self.assertIs(again, s)
        self.assertEqual(s["next_tenure_id"], 3)

    def test_v1_with_one_slot(self):
        v = self.v1(); del v["slots"]["BAT1"]
        s = registry.migrate_v1(v)
        self.assertEqual(sorted(s["packs"]), ["A"])
        self.assertIsNone(s["slots"]["BAT1"])


if __name__ == "__main__":
    unittest.main()

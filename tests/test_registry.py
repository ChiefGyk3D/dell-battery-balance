import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
from dbb import registry, state as st, wear

DESIGN = 4600000


def bat(charge=2300000, full=DESIGN, capacity=50, present=1, status="Discharging", design=DESIGN):
    return dict(status=status, capacity=capacity, charge_now_uah=charge, charge_full_uah=full,
                charge_full_design_uah=design, voltage_now_uv=11400000,
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


class Detection(unittest.TestCase):
    def setUp(self):
        self.s = st.new_state()
        registry.observe(self.s, "BAT1", bat(charge=2300000, capacity=50), sample(0), None)

    def test_small_change_is_same_tenure(self):
        t, changed = registry.observe(self.s, "BAT1", bat(charge=2200000, capacity=48), sample(120), 120.0)
        self.assertFalse(changed); self.assertEqual(t["id"], 1)

    def test_big_jump_opens_new_tenure_with_pending(self):
        t, changed = registry.observe(self.s, "BAT1", bat(charge=4500000, capacity=98), sample(120), 120.0)
        self.assertTrue(changed); self.assertEqual(t["id"], 2)
        self.assertEqual(registry.tenure_by_id(self.s, 1)["end_ts"], 120.0)
        p = self.s["pending"]["BAT1"]
        self.assertEqual(p["tenure_id"], 2); self.assertEqual(p["reason"], "discontinuity")
        self.assertEqual(p["guess"], "unsure"); self.assertIsNone(p["previous_pack"])
        self.assertAlmostEqual(p["delta_pct"], (4500000 - 2300000) / DESIGN * 100, places=3)

    def test_allowed_delta_scales_with_time(self):
        self.assertAlmostEqual(registry.allowed_delta_uah(DESIGN, 60), DESIGN * 0.10)
        self.assertAlmostEqual(registry.allowed_delta_uah(DESIGN, 1200), DESIGN * 1.0)
        # a 4-hour suspend on the charger can legitimately move the whole pack
        t, changed = registry.observe(self.s, "BAT1", bat(charge=4600000, capacity=100), sample(4 * 3600), 4 * 3600.0)
        self.assertFalse(changed)

    def test_charge_full_change_trips(self):
        t, changed = registry.observe(self.s, "BAT1", bat(charge=2300000, full=4500000), sample(120), 120.0)
        self.assertTrue(changed); self.assertEqual(self.s["pending"]["BAT1"]["reason"], "charge_full")

    def test_reinsertion_guesses_same_when_close(self):
        registry.assign(self.s, "BAT1", "B", new=True)
        registry.note_absent(self.s, "BAT1", 100.0)
        t, changed = registry.observe(self.s, "BAT1", bat(charge=2250000, capacity=49), sample(200), 100.0)
        self.assertTrue(changed)
        p = self.s["pending"]["BAT1"]
        self.assertEqual(p["reason"], "insert"); self.assertEqual(p["guess"], "same")
        self.assertEqual(p["previous_pack"], "B")

    def test_reinsertion_after_external_charge_is_unsure_not_different(self):
        registry.assign(self.s, "BAT1", "B", new=True)
        registry.note_absent(self.s, "BAT1", 100.0)
        registry.observe(self.s, "BAT1", bat(charge=4600000, capacity=100), sample(7200), 7100.0)
        self.assertEqual(self.s["pending"]["BAT1"]["guess"], "unsure")

    def test_removal_above_70_warns(self):
        registry.assign(self.s, "BAT1", "B", new=True)
        registry.observe(self.s, "BAT1", bat(charge=4500000, capacity=98), sample(120), 120.0)  # trips; fine
        registry.assign(self.s, "BAT1", "B")   # confirm it is still B
        registry.note_absent(self.s, "BAT1", 300.0)
        kinds = [e["kind"] for e in self.s["events"]]
        self.assertIn("warning", kinds)
        self.assertEqual(self.s["packs"]["B"]["removed_at_soc"], 98)
        self.assertEqual(self.s["packs"]["B"]["removed_ts"], 300.0)

    def test_design_capacity_change_trips(self):
        # A different design capacity is as strong a signal as a charge_full
        # change or a discontinuity, and must be its own reason.
        t, changed = registry.observe(self.s, "BAT1", bat(charge=2300000, capacity=50, design=5200000),
                                       sample(120), 120.0)
        self.assertTrue(changed)
        self.assertEqual(self.s["pending"]["BAT1"]["reason"], "design")

    def test_none_design_uah_does_not_raise_and_self_heals(self):
        # A transient sysfs read failure can leave charge_full_design_uah
        # unreadable; the slot must not crash and must recover once a good
        # reading shows up, rather than staying blind forever.
        t0, changed0 = registry.observe(self.s, "BAT0", bat(charge=2300000, capacity=50, design=None),
                                         sample(0), None)
        self.assertIsNone(t0["design_uah"])
        # A huge jump arrives together with the first good design reading:
        # with no yardstick for the *previous* interval, no discontinuity is
        # claimed for it, but the tenure self-heals its design_uah.
        t1, changed1 = registry.observe(self.s, "BAT0", bat(charge=4500000, capacity=98), sample(120), 120.0)
        self.assertFalse(changed1)
        self.assertEqual(t1["design_uah"], DESIGN)
        # Now that design_uah is real, a later big jump does trip.
        t2, changed2 = registry.observe(self.s, "BAT0", bat(charge=2300000, capacity=50), sample(240), 120.0)
        self.assertTrue(changed2)


class Identification(unittest.TestCase):
    def setUp(self):
        self.s = st.new_state()
        registry.observe(self.s, "BAT0", bat(), sample(0), None)
        registry.observe(self.s, "BAT1", bat(), sample(0), None)

    def test_assign_new_and_known(self):
        t = registry.assign(self.s, "BAT0", "A", new=True)
        self.assertEqual(t["pack"], "A"); self.assertIn("A", self.s["packs"])
        self.assertNotIn("BAT0", self.s["pending"])
        with self.assertRaises(registry.RegistryError):
            registry.assign(self.s, "BAT1", "C")            # unknown, not new
        with self.assertRaises(registry.RegistryError):
            registry.assign(self.s, "BAT1", "A", new=True)  # exists

    def test_conflict_same_pack_in_two_slots(self):
        registry.assign(self.s, "BAT0", "A", new=True)
        with self.assertRaises(registry.RegistryError) as cm:
            registry.assign(self.s, "BAT1", "A")
        self.assertIn("BAT0", str(cm.exception))

    def test_bad_name(self):
        with self.assertRaises(registry.RegistryError):
            registry.assign(self.s, "BAT0", "no spaces here", new=True)

    def test_same_uses_previous_pack(self):
        registry.assign(self.s, "BAT1", "B", new=True)
        registry.note_absent(self.s, "BAT1", 10.0)
        registry.observe(self.s, "BAT1", bat(), sample(20), 10.0)
        t = registry.same(self.s, "BAT1")
        self.assertEqual(t["pack"], "B")
        self.assertIsNone(self.s["packs"]["B"]["removed_ts"])
        with self.assertRaises(registry.RegistryError):
            registry.same(self.s, "BAT0")   # never had a previous pack

    def test_same_accepted_even_when_guess_is_unsure(self):
        registry.assign(self.s, "BAT1", "B", new=True)
        registry.note_absent(self.s, "BAT1", 100.0)
        # A big jump after reinsertion makes the guess "unsure", not "same" --
        # `pack same` must still be usable; the guess only labels the
        # suggestion, it never gates which answers are legal.
        registry.observe(self.s, "BAT1", bat(charge=4600000, capacity=100), sample(7200), 7100.0)
        self.assertEqual(self.s["pending"]["BAT1"]["guess"], "unsure")
        t = registry.same(self.s, "BAT1")
        self.assertEqual(t["pack"], "B")

    def test_reassign_moves_history(self):
        registry.assign(self.s, "BAT0", "A", new=True)
        registry.assign(self.s, "BAT1", "B", new=True)
        registry.note_absent(self.s, "BAT1", 10.0)
        t = registry.reassign(self.s, 2, "A")           # closed tenure 2 now belongs to A
        self.assertEqual(t["pack"], "A")
        # (tenure 1 is open in BAT0; B is free, so this should succeed:)
        t1 = registry.reassign(self.s, 1, "B")
        self.assertEqual(t1["pack"], "B")
        registry.observe(self.s, "BAT1", bat(), sample(20), 10.0)
        registry.assign(self.s, "BAT1", "Z", new=True)
        with self.assertRaises(registry.RegistryError):
            registry.reassign(self.s, 1, "Z")           # Z is open in BAT1, so tenure 1 (open in BAT0) cannot take it
        with self.assertRaises(registry.RegistryError):
            registry.reassign(self.s, 99, "A")

    def test_reassign_of_open_tenure_clears_that_slots_pending(self):
        registry.assign(self.s, "BAT0", "A", new=True)
        # Trip a discontinuity: closes tenure 1 (still "A"), opens tenure 3
        # (unidentified) in BAT0, and leaves BAT0 pending.
        registry.observe(self.s, "BAT0", bat(charge=4500000, capacity=98), sample(120), 120.0)
        tid = registry.open_tenure(self.s, "BAT0")["id"]
        self.assertIn("BAT0", self.s["pending"])
        registry.reassign(self.s, tid, "A")
        self.assertNotIn("BAT0", self.s["pending"])

    def test_retire_and_rename(self):
        registry.assign(self.s, "BAT0", "A", new=True)
        with self.assertRaises(registry.RegistryError):
            registry.retire_pack(self.s, "A")           # in a slot
        registry.note_absent(self.s, "BAT0", 5.0)
        registry.retire_pack(self.s, "A")
        self.assertTrue(self.s["packs"]["A"]["retired"])
        registry.observe(self.s, "BAT0", bat(), sample(6), 1.0)
        with self.assertRaises(registry.RegistryError):
            registry.assign(self.s, "BAT0", "A")        # retired
        registry.unretire_pack(self.s, "A")
        registry.rename_pack(self.s, "A", "Alpha")
        self.assertIn("Alpha", self.s["packs"]); self.assertNotIn("A", self.s["packs"])
        self.assertEqual(registry.tenure_by_id(self.s, 1)["pack"], "Alpha")
        self.assertEqual(registry.packs_in_slots(self.s), {})

    def test_rename_rewrites_pending_previous_pack(self):
        registry.assign(self.s, "BAT1", "B", new=True)
        registry.note_absent(self.s, "BAT1", 100.0)
        # A big jump keeps BAT1 pending (guess "unsure") with previous_pack B.
        registry.observe(self.s, "BAT1", bat(charge=4600000, capacity=100), sample(7200), 7100.0)
        self.assertEqual(self.s["pending"]["BAT1"]["previous_pack"], "B")
        registry.rename_pack(self.s, "B", "Beta")
        self.assertEqual(self.s["pending"]["BAT1"]["previous_pack"], "Beta")


class Totals(unittest.TestCase):
    def setUp(self):
        self.s = st.new_state()
        registry.observe(self.s, "BAT0", bat(), sample(0), None)
        registry.assign(self.s, "BAT0", "A", new=True)
        registry.open_tenure(self.s, "BAT0")["discharge_uah"] = 1.0 * DESIGN
        registry.note_absent(self.s, "BAT0", 100.0)              # A to the bench at 50%
        registry.observe(self.s, "BAT0", bat(), sample(200), 100.0)
        registry.assign(self.s, "BAT0", "C", new=True)
        registry.open_tenure(self.s, "BAT0")["discharge_uah"] = 0.2 * DESIGN
        registry.observe(self.s, "BAT1", bat(), sample(200), None)
        registry.assign(self.s, "BAT1", "B", new=True)
        registry.open_tenure(self.s, "BAT1")["discharge_uah"] = 1.4 * DESIGN

    def test_totals_sum_tenures(self):
        registry.observe(self.s, "BAT0", bat(), sample(300), 100.0)
        registry.note_absent(self.s, "BAT0", 400.0)
        registry.observe(self.s, "BAT0", bat(), sample(500), 100.0)
        registry.assign(self.s, "BAT0", "A")                       # A back in; second tenure
        registry.open_tenure(self.s, "BAT0")["discharge_uah"] = 0.5 * DESIGN
        tot = registry.pack_totals(self.s, "A", now=600.0, bench_temp_c=25.0)
        self.assertAlmostEqual(tot["efc"], 1.5)
        self.assertEqual(tot["tenures"], 2)
        self.assertEqual(tot["in_slot"], "BAT0")
        self.assertEqual(tot["bench_calendar"], 0.0)

    def test_bench_estimate_accrues_while_out(self):
        tot = registry.pack_totals(self.s, "A", now=100.0 + 10 * 3600, bench_temp_c=25.0)
        self.assertIsNone(tot["in_slot"])
        self.assertAlmostEqual(tot["bench_hours"], 10.0)
        self.assertAlmostEqual(tot["bench_calendar"], 10.0 * wear.calendar_stress(50, 25.0))
        hot = registry.pack_totals(self.s, "A", now=100.0 + 10 * 3600, bench_temp_c=35.0)
        self.assertGreater(hot["bench_calendar"], tot["bench_calendar"])

    def test_bench_calendar_carried_into_new_tenure_on_reassign(self):
        # A has been on the bench since ts=100.0 at 50% (set up above). Move
        # C out of BAT0, reinsert A there ten hours later, and confirm the
        # bench-aging estimate lands on the NEW open tenure rather than
        # vanishing when removed_at_soc/removed_ts get cleared.
        registry.note_absent(self.s, "BAT0", 300.0)                 # C leaves
        now = 100.0 + 10 * 3600
        registry.observe(self.s, "BAT0", bat(), sample(now), None)  # opens a fresh, unidentified tenure
        registry.assign(self.s, "BAT0", "A", now=now, bench_temp_c=25.0)
        new_t = registry.open_tenure(self.s, "BAT0")
        expected_bench = 10.0 * wear.calendar_stress(50, 25.0)
        self.assertAlmostEqual(new_t["calendar_score"], expected_bench, places=4)
        tot = registry.pack_totals(self.s, "A", now=now, bench_temp_c=25.0)
        # A's pre-removal tenure had calendar_score 0.0; total is just the
        # carried bench amount, and it is not double-counted now A is in a
        # slot again.
        self.assertAlmostEqual(tot["calendar_score"], expected_bench, places=4)
        self.assertEqual(tot["bench_calendar"], 0.0)

    def test_pack_totals_with_zero_tenures(self):
        self.s["packs"]["Z"] = {"label": "Z", "first_seen": "x", "retired": False,
                                "notes": "", "removed_at_soc": None, "removed_ts": None}
        tot = registry.pack_totals(self.s, "Z", now=0.0, bench_temp_c=25.0)
        self.assertEqual(tot["efc"], 0.0)
        self.assertEqual(tot["tenures"], 0)

    def test_efc_for_slot_uses_pack_total(self):
        self.assertAlmostEqual(registry.efc_for_slot(self.s, "BAT1"), 1.4)
        registry.observe(self.s, "BAT1", bat(charge=4500000), sample(300), 100.0)   # trips → unidentified
        self.assertAlmostEqual(registry.efc_for_slot(self.s, "BAT1"), 0.0)          # tenure-only

    def test_rotation_hint_names_least_worn_bench_pack(self):
        hint = registry.rotation_hint(self.s, deadband=0.5, now=200.0, bench_temp_c=25.0)
        # inserted: C 0.2, B 1.4 (max); bench: A 1.0 → 1.4 - 1.0 = 0.4 < 0.5 → no hint
        self.assertIsNone(hint)
        registry.open_tenure(self.s, "BAT1")["discharge_uah"] = 2.0 * DESIGN
        hint = registry.rotation_hint(self.s, deadband=0.5, now=200.0, bench_temp_c=25.0)
        self.assertEqual(hint, {"swap_in": "A", "behind_by_efc": 1.0, "replace": "B"})

    def test_all_packs_sorted_and_flags(self):
        registry.note_absent(self.s, "BAT1", 300.0)
        registry.retire_pack(self.s, "B")
        rows = registry.all_packs(self.s, now=400.0, bench_temp_c=25.0)
        self.assertEqual([r["name"] for r in rows], ["C", "A", "B"])
        self.assertTrue(rows[2]["retired"])
        self.assertEqual(rows[0]["in_slot"], "BAT0")


if __name__ == "__main__":
    unittest.main()

import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
from dbb import metrics


def view():
    """A trimmed render.state_json() result with the fields the exporter reads."""
    return {
        "ts": "2026-09-13T20:00:00+00:00", "version": "0.3.1", "ac_online": 1,
        "sessions": 5, "drain_first": {"BAT0": 0, "BAT1": 5},
        "policy": {"ts": "x", "profile": "daily", "roles": {"BAT0": "neutral", "BAT1": "protect"}, "mode": "balancing"},
        "field_mode": False,
        "bats": {
            "BAT0": {"present": True, "capacity": 79, "status": "Discharging", "temp_c": 31.5,
                     "mode": "Custom", "start": 50, "stop": 80, "efc": 0.31, "calendar_score": 39.6,
                     "tenure_efc": 0.01, "tenure_calendar_score": 1.2, "mean_soc": 78.5, "pct_ge90": 0.0,
                     "discharged_wh": 0.5, "power_w": -12.34, "voltage_v": 11.4, "health_pct": 98.5,
                     "in_slot_hours": 7.2, "pack": "B", "tenure_id": 3, "pending": None},
            "BAT1": {"present": False},
        },
        "divergence_efc": None, "divergence_calendar": None,
        "profile": {"name": "daily", "label": "Daily", "type": "balancing", "description": "", "previous": "daily"},
        "profiles": [], "revert": None,
        "firmware": {"BAT0": {"requested": [50, 80], "observed": [50, 80], "ts": "x", "error": None},
                     "BAT1": {"requested": [50, 80], "observed": None, "ts": "x", "error": "boom"}},
        "config_error": None, "events": [],
        "packs": [{"name": "A", "efc": 0.65, "discharge_uah": 1.0, "calendar_score": 74.9, "bench_calendar": 2.0,
                   "bench_hours": 3.0, "in_slot": None, "tenures": 2, "retired": False, "removed_at_soc": 35},
                  {"name": "B", "efc": 0.31, "discharge_uah": 1.0, "calendar_score": 39.6, "bench_calendar": 0.0,
                   "bench_hours": 0.0, "in_slot": "BAT0", "tenures": 2, "retired": False, "removed_at_soc": None}],
        "pending": {"BAT1": {"guess": "unsure"}},
        "rotation": None,
        "recommendation": {"why": "", "roles": {}, "bands": {"BAT0": [50, 80], "BAT1": [50, 80]}},
    }


class Render(unittest.TestCase):
    def setUp(self):
        self.text = metrics.render_prometheus(view())
        self.lines = [l for l in self.text.splitlines() if l and not l.startswith("#")]

    def line(self, prefix):
        hits = [l for l in self.lines if l.startswith(prefix)]
        self.assertEqual(len(hits), 1, (prefix, hits))
        return hits[0]

    def test_pack_wear_series(self):
        self.assertEqual(self.line('dbb_pack_efc{pack="A"}'), 'dbb_pack_efc{pack="A"} 0.65')
        self.assertEqual(self.line('dbb_pack_calendar_score{pack="B"}'), 'dbb_pack_calendar_score{pack="B"} 39.6')
        self.assertEqual(self.line('dbb_pack_in_slot{pack="B",slot="BAT0"}'), 'dbb_pack_in_slot{pack="B",slot="BAT0"} 1')
        self.assertEqual(self.line('dbb_pack_bench_hours{pack="A"}'), 'dbb_pack_bench_hours{pack="A"} 3.0')
        self.assertEqual(self.line('dbb_pack_retired{pack="A"}'), 'dbb_pack_retired{pack="A"} 0')

    def test_slot_live_series(self):
        self.assertEqual(self.line('dbb_slot_present{slot="BAT0"}'), 'dbb_slot_present{slot="BAT0"} 1')
        self.assertEqual(self.line('dbb_slot_present{slot="BAT1"}'), 'dbb_slot_present{slot="BAT1"} 0')
        self.assertEqual(self.line('dbb_slot_capacity_percent{slot="BAT0"}'), 'dbb_slot_capacity_percent{slot="BAT0"} 79')
        self.assertEqual(self.line('dbb_slot_power_watts{slot="BAT0"}'), 'dbb_slot_power_watts{slot="BAT0"} -12.34')
        self.assertEqual(self.line('dbb_slot_temperature_celsius{slot="BAT0"}'), 'dbb_slot_temperature_celsius{slot="BAT0"} 31.5')
        self.assertEqual(self.line('dbb_slot_health_percent{slot="BAT0"}'), 'dbb_slot_health_percent{slot="BAT0"} 98.5')
        self.assertEqual(self.line('dbb_slot_status{slot="BAT0",status="Discharging"}'),
                         'dbb_slot_status{slot="BAT0",status="Discharging"} 1')
        # an absent slot has no capacity/power/... series at all
        self.assertFalse(any('capacity_percent{slot="BAT1"' in l for l in self.lines))

    def test_ceiling_from_firmware_readback(self):
        self.assertEqual(self.line('dbb_slot_ceiling_stop_percent{slot="BAT0"}'), 'dbb_slot_ceiling_stop_percent{slot="BAT0"} 80')
        self.assertEqual(self.line('dbb_slot_ceiling_start_percent{slot="BAT0"}'), 'dbb_slot_ceiling_start_percent{slot="BAT0"} 50')
        # BAT1's read-back failed: no observed ceiling, but the error is counted
        self.assertFalse(any('ceiling_stop_percent{slot="BAT1"' in l for l in self.lines))
        self.assertEqual(self.line('dbb_slot_firmware_error{slot="BAT1"}'), 'dbb_slot_firmware_error{slot="BAT1"} 1')
        self.assertEqual(self.line('dbb_slot_firmware_error{slot="BAT0"}'), 'dbb_slot_firmware_error{slot="BAT0"} 0')

    def test_policy_profile_and_totals(self):
        self.assertEqual(self.line('dbb_profile_info{'), 'dbb_profile_info{profile="daily",type="balancing"} 1')
        self.assertEqual(self.line('dbb_slot_role{slot="BAT1",role="protect"}'), 'dbb_slot_role{slot="BAT1",role="protect"} 1')
        self.assertEqual(self.line('dbb_ac_online'), 'dbb_ac_online 1')
        self.assertEqual(self.line('dbb_field_mode'), 'dbb_field_mode 0')
        self.assertEqual(self.line('dbb_unplug_sessions_total'), 'dbb_unplug_sessions_total 5')
        self.assertEqual(self.line('dbb_drain_first_total{slot="BAT1"}'), 'dbb_drain_first_total{slot="BAT1"} 5')
        self.assertEqual(self.line('dbb_pending_questions'), 'dbb_pending_questions 1')
        self.assertEqual(self.line('dbb_config_error'), 'dbb_config_error 0')
        self.assertEqual(self.line('dbb_info{'), 'dbb_info{version="0.3.1"} 1')

    def test_divergence_absent_when_none(self):
        self.assertFalse(any(l.startswith("dbb_divergence") for l in self.lines))
        v = view(); v["divergence_efc"] = 0.34; v["divergence_calendar"] = 35.3
        t = metrics.render_prometheus(v)
        self.assertIn("dbb_divergence_efc 0.34\n", t)
        self.assertIn("dbb_divergence_calendar_score 35.3\n", t)

    def test_help_and_type_lines_once_per_metric(self):
        heads = [l for l in self.text.splitlines() if l.startswith("# TYPE dbb_pack_efc ")]
        self.assertEqual(heads, ["# TYPE dbb_pack_efc gauge"])
        self.assertIn("# HELP dbb_pack_efc ", self.text)
        self.assertTrue(self.text.endswith("\n"))

    def test_label_values_are_escaped(self):
        v = view(); v["bats"]["BAT0"]["status"] = 'Not "charging"\\x\n'
        t = metrics.render_prometheus(v)
        self.assertIn('status="Not \\"charging\\"\\\\x\\n"', t)

    def test_nan_and_none_never_emitted(self):
        v = view(); v["bats"]["BAT0"]["power_w"] = None; v["bats"]["BAT0"]["temp_c"] = float("nan")
        t = metrics.render_prometheus(v)
        self.assertNotIn("power_watts", t)
        self.assertNotIn(" nan", t.lower())


class OvernightSeries(unittest.TestCase):
    def test_phase_one_hot_and_timestamps(self):
        j = view()
        j["overnight"] = {"active": True, "mode": "topoff", "phase": "holding", "topoff_start_ts": 1789400000,
                          "leave_ts": 1789405400}
        j["revert"] = {"warning": True}
        text = metrics.render_prometheus(j)
        self.assertIn('dbb_overnight_phase{phase="holding"} 1', text)
        self.assertIn('dbb_overnight_phase{phase="off"} 0', text)
        self.assertIn("dbb_topoff_start_timestamp_seconds 1789400000", text)
        self.assertIn("dbb_leave_timestamp_seconds 1789405400", text)
        self.assertIn("dbb_revert_warning 1", text)

    def test_absent_overnight_emits_off(self):
        text = metrics.render_prometheus(view())
        self.assertIn('dbb_overnight_phase{phase="off"} 1', text)
        self.assertNotIn("dbb_topoff_start_timestamp_seconds", text)
        self.assertIn("dbb_revert_warning 0", text)


if __name__ == "__main__":
    unittest.main()

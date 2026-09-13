"""dbb.state unit tests that don't need the full CLI harness."""
import datetime
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))


class SampleLogPath(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DBB_STATE_DIR"] = self.tmp.name
        for m in list(sys.modules):
            if m.startswith("dbb"):
                del sys.modules[m]

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("DBB_STATE_DIR", None)
        for m in list(sys.modules):
            if m.startswith("dbb"):
                del sys.modules[m]

    def test_sample_log_path_for_a_2027_timestamp(self):
        from dbb import state as st
        ts = datetime.datetime(2027, 6, 1, tzinfo=datetime.timezone.utc).timestamp()
        self.assertEqual(st.sample_log_path(ts).name, "samples-2027.csv")

    def _touch(self, *names):
        for n in names:
            with open(os.path.join(self.tmp.name, n), "w") as fh:
                fh.write("x\n")

    def test_prune_keeps_the_newest_n_years_and_nothing_else_is_touched(self):
        from dbb import state as st
        self._touch("samples-2022.csv", "samples-2023.csv", "samples-2024.csv",
                    "samples-2025.csv", "samples-2026.csv", "samples.csv",
                    "samples-2019.csv.bak", "state.json", "metrics.prom")
        now = datetime.datetime(2026, 9, 13, tzinfo=datetime.timezone.utc).timestamp()
        removed = st.prune_sample_logs(now, keep_years=3)
        self.assertEqual(removed, ["samples-2022.csv", "samples-2023.csv"])
        left = sorted(os.listdir(self.tmp.name))
        self.assertEqual(left, ["metrics.prom", "samples-2019.csv.bak", "samples-2024.csv",
                                "samples-2025.csv", "samples-2026.csv", "samples.csv", "state.json"])

    def test_prune_with_nothing_old_removes_nothing(self):
        from dbb import state as st
        self._touch("samples-2026.csv")
        now = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc).timestamp()
        self.assertEqual(st.prune_sample_logs(now, keep_years=1), [])
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, "samples-2026.csv")))

    def test_prune_of_a_missing_state_dir_is_a_no_op(self):
        from dbb import state as st
        os.environ["DBB_STATE_DIR"] = os.path.join(self.tmp.name, "nope")
        for m in list(sys.modules):
            if m.startswith("dbb"):
                del sys.modules[m]
        from dbb import state as st2
        self.assertEqual(st2.prune_sample_logs(0.0, keep_years=3), [])


class EventIds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DBB_STATE_DIR"] = self.tmp.name
        for m in list(sys.modules):
            if m.startswith("dbb"):
                del sys.modules[m]

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("DBB_STATE_DIR", None)
        for m in list(sys.modules):
            if m.startswith("dbb"):
                del sys.modules[m]

    def test_ids_increase_and_survive_the_trim(self):
        from dbb import state as st
        s = st.new_state()
        for i in range(503):
            st.add_event(s, "test", str(i))
        ids = [e["id"] for e in s["events"]]
        self.assertEqual(len(ids), 500)
        self.assertEqual(ids[0], 4)          # the first three were trimmed
        self.assertEqual(ids[-1], 503)
        self.assertEqual(s["next_event_id"], 504)

    def test_old_events_are_numbered_once_on_load(self):
        from dbb import state as st
        s = st.new_state()
        s["events"] = [{"ts": "t1", "kind": "pack", "detail": "a"},
                       {"ts": "t2", "kind": "profile", "detail": "b"}]
        s.pop("next_event_id")
        st.save_state(s)
        loaded = st.load_state()
        self.assertEqual([e["id"] for e in loaded["events"]], [1, 2])
        self.assertEqual(loaded["next_event_id"], 3)
        st.add_event(loaded, "x", "c")
        self.assertEqual(loaded["events"][-1]["id"], 3)


if __name__ == "__main__":
    unittest.main()

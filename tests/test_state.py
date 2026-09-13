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

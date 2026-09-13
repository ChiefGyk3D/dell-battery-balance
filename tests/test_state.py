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


if __name__ == "__main__":
    unittest.main()

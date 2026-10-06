"""The pack's own cycle count read through Dell's DDV WMI interface: the root
helper (against a fake sysfs and a fake acpi_call), the sampler that reads
its file, and the tracker's handling of the switch from the kernel's
placeholder."""
import os, stat, subprocess, sys, tempfile, time, unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
from dbb import registry, state as st, sysfs, wear
from tests.fakesys import FakeSys

HELPER = os.path.join(os.path.dirname(__file__), os.pardir, "libexec", "dell-battery-balance-cycles")
GUID = "8A42EA14-4F2A-FD45-6422-0087F7A7E608"
DESIGN = 4646000
# Placeholders shaped like a Dell ePPID; never a real pack's.
EPPID_X = "CN0XXXXXSLW0000E00AAA01"
EPPID_Y = "CN0XXXXXSLW0000E00ABA01"

# A fake acpi_call: logs each request, answers from a table keyed by
# "<method id> <index>", and prints acpi_call's error text for anything else.
FAKE_CALL = """#!/bin/sh
printf "%s\\n" "$*" >> "{log}"
set -- $*
key="$3 $4"
while read -r k1 k2 v; do
    [ "$k1 $k2" = "$key" ] && {{ printf '%s' "$v"; exit 0; }}
done < "{table}"
printf 'Error: AE_NOT_FOUND'
"""


class Helper(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.sys = d / "sys"
        plat = self.sys / "devices/platform/PNP0C14:07"
        dev = plat / "wmi_bus/wmi_bus-PNP0C14:07" / f"{GUID}-25"
        dev.mkdir(parents=True)
        (dev / "object_id").write_text("DV\n")
        (plat / "firmware_node").mkdir()
        (plat / "firmware_node/path").write_text("\\_SB_.AMWV\n")
        (self.sys / "bus/wmi/devices").mkdir(parents=True)
        (self.sys / "bus/wmi/devices" / f"{GUID}-25").symlink_to(dev)
        for b, e in (("BAT0", EPPID_X), ("BAT1", EPPID_Y)):
            (self.sys / "class/power_supply" / b).mkdir(parents=True)
            (self.sys / "class/power_supply" / b / "eppid").write_text(e + "\n")
        self.log, self.table, self.out = d / "calls.log", d / "table", d / "run"
        call = d / "acpi_call"
        call.write_text(FAKE_CALL.format(log=self.log, table=self.table))
        call.chmod(0o755)
        self.answers({"0x0D 0x01": f'"{EPPID_X}"', "0x0C 0x01": "0x2",
                      "0x0D 0x02": f'"{EPPID_Y}"', "0x0C 0x02": "0x3"})
        self.env = dict(os.environ, DBB_SYSFS_ROOT=str(self.sys), DBB_CYCLES_DIR=str(self.out),
                        DBB_ACPI_CALL_CMD=str(call), DBB_ACPI_CALL=str(d / "no-proc-call"))

    def tearDown(self):
        self.tmp.cleanup()

    def answers(self, table):
        # The helper formats the index as b0100 00; key the fake on it.
        rows = []
        for k, v in table.items():
            m, i = k.split()
            rows.append(f"{m} b{int(i, 16):02x}0000 {v}")
        self.table.write_text("\n".join(rows) + "\n")

    def run_helper(self):
        r = subprocess.run(["sh", HELPER], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        p = self.out / "cycles"
        return p.read_text() if p.exists() else None

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_reads_both_packs(self):
        self.assertEqual(self.run_helper(), "BAT0 2\nBAT1 3\n")
        mode = stat.S_IMODE((self.out / "cycles").stat().st_mode)
        self.assertEqual(mode, 0o644)

    def test_calls_only_the_ddv_method_for_eppid_and_cycles(self):
        self.run_helper()
        calls = self.calls()
        self.assertEqual(len(calls), 4, calls)
        for c in calls:
            method, inst, mid, buf = c.split()
            self.assertEqual((method, inst), ("\\_SB_.AMWV.WMDV", "0"))
            self.assertIn(mid, ("0x0D", "0x0C"))
            self.assertIn(buf, ("b010000", "b020000"))

    def test_an_index_whose_eppid_is_another_packs_is_skipped(self):
        self.answers({"0x0D 0x01": f'"{EPPID_Y}"', "0x0C 0x01": "0x2",
                      "0x0D 0x02": f'"{EPPID_Y}"', "0x0C 0x02": "0x3"})
        self.assertEqual(self.run_helper(), "BAT1 3\n")

    def test_errors_and_non_numbers_are_dropped(self):
        self.answers({"0x0D 0x01": f'"{EPPID_X}"', "0x0D 0x02": f'"{EPPID_Y}"',
                      "0x0C 0x02": '"7"'})
        self.assertEqual(self.run_helper(), "")

    def test_a_pack_without_eppid_is_never_read(self):
        (self.sys / "class/power_supply/BAT1/eppid").unlink()
        self.assertEqual(self.run_helper(), "BAT0 2\n")
        self.assertFalse(any("b020000" in c for c in self.calls()))

    def test_off_without_acpi_call(self):
        self.run_helper()
        del self.env["DBB_ACPI_CALL_CMD"]
        self.assertIsNone(self.run_helper())          # stale output removed
        self.assertEqual(len(self.calls()), 4)

    def test_unexpected_acpi_path_is_refused(self):
        (self.sys / "devices/platform/PNP0C14:07/firmware_node/path").write_text("\\_SB_.AM WV; x\n")
        self.assertIsNone(self.run_helper())
        self.assertEqual(self.calls(), [])

    def test_no_ddv_device_does_nothing(self):
        (self.sys / "bus/wmi/devices" / f"{GUID}-25").unlink()
        self.assertIsNone(self.run_helper())
        self.assertEqual(self.calls(), [])


class Sampler(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fs = FakeSys(self.tmp.name)
        for b in ("BAT0", "BAT1"):
            self.fs.bat(b)
            self.fs._w(f"class/power_supply/{b}/cycle_count", 0)
        self.file = Path(self.tmp.name) / "cycles"
        ps = Path(self.tmp.name) / "class/power_supply"
        self.patches = [mock.patch.object(sysfs, "PS", ps), mock.patch.object(sysfs, "DDV_CYCLES", self.file)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def bats(self):
        return sysfs.sample_all()["bats"]

    def test_without_the_helper_the_kernel_value_is_used(self):
        b = self.bats()
        self.assertEqual((b["BAT0"]["cycle_count"], b["BAT0"]["cycle_source"]), (0, "sysfs"))

    def test_the_ddv_reading_replaces_the_placeholder(self):
        self.file.write_text("BAT0 2\nBAT1 3\n")
        b = self.bats()
        self.assertEqual((b["BAT0"]["cycle_count"], b["BAT0"]["cycle_source"]), (2, "ddv"))
        self.assertEqual((b["BAT1"]["cycle_count"], b["BAT1"]["cycle_source"]), (3, "ddv"))

    def test_a_stale_file_is_ignored(self):
        self.file.write_text("BAT0 2\n")
        old = time.time() - sysfs.DDV_CYCLES_MAX_AGE_S - 60
        os.utime(self.file, (old, old))
        self.assertEqual(self.bats()["BAT0"]["cycle_source"], "sysfs")

    def test_garbage_lines_are_ignored(self):
        self.file.write_text("BAT0 -1\nBAT9 4\nBAT1 x\nBAT1 3 extra\n")
        self.assertEqual(sysfs.ddv_cycles(), {})

    def test_a_fake_sys_never_reads_the_real_run_file(self):
        # Found 2026-10-05: with the helper enabled on the laptop, the CLI
        # tests read the real packs' counts through /run.
        with mock.patch.object(sysfs, "DDV_CYCLES", None), \
                mock.patch.dict(os.environ, {"DBB_SYSFS_ROOT": self.tmp.name}):
            os.environ.pop("DBB_CYCLES_FILE", None)
            self.assertTrue(str(sysfs._ddv_cycles_path()).startswith(self.tmp.name))

    def test_cycle_source_never_reaches_the_sample_log(self):
        self.assertNotIn("cycle_source", st.CSV_FIELDS)


def bat(cycles=0, src="sysfs", eppid=EPPID_X):
    return dict(status="Discharging", capacity=50, charge_now_uah=2300000,
                charge_full_uah=DESIGN, charge_full_design_uah=DESIGN,
                voltage_now_uv=11400000, voltage_min_design_uv=11400000,
                current_now_ua=0, temp_dc=313, present=1, eppid=eppid, serial="101",
                cycle_count=cycles, cycle_source=src)


class Tracker(unittest.TestCase):
    def setUp(self):
        self.s = st.new_state()
        self.ts = 0
        self.tick(bat(), bat(eppid=EPPID_Y))
        registry.assign(self.s, "BAT0", "Alpha", new=True)
        registry.assign(self.s, "BAT1", "Bravo", new=True)
        self.tick(bat(), bat(eppid=EPPID_Y))         # placeholder 0 recorded

    def tick(self, b0, b1):
        wear.integrate(self.s, dict(ts=float(self.ts), boot_id="b", ac_online=1,
                                    bats={"BAT0": b0, "BAT1": b1}))
        self.ts += 120

    def fw(self, name):
        return self.s["packs"][name]["fw_cycles"]

    def events(self, kind):
        return [e["detail"] for e in self.s["events"] if e["kind"] == kind]

    def test_switching_to_ddv_restarts_the_record_without_a_false_change(self):
        for _ in range(2):
            self.tick(bat(2, "ddv"), bat(3, "ddv", EPPID_Y))
        fw = self.fw("Alpha")
        self.assertEqual((fw["value"], fw["first_value"], fw["source"]), (2, 2, "ddv"))
        self.assertIsNone(fw["changed_ts"])
        ev = self.events("cycles")
        self.assertEqual(len(ev), 2, ev)
        self.assertIn("Alpha: firmware cycle count 2, read from the pack through Dell WMI", ev[0])
        self.assertFalse(self.events("warning"))

    def test_a_stale_ddv_reading_never_falls_back_to_the_placeholder(self):
        for _ in range(2):
            self.tick(bat(2, "ddv"), bat(3, "ddv", EPPID_Y))
        for _ in range(3):
            self.tick(bat(0), bat(0, eppid=EPPID_Y))
        self.assertEqual(self.fw("Alpha")["value"], 2)
        self.assertFalse(self.events("warning"))

    def test_a_ddv_increase_is_a_cycles_event(self):
        for c in (2, 2, 3, 3):
            self.tick(bat(c, "ddv"), bat(3, "ddv", EPPID_Y))
        self.assertEqual(self.fw("Alpha")["value"], 3)
        self.assertTrue(any("Alpha: firmware cycle count 2 -> 3" in e for e in self.events("cycles")))

    def test_one_sample_of_the_new_source_is_not_enough(self):
        self.tick(bat(2, "ddv"), bat(3, "ddv", EPPID_Y))
        self.assertEqual(self.fw("Alpha").get("source", "sysfs"), "sysfs")

    def test_a_new_pack_reading_zero_still_needs_two_ddv_samples(self):
        # The placeholder and a brand-new pack's real count are both 0; the
        # value agreeing is not enough, the source must agree too.
        self.tick(bat(0, "ddv"), bat(0, "ddv", EPPID_Y))
        self.assertEqual(self.fw("Alpha").get("source", "sysfs"), "sysfs")
        self.tick(bat(0, "ddv"), bat(0, "ddv", EPPID_Y))
        self.assertEqual(self.fw("Alpha")["source"], "ddv")


if __name__ == "__main__":
    unittest.main()

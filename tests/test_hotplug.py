"""The hotplug trigger: udev starts an immediate check when a pack goes in or
out. These guard the wiring, which no other test exercises: the rule, the
unit it starts, and the installer that places both."""
import os, re, unittest

ROOT = os.path.join(os.path.dirname(__file__), os.pardir)


def read(*parts):
    with open(os.path.join(ROOT, *parts)) as fh:
        return fh.read()


def directives(unit):
    return [l.strip() for l in unit.splitlines() if "=" in l and not l.lstrip().startswith("#")]


class Hotplug(unittest.TestCase):
    def setUp(self):
        self.rules = read("udev", "90-dell-battery-balance.rules")
        self.tick = read("systemd", "dell-battery-balance.service")
        self.check = read("systemd", "dell-battery-balance-check.service")

    def rule(self):
        lines = [l for l in self.rules.splitlines() if "dell-battery-balance-check.service" in l]
        self.assertEqual(len(lines), 1, lines)
        return lines[0]

    def test_rule_fires_on_both_slots_for_insert_and_removal(self):
        r = self.rule()
        self.assertIn('SUBSYSTEM=="power_supply"', r)
        self.assertIn('KERNEL=="BAT[01]"', r)
        # Measured: removal is "remove", insertion a "change" with PRESENT=1.
        self.assertIn('ACTION=="add|remove|change"', r)
        # Never blocks udev's event queue waiting for the 15 s check.
        self.assertIn("systemctl --no-block start", r)

    def test_rule_starts_a_unit_that_exists(self):
        unit = re.search(r"start (\S+\.service)", self.rule()).group(1)
        self.assertTrue(os.path.exists(os.path.join(ROOT, "systemd", unit)), unit)

    def test_check_unit_is_as_locked_down_as_the_tick(self):
        skip = ("Description=", "ExecStart=", "ExecStartPost=")
        want = [d for d in directives(self.tick) if not d.startswith(skip)]
        have = [d for d in directives(self.check) if not d.startswith(skip)]
        self.assertEqual(have, want)

    def test_check_unit_runs_check_not_tick(self):
        self.assertIn("ExecStart=/usr/local/bin/dell-battery-balance check\n", self.check)
        self.assertIn("Type=oneshot", self.check)    # a start while running joins the job

    def test_installed_and_uninstalled(self):
        self.assertIn("systemd/dell-battery-balance-check.service", read("install.sh"))
        self.assertIn("$SVC-check.service", read("uninstall.sh"))


if __name__ == "__main__":
    unittest.main()

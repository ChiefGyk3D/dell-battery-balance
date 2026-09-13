"""Builds a fake /sys tree so tests never touch real hardware."""
from pathlib import Path


class FakeSys:
    def __init__(self, tmpdir):
        self.root = Path(tmpdir)
        (self.root / "class/power_supply/AC").mkdir(parents=True)
        (self.root / "class/firmware-attributes/dell-wmi-sysman/attributes").mkdir(parents=True)
        (self.root / "class/firmware-attributes/dell-wmi-sysman/authentication/Admin").mkdir(parents=True)
        self.ac(1)

    def _w(self, rel, value):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"{value}\n")

    def ac(self, online):
        self._w("class/power_supply/AC/online", online)

    def bat(self, slot, present=1, status="Discharging", capacity=50,
            charge_now=2300000, charge_full=4600000, charge_full_design=4600000,
            voltage_now=11400000, voltage_min_design=11400000, current_now=0,
            temp=313, charge_types=None, start=None, stop=None):
        base = f"class/power_supply/{slot}/"
        for k, v in dict(present=present, status=status, capacity=capacity,
                         charge_now=charge_now, charge_full=charge_full,
                         charge_full_design=charge_full_design,
                         voltage_now=voltage_now, voltage_min_design=voltage_min_design,
                         current_now=current_now, temp=temp).items():
            self._w(base + k, v)
        if charge_types is not None:
            self._w(base + "charge_types", charge_types)
        if start is not None:
            self._w(base + "charge_control_start_threshold", start)
        if stop is not None:
            self._w(base + "charge_control_end_threshold", stop)

    def sysman_attr(self, name, current, possible="Adaptive;Standard;Express;PrimAcUse;Custom;"):
        base = f"class/firmware-attributes/dell-wmi-sysman/attributes/{name}/"
        self._w(base + "current_value", current)
        self._w(base + "possible_values", possible)

    def read(self, rel):
        return (self.root / rel).read_text().strip()

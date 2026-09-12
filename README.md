# dell-battery-balance

Wear tracking and charge-ceiling balancing for the two battery packs in a
Dell Latitude Rugged (primary + slice).

## The problem

The embedded controller charges and discharges the two packs **sequentially,
not in parallel**. One pack ends up doing nearly all the work while the other
sits pinned near 100%. That produces two *different* kinds of wear:

| Pack | Wear mechanism |
|---|---|
| The one the EC drains first | **Cycle wear** — repeated deep discharges |
| The one left full | **Calendar wear** — Li-ion ages fastest held at high SoC, and faster still when warm |

So "one battery drains faster" is not a fault. It is the design. Evening out
the wear means stopping the idle pack from floating at 100% *and* stopping the
same pack from taking every cycle.

## Why this tool has to measure rather than read

On this platform **neither pack reports `cycle_count`** — it reads 0 on both,
and `dell_wmi_ddv` does not extend it. There is no wear counter to query. The
tool therefore derives wear itself, by integrating charge flow over time:

- **EFC (equivalent full cycles)** = cumulative charge out ÷ design capacity.
  This is the cycle-wear number. Integrated from `charge_now` deltas in µAh,
  so it needs no voltage estimate and is immune to voltage sag.
- **Calendar score** = hours, weighted by state of charge and temperature.
  A heuristic (SoC term rising to 4× at 100%, Arrhenius doubling per 10 °C),
  used *only* to compare the two packs against each other. Both packs are
  measured identically, so the absolute scale does not matter.

## The lever

`dell-wmi-sysman` exposes **independent** charge configuration per pack:

```
PrimaryBattChargeCfg   + CustomChargeStart / CustomChargeStop            -> BAT0
SliceBattChargeCfg     + SliceBattCustomChargeStart / ...ChargeStop      -> BAT1
```

Firmware limits: start 50–95, stop 55–100.

Lowering a pack's ceiling shifts load away from it under **either** EC
behaviour:

- If the EC drains the fuller pack first, a lower ceiling makes it lose that race.
- If the EC drains in a fixed order, a lower ceiling means it delivers fewer
  Wh before handing over.

Either way, cycles move to the other pack. That is what makes the policy safe
to run before the EC's exact behaviour is known — and the tool measures that
behaviour anyway (`EC reaches for first` in the report).

## Policy

| Role | Charge band | Applied to |
|---|---|---|
| `protect` | 50 / 60 | the pack that is **ahead** on wear |
| `work` | 80 / 90 | the pack that is behind |
| `neutral` | 50 / 80 | both, while they are within the deadband |
| `field` | 90 / 100 | both, for maximum runtime |

Roles only flip once EFC divergence exceeds `--deadband` (default 0.50).
Without that hysteresis the policy would oscillate on every run.

## Install

```sh
sudo ./install.sh
```

That installs the binary, creates `/var/lib/dell-battery-balance`, and starts
sampling every 2 minutes. **The balancing timer is deliberately left off.**
Gather data first — a week of real use — then review and enable:

```sh
dell-battery-balance report
sudo systemctl enable --now dell-battery-balance.timer
```

## Usage

```sh
dell-battery-balance status          # wear summary
dell-battery-balance report          # + firmware config and recommendation
sudo dell-battery-balance balance    # dry run, shows what it would set
sudo dell-battery-balance balance --apply
sudo dell-battery-balance field      # lift both ceilings before a field day
sudo dell-battery-balance restore    # back to the balancing policy
```

If a BIOS admin password is set, pass `--bios-password` or set
`DBB_BIOS_PASSWORD` (an `EnvironmentFile` hook is stubbed in the unit).

## Plasma applet

A Plasma 6 tray widget lives in `plasmoid/`. It sits next to the battery icon
and shows each pack's EFC and calendar score, the measured EC drain order, the
active policy, and buttons for Balance / Field / Restore. Privileged actions
go through `pkexec` against the polkit action in `polkit/`, so no terminal and
no passwordless sudo is needed.

`install.sh` installs both. To do the applet by hand:

```sh
kpackagetool6 --type Plasma/Applet --install plasmoid/package    # or --upgrade
sudo install -Dm644 polkit/com.chiefgyk3d.dellbatterybalance.policy \
    /usr/share/polkit-1/actions/com.chiefgyk3d.dellbatterybalance.policy
```

Then: right-click the panel or system tray, Add Widgets, search for
"Dell Battery Balance". Test it standalone with
`plasmawindowed com.chiefgyk3d.dellbatterybalance`.

Field mode is flagged in two places on purpose — a red dot on the tray icon
and a warning banner in the popup — because it disables the calendar-wear
protection and is otherwise easy to leave on by accident.

## State

Everything durable lives in `/var/lib/dell-battery-balance`:

- `state.json` — cumulative counters, written atomically
- `samples.csv` — raw sample log, so the wear model can be recomputed or
  re-derived later if the heuristics change

## Roadmap

The next version adds usage profiles (daily / field / travel / storage /
custom), a system-wide `config.toml`, a pack registry that tracks wear per
physical pack across swaps and rotations, safe auto-revert out of field mode,
a full Plasma config dialog, and a scoped service account so that nothing
which parses input or writes firmware values runs as root. The design is written up in
[docs/superpowers/specs/2026-09-12-profiles-packs-config-design.md](docs/superpowers/specs/2026-09-12-profiles-packs-config-design.md),
including the measured hardware constraint that drives it: these packs expose
no per-unit identity, so swaps are detected and confirmed rather than
recognised.

## Known limits

- **Pack swaps are not reliably detected.** Both packs report an identical
  serial (`88`) and ePPID, so only a design-capacity change is visible. After
  physically swapping a pack, run `reset --slot BAT0` (or `BAT1`) so its
  counters do not carry over.
- **Only BAT0 exposes `charge_control_*` to Linux sysfs.** BAT1 is reachable
  only via `dell-wmi-sysman`, so generic tools like TLP can never manage it.
  The script uses sysman for both and falls back to `power_supply` for BAT0.
- **Sysman writes may need a reboot** to take effect on some attributes.
  `report` reads back what the firmware actually holds — check it after applying.
- Setting Battery 1 to `Custom` greys out Dell Optimizer's Dynamic Charge
  Policy. That is Windows-only software and irrelevant here.
- Energy that leaves a pack while the machine is off or suspended is still
  counted as discharge. Gaps longer than 15 minutes skip *calendar* accrual
  but still count charge deltas.

## Tests

```sh
python3 tests/test_wear_model.py
```

Simulates three full sequential-discharge cycles and asserts that the pack
doing the draining accumulates more EFC, that the idle pack accumulates more
calendar score, that the drain-order detector credits one event per unplug,
that the deadband holds near-equal packs neutral, and that bands clamp to the
firmware's limits.

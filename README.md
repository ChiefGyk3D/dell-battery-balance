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

## Profiles

| Profile | Type | Bands | Notes |
|---|---|---|---|
| `daily` | balancing | neutral 50/80, protect 50/60, work 80/90 | Docked/desk default. Cannot be deleted. |
| `field` | fixed | all 90/100 | Maximum runtime, wear protection off. Auto-reverts after 72h, or 12h once back on AC. |
| `travel` | balancing | neutral 70/90, protect 60/80, work 80/95 | Reserve without the 100% float. |
| `storage` | fixed | all 50/55 | Long idle, least wear. |

A balancing profile flips a pack between `protect` and `work` once EFC
divergence exceeds `general.deadband_efc` (default 0.5) — without that
hysteresis the policy would oscillate on every tick. A fixed profile applies
the same band to both packs regardless of wear. Profiles, bands, pins and
revert rules all live in `/etc/dell-battery-balance/config.toml`; see
[the design doc, §1](docs/superpowers/specs/2026-09-12-profiles-packs-config-design.md#1-configuration)
for the full schema.

## Install

```sh
sudo ./install.sh
```

This creates the scoped `dell-battery-balance` system account, installs the
package to `/usr/local/lib/dell-battery-balance`, the launcher, the two
privilege wrappers and the grant script to `/usr/local/libexec`, the udev
rule and both polkit actions, and the systemd unit + timer — then enables
`dell-battery-balance.timer` immediately (tick every 2 minutes; ticks sample
unconditionally and apply the resolved profile's bands whenever
`general.auto_balance` is true, which is the default).

```sh
sudo ./uninstall.sh            # leaves /etc and /var/lib in place
sudo ./uninstall.sh --purge    # also removes config and state
```

## Usage

Read-only commands run as any user — state and config are group/world
readable:

```sh
dell-battery-balance status          # wear summary
dell-battery-balance report          # + firmware config and recommendation
dell-battery-balance profile list
dell-battery-balance profile show daily
```

Commands that switch profiles, apply ceilings, or edit configuration run as
the `dell-battery-balance` service account, through one of two polkit-gated
wrappers:

```sh
pkexec --user dell-battery-balance /usr/local/libexec/dbb-control profile set travel
pkexec --user dell-battery-balance /usr/local/libexec/dbb-control field
pkexec --user dell-battery-balance /usr/local/libexec/dbb-control restore
pkexec --user dell-battery-balance /usr/local/libexec/dbb-control balance --apply
pkexec --user dell-battery-balance /usr/local/libexec/dbb-configure config set general.deadband_efc=0.4
pkexec --user dell-battery-balance /usr/local/libexec/dbb-configure profile create trip --from travel
```

`dbb-control` refuses configure-class subcommands — `config set/apply/validate`,
`profile create/edit/delete`, `reset` — with exit 3; `config get` and
everything else above stay control-class. See Privilege model below.

If a BIOS admin password is set, point `general.bios_password_file` at a
file readable only by the service account; the tool never takes it as an
argument or environment variable.

## Privilege model

Root runs exactly one thing after install: a ~15-line grant script,
`/usr/local/libexec/dell-battery-balance-grant`. Everything that parses
config, evaluates policy, or writes a firmware value runs as a scoped system
account, `dell-battery-balance` (`--system`, `nologin`, no other members —
not even the interactive user).

The grant script `chgrp`s and `chmod`s exactly eight sysfs files to the
service group — the six `dell-wmi-sysman` charge-config attributes plus
BAT0's two `charge_control_*` thresholds — and a ninth,
`Admin/current_password`, only when `bios_password_file` is set. It touches
nothing else and is idempotent. Sysfs permissions do not survive a reboot,
so it runs twice over: via `ExecStartPre=+` on every tick (self-healing every
2 minutes) and via `/etc/udev/rules.d/90-dell-battery-balance.rules` when the
`dell-wmi-sysman` or `BAT0` device appears, so it's also correct for the
first applet click after a fresh boot.

Two polkit actions gate the two wrappers used above:

| Action | Wrapper | Used for | Default prompt |
|---|---|---|---|
| `com.chiefgyk3d.dellbatterybalance.control` | `dbb-control` | `config get`, `profile set`, `field`, `restore`, `balance --apply` | the user's own password, kept (`auth_self_keep`) |
| `com.chiefgyk3d.dellbatterybalance.configure` | `dbb-configure` | `config set/apply/validate`, `profile create/edit/delete`, `reset` | admin password, kept (`auth_admin_keep`) |

`control` deliberately still prompts: a profile switch can park both packs
at 100% for days, the exact harm this tool exists to prevent. To loosen it
to no prompt at all, add a rule to `/etc/polkit-1/rules.d/` that resolves
`com.chiefgyk3d.dellbatterybalance.control` to `polkit.Result.YES`. That
cannot be used to sneak a configuration write past the `configure` action —
the tool itself rejects a configure-class subcommand under
`--polkit-class control` with exit 3, regardless of what polkit allowed, and
refuses outright (exit 3, before even parsing the command) if
`--polkit-class` appears more than once — the wrappers pin it once and pass
`--` before the rest of `"$@"`, so argparse's "last occurrence wins" behavior
can never be used to swap `control` for `configure` after the fact.

### Verify

```sh
sudo ./install.sh
id dell-battery-balance
stat -c '%A %U:%G %n' /sys/class/firmware-attributes/dell-wmi-sysman/attributes/SliceBattCustomChargeStop/current_value
systemctl status dell-battery-balance.timer
sudo -u dell-battery-balance dell-battery-balance status
pkexec --user dell-battery-balance /usr/local/libexec/dbb-control profile set travel
pkexec --user dell-battery-balance /usr/local/libexec/dbb-control config set general.deadband_efc=0.4
    # must fail, exit 3 -- config set is configure-class
pkexec --user dell-battery-balance /usr/local/libexec/dbb-control --polkit-class configure config set general.deadband_efc=0.4
    # must fail (exit 2 or 3) and leave the value unchanged -- the second
    # --polkit-class cannot override the class the wrapper already set
sudo journalctl -u dell-battery-balance.service -n 5
```

## Plasma applet

A Plasma 6 tray widget lives in `plasmoid/`. It sits next to the battery icon
and shows each pack's EFC and calendar score, the measured EC drain order, the
active policy, and buttons for Balance / Field / Restore. Privileged actions
go through `pkexec` against the two polkit actions above, so no terminal and
no passwordless sudo is needed.

The applet on its own is not enough: `pkexec` selecting the right polkit
action depends on the wrappers, the two `.policy` files, the grant script and
the scoped account all being in place together, so there is no supported
"just the applet, by hand" path — run:

```sh
sudo ./install.sh
```

It installs the service account, grant script, udev rule, both polkit
actions, and the applet, in one pass. After editing the applet's own files
(`plasmoid/package/`), re-push just that piece:

```sh
kpackagetool6 --type Plasma/Applet --upgrade plasmoid/package
```

Then: right-click the panel or system tray, Add Widgets, search for
"Dell Battery Balance". Test it standalone with
`plasmawindowed com.chiefgyk3d.dellbatterybalance`.

Field mode is flagged in two places on purpose — a red dot on the tray icon
and a warning banner in the popup — because it disables the calendar-wear
protection and is otherwise easy to leave on by accident.

## State

Durable data lives in `/var/lib/dell-battery-balance`, owned
`dell-battery-balance:dell-battery-balance` (`0755`, files `0644` so the
applet can read them without privilege):

- `state.json` — cumulative counters, written atomically
- `samples.csv` — raw sample log, so the wear model can be recomputed or
  re-derived later if the heuristics change

Configuration lives in `/etc/dell-battery-balance/config.toml`, owned
`root:dell-battery-balance` (directory `2775`, file `0664`) so `config
set`/`config apply`, run as the service account through `dbb-configure`, can
write it.

## Roadmap

Usage profiles (daily / field / travel / storage / custom), the system-wide
`config.toml`, safe auto-revert out of field mode, and the scoped service
account are all implemented, per
[docs/superpowers/specs/2026-09-12-profiles-packs-config-design.md](docs/superpowers/specs/2026-09-12-profiles-packs-config-design.md).
What remains: a pack registry that tracks wear per physical pack across
swaps and rotations — needed because these packs expose no per-unit
identity, so swaps must be detected and confirmed rather than recognised —
and, on the applet side, a full Plasma config dialog and notifications.

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

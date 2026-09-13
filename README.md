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
| `field` | fixed | all 90/100 | Maximum runtime, wear protection off. Auto-reverts after 72 h, or after 12 h back on AC — both adjustable or removable, see below. |
| `travel` | balancing | neutral 70/90, protect 60/80, work 80/95 | Reserve without the 100% float. |
| `storage` | fixed | all 50/55 | Long idle, least wear. |

A balancing profile flips a pack between `protect` and `work` once EFC
divergence exceeds `general.deadband_efc` (default 0.5) — without that
hysteresis the policy would oscillate on every tick. A fixed profile applies
the same band to both packs regardless of wear. Profiles, bands, pins and
revert rules all live in `/etc/dell-battery-balance/config.toml`; see
[the design doc, §1](docs/superpowers/specs/2026-09-12-profiles-packs-config-design.md#1-configuration)
for the full schema.

### Field mode, auto-revert, and why

Field holds both packs at 90–100%. That is exactly the state the rest of
this tool exists to avoid — high state of charge is the dominant
calendar-wear input, and a rugged laptop in a bag is usually warm too — so
the failure mode of field mode is forgetting to leave it. The default
revert is therefore on: 72 h covers a conference or a long weekend in the
field, and 12 h of continuous AC means you are back at a desk. You are not
locked into either:

```sh
dell-battery-balance profile field                    # same as: profile set field
dell-battery-balance profile field --for 5d           # this switch reverts after 5 days, nothing else
dell-battery-balance profile field --stay             # this switch never reverts; you change it yourself
dell-battery-balance profile edit field revert.after_hours=120
dell-battery-balance profile edit field revert.on_ac_hours=none
dell-battery-balance profile edit field revert.to=daily
dell-battery-balance profile edit field revert=none   # never revert, permanently
dell-battery-balance profile edit travel revert.after_hours=24   # any profile can revert
```

`--for` and `--stay` belong to the switch: they replace the profile's own
triggers for that switch and are forgotten on the next one. Editing
`revert.*` changes the profile for good; `none`/`off`/`0` removes a
trigger, and a table with no trigger left is removed with it. Every
privileged form above goes through `dbb-control` (`--for`/`--stay`) or
`dbb-configure` (`profile edit`), as in Usage.

## Packs and swapping

**Label each pack physically** — a sticker with the name you register it
under (`A`, `B`, or whatever you pick) — the moment you name it. The tool
has no way to tell the packs apart electrically, so a wrong `pack same` or
`pack assign` silently corrupts that pack's wear history, and there is no
way to detect the mistake after the fact.

Both packs report an identical serial, ePPID and manufacture date, so the
tool cannot read which physical pack is in which slot — it has to ask.
Wear is tracked per *tenure* (one continuous occupancy of a slot) and rolled
up per named *pack* across every tenure it has ever held, so EFC and calendar
score for pack "A" survive it moving between BAT0 and BAT1, or sitting on the
bench.

A tenure opens whenever a slot goes from empty to occupied, or its readings
jump by more than the plausible-continuity threshold (a `charge_full` change,
or a `charge_now` discontinuity too large for the elapsed time) — either one
means "this might not be the same physical pack any more," and the tool asks
rather than assumes. `status`/`report` show a `PENDING <slot>` line, and the
applet surfaces the same question in its popup, with one of three answers:

- `pack same <slot>` — confirm it is the pack that was previously in that
  slot. Always available whenever that slot has a previous pack, whatever
  the guess; the guess (`same` when the new reading is within 3% of design
  capacity of where the previous occupant left off, with `charge_full`
  unchanged, else `unsure`) only labels which answer looks likely — it never
  restricts which answer you may give.
- `pack assign <slot> <name>` — identify it as an existing named pack (e.g.
  after a deliberate swap with a pack you already track).
- `pack new <slot> <name>` — register a pack seen for the first time.

`pack list` prints every known pack's EFC, calendar score, and whether it is
in a slot, on the bench, or retired, plus how long a benched pack has been
sitting and at what SoC. It also lists any pending questions. A pack removed
above ~70% SoC — poor storage practice for Li-ion — is flagged once, as a
`warning` event at removal time shown by `report`; it is not an ongoing
flag on `pack list` rows. When one bench pack
has drifted more than `general.deadband_efc` behind the most-worn inserted
pack, `status`/`report`/`pack list` print a rotation hint naming which pack
to swap in and which to pull.

Getting an answer wrong is not permanent: `pack reassign <tenure-id> <name>`
re-labels a specific tenure after the fact — the undo for a misidentified
`pack same`/`assign`/`new` — and moves that tenure's history to the
(possibly different) named pack. `pack rename`, `pack retire` and `pack
unretire` manage the pack roster itself; `reset --pack <name>` deletes a
pack and every tenure that ever carried it (refused while the pack is still
in a slot — take it out first).

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

## Upgrading from 0.1 (the root-run install)

Re-running `sudo ./install.sh` on a machine that already has the older,
root-run 0.1 layout is safe and upgrades in place. Exactly what it does to
an existing install:

- Disables and removes `dell-battery-balance-sample.{service,timer}` and the
  old single `com.chiefgyk3d.dellbatterybalance.policy` polkit action — the
  0.1 layout that this scoped-account model replaces.
- Creates the `dell-battery-balance` service account (skipped if it already
  exists from a previous run).
- Re-owns `/var/lib/dell-battery-balance` to `dell-battery-balance:dell-battery-balance`.
  `state.json` and the sample-log CSVs are preserved — only ownership and mode
  change, never the content.
- Installs `/etc/dell-battery-balance/config.toml` from the shipped default
  only if no config file is already present; an existing config.toml,
  including any custom profiles, is left untouched.
- Upgrades the Plasma applet in place (`kpackagetool6 ... --upgrade`, falling
  back to `--install` only if it was never installed).
- Enables `dell-battery-balance.timer` (replacing the old sample timer).
- An existing single `samples.csv` from 0.1 is left in place but no longer
  appended to — new samples go to `samples-YYYY.csv`, one file per calendar
  year, from the first sample after upgrading.

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
`profile create/edit/delete`, `reset`, `pack reassign/rename/retire/unretire`
— with exit 3; `config get`, `pack list/assign/new/same`, and everything else
above stay control-class. See Privilege model below.

Plain `sudo dell-battery-balance …` also works — root can do everything —
but leaves files it creates root-owned. Since 0.3 the state directory is
setgid and files are group-writable, so a stray root run no longer locks
the service account out; prefer `sudo -u dell-battery-balance
dell-battery-balance …` or the wrappers all the same.

If a BIOS admin password is set, point `general.bios_password_file` at a
file readable only by the service account; the tool never takes it as an
argument or environment variable. The grant script's gate for this is a
plain-text match against `config.toml`, so `bios_password_file` must be
written as a bare key under `[general]` (`bios_password_file = "/path"` on
its own line, which is what `config set`/`config apply` always produce) —
not the dotted `general.bios_password_file = "/path"` form, which the gate
does not recognize even though it is valid, equivalent TOML.

### CLI reference

| Command | What it does |
|---|---|
| `tick` | sample, evaluate reverts, apply if `auto_balance` is on (or a revert fired) — what the timer runs |
| `sample` | one measurement, no policy |
| `status [--json]` | wear summary; `--json` is the machine-readable view the applet consumes |
| `report` | `status`, plus events, firmware read-back, and the current recommendation |
| `balance [--apply]` | resolve the active profile's bands; dry run unless `--apply` |
| `profile list` | show all profiles, marking the active one |
| `profile show <name>` | print one profile's TOML |
| `profile <name>` | shortcut for `profile set <name>` |
| `profile set <name> [--for <duration> \| --stay]` | switch profiles and apply immediately; `--for` reverts after that long and `--stay` never, either one replacing the profile's own triggers for this switch |
| `profile create <name> --from <name>` | clone an existing profile |
| `profile edit <name> key=value ...` | change one profile's fields; `revert=none` or `revert.after_hours=none` remove auto-revert |
| `profile delete <name>` | remove a profile (not `daily`, not the active one) |
| `config get [key] [--json]` | print the whole config or one dotted key; `--json` is what the applet's config dialog reads |
| `config set key=value ...` | change `general.*` or `profiles.*` fields |
| `config validate [--json] <path>` | check a candidate file without writing anything |
| `config apply [--json] <path>` | replace the whole config from a TOML file, or with `--json` a JSON document of the same shape (must be complete and valid) |
| `field [--for <duration> \| --stay]` | alias: `profile set field` |
| `restore` | alias: `profile set <previous_profile>` |
| `pack list` | list known packs (EFC, calendar score, slot/bench/retired) and any pending identity questions |
| `pack assign <slot> <name>` | identify the pack in `<slot>` as an existing named pack |
| `pack new <slot> <name>` | register the pack in `<slot>` as a brand-new named pack |
| `pack same <slot>` | confirm the pack in `<slot>` is the one that was previously there |
| `pack reassign <tenure-id> <name>` | re-label one tenure's history to a (possibly different) pack — the undo for a wrong `assign`/`new`/`same` |
| `pack rename <old> <new>` | rename a pack |
| `pack retire <name>` | mark a pack retired (must not be in a slot) |
| `pack unretire <name>` | un-retire a pack |
| `reset --slot <SLOT> \| --pack <NAME> \| --all` | clear counters for one slot, delete a bench pack and its tenures, or reset everything |

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

Clearing `bios_password_file` does not revoke the group grant on
`Admin/current_password` until the next reboot (sysfs permissions are reset
then) or until the grant script is edited and the file is `chmod`ed back
manually in the meantime.

Two polkit actions gate the two wrappers used above:

| Action | Wrapper | Used for | Default prompt |
|---|---|---|---|
| `com.chiefgyk3d.dellbatterybalance.control` | `dbb-control` | `config get`, `profile set`, `field`, `restore`, `balance --apply`, `pack list/assign/new/same` | the user's own password, kept (`auth_self_keep`) |
| `com.chiefgyk3d.dellbatterybalance.configure` | `dbb-configure` | `config set/apply/validate`, `profile create/edit/delete`, `reset`, `pack reassign/rename/retire/unretire` | admin password, kept (`auth_admin_keep`) |

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
cannot be used to swap `control` for `configure` after the fact: a repeated
`--polkit-class` is rejected outright, and the top-level parser also sets
`allow_abbrev=False` so the flag cannot be smuggled in under a
prefix-abbreviated spelling either.

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
ls -l /etc/systemd/system | grep dell-battery
    # after an upgrade from 0.1: only dell-battery-balance.{service,timer} --
    # no leftover dell-battery-balance-sample.{service,timer}
dell-battery-balance status
    # after an upgrade: EFC/calendar-score numbers match what they were
    # pre-upgrade -- state.json was preserved, not reset
dell-battery-balance --version
    # 0.3.0
ls /usr/share/knotifications6/dell_battery_balance.notifyrc
dell-battery-balance config get --json | python3 -m json.tool > /dev/null
```

## Plasma applet

A Plasma 6 tray widget lives in `plasmoid/`. It sits next to the battery icon
and shows each pack's EFC and calendar score, the measured EC drain order, the
active policy, and buttons for Balance / Field / Restore. Any pending pack
identity questions (see Packs and swapping above) surface in the popup too,
with buttons to answer them, since they show up in `status --json`'s
`pending` field the same way they do on the CLI. Privileged actions go
through `pkexec` against the two polkit actions above, so no terminal and no
passwordless sudo is needed.

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
"Dell Battery Balance". Left-click the icon for the popup; right-click →
Configure for the dialog. Test it standalone with
`plasmawindowed com.chiefgyk3d.dellbatterybalance` — but note that a
windowed applet is always expanded and reads config from its own file, so
it cannot show a broken click handler or a broken config default;
`tests/test_applet_package.py` guards the two such mistakes found so far
(no XML comments in `contents/config/main.xml`, expansion toggled on the
root item), and plasmashell must be restarted after
`kpackagetool6 --upgrade` for a panel instance to pick the new files up.

Field mode is flagged in two places on purpose — a red dot on the tray icon
and a warning banner in the popup — because it disables the calendar-wear
protection and is otherwise easy to leave on by accident.

### Configuring from the applet

Right-click the widget → Configure. Four pages:

- **Display** — text beside the icon (none / profile label / EFC
  divergence; only visible when the widget sits in a panel, the system tray
  keeps items icon-only), poll interval, whether pack details start
  expanded, and whether to raise notifications. Saved in the applet's own
  KConfig.
- **Profiles** — pick a profile, add one as a copy, delete one (`daily` and
  the active profile are protected), and edit its label, type (balancing or
  fixed), bands, auto-revert triggers and per-slot pins.
- **Packs** — rename, retire or un-retire packs, and answer pending identity
  questions.
- **General** — balance deadband, automatic balancing, bench temperature.

Profiles and General edit a working copy and submit the *whole* config on
Apply/OK through the configure-class polkit action, so one polkit prompt
per apply. The candidate is staged as a JSON file under `/tmp` (mode 644,
removed straight after; it carries no secrets) because the service account
cannot read the user's runtime directory. The tool validates before
installing and refuses on any error; use **Apply** rather than OK to see
the error message, since OK closes the dialog. Packs actions run
immediately, one prompt each.

### Notifications

The applet raises a desktop notification once per state event for: a
pending pack identity question, an auto-revert firing, a firmware
read-back mismatch, and a pack removed above 70%. They come from the
applet (root has no session bus), so they need the widget running and lag
by at most one poll interval. The event definitions live in
`/usr/share/knotifications6/dell_battery_balance.notifyrc`, which
`install.sh` places — re-run `sudo ./install.sh` when upgrading from 0.2,
then restart the shell so the applet package reloads:

```sh
systemctl --user restart plasma-plasmashell.service
```

Notification sounds/popups per event are configurable in System Settings
→ Notifications → Application-specific → Dell Battery Balance.

## State

Durable data lives in `/var/lib/dell-battery-balance`, owned
`dell-battery-balance:dell-battery-balance` (`2775`, files `0664`) so the
applet can read them without privilege and a stray `sudo` run's root-owned
files stay group-writable by the service account (the directory's setgid
bit keeps new files in the service group):

- `state.json` — cumulative counters: per-slot tenures, the named-pack
  registry, and everything else in the schema. Currently version 2.
  A version-1 file (the pre-registry, per-slot-only layout from 0.2) is
  migrated automatically the first time anything *writes* state (`sample`,
  `tick`, ...); it is a one-way, in-place upgrade — the two slots become
  packs "A" and "B" — and read-only commands like `status`/`report` load
  and display the migrated data without rewriting the file until then.
- `samples-YYYY.csv` — raw sample log, one file per calendar year (UTC), so
  the wear model can be recomputed or re-derived later if the heuristics
  change without ever-growing files.

Events carry an `id` (monotonic, never reused) since 0.3; a 0.2 state file
gets its existing events numbered once on first load. `reset --all` keeps
the id sequence running rather than restarting it, so an applet that has
already notified past a given id never re-notifies just because the state
was reset.

Configuration lives in `/etc/dell-battery-balance/config.toml`, owned
`root:dell-battery-balance` (directory `2775`, file `0664`) so `config
set`/`config apply`, run as the service account through `dbb-configure`, can
write it. After the first `config set` (or `config apply`), the file itself
becomes owned `dell-battery-balance:dell-battery-balance` — the group write
in `0664` is what let the service account replace it, and the replacement it
writes is naturally owned by whoever wrote it. Group permissions are
unaffected; `dbb-configure` keeps working the same way afterward.

## Roadmap

Usage profiles (daily / field / travel / storage / custom), the system-wide
`config.toml`, safe auto-revert out of field mode, the scoped service
account, and the pack registry that tracks wear per physical pack across
swaps and rotations (with confirm-on-swap identity, since these packs expose
no per-unit identity) are all implemented, per
[docs/superpowers/specs/2026-09-12-profiles-packs-config-design.md](docs/superpowers/specs/2026-09-12-profiles-packs-config-design.md).
The applet's config dialog and notifications landed in 0.3, completing the
spec. Open follow-ups are tracked in the issues (`pack swap`, `--for`
clipping, sysfs-hiccup pending).

## Known limits

- **Pack identity is confirmed, not read.** Both packs report an identical
  serial (`88`) and ePPID, so a swap is detected heuristically (a
  `charge_full` change or an implausible `charge_now` jump) and the tool
  asks which physical pack it is seeing rather than assuming — see Packs
  and swapping above. A swap that happens to look continuous (same design
  capacity, charge picked back up close to where it left off) can still be
  missed; `pack reassign` fixes a tenure that was mislabeled this way.
- **A swap across a shutdown or long suspend can be undetectable.** The
  discontinuity check's charge allowance scales up with the elapsed gap, so
  a swap that happens during a shutdown or suspend longer than about 20
  minutes can look perfectly plausible either way — confirm identity
  yourself (`pack same`/`assign`) after any such gap rather than trusting
  the guess.
- **Repairing a pair that got swapped while both were out:** if BAT0 and
  BAT1 both end up mislabeled after being pulled together and reinserted
  swapped, the fix is: `pack new BAT0 TMP` (frees BAT0's current label),
  `pack assign BAT1 A`, `pack assign BAT0 B` (now that `A` is free to move),
  then `reset --pack TMP` (discards the throwaway tenure the first step
  created).
- **Only BAT0 exposes `charge_control_*` to Linux sysfs.** BAT1 is reachable
  only via `dell-wmi-sysman`, so generic tools like TLP can never manage it.
  The script uses sysman for both and falls back to `power_supply` for BAT0.
  Measured 2026-09-13: after a hot-swap of BAT0 the re-created device comes
  back *without* `charge_types` and the `charge_control_*` files (the
  dell-laptop battery hook does not re-attach), and they stay gone until a
  reboot. Only the unprivileged live read-back loses its source; the service
  account still reads and writes the real ceilings through sysman, so
  balancing is unaffected and the popup falls back to the last apply's
  read-back with its age.
- **Sysman writes may need a reboot** to take effect on some attributes.
  `report` reads back what the firmware actually holds — check it after applying.
- Setting Battery 1 to `Custom` greys out Dell Optimizer's Dynamic Charge
  Policy. That is Windows-only software and irrelevant here.
- Energy that leaves a pack while the machine is off or suspended is still
  counted as discharge. Gaps longer than 15 minutes skip *calendar* accrual
  but still count charge deltas.
- Tray text is shown only when the widget sits in a panel; the system tray
  keeps items icon-only regardless of the Display page's setting.
- Notifications need the applet running and lag by up to one poll interval,
  since root has no session bus to raise them from.
- A config error after pressing OK in the applet's dialog is not shown —
  OK closes the dialog before the reply arrives; use **Apply** to see it.
- More than ten qualifying events between two applet polls are marked seen
  without being raised: `status --json` carries only the last ten events,
  and the applet advances its notified-id watermark to the highest one it
  sees, so anything older than that window is skipped silently. The four
  notified kinds are rare enough that this is unlikely to matter in practice.
- With notifications switched off, the applet still advances its watermark,
  so events that happened while they were off are not raised when they are
  switched back on.
- The Profiles and General pages submit the whole config as loaded when the
  page opened, so a profile switch that happens while the dialog is open (a
  popup button, an auto-revert) is overwritten by Apply — reopen the dialog
  after switching.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

178 tests across eight files (`test_wear_model.py`, `test_policy.py`,
`test_config.py`, `test_apply.py`, `test_registry.py`, `test_cli.py`,
`test_state.py`, `test_cli_surface.py`), all against a fake `/sys` tree and
temp state/config dirs (`DBB_SYSFS_ROOT`, `DBB_STATE_DIR`, `DBB_CONFIG_DIR`)
— never real hardware or files. Coverage includes: three full
sequential-discharge cycles, asserting the pack doing the draining
accumulates more EFC, the idle pack accumulates more calendar score, the
drain-order detector credits one event per unplug, the deadband holds
near-equal packs neutral, and bands clamp to the firmware's limits; the
policy engine's role/band/pin/revert resolution; config schema validation
and `set_dotted` coercion; firmware apply/read-back and mismatch recording;
the pack registry's tenure lifecycle, swap detection (including both packs
pulled and reinserted swapped) and identity guessing, totals/rotation-hint
math with bench calendar-aging carried across a reinsertion, and
version-1-to-2 state migration; the full CLI surface, including profile
switching, `--for` one-off reverts, config get/set/validate/apply
strictness (both TOML and `--json`), the `pack ...` subcommands and
`reset --pack`, the `report` tenure table, the per-year sample-log rotation,
and the polkit-class gate (control vs. pack-admin/configure); and
`test_cli_surface.py`, which parses the README's CLI reference table and
asserts every documented form actually parses and every parser subcommand
is documented, so the two cannot drift apart silently.

`plasmoid/tools/load-page.py` is a separate, headless check for the
applet's config pages — it loads a QML file with no Plasma shell present
and reports `ok` or the QML error (needs `python3-pyqt6`); it is not part
of the unit suite and does not run under `unittest discover`.


---

## 💝 Support This Project

If you find dell-battery-balance useful, consider supporting continued development.
Everything is also collected at **[support.chiefgyk3d.com](https://support.chiefgyk3d.com)**.

### Recurring Support

<div align="center">
<table>
  <tr>
    <td align="center" width="150">
      <a href="https://patreon.com/chiefgyk3d" title="Patreon">
        <img src="media/icons/patreon.svg" width="36" height="36" alt="Patreon"><br>
        <sub><b>Patreon</b></sub>
      </a>
    </td>
    <td align="center" width="150">
      <a href="https://streamelements.com/chiefgyk3d/tip" title="StreamElements">
        <img src="media/streamelements.png" width="36" height="36" alt="StreamElements"><br>
        <sub><b>StreamElements</b></sub>
      </a>
    </td>
    <td align="center" width="150">
      <a href="https://shop.chiefgyk3d.com/" title="Merch Store">
        <img src="media/icons/merch.svg" width="36" height="36" alt="Merch"><br>
        <sub><b>Merch Store</b></sub>
      </a>
    </td>
  </tr>
</table>
</div>

### Cryptocurrency Tips

<div align="center">
<table>
  <tr>
    <td><img src="media/icons/bitcoin.svg" width="28" height="28" alt="Bitcoin">&nbsp;<b>Bitcoin</b><br><code>bc1qztdzcy2wyavj2tsuandu4p0tcklzttvdnzalla</code></td>
  </tr>
  <tr>
    <td><img src="media/icons/monero.svg" width="28" height="28" alt="Monero">&nbsp;<b>Monero</b><br><code>84Y34QubRwQYK2HNviezeH9r6aRcPvgWmKtDkN3EwiuVbp6sNLhm9ffRgs6BA9X1n9jY7wEN16ZEpiEngZbecXseUrW8SeQ</code></td>
  </tr>
  <tr>
    <td><img src="media/icons/ethereum.svg" width="28" height="28" alt="Ethereum">&nbsp;<b>Ethereum</b><br><code>0x554f18cfB684889c3A60219BDBE7b050C39335ED</code></td>
  </tr>
  <tr>
    <td><img src="media/icons/solana.svg" width="28" height="28" alt="Solana">&nbsp;<b>Solana</b><br><code>5T8h3HbyvHgLxwXgchRYbHSqRjZyAr8J7uwjLN9Fh8Jh</code></td>
  </tr>
</table>
</div>

---

## 👤 Author & Socials

<div align="center">
<table>
  <tr>
    <td align="center" width="90"><a href="https://social.chiefgyk3d.com/@chiefgyk3d" title="Mastodon"><img src="media/icons/mastodon.svg" width="30" height="30" alt="Mastodon"><br><sub>Mastodon</sub></a></td>
    <td align="center" width="90"><a href="https://bsky.app/profile/chiefgyk3d.com" title="Bluesky"><img src="media/icons/bluesky.svg" width="30" height="30" alt="Bluesky"><br><sub>Bluesky</sub></a></td>
    <td align="center" width="90"><a href="https://twitch.tv/chiefgyk3d" title="Twitch"><img src="media/icons/twitch.svg" width="30" height="30" alt="Twitch"><br><sub>Twitch</sub></a></td>
    <td align="center" width="90"><a href="https://www.youtube.com/channel/UCvFY4KyqVBuYd7JAl3NRyiQ" title="YouTube"><img src="media/icons/youtube.svg" width="30" height="30" alt="YouTube"><br><sub>YouTube</sub></a></td>
    <td align="center" width="90"><a href="https://kick.com/chiefgyk3d" title="Kick"><img src="media/icons/kick.svg" width="30" height="30" alt="Kick"><br><sub>Kick</sub></a></td>
    <td align="center" width="90"><a href="https://www.tiktok.com/@chiefgyk3d" title="TikTok"><img src="media/icons/tiktok.svg" width="30" height="30" alt="TikTok"><br><sub>TikTok</sub></a></td>
    <td align="center" width="90"><a href="https://www.instagram.com/chiefgyk3d" title="Instagram"><img src="media/icons/instagram.svg" width="30" height="30" alt="Instagram"><br><sub>Instagram</sub></a></td>
    <td align="center" width="90"><a href="https://www.threads.net/@chiefgyk3d" title="Threads"><img src="media/icons/threads.svg" width="30" height="30" alt="Threads"><br><sub>Threads</sub></a></td>
    <td align="center" width="90"><a href="https://discord.chiefgyk3d.com" title="Discord"><img src="media/icons/discord.svg" width="30" height="30" alt="Discord"><br><sub>Discord</sub></a></td>
    <td align="center" width="90"><a href="https://matrix-invite.chiefgyk3d.com" title="Matrix"><img src="media/icons/matrix.svg" width="30" height="30" alt="Matrix"><br><sub>Matrix</sub></a></td>
  </tr>
</table>
</div>

<div align="center"><sub>Made with ❤️ by <a href="https://github.com/ChiefGyk3D">ChiefGyk3D</a></sub></div>

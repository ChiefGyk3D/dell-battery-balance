# Profiles, pack registry, and configuration — design

Date: 2026-09-12
Status: approved for planning

## Goal

Turn dell-battery-balance from a fixed two-slot balancer into a configurable
tool that:

- switches cleanly between usage modes (daily, field/conference, travel,
  storage, user-defined) with safe automatic reversion;
- tracks wear per *physical pack* across removals, swaps and a rotation of
  more packs than slots;
- exposes every tunable and every measured value through both the CLI and
  the Plasma applet, with one system-wide source of truth.

## Hard constraint: packs have no readable identity

Measured 2026-09-12 on the target machine: every static sysfs attribute is
identical across the two packs — `serial_number` (`88`), `eppid`,
`model_name`, `manufacturer`, manufacture date, `charge_full_design`,
`voltage_min_design`. `dell_wmi_ddv` exposes nothing further to an
unprivileged reader. Software therefore cannot know which physical pack
occupies a slot. It can detect that occupancy *changed* and ask the user.
Every part of this design that touches identity is built on that fact.

## 1. Configuration

### 1.1 Location and ownership

`/etc/dell-battery-balance/config.toml`, owned `root:root 0644`. Read by the
root timer and by the unprivileged applet. Written only by
`dell-battery-balance` running as root (directly or via pkexec). Every write
first copies the current file to `config.toml.bak`.

Python's stdlib `tomllib` reads TOML; writing uses a small in-tree emitter
limited to the subset this schema needs (tables, strings, ints, bools, arrays
of ints). No third-party dependency.

### 1.2 Schema

```toml
[general]
active_profile      = "daily"
previous_profile    = "daily"      # maintained by the tool, used by revert.to = "previous"
deadband_efc        = 0.5
auto_balance        = true         # tick applies balancing when true
sample_interval_s   = 120          # informational; the systemd timer sets the real cadence
bench_temp_c        = 25.0         # assumed temperature for packs on the shelf
bios_password_file  = ""           # root-only file; empty = no BIOS admin password
firmware_write_needs_reboot = false  # set true by the tool if read-back ever disagrees

[profiles.daily]
label       = "Daily"
description = "Docked / desk. Wear balancing on."
balancing   = true
bands.neutral = [50, 80]
bands.protect = [50, 60]
bands.work    = [80, 90]

[profiles.field]
label       = "Field / Conference"
description = "Maximum runtime. Wear protection off."
balancing   = false
bands.all   = [90, 100]
revert.after_hours = 72
revert.on_ac_hours = 12
revert.to          = "previous"

[profiles.travel]
label       = "Travel"
description = "Reserve without the 100% float."
balancing   = true
bands.neutral = [70, 90]
bands.protect = [60, 80]
bands.work    = [80, 95]

[profiles.storage]
label       = "Storage"
description = "Long idle. Least wear."
balancing   = false
bands.all   = [50, 55]

# Optional, per profile. Pins override the wear logic.
# [profiles.daily.pins]
# BAT0 = { role = "protect" }
# BAT1 = { start = 55, stop = 70 }
```

Rules:

- A profile has either `bands.all` (fixed) or all three of
  `bands.neutral/protect/work` (balancing). `balancing` must agree with which
  is present; validation rejects a mismatch.
- Bands are `[start, stop]` with firmware limits start 50–95, stop 55–100,
  start < stop. Validation clamps nothing; it rejects, with the offending key
  named.
- `revert` is optional. `revert.to` is `"previous"` or a profile name. Either
  trigger may be omitted; if both are, `revert` is rejected as meaningless.
- A pin is `{ role = "protect"|"work"|"neutral" }` or
  `{ start = N, stop = M }`, never both.
- Profile names: `[a-z0-9_-]{1,32}`. `daily` must exist; it is the fallback.
- Unknown keys are rejected so typos surface instead of silently doing nothing.

### 1.3 Editing surfaces

- CLI: `config get|set`, `profile edit`, `profile create`, `profile delete`.
- Applet config dialog: writes a complete TOML document to
  `$XDG_RUNTIME_DIR/dell-battery-balance/config-<pid>.toml` and runs
  `pkexec dell-battery-balance config apply <path>`. Root validates the file
  before installing it and refuses on any error, printing the reason for the
  dialog to display. The applet never writes `/etc` itself.

## 2. Pack registry and tenures

### 2.1 Model

```
packs:    { "A": {label, first_seen, retired, notes, removed_at_soc,
                  removed_ts}, ... }
tenures:  [ {id, slot, start_ts, end_ts|null, pack|null,
             discharge_uah, charge_uah, calendar_score, soc_hours,
             soc_hours_sum, seconds_observed, seconds_ge_90,
             start_charge_uah, start_charge_full_uah}, ... ]
slots:    { "BAT0": tenure_id|null, "BAT1": tenure_id|null }
```

A tenure is one continuous occupancy of a slot. Accrual goes into the open
tenure for the slot and never pauses. A pack's totals are the sum over its
tenures plus its bench estimate (2.4). Identity is a label on the tenure;
`pack = null` means unidentified.

Pack names are user-chosen: `[A-Za-z0-9_-]{1,16}`. The intent is a physical
sticker on each pack.

### 2.2 Occupancy-change detection

Evaluated on every tick against the previous sample of the same slot:

1. `present` 0 → 1: unambiguous insertion. Close the previous tenure (if any
   was still open, it ends at the last sample that saw the pack) and open a
   new one, `pack = null`.
2. Discontinuity with `present` staying 1: `|Δcharge_now|` exceeds what the
   maximum observed charge or discharge rate could move in `Δt` (bounded
   below by 10% of design per 2 min), or `charge_full` changed. Close and
   reopen as above.
3. First sample after boot (boot_id changed): apply test 2 against the last
   pre-shutdown sample.

Removal: `present` 1 → 0 closes the tenure and records
`removed_at_soc`/`removed_ts` on its pack if identified.

### 2.3 Identification

When a tenure opens with `pack = null` the tool records a **pending
question** for that slot with a guess:

- `"same"` if `charge_now` is within 3% of the closed tenure's last reading
  and `charge_full` is unchanged;
- `"unsure"` otherwise, with the delta shown.

The guess is never `"different"` — a pack charged off-machine looks different
and is not. The guess only preselects a button; confirmation is mandatory.
Answers: *same pack* (label with the previous tenure's pack), *known pack*
(choose from non-retired packs not currently in another slot), *new pack*
(name it). Two simultaneous pending questions may not resolve to the same
pack; the second answer is rejected with the conflict named.

`pack reassign <tenure-id> <pack>` relabels any tenure, which is how a wrong
confirmation is repaired. `pack same <slot>` is shorthand for the first
answer.

### 2.4 Bench estimate

A pack that is identified, not in a slot, and not retired accrues calendar
score from `removed_ts` at `removed_at_soc` and `bench_temp_c`, computed on
read rather than stored. On removal the tool emits an event and, if
`removed_at_soc > 70`, a warning to discharge before storing.

### 2.5 Rotation guidance

`status` lists non-retired packs sorted by total EFC. If a bench pack has
lower EFC than the highest-EFC inserted pack by more than `deadband_efc`,
`status` names it as "swap in next".

### 2.6 Migration

On first load of a version-1 state: create packs `A` (from `BAT0`) and `B`
(from `BAT1`), one open tenure each carrying the existing counters, slots
pointing at them, `discharge_first` and `sessions` preserved. Write as
version 2. Version-2 state is never downgraded.

## 3. Policy engine

`resolve(config, state, sample) -> {slot: (start, stop) | None}`:

1. **Revert.** If the active profile has `revert` and either trigger fired,
   set `active_profile = revert.to` (resolving `"previous"`, falling back to
   `daily` if the previous profile itself has `revert`), record an event,
   and continue with the new profile.
   - `after_hours` counts from the profile-switch timestamp.
   - `on_ac_hours` is the length of the current run of samples with
     `ac_online = 1`, where a gap between two AC samples counts as AC.
2. **Fixed profile.** Both present slots get `bands.all`.
3. **Balancing profile.** Roles from EFC divergence of the *packs* currently
   in the slots (unidentified tenure → that tenure's own accrual, since that
   is the best available), with `deadband_efc` hysteresis exactly as today.
   One slot empty → the present slot gets `bands.neutral`.
4. **Pins** override the result for their slot.
5. **Write only on change.** Compare with the last recorded read-back; write
   through sysman (power_supply fallback for BAT0 as now); read back; store
   `{requested, observed, ts}` per slot in state. If observed ≠ requested,
   set `general.firmware_write_needs_reboot = true` and record an event.

`tick` = sample + accrual + detection + `resolve` + apply (apply only when
`auto_balance` is true or a revert fired). `profile set`, `field`, `restore`
and `balance --apply` always apply immediately, independent of `auto_balance`;
that flag only governs whether the timer re-applies on its own. The systemd timer runs `tick`
every 2 min. The separate daily balancing timer is removed.

## 4. CLI

```
tick                              # what the timer runs
sample                            # kept for manual/testing use, no policy
status [--json]                   # packs, tenures summary, profile, revert countdown,
                                  # pending questions, firmware read-back with age
report                            # + events, per-tenure history, bench estimates
balance [--apply]                 # one-shot resolve+apply (dry run by default)

profile list | show <name> | set <name> [--for <duration>] |
        create <name> --from <name> | edit <name> key=value ... | delete <name>
pack    list | assign <slot> <name> | new <slot> <name> | same <slot> |
        reassign <tenure-id> <name> | rename <old> <new> | retire <name> |
        unretire <name>
config  get [key] | set key=value ... | apply <path> | validate <path>
field                             # alias: profile set field
restore                           # alias: profile set <previous_profile>
reset [--slot | --pack | --all]
```

`profile set --for 8h` writes a one-off `revert.after_hours` override into
state (not config) so a temporary switch does not edit the profile.

All output goes through one JSON model; the text renderer and `--json`
consume the same dict so the applet can never see something the CLI cannot.

## 5. State file

`/var/lib/dell-battery-balance/state.json`, version 2, 0644, atomic
replace. Contains: `packs`, `tenures`, `slots`, `last` sample, `boot_id`,
`discharge_first`, `sessions`, `profile_switched_ts`, `one_off_revert`,
`firmware` read-back per slot, `pending` questions, `events` (bounded to the
last 500). `samples-YYYY.csv` next to it, one file per year.

## 6. Applet

### 6.1 Popup

Per inserted pack, expandable: label, %, status, power (W, signed), voltage,
temperature, ceiling in force with "matches requested"/"mismatch" indicator
and read-back age, EFC, calendar score, mean SoC, % time ≥ 90%, health
(`charge_full`/design, labelled "as reported, updates rarely"), time in slot.

Then: bench packs with EFC and "swap in next"; profile switcher (one button
per profile, current highlighted, field-type profiles red); revert countdown
when active; EC drain order; recommendation text; pending identity questions
inline with their buttons; last three events.

Tray icon: profile-specific icon; red dot while the active profile has
`bands.all` with stop ≥ 95; amber dot while a question is pending.

Optional tray text (config): none / profile label / EFC divergence.

### 6.2 Config dialog

Plasma config pages (`contents/config/main.xml` for applet-local display
options; `ui/config*.qml`). Pages: **Display** (tray text, poll interval,
which per-pack fields expand by default), **Profiles** (list; add/duplicate/
delete; per-profile editor for label, description, type, bands, revert,
pins), **Packs** (list with rename/retire/unretire; pending questions),
**General** (deadband, auto-balance, bench temperature).

Profiles/Packs/General pages edit a working copy of the TOML and apply via
1.3. Display page saves to KConfig only.

### 6.3 Notifications

The applet, not the timer, raises desktop notifications (root has no session
bus). It notifies once per event id for: pending identity question, revert
fired, firmware mismatch, pack removed above 70%. Event ids seen are kept in
KConfig so a restart does not re-notify.

## 7. Errors

- Config invalid → tool refuses to apply, keeps running on the last good
  config, reports the error in status; applet shows it.
- Firmware write fails → recorded per slot, surfaced in status; tick keeps
  sampling.
- State unreadable/corrupt → tool exits non-zero with the path; it never
  starts a fresh state over a corrupt one. `reset --all` is explicit.
- pkexec dismissed (126/127) → applet treats as cancel, no error banner.

## 8. Testing

- `resolve()` table-driven: every profile type × pins × slot-empty ×
  unidentified × revert trigger combinations.
- Detection: synthetic sample streams through all three trip paths, the
  same/unsure guess, off-machine charging, both-slots-swapped conflict,
  removal at high SoC.
- Tenure arithmetic: reassign moves totals exactly; bench estimate is
  monotone in time and SoC.
- Migration: version-1 fixture → version-2 with totals preserved.
- TOML: emit → parse round-trips every shipped profile; validation rejects
  each rule in 1.2 with the key named.
- Existing wear-model test kept.
- Applet: `plasmawindowed` load with no QML errors; config dialog opened via
  the applet's configure action; manual walkthrough of one profile edit and
  one identity confirmation.

## 9. Known limits (stated, not solved)

- A swap of two packs at near-identical charge within one tick and without a
  `present` transition is undetectable. Tenures make it repairable
  (`reassign`), not detectable.
- Bench calendar wear is an estimate at an assumed temperature and the SoC at
  removal; external charging on the shelf is invisible.
- `charge_full` health tracking depends on firmware recalibration cadence.
- Notifications require the applet to be running.

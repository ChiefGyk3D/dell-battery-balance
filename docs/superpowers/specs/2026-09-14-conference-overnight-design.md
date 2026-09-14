# Conference profile, overnight top-off, and revert flexibility

Date: 2026-09-14. Extends the 2026-09-12 profiles/packs/config design; section
numbers there are referenced as "base §N".

## Goal

A con week (BSides LV + DEF CON, six or seven days; GrrCon, two) must not be
fought by the tool, and the tool must keep reducing wear while the user is
at the con. Today `field` reverts after 72 h regardless, and after 12 h of
continuous AC — exactly one hotel night from 19:00 to 07:00 — so the user
wakes up reverted to 50/80 and walks out short. `field --for 8d` fixes the
fight but parks both packs at 100% for every hotel night, which is the
calendar wear the tool exists to avoid.

Deliverables:

1. A **conference** profile: field bands by day, a lower hold overnight, and
   a timed top-off so both packs are full at a departure time the user sets.
   Overnight behaviour is the user's choice: `topoff` (recommended, default)
   or `full` (leave the packs at 100%, today's field behaviour).
2. Two quick actions for the evenings that differ — "top off now" for going
   back out to a CTF or a party, "in for the night" for an early night — so
   the automatic rule is never in the way.
3. Revert flexibility for every profile that reverts: the on-AC trigger
   recognises "still going out daily"; a warning an hour before any revert
   with an **extend** action; `--until <datetime>` next to `--for`.
4. Documentation and screenshots updated to match.

## Measured constraints (2026-09-13/14, Latitude 5430 Rugged)

- The firmware exposes only charge ceilings. It cannot lower a pack that is
  already full, so an overnight hold only saves wear if the charge is
  stopped at the hold level when the laptop is plugged in for the night.
- `AdvBatteryChargeCfg` exists as an enable switch only; the per-day time
  attributes of Dell's Advanced Battery Charge Mode are not exposed by this
  BIOS. Scheduling is therefore done in software.
- Charging current is ~2.28 A on both packs (median of 56 charging samples),
  and the EC charges the two packs one after the other, never in parallel.
  4.6 Ah design. 80 → 100% on both packs is on the order of an hour plus the
  constant-voltage tail.
- The tick timer (`OnUnitActiveSec=2min`, monotonic) does not run while
  suspended and fires within `AccuracySec=15s` of resume.
- `/sys/class/rtc/rtc0/wakealarm` is present, `0644 root`; the lid state is
  readable at `/proc/acpi/button/lid/*/state`. Both usable from the root
  helper step, neither from the scoped service user.
- `charge_full` has never left `charge_full_design` on either pack (768
  samples), so "full" is design capacity for the estimate.

## 1. Conference profile

### 1.1 Schema

A profile may carry an `[overnight]` table. Only a fixed profile (one with
`bands.all`) may carry it; `profile_type()` returns `"conference"` for such a
profile, and the policy engine treats it exactly like `fixed` for band
resolution. Base §1.2 `PROFILE_KEYS` gains `overnight`.

```toml
[profiles.conference]
label = "Conference"
description = "Con week. Full by day, held overnight, topped off before you leave."
balancing = false
bands.all = [90, 100]
revert = { after_hours = 168, on_ac_hours = 12, to = "previous" }

[profiles.conference.overnight]
mode = "topoff"      # "topoff" (recommended) or "full"
leave_at = "07:00"   # local time, HH:MM
night_from = "23:00" # local time, HH:MM; the window is [night_from, leave_at)
hold = [70, 80]      # start/stop while holding
margin_min = 30      # added to the estimated top-off duration
```

Validation, each rule named in the error: `mode` ∈ {`topoff`, `full`};
`leave_at`/`night_from` match `^([01]\d|2[0-3]):[0-5]\d$` and differ from
each other; `hold` is a two-int band accepted by `clamp_band` with
`start < stop`; `margin_min` is an int ≥ 0; `overnight` on a balancing
profile is rejected. All keys required within the table when it is present.
In `full` mode the other keys are still validated but unused.

The built-in `conference` profile above ships in `default_config()`.
Existing `config.toml` files do not gain it on load (validation of old
configs is unchanged, per the `GENERAL_OPTIONAL` rule); they get it through
`profile create`, which gains `--template <builtin>` as an alternative to
`--from <existing>` — the source is the shipped default of that name. The
`field` profile is unchanged. New general key: `topoff_resuspend = true`,
added to both `GENERAL_KEYS` and `GENERAL_OPTIONAL`.

Recommended use: `profile create defcon --template conference` and
`profile edit defcon overnight.night_from=23:00`; `profile create grrcon
--template conference` with `overnight.night_from=19:00`. Field mode's
existing `--for`/`--stay` and the new `--until` apply to conference
profiles unchanged.

### 1.2 Editing

`profile edit <name> overnight.<key>=<value>` for each key;
`overnight=none` removes the table (the profile becomes plain `fixed`).
`config set profiles.<name>.overnight.<key>=...` mirrors it, as for
`revert`. The applet's Profiles page (base §6.2) adds "Conference" to the
type picker; choosing it adds the table with the defaults above, choosing
another type removes it.

## 2. Overnight state machine

Evaluated on every tick, after `policy.resolve()` and before apply, only when
the resolved profile is a conference profile with `mode = "topoff"`. In
`full` mode, or for any other profile, the state is `off` and the bands are
whatever `resolve()` said. The machine overrides the resolved bands for both
present slots; pins still apply on top (base §3), so a pinned slot is left
alone.

State key `overnight` in `state.json`:

```
{ "phase": "off" | "charging_full" | "holding" | "topping",
  "since_ts": float | null,
  "topoff_start_ts": float | null,    # when holding → topping is due
  "leave_ts": float | null,           # the departure the estimate targets
  "manual": null | "topoff" | "night",
  "leave_at_override_ts": float | null,
  "charge_ua": { "BAT0": [..], "BAT1": [..] } }   # last 48 charging currents
```

Transitions, in this order on each tick, with `now` local time:

1. **AC off** → `phase = off`, `manual = null`, `topoff_start_ts = null`,
   `leave_ts = null`. Bands: the profile's `bands.all`.
2. **AC on, phase `off`** (plug-in, or first tick after switching to the
   profile): if `manual == "night"` or `now` is inside the night window →
   `holding`; else → `charging_full`. Event.
3. **`charging_full`**: bands `bands.all`. If `now` enters the night window
   while still on AC → `holding` (the packs may already be above the hold;
   see 6). `manual == "night"` → `holding` at once.
4. **`holding`**: bands `overnight.hold`. Recompute `leave_ts` (§2.1) and
   `topoff_start_ts` (§3) every tick. When `now >= topoff_start_ts`, or
   `manual == "topoff"` → `topping`. Event on entry
   ("holding 70/80, top-off 05:31 for 07:00").
5. **`topping`**: bands `bands.all`. Stays until AC drops (a late departure
   keeps 100%). Event on entry ("top-off started, ready by 07:00").
6. `manual == "topoff"` from any on-AC phase → `topping`. `manual ==
   "night"` from `charging_full` → `holding`; if a present pack is already
   above `hold[1]` the event says so ("BAT0 is at 96%, above the 80% hold;
   the firmware cannot lower it") and status shows the same.

Both manual overrides clear on AC off (rule 1). The night window is
`[night_from, leave_at)` on the local clock and may cross midnight; a window
where `leave_at < night_from` wraps, one where `leave_at > night_from` does
not. "Inside" is a pure function of `(now, night_from, leave_at)` and is
unit-tested across midnight and DST.

### 2.1 Departure time

`leave_ts` = `leave_at_override_ts` if set and still in the future, else the
next occurrence of `overnight.leave_at` on the local clock after `now`. An
override that has passed is cleared and an event records it.
`leave-at HH:MM` sets the override to the next occurrence of that time;
`--tomorrow` forces tomorrow's. The override is per switch: `switch_profile`
clears it.

## 3. Top-off estimate

Inputs: for each present slot, `charge_now_uah`, `charge_full_uah`; the
per-slot median of the last 48 charging currents (`status == "Charging"`,
`current_now_ua > 0`, appended on every tick as `overnight.charge_ua`),
falling back to 2,000,000 µA for a slot with no samples yet.

```
duration_s = margin_min * 60
for each present slot with deficit = max(0, charge_full - charge_now) > 0:
    duration_s += deficit / median_ua * 3600 + CV_TAIL_S     # CV_TAIL_S = 20 min
topoff_start_ts = leave_ts - duration_s
```

Sequential charging is why the per-slot times are summed, not maxed. The
estimate is recomputed every tick while holding, so it tightens as packs
drift and as the current log grows. If `topoff_start_ts <= now` on entry to
`holding`, the phase goes straight to `topping`. Both `topoff_start_ts` and
the duration are shown in status.

## 4. Waking from suspend

The scoped service cannot set an RTC alarm or suspend the machine, so the
root step is extended, in the same spirit as the grant helper (base §10.2):
one small allowlisted script, `/usr/local/libexec/dell-battery-balance-wake`,
run as `ExecStartPost=+` on the tick service.

The tick writes `/var/lib/dell-battery-balance/wakealarm`: the integer
`topoff_start_ts` while holding, empty otherwise (the file is rewritten
every tick, atomically). The wake helper then:

1. Reads at most 32 bytes of the file. Accepts only an integer strictly in
   the future and at most 24 h ahead; anything else is treated as "no
   alarm".
2. Programs it: writes `0` then the value to `/sys/class/rtc/rtc0/wakealarm`,
   and records the value in `/var/lib/dell-battery-balance-wake/wakealarm.set`
   (root:root `0755`, outside the service-writable state dir; a symlink
   planted at that path is removed, never followed). Idempotent when the
   value is unchanged.
3. Clears it: if the file is empty and `wakealarm.set` exists, writes `0` to
   the RTC alarm and removes the marker. An alarm the tool did not set is
   never touched — the marker is the proof of ownership.
4. Re-suspends: if `wakealarm.set` exists, `now` is within 3 minutes after
   its value, the lid is closed, and `topoff_resuspend = true` in
   `config.toml` (grepped, bounded, as the grant helper reads
   `bios_password_file`), it removes the marker and runs `systemctl
   suspend`. The marker removal comes first, so the wake cannot loop.

The tick applies the raised ceilings in `ExecStart`, so they are in firmware
before the helper can suspend. If the desktop's own lid handling re-suspends
first, the helper simply finds the marker gone on the next tick. The RTC
alarm does not wake a hibernated machine; the README says to suspend, not
hibernate, on con nights. If the machine is awake and in use when the alarm
fires, nothing happens unless the lid is closed.

Uninstall clears any alarm the tool set (marker present → write `0`) before
removing the helper.

## 5. Revert changes (all reverting profiles)

**Still-going-out guard.** `wear._track_ac_run` gains the mirror image: a
battery run (`ac_online != 1`) is tracked with `battery_run_start_ts`, and
when it ends after ≥ 30 min, `last_battery_stint_end_ts` is set. `on_ac_hours`
fires only when the run is long enough **and** `last_battery_stint_end_ts`
is `None` or more than 24 h ago. Both numbers are module constants
(`ACTIVE_DAY_STINT_MIN = 30`, `ACTIVE_DAY_WINDOW_H = 24`), documented, not
config. `after_hours` is unchanged; on a con week it is expected to be set
to 168 or replaced by `--until`.

**Warning and extend.** When either trigger is due within 60 min,
`revert_due` reports it separately as `revert_soon` and the tick adds one
`revert-warning` event per switch (state key `revert_warned_ts`, cleared by
`switch_profile` and by `extend`). `profile extend <duration>` (control
class) moves `profile_switched_ts` forward by the duration, so both
`after_hours` and a `--for` one-off get that much more, and resets
`ac_run_start_ts` to `now` so the on-AC clock restarts too. It refuses when
the active profile has no revert and no one-off ("nothing to extend"). Event.

**`--until`.** `profile set <name> --until <when>` and `field --until <when>`
accept `YYYY-MM-DD` (end of that local day, 23:59), `YYYY-MM-DD HH:MM`, or
`HH:MM` (next occurrence). It is converted to hours from now and stored as
the existing `one_off_revert_hours`, so nothing downstream changes; a time
in the past is an error. `--for`, `--stay` and `--until` are mutually
exclusive.

`render._revert_info` gains `warning: bool` and `guard_blocking: bool`
(the on-AC clock has expired but the guard is holding it), and status
prints both in words.

## 6. CLI

| Command | Class | Effect |
|---|---|---|
| `topoff now` | control | `manual = "topoff"`, applies immediately |
| `night` | control | `manual = "night"`, applies immediately |
| `leave-at HH:MM [--tomorrow]` | control | sets `leave_at_override_ts`; `leave-at none` clears |
| `profile extend <duration>` | control | §5 |
| `profile set … --until <when>`, `field --until <when>` | control | §5 |
| `profile create <name> --template <builtin>` | configure | §1.1 |
| `profile edit <name> overnight.<key>=…`, `overnight=none` | configure | §1.2 |

`topoff now`, `night` and `leave-at` refuse (exit 2, reason printed) unless
the active profile is a conference profile in `topoff` mode; `topoff now`
and `night` additionally refuse when AC is off, since rule 1 would clear
the override on the next tick anyway. The applet only shows the buttons
when they would be accepted.

`status`/`report` add, for a conference profile, one line:
`overnight: holding 70/80, top-off 05:31 (1h29m) for 07:00` /
`charging to 100% until 23:00, then hold` / `topping off, ready by 07:00` /
`mode full: packs stay at 100% on AC`, and the revert line gains
`(warning: reverts in 42m — profile extend)` or `(on-AC revert held: on
battery 3h ago)`. `status --json` carries the `overnight` object and the
two new revert flags.

## 7. State file

New keys: `overnight` (§2), `battery_run_start_ts`, `last_battery_stint_end_ts`,
`revert_warned_ts`. Version stays 2; missing keys default on load. One new
file in the state dir, `wakealarm` (service-owned, rewritten every tick,
listed in the README's state-directory table); its root-owned ownership
marker `wakealarm.set` lives outside the state dir, in its own directory
`/var/lib/dell-battery-balance-wake` (§4), created by install.sh and removed
by uninstall.

## 8. Applet

Popup (base §6.1): for a conference profile the banner reads "Conference
mode is on…" and gains the overnight line from §6 beneath it, with
`Top off now` and `In for the night` buttons (control class via pkexec, like
Field/Restore), and a `Leaving at` time field with `Set` (tomorrow) and
`Clear`. When `revert_info.warning` is set, an `Extend 24 h` button appears
next to the revert countdown. The field banner keeps `Restore`.

Config dialog, Profiles page (base §6.2): type picker gains "Conference";
fields for mode (radio: "Hold overnight and top off before I leave
(recommended)" / "Leave the packs at 100%"), leave at, night from, hold
band, margin.

Notifications (base §6.3): two new notifyrc events, `revertWarning`
("Profile reverts in an hour") and `topoffStarted` ("Top-off started, ready
by 07:00"), raised from events like the existing four. The `holding` entry
event is not notified — it happens when the user plugs in and can see the
popup.

## 9. Metrics

`dbb_overnight_phase{phase}` (one-hot 0/1 over the four phases),
`dbb_topoff_start_timestamp_seconds`, `dbb_leave_timestamp_seconds`,
`dbb_revert_warning` (0/1). Added to the README metrics table.

## 10. Documentation and screenshots

README: the profiles table gains the `conference` row; "Field mode,
auto-revert, and why" is extended with a "Conference weeks" subsection
covering the night window, the two quick actions, `leave-at`, `--until`,
`extend`, the still-going-out guard, and the suspend/hibernate note; the
command table gains §6's rows; the state-directory and Monitoring tables
gain §7 and §9; Known limits gains §12; the roadmap paragraph and test
count are updated; the config example shows the overnight table.
`docs/` gets nothing new beyond this spec.

Screenshots (all in `media/`, same widths as the current ones, captured
with the same tool and theme as the 0.3 set): `applet-popup.png` retaken
with a conference profile active in the `holding` phase so the overnight
line and both buttons are visible; `applet-config-profiles.png` retaken on
the Conference type with the overnight fields; one new
`applet-popup-revert-warning.png` showing the countdown with `Extend 24 h`.
Alt text updated to match. The README test count line is updated last,
from the real run.

## 11. Testing

Unit (unittest, fake clock via injected `now`, `TZ=America/New_York` and one
run with `TZ=Pacific/Auckland` for the window arithmetic, existing
`fakesys` harness):

- Window membership across midnight and across a DST change; `leave_ts`
  next-occurrence and override consumption.
- State machine: every transition in §2 as a table of (phase, ac, in-window,
  manual, now vs topoff_start) → (phase, bands, event); manual overrides
  clear on AC off; pins win over the machine; `full` mode and non-conference
  profiles leave `resolve()` untouched.
- Estimate: sequential sum, fallback current, margin, zero-deficit slots
  skipped, immediate `topping` when the start is already past, median over
  the ring buffer.
- Wake file: written while holding, empty otherwise, integer only.
- Wake helper (shell, `DBB_RTC_PATH`/`DBB_LID_PATH` overrides to temp
  files): programs, is idempotent, refuses past/far/garbage values, clears
  only with the marker, never runs suspend without marker + lid + config.
- Revert: guard blocks on a recent stint and releases after 24 h;
  `revert_soon` and single warning per switch; `extend` arithmetic for
  profile triggers and for a `--for` one-off, refusal with nothing to
  extend; `--until` parsing of all three forms, past-time rejection,
  exclusivity with `--for`/`--stay`.
- Config: schema round-trip, every validation rule in §1.1 named, `--template`
  for a missing built-in and refusal for an unknown one, `overnight=none`.
- CLI surface test updated for the new verbs and classes (control refuses
  configure-class, as today).
- Metrics: new series rendered with the right labels.

On the laptop: one staged night — conference profile, `leave-at` 30 min out,
lid closed on AC — verifying the RTC alarm is programmed (`cat
/sys/class/rtc/rtc0/wakealarm`), the machine wakes, the ceilings read back
at 90/100, and it re-suspends; then the same with `topoff_resuspend =
false`. Applet loaded with `plasmawindowed`, no QML errors, manual
walkthrough of both quick actions and one `Extend`.

## 12. Known limits (stated, not solved)

- The firmware cannot lower a full pack: a night that starts in
  `charging_full` and reaches 100% before `night_from` is a full float. The
  fix is `night` (or an earlier `night_from`), not software.
- The RTC alarm does not wake from hibernate.
- Re-suspend relies on the lid being closed; a closed-lid docked setup will
  be put back to sleep after the top-off starts. `topoff_resuspend = false`
  disables it.
- The estimate assumes design capacity is full and a constant-current
  charge at the logged median; the 30 min margin covers the tail. A cold
  pack or a throttled charger can still run late.
- The still-going-out guard uses fixed 30 min / 24 h constants.

## 13. Version

0.4.0. No state-file migration; new keys default on load.

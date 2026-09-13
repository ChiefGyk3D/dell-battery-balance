#
# dell-battery-balance - wear tracking and charge-ceiling balancing for the
# two battery packs in a Dell Latitude Rugged.
#
# Copyright (C) 2026 ChiefGyk3D
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. See the LICENSE file for the full text.
#
"""Command-line entry point: argument parsing and the sample/status/balance/... commands."""

import argparse
import json
import os
import sys

from dbb import config, policy
from dbb.render import fmt_status, state_json
from dbb.state import append_log, load_state, now_iso, save_state
from dbb.sysfs import BATS, PS, apply_band, read_applied, sample_all
from dbb.wear import integrate


def require_root(action):
    if os.geteuid() != 0:
        sys.exit(f"error: {action} needs root (sysfs battery controls are root-only)")


def load_cfg():
    """Load the on-disk config; fall back to defaults if it is invalid."""
    try:
        return config.load()
    except config.ConfigError as e:
        print(f"warning: {e}; using defaults", file=sys.stderr)
        return config.default_config()


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_sample(args):
    s = sample_all()
    if not s["bats"]:
        sys.exit("error: no batteries present")
    state = load_state()
    info = integrate(state, s)
    save_state(state)
    if not args.no_log:
        append_log(s)
    if args.verbose:
        print(json.dumps({"sample": s, "integrated": info}, indent=2))


def cmd_status(args):
    state, s = load_state(), sample_all()
    cfg = load_cfg()
    if args.json:
        print(json.dumps(state_json(state, s, cfg), indent=2, sort_keys=True))
    else:
        print(fmt_status(state, s, cfg))


def cmd_report(args):
    state = load_state()
    s = sample_all()
    cfg = load_cfg()
    print(fmt_status(state, s, cfg))
    print()
    print("firmware charge configuration:")
    for b in BATS:
        applied = read_applied(b)
        if applied.get("mode") is None and applied.get("charge_types") is None:
            print(f"  {b}: unreadable (needs root)")
            continue
        print(f"  {b}: " + "  ".join(f"{k}={v}" for k, v in applied.items() if v))
    print()
    deadband = args.deadband if args.deadband is not None else cfg["general"]["deadband_efc"]
    roles, why = policy.decide_roles(policy.efc_by_slot(state), deadband)
    print(f"recommendation: {why}")
    if roles:
        bands = policy.bands_for(cfg, cfg["general"]["active_profile"])
        for b, r in roles.items():
            st, sp = bands.get(r, bands.get("all", (50, 80)))
            print(f"  {b}: {r}  -> charge {st}/{sp}")


def cmd_balance(args):
    state = load_state()
    cfg = load_cfg()
    deadband = args.deadband if args.deadband is not None else cfg["general"]["deadband_efc"]
    roles, why = policy.decide_roles(policy.efc_by_slot(state), deadband)
    print(why)
    if not roles:
        return
    if args.apply:
        require_root("applying charge ceilings")
    bands = policy.bands_for(cfg, cfg["general"]["active_profile"])
    failures = []
    for b, r in roles.items():
        st, sp = bands.get(r, bands.get("all", (50, 80)))
        msg, err = apply_band(b, st, sp, args.bios_password, dry_run=not args.apply)
        print(f"  {err if err else msg}")
        if err:
            failures.append(err)
    if args.apply and not failures:
        state["policy"] = {"ts": now_iso(), "roles": roles, "mode": "balance"}
        save_state(state)
    elif failures:
        sys.exit("error: some ceilings were not applied (see above)")


def cmd_field(args):
    require_root("lifting charge ceilings")
    cfg = load_cfg()
    st, sp = policy.bands_for(cfg, "field")["all"]
    state = load_state()
    failures = []
    for b in BATS:
        if b not in state.get("slots", {}) and not (PS / b).exists():
            continue
        msg, err = apply_band(b, st, sp, args.bios_password, dry_run=False)
        print(f"  {err if err else msg}")
        if err:
            failures.append(err)
    if failures:
        sys.exit("error: field mode not fully applied")
    state["policy"] = {"ts": now_iso(), "roles": {b: "field" for b in BATS}, "mode": "field"}
    save_state(state)
    print("field mode set. Run 'restore' when back at the desk.")


def cmd_restore(args):
    args.apply = True
    cmd_balance(args)


def cmd_reset(args):
    state = load_state()
    if args.slot:
        state.get("slots", {}).pop(args.slot, None)
        state["last"] = None
        print(f"reset counters for {args.slot}")
    else:
        state = {
            "version": 1, "created": now_iso(), "slots": {}, "last": None,
            "discharge_first": {b: 0 for b in BATS}, "sessions": 0, "policy": None,
        }
        print("reset all counters")
    save_state(state)


def main():
    p = argparse.ArgumentParser(
        prog="dell-battery-balance",
        description="Track and equalize wear across a Dell Rugged's two packs.")
    p.add_argument("--deadband", type=float, default=None,
                   help="EFC divergence required before roles flip "
                        "(default: general.deadband_efc from config)")
    p.add_argument("--bios-password", default=os.environ.get("DBB_BIOS_PASSWORD"),
                   help="BIOS admin password, if one is set "
                        "(or set DBB_BIOS_PASSWORD)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("sample", help="take one measurement")
    sp.add_argument("-v", "--verbose", action="store_true")
    sp.add_argument("--no-log", action="store_true", help="update state but skip the CSV")
    sp.set_defaults(func=cmd_sample)

    sp = sub.add_parser("status", help="wear summary")
    sp.add_argument("--json", action="store_true",
                    help="machine-readable output (used by the Plasma applet)")
    sp.set_defaults(func=cmd_status)
    sub.add_parser("report", help="detailed analysis").set_defaults(func=cmd_report)

    sp = sub.add_parser("balance", help="apply the charge-ceiling policy")
    sp.add_argument("--apply", action="store_true",
                    help="actually write to firmware (default is a dry run)")
    sp.set_defaults(func=cmd_balance)

    sub.add_parser("field", help="lift both ceilings for maximum runtime") \
        .set_defaults(func=cmd_field)
    sub.add_parser("restore", help="return to the balancing policy") \
        .set_defaults(func=cmd_restore)

    sp = sub.add_parser("reset", help="clear accumulated counters")
    sp.add_argument("--slot", choices=BATS, help="reset one pack only")
    sp.set_defaults(func=cmd_reset)

    args = p.parse_args()
    args.func(args)

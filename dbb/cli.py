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
"""Command-line surface. Every command reads config, state and a sample the same way."""
import argparse
import copy
import json
import sys
import time
import tomllib
from pathlib import Path

from dbb import VERSION, apply as apply_mod, config as cfg_mod, policy, render
from dbb.state import add_event, append_log, load_state, save_state
from dbb.sysfs import BATS, sample_all
from dbb.wear import integrate

CONFIGURE_CLASS = {"config", "profile-create", "profile-edit", "profile-delete", "reset"}


def die(msg, code=1):
    """sys.exit(str) only auto-prints via the default excepthook; our own
    callers (and the test harness) catch SystemExit directly, so the message
    must be written to stderr explicitly."""
    print(msg, file=sys.stderr)
    sys.exit(code)


def parse_duration(text):
    units = {"m": 1 / 60.0, "h": 1.0, "d": 24.0}
    t = text.strip().lower()
    if not t or t[-1] not in units:
        raise ValueError(f"bad duration {text!r}; use e.g. 90m, 8h, 3d")
    return float(t[:-1]) * units[t[-1]]


def _deep_merge(base, patch):
    """A copy of base with patch's tables merged in (recursively), scalars overwritten."""
    out = copy.deepcopy(base)

    def merge(dst, src):
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                merge(dst[k], v)
            else:
                dst[k] = v

    merge(out, patch)
    return out


def load_config_or_snapshot(state):
    """Read the on-disk config as a delta over the defaults, so a file that
    only overrides one setting still validates on the fields it actually
    touches -- a strict, full-schema load would instead fail on unrelated
    keys the file never mentioned, obscuring the real error."""
    cfg_mod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if cfg_mod.CONFIG_FILE.exists():
            with cfg_mod.CONFIG_FILE.open("rb") as fh:
                raw = tomllib.load(fh)
            cfg = _deep_merge(cfg_mod.default_config(), raw)
        else:
            cfg = cfg_mod.default_config()
        cfg_mod.validate(cfg)
    except (OSError, tomllib.TOMLDecodeError, cfg_mod.ConfigError) as e:
        state["config_error"] = str(e)
        if state.get("config_snapshot"):
            return state["config_snapshot"]
        return cfg_mod.default_config()
    state["config_error"] = None
    state["config_snapshot"] = cfg
    return cfg


def _save_config(cfg, state, what):
    try:
        cfg_mod.save(cfg)
    except cfg_mod.ConfigError as e:
        die(f"error: {what}: {e}")
    state["config_snapshot"] = cfg
    state["config_error"] = None


def _apply(cfg, state, sample, res):
    pw = apply_mod.read_password(cfg)
    r = apply_mod.apply_bands(res.bands, state, pw)
    if r["mismatch"] and not cfg["general"]["firmware_write_needs_reboot"]:
        cfg["general"]["firmware_write_needs_reboot"] = True
        _save_config(cfg, state, "recording firmware mismatch")
    state["policy"] = {"ts": time.time(), "profile": res.profile, "roles": res.roles,
                       "mode": res.profile_type}
    return r


def _switch_and_apply(cfg, state, name, reason, one_off_hours=None):
    now = time.time()
    policy.switch_profile(cfg, state, name, now, reason)
    if one_off_hours is not None:
        state["one_off_revert_hours"] = one_off_hours
    _save_config(cfg, state, "switching profile")
    sample = sample_all()
    res = policy.resolve(cfg, state, sample, now)
    r = _apply(cfg, state, sample, res)
    save_state(state)
    print(f"profile -> {name}: " + ", ".join(f"{s}={b[0]}/{b[1]}" for s, b in sorted(res.bands.items())))
    for s, e in r["errors"].items():
        print(f"  {s}: {e}", file=sys.stderr)
    return 0 if not r["errors"] else 2


# ----------------------------------------------------------------- commands

def cmd_tick(args):
    state = load_state()
    sample = sample_all()
    if not sample["bats"]:
        save_state(state)
        return
    integrate(state, sample)
    append_log(sample)
    cfg = load_config_or_snapshot(state)
    now = time.time()
    res = policy.resolve(cfg, state, sample, now)
    if res.revert:
        policy.switch_profile(cfg, state, res.revert["to"], now, f"revert: {res.revert['reason']}")
        _save_config(cfg, state, "auto-revert")
    if cfg["general"]["auto_balance"] or res.revert:
        _apply(cfg, state, sample, res)
    save_state(state)


def cmd_sample(args):
    state = load_state()
    s = sample_all()
    if not s["bats"]:
        die("error: no batteries present")
    integrate(state, s)
    save_state(state)
    if not args.no_log:
        append_log(s)


def _view():
    state = load_state()
    cfg = load_config_or_snapshot(state)
    return state, cfg, sample_all()


def cmd_status(args):
    state, cfg, s = _view()
    if args.json:
        print(json.dumps(render.state_json(state, s, cfg), indent=2, sort_keys=True))
    else:
        print(render.fmt_status(state, s, cfg))


def cmd_report(args):
    state, cfg, s = _view()
    print(render.fmt_status(state, s, cfg))
    print()
    print("events:")
    for e in state.get("events", [])[-20:]:
        print(f"  {e['ts']}  {e['kind']:9} {e['detail']}")
    print()
    print("firmware read-back:")
    for slot, rec in sorted(state.get("firmware", {}).items()):
        print(f"  {slot}: requested={rec['requested']} observed={rec['observed']} at {rec['ts']}"
              + (f"  ERROR {rec['error']}" if rec.get("error") else ""))


def cmd_balance(args):
    state, cfg, s = _view()
    res = policy.resolve(cfg, state, s, time.time())
    print(res.why)
    for slot, b in sorted(res.bands.items()):
        print(f"  {'[dry-run] ' if not args.apply else ''}{slot} -> {b[0]}/{b[1]}")
    if args.apply:
        r = _apply(cfg, state, s, res)
        save_state(state)
        if r["errors"]:
            die("error: " + "; ".join(f"{k}: {v}" for k, v in r["errors"].items()))


def cmd_profile_list(args):
    state, cfg, _ = _view()
    active = cfg["general"]["active_profile"]
    for name in sorted(cfg["profiles"]):
        p = cfg["profiles"][name]
        mark = "*" if name == active else " "
        print(f"{mark} {name:12} {cfg_mod.profile_type(p):9} {p['label']}")


def cmd_profile_show(args):
    state, cfg, _ = _view()
    if args.name not in cfg["profiles"]:
        die(f"error: no profile {args.name!r}")
    one = {"general": cfg["general"], "profiles": {args.name: cfg["profiles"][args.name]}}
    text = cfg_mod.emit(one)
    start = text.index(f"[profiles.{args.name}]")
    rest = text[start:]
    nxt = rest.find("\n[profiles.", 1)
    print(rest if nxt == -1 else rest[:nxt + 1], end="")


def cmd_profile_set(args):
    state, cfg, _ = _view()
    if args.name not in cfg["profiles"]:
        die(f"error: no profile {args.name!r}")
    hours = parse_duration(args.for_) if args.for_ else None
    sys.exit(_switch_and_apply(cfg, state, args.name, "cli", hours))


def cmd_profile_create(args):
    state, cfg, _ = _view()
    if args.name in cfg["profiles"]:
        die(f"error: profile {args.name!r} exists")
    if args.from_ not in cfg["profiles"]:
        die(f"error: no profile {args.from_!r}")
    cfg["profiles"][args.name] = json.loads(json.dumps(cfg["profiles"][args.from_]))
    cfg["profiles"][args.name]["label"] = args.name
    _save_config(cfg, state, "creating profile")
    save_state(state)


def cmd_profile_edit(args):
    state, cfg, _ = _view()
    if args.name not in cfg["profiles"]:
        die(f"error: no profile {args.name!r}")
    for kv in args.assignments:
        k, _, v = kv.partition("=")
        cfg_mod.set_dotted(cfg, f"profiles.{args.name}.{k}", v)
    _save_config(cfg, state, "editing profile")
    save_state(state)


def cmd_profile_delete(args):
    state, cfg, _ = _view()
    if args.name == "daily":
        die("error: daily cannot be deleted")
    if args.name == cfg["general"]["active_profile"]:
        die("error: cannot delete the active profile")
    if args.name not in cfg["profiles"]:
        die(f"error: no profile {args.name!r}")
    del cfg["profiles"][args.name]
    if cfg["general"]["previous_profile"] == args.name:
        cfg["general"]["previous_profile"] = "daily"
    _save_config(cfg, state, "deleting profile")
    save_state(state)


def cmd_config_get(args):
    state, cfg, _ = _view()
    if not args.key:
        print(cfg_mod.emit(cfg), end="")
        return
    node = cfg
    for p in args.key.split("."):
        if not isinstance(node, dict) or p not in node:
            die(f"error: no key {args.key!r}")
        node = node[p]
    if isinstance(node, dict):
        print(cfg_mod.emit(node) if "general" in node else json.dumps(node, indent=2))
    else:
        print(node if not isinstance(node, bool) else str(node).lower())


def cmd_config_set(args):
    state, cfg, _ = _view()
    for kv in args.assignments:
        k, _, v = kv.partition("=")
        cfg_mod.set_dotted(cfg, k, v)
    _save_config(cfg, state, "config set")
    save_state(state)


def cmd_config_validate(args):
    try:
        cfg_mod.load(args.path)
    except cfg_mod.ConfigError as e:
        die(f"error: {e}")
    print("ok")


def cmd_config_apply(args):
    # A patch file need not repeat every general/profile key: merge it onto
    # the live config so validate() reports the field that is actually wrong,
    # not unrelated keys the patch never mentioned.
    state, cfg, _ = _view()
    try:
        with open(args.path, "rb") as fh:
            patch = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as e:
        die(f"error: {args.path}: {e}")
    merged = _deep_merge(cfg, patch)
    _save_config(merged, state, "config apply")
    add_event(state, "config", f"applied from {Path(args.path).name}")
    save_state(state)
    print("ok")


def cmd_field(args):
    state, cfg, _ = _view()
    sys.exit(_switch_and_apply(cfg, state, "field", "cli"))


def cmd_restore(args):
    state, cfg, _ = _view()
    sys.exit(_switch_and_apply(cfg, state, cfg["general"]["previous_profile"], "cli restore"))


def cmd_reset(args):
    state = load_state()
    if args.slot:
        state.get("slots", {}).pop(args.slot, None)
        state["last"] = None
        add_event(state, "reset", args.slot)
    elif args.all:
        from dbb.state import new_state
        state = new_state()
    else:
        die("error: give --slot SLOT or --all")
    save_state(state)


# --------------------------------------------------------------------- main

def build_parser():
    p = argparse.ArgumentParser(prog="dell-battery-balance",
                                description="Wear tracking and charge-ceiling balancing for a Dell Rugged's packs.")
    p.add_argument("--version", action="version", version=VERSION)
    p.add_argument("--polkit-class", choices=("control", "configure"), help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("tick", help="sample, evaluate reverts, apply if auto_balance").set_defaults(func=cmd_tick, cls="control")
    sp = sub.add_parser("sample", help="one measurement, no policy")
    sp.add_argument("--no-log", action="store_true")
    sp.set_defaults(func=cmd_sample, cls="control")
    sp = sub.add_parser("status", help="wear summary")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_status, cls="control")
    sub.add_parser("report", help="detailed analysis").set_defaults(func=cmd_report, cls="control")
    sp = sub.add_parser("balance", help="resolve and optionally apply")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_balance, cls="control")

    pr = sub.add_parser("profile", help="manage profiles").add_subparsers(dest="pcmd", required=True)
    pr.add_parser("list").set_defaults(func=cmd_profile_list, cls="control")
    sp = pr.add_parser("show"); sp.add_argument("name"); sp.set_defaults(func=cmd_profile_show, cls="control")
    sp = pr.add_parser("set"); sp.add_argument("name"); sp.add_argument("--for", dest="for_", metavar="DURATION")
    sp.set_defaults(func=cmd_profile_set, cls="control")
    sp = pr.add_parser("create"); sp.add_argument("name"); sp.add_argument("--from", dest="from_", required=True)
    sp.set_defaults(func=cmd_profile_create, cls="profile-create")
    sp = pr.add_parser("edit"); sp.add_argument("name"); sp.add_argument("assignments", nargs="+", metavar="key=value")
    sp.set_defaults(func=cmd_profile_edit, cls="profile-edit")
    sp = pr.add_parser("delete"); sp.add_argument("name"); sp.set_defaults(func=cmd_profile_delete, cls="profile-delete")

    cf = sub.add_parser("config", help="general settings").add_subparsers(dest="ccmd", required=True)
    sp = cf.add_parser("get"); sp.add_argument("key", nargs="?"); sp.set_defaults(func=cmd_config_get, cls="control")
    sp = cf.add_parser("set"); sp.add_argument("assignments", nargs="+", metavar="key=value"); sp.set_defaults(func=cmd_config_set, cls="config")
    sp = cf.add_parser("validate"); sp.add_argument("path"); sp.set_defaults(func=cmd_config_validate, cls="control")
    sp = cf.add_parser("apply"); sp.add_argument("path"); sp.set_defaults(func=cmd_config_apply, cls="config")

    sub.add_parser("field", help="alias: profile set field").set_defaults(func=cmd_field, cls="control")
    sub.add_parser("restore", help="alias: profile set <previous>").set_defaults(func=cmd_restore, cls="control")
    sp = sub.add_parser("reset", help="clear counters")
    sp.add_argument("--slot", choices=BATS); sp.add_argument("--all", action="store_true")
    sp.set_defaults(func=cmd_reset, cls="reset")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.polkit_class == "control" and args.cls in CONFIGURE_CLASS:
        print("error: this command needs the configure action (dbb-configure), not control",
              file=sys.stderr)
        sys.exit(3)
    args.func(args)

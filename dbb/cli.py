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
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dbb import VERSION, apply as apply_mod, config as cfg_mod, policy, registry, render
from dbb.state import add_event, append_log, load_state, now_iso, save_state
from dbb.sysfs import BATS, sample_all
from dbb.wear import efc, integrate

CONFIGURE_CLASS = {"config", "profile-create", "profile-edit", "profile-delete", "reset", "pack-admin"}

PROFILE_SUBCOMMANDS = ("list", "show", "set", "create", "edit", "delete")


def expand_profile_shortcut(argv):
    """`profile <name>` means `profile set <name>`: it is the first thing
    people type, and argparse's "invalid choice" reply taught nobody the
    real spelling. Only the command position is rewritten (after the
    wrappers' `--polkit-class X --` prefix), never a later positional, so a
    pack or profile literally named "profile" is untouched."""
    out = list(argv)
    i = 0
    while i < len(out):
        tok = out[i]
        if tok == "--":
            i += 1
            break
        if tok == "--polkit-class":
            i += 2
            continue
        if tok.startswith("--polkit-class="):
            i += 1
            continue
        break
    if (i + 1 < len(out) and out[i] == "profile"
            and out[i + 1] not in PROFILE_SUBCOMMANDS
            and not out[i + 1].startswith("-")):
        out.insert(i + 1, "set")
    return out


_NEGATIVE_FOR_VALUE = re.compile(r"^-\d")


def rejoin_negative_for_value(argv):
    """argparse treats a token starting with '-' as another option rather
    than the previous option's value, so `--for -2h` fails with a generic
    "expected one argument" before parse_duration ever sees it and gets a
    chance to reject it as non-positive. Rejoin only tokens that look like
    a negative duration (-<digit>...) into `--for=<value>`."""
    out = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--for" and i + 1 < len(argv) and _NEGATIVE_FOR_VALUE.match(argv[i + 1]):
            out.append(f"--for={argv[i + 1]}")
            i += 2
            continue
        out.append(tok)
        i += 1
    return out


def _one_off_hours(args):
    """--stay -> 0.0 (no automatic revert this switch); --for -> hours;
    neither -> None (the profile's own [revert] table applies)."""
    if getattr(args, "stay", False):
        return 0.0
    if getattr(args, "for_", None):
        try:
            return parse_duration(args.for_)
        except ValueError as e:
            die(f"error: {e}")
    return None


def _require_profile(cfg, name):
    if name not in cfg["profiles"]:
        die(f"error: no profile {name!r}. Profiles: {', '.join(sorted(cfg['profiles']))}")


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
    hours = float(t[:-1]) * units[t[-1]]
    if hours <= 0:
        raise ValueError(f"bad duration {text!r}; must be positive")
    return hours


def load_config_or_snapshot(state):
    try:
        cfg = cfg_mod.load()
    except cfg_mod.ConfigError as e:
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
    state["policy"] = {"ts": now_iso(), "profile": res.profile, "roles": res.roles,
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
        # Both packs are out -- close their tenures now (instead of leaving
        # them open) and record this empty sample as `last`, so a reinsertion
        # (even both packs, swapped) is measured as a fresh "insert" from
        # this moment rather than a discontinuity dated from before removal.
        for b in BATS:
            registry.note_absent(state, b, sample["ts"])
        state["last"] = sample
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
        for b in BATS:
            registry.note_absent(state, b, s["ts"])
        state["last"] = s
        save_state(state)
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


def _iso_or_dash(ts):
    if ts is None:
        return "-"
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


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
    print()
    print(f"{'tenure':7} {'slot':5} {'pack':5} {'start':20} {'end':20} {'EFC':>6} {'discharged Wh':>14}")
    for t in sorted(state.get("tenures", []), key=lambda x: x["id"], reverse=True):
        pack = t["pack"] or "?"
        start = _iso_or_dash(t.get("start_ts"))
        end = _iso_or_dash(t.get("end_ts"))
        nominal = (s["bats"].get(t["slot"]) or {}).get("voltage_min_design_uv")
        dwh = render.wh(t["discharge_uah"], nominal)
        print(f"{t['id']:<7} {t['slot']:5} {pack:5} {start:20} {end:20} {efc(t):>6.2f} {dwh:>14.1f}")


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
    _require_profile(cfg, args.name)
    one = {"general": cfg["general"], "profiles": {args.name: cfg["profiles"][args.name]}}
    text = cfg_mod.emit(one)
    start = text.index(f"[profiles.{args.name}]")
    rest = text[start:]
    nxt = rest.find("\n[profiles.", 1)
    print(rest if nxt == -1 else rest[:nxt + 1], end="")


def cmd_profile_set(args):
    state, cfg, _ = _view()
    _require_profile(cfg, args.name)
    sys.exit(_switch_and_apply(cfg, state, args.name, "cli", _one_off_hours(args)))


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
    _require_profile(cfg, args.name)
    for kv in args.assignments:
        if "=" not in kv:
            die(f"error: expected key=value, got {kv!r}")
        k, _, v = kv.partition("=")
        try:
            cfg_mod.set_dotted(cfg, f"profiles.{args.name}.{k}", v)
        except cfg_mod.ConfigError as e:
            die(f"error: {e}")
    _save_config(cfg, state, "editing profile")
    save_state(state)


def cmd_profile_delete(args):
    state, cfg, _ = _view()
    if args.name == "daily":
        die("error: daily cannot be deleted")
    if args.name == cfg["general"]["active_profile"]:
        die("error: cannot delete the active profile")
    _require_profile(cfg, args.name)
    del cfg["profiles"][args.name]
    if cfg["general"]["previous_profile"] == args.name:
        cfg["general"]["previous_profile"] = "daily"
    _save_config(cfg, state, "deleting profile")
    save_state(state)


def cmd_config_get(args):
    state, cfg, _ = _view()
    node = cfg
    if args.key:
        for p in args.key.split("."):
            if not isinstance(node, dict) or p not in node:
                die(f"error: no key {args.key!r}")
            node = node[p]
    if args.json:
        print(json.dumps(node, indent=2, sort_keys=True))
    elif not args.key:
        print(cfg_mod.emit(cfg), end="")
    elif isinstance(node, dict):
        print(cfg_mod.emit(node) if "general" in node else json.dumps(node, indent=2))
    else:
        print(node if not isinstance(node, bool) else str(node).lower())


def _load_candidate(args):
    """The file named on the command line, as TOML or (--json) JSON. Never
    falls back to defaults: a missing path must not replace the real config."""
    try:
        if args.json:
            return cfg_mod.load_json(args.path)
        return cfg_mod.load(args.path, must_exist=True)
    except cfg_mod.ConfigError as e:
        die(f"error: {e}")


def cmd_config_set(args):
    state, cfg, _ = _view()
    for kv in args.assignments:
        if "=" not in kv:
            die(f"error: expected key=value, got {kv!r}")
        k, _, v = kv.partition("=")
        try:
            cfg_mod.set_dotted(cfg, k, v)
        except cfg_mod.ConfigError as e:
            die(f"error: {e}")
    _save_config(cfg, state, "config set")
    save_state(state)


def cmd_config_validate(args):
    _load_candidate(args)
    print("ok")


def cmd_config_apply(args):
    # The applied file replaces the whole config -- same strict, full-schema
    # semantics as config.load()/config validate, not a partial overlay.
    state = load_state()
    cfg = _load_candidate(args)
    _save_config(cfg, state, "config apply")
    add_event(state, "config", f"applied from {Path(args.path).name}")
    save_state(state)
    print("ok")


def cmd_field(args):
    state, cfg, _ = _view()
    sys.exit(_switch_and_apply(cfg, state, "field", "cli", _one_off_hours(args)))


def cmd_restore(args):
    state, cfg, _ = _view()
    sys.exit(_switch_and_apply(cfg, state, cfg["general"]["previous_profile"], "cli restore"))


def _registry_op(fn, *a, needs_cfg=False, **kw):
    """Load state, apply a pure registry edit, save. Never samples/integrates:
    these commands only change identity bookkeeping, not wear counters.

    needs_cfg=True also loads config, so `now`/`bench_temp_c` can be passed
    to registry ops (assign/same) that carry bench calendar-aging forward.
    """
    state = load_state()
    if needs_cfg:
        cfg = load_config_or_snapshot(state)
        kw.setdefault("now", time.time())
        kw.setdefault("bench_temp_c", cfg["general"]["bench_temp_c"])
    try:
        fn(state, *a, **kw)
    except registry.RegistryError as e:
        die(f"error: {e}")
    save_state(state)


def cmd_pack_list(args):
    state, cfg, _ = _view()
    now = time.time()
    rows = registry.all_packs(state, now, cfg["general"]["bench_temp_c"])
    if not rows:
        print("no packs registered yet; run 'pack new SLOT NAME' to name the inserted ones")
    for r in rows:
        where = r["in_slot"] or ("retired" if r["retired"] else "bench")
        extra = f"  out {r['bench_hours']:.0f}h at {r['removed_at_soc']}%" if (where == "bench" and r["removed_at_soc"] is not None) else ""
        print(f"{r['name']:16} EFC {r['efc']:6.2f}  cal {r['calendar_score']:7.1f}  {where:8}{extra}")
    hint = registry.rotation_hint(state, cfg["general"]["deadband_efc"], now, cfg["general"]["bench_temp_c"])
    if hint:
        print(f"swap in next: {hint['swap_in']} for {hint['replace']} ({hint['behind_by_efc']:.2f} EFC behind)")
    for slot, q in sorted(state.get("pending", {}).items()):
        print(f"PENDING {slot}: guess={q['guess']} ({q['reason']}"
              + (f", was {q['previous_pack']}" if q.get("previous_pack") else "") + ")")


def cmd_pack_assign(args):
    _registry_op(registry.assign, args.slot, args.name, needs_cfg=True)


def cmd_pack_new(args):
    _registry_op(registry.assign, args.slot, args.name, new=True, needs_cfg=True)


def cmd_pack_same(args):
    _registry_op(registry.same, args.slot, needs_cfg=True)


def cmd_pack_reassign(args):
    _registry_op(registry.reassign, args.tenure_id, args.name)


def cmd_pack_rename(args):
    _registry_op(registry.rename_pack, args.old, args.new)


def cmd_pack_retire(args):
    _registry_op(registry.retire_pack, args.name)


def cmd_pack_unretire(args):
    _registry_op(registry.unretire_pack, args.name)


def cmd_reset(args):
    state = load_state()
    if args.slot:
        registry.close_tenure(state, args.slot, time.time())
        state["last"] = None
        add_event(state, "reset", args.slot)
    elif args.pack:
        if args.pack not in state["packs"]:
            die(f"error: unknown pack {args.pack}")
        where = registry.packs_in_slots(state).get(args.pack)
        if where:
            die(f"error: pack {args.pack} is in {where}; remove it before resetting")
        state["tenures"] = [t for t in state["tenures"] if t["pack"] != args.pack]
        del state["packs"][args.pack]
        for q in state.get("pending", {}).values():
            if q.get("previous_pack") == args.pack:
                q["previous_pack"] = None
        add_event(state, "reset", f"pack {args.pack} and its tenures deleted")
    else:
        assert args.all   # argparse's mutually-exclusive required group guarantees one of the three
        from dbb.state import new_state
        old_next_event_id = state.get("next_event_id", 1)
        state = new_state()
        # Event ids are never reused (Task 1 interface contract), so a full
        # reset must not restart the sequence at 1 -- the applet's KConfig
        # high-water mark would then skip every subsequent event forever.
        state["next_event_id"] = old_next_event_id
        add_event(state, "reset", "all counters, packs and tenures")
    save_state(state)


# --------------------------------------------------------------------- main

def build_parser():
    p = argparse.ArgumentParser(prog="dell-battery-balance", allow_abbrev=False,
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
    sp = pr.add_parser("set"); sp.add_argument("name")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--for", dest="for_", metavar="DURATION",
                   help="revert after this long (90m, 8h, 3d), replacing the profile's own triggers for this switch")
    g.add_argument("--stay", action="store_true", help="no automatic revert for this switch")
    sp.set_defaults(func=cmd_profile_set, cls="control")
    sp = pr.add_parser("create"); sp.add_argument("name"); sp.add_argument("--from", dest="from_", required=True)
    sp.set_defaults(func=cmd_profile_create, cls="profile-create")
    sp = pr.add_parser("edit"); sp.add_argument("name"); sp.add_argument("assignments", nargs="+", metavar="key=value")
    sp.set_defaults(func=cmd_profile_edit, cls="profile-edit")
    sp = pr.add_parser("delete"); sp.add_argument("name"); sp.set_defaults(func=cmd_profile_delete, cls="profile-delete")

    cf = sub.add_parser("config", help="general settings").add_subparsers(dest="ccmd", required=True)
    sp = cf.add_parser("get"); sp.add_argument("key", nargs="?")
    sp.add_argument("--json", action="store_true", help="print the value as JSON (what the applet's config dialog reads)")
    sp.set_defaults(func=cmd_config_get, cls="control")
    sp = cf.add_parser("set"); sp.add_argument("assignments", nargs="+", metavar="key=value"); sp.set_defaults(func=cmd_config_set, cls="config")
    sp = cf.add_parser("validate"); sp.add_argument("--json", action="store_true", help="the file is JSON with the TOML's shape"); sp.add_argument("path")
    sp.set_defaults(func=cmd_config_validate, cls="config")
    sp = cf.add_parser("apply"); sp.add_argument("--json", action="store_true", help="the file is JSON with the TOML's shape"); sp.add_argument("path")
    sp.set_defaults(func=cmd_config_apply, cls="config")

    pk = sub.add_parser("pack", help="physical pack registry").add_subparsers(dest="kcmd", required=True)
    pk.add_parser("list").set_defaults(func=cmd_pack_list, cls="control")
    sp = pk.add_parser("assign"); sp.add_argument("slot", choices=BATS); sp.add_argument("name")
    sp.set_defaults(func=cmd_pack_assign, cls="control")
    sp = pk.add_parser("new"); sp.add_argument("slot", choices=BATS); sp.add_argument("name")
    sp.set_defaults(func=cmd_pack_new, cls="control")
    sp = pk.add_parser("same"); sp.add_argument("slot", choices=BATS)
    sp.set_defaults(func=cmd_pack_same, cls="control")
    sp = pk.add_parser("reassign"); sp.add_argument("tenure_id", type=int, metavar="tenure-id"); sp.add_argument("name")
    sp.set_defaults(func=cmd_pack_reassign, cls="pack-admin")
    sp = pk.add_parser("rename"); sp.add_argument("old"); sp.add_argument("new")
    sp.set_defaults(func=cmd_pack_rename, cls="pack-admin")
    sp = pk.add_parser("retire"); sp.add_argument("name")
    sp.set_defaults(func=cmd_pack_retire, cls="pack-admin")
    sp = pk.add_parser("unretire"); sp.add_argument("name")
    sp.set_defaults(func=cmd_pack_unretire, cls="pack-admin")

    sp = sub.add_parser("field", help="alias: profile set field")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--for", dest="for_", metavar="DURATION")
    g.add_argument("--stay", action="store_true")
    sp.set_defaults(func=cmd_field, cls="control")
    sub.add_parser("restore", help="alias: profile set <previous>").set_defaults(func=cmd_restore, cls="control")
    sp = sub.add_parser("reset", help="clear counters")
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--slot", choices=BATS); g.add_argument("--pack", metavar="NAME"); g.add_argument("--all", action="store_true")
    sp.set_defaults(func=cmd_reset, cls="reset")
    return p


def main(argv=None):
    raw = sys.argv[1:] if argv is None else list(argv)
    # argparse silently takes the LAST occurrence of a repeated option, so a
    # caller who can append arguments after the wrapper's fixed prefix (e.g.
    # `dbb-control --polkit-class configure ...`) could otherwise override
    # the class the wrapper set. Refuse outright rather than let the last
    # one win. Stop scanning at a bare "--": that separator is how the
    # wrappers themselves pin the class before user-controlled args begin,
    # and argparse treats anything after it as positional.
    count = 0
    for tok in raw:
        if tok == "--":
            break
        if tok == "--polkit-class" or tok.startswith("--polkit-class="):
            count += 1
    if count > 1:
        print("error: --polkit-class may be given once", file=sys.stderr)
        sys.exit(3)
    args = build_parser().parse_args(rejoin_negative_for_value(expand_profile_shortcut(raw)))
    if args.polkit_class == "control" and args.cls in CONFIGURE_CLASS:
        print("error: this command needs the configure action (dbb-configure), not control",
              file=sys.stderr)
        sys.exit(3)
    args.func(args)

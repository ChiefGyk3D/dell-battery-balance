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
"""System-wide configuration: /etc/dell-battery-balance/config.toml."""
import copy
import json
import os
import re
import tomllib
from pathlib import Path

from dbb.sysfs import START_MIN, START_MAX, STOP_MIN, STOP_MAX

CONFIG_DIR = Path(os.environ.get("DBB_CONFIG_DIR", "/etc/dell-battery-balance"))
CONFIG_FILE = CONFIG_DIR / "config.toml"

SLOTS = ("BAT0", "BAT1")
ROLES = ("neutral", "protect", "work")
PROFILE_NAME_RE = re.compile(r"^[a-z0-9_-]{1,32}$")

GENERAL_KEYS = {
    "active_profile": str, "previous_profile": str, "deadband_efc": float,
    "auto_balance": bool, "sample_interval_s": int, "bench_temp_c": float,
    "bios_password_file": str, "firmware_write_needs_reboot": bool,
}
PROFILE_KEYS = {"label", "description", "balancing", "bands", "revert", "pins"}
REVERT_KEYS = {"after_hours", "on_ac_hours", "to"}


class ConfigError(ValueError):
    pass


def default_config():
    return copy.deepcopy({
        "general": {
            "active_profile": "daily", "previous_profile": "daily",
            "deadband_efc": 0.5, "auto_balance": True, "sample_interval_s": 120,
            "bench_temp_c": 25.0, "bios_password_file": "",
            "firmware_write_needs_reboot": False,
        },
        "profiles": {
            "daily": {"label": "Daily", "description": "Docked / desk. Wear balancing on.",
                      "balancing": True,
                      "bands": {"neutral": [50, 80], "protect": [50, 60], "work": [80, 90]}},
            "field": {"label": "Field / Conference",
                      "description": "Maximum runtime. Wear protection off.",
                      "balancing": False, "bands": {"all": [90, 100]},
                      "revert": {"after_hours": 72, "on_ac_hours": 12, "to": "previous"}},
            "travel": {"label": "Travel", "description": "Reserve without the 100% float.",
                       "balancing": True,
                       "bands": {"neutral": [70, 90], "protect": [60, 80], "work": [80, 95]}},
            "storage": {"label": "Storage", "description": "Long idle. Least wear.",
                        "balancing": False, "bands": {"all": [50, 55]}},
        },
    })


def profile_type(profile):
    return "fixed" if "all" in profile.get("bands", {}) else "balancing"


# ---------------------------------------------------------------- validation

def _band(path, v):
    if (not isinstance(v, list) or len(v) != 2
            or not all(isinstance(x, int) and not isinstance(x, bool) for x in v)):
        raise ConfigError(f"{path}: must be [start, stop] integers")
    start, stop = v
    if not START_MIN <= start <= START_MAX:
        raise ConfigError(f"{path}: start {start} outside {START_MIN}-{START_MAX}")
    if not STOP_MIN <= stop <= STOP_MAX:
        raise ConfigError(f"{path}: stop {stop} outside {STOP_MIN}-{STOP_MAX}")
    if start >= stop:
        raise ConfigError(f"{path}: start must be below stop")


def _typed(path, v, t):
    if t is float:
        ok = isinstance(v, (int, float)) and not isinstance(v, bool)
    elif t is int:
        ok = isinstance(v, int) and not isinstance(v, bool)
    else:
        ok = isinstance(v, t)
    if not ok:
        raise ConfigError(f"{path}: expected {t.__name__}")


def validate(cfg):
    unknown_top = set(cfg) - {"general", "profiles"}
    if unknown_top:
        key = sorted(unknown_top)[0]
        raise ConfigError(f"{key}: unknown top-level key")
    g = cfg.get("general", {})
    for k in g:
        if k not in GENERAL_KEYS:
            raise ConfigError(f"general.{k}: unknown key")
    for k, t in GENERAL_KEYS.items():
        if k not in g:
            raise ConfigError(f"general.{k}: missing")
        _typed(f"general.{k}", g[k], t)
    if g["deadband_efc"] < 0:
        raise ConfigError("general.deadband_efc: must be >= 0")

    profiles = cfg.get("profiles", {})
    if "daily" not in profiles:
        raise ConfigError("profiles.daily: required")
    for name, p in profiles.items():
        base = f"profiles.{name}"
        if not PROFILE_NAME_RE.match(name):
            raise ConfigError(f"{base}: bad profile name {name!r}")
        for k in p:
            if k not in PROFILE_KEYS:
                raise ConfigError(f"{base}.{k}: unknown key")
        for k in ("label", "balancing", "bands"):
            if k not in p:
                raise ConfigError(f"{base}.{k}: missing")
        _typed(f"{base}.label", p["label"], str)
        _typed(f"{base}.description", p.get("description", ""), str)
        _typed(f"{base}.balancing", p["balancing"], bool)
        bands = p["bands"]
        if "all" in bands:
            if set(bands) != {"all"}:
                raise ConfigError(f"{base}.bands: 'all' cannot be mixed with role bands")
            if p["balancing"]:
                raise ConfigError(f"{base}.balancing: must be false for a fixed profile")
            _band(f"{base}.bands.all", bands["all"])
        else:
            for r in ROLES:
                if r not in bands:
                    raise ConfigError(f"{base}.bands.{r}: missing")
                _band(f"{base}.bands.{r}", bands[r])
            if set(bands) - set(ROLES):
                raise ConfigError(f"{base}.bands: unknown roles {sorted(set(bands) - set(ROLES))}")
            if not p["balancing"]:
                raise ConfigError(f"{base}.balancing: must be true for a balancing profile")
        rv = p.get("revert")
        if rv is not None:
            for k in rv:
                if k not in REVERT_KEYS:
                    raise ConfigError(f"{base}.revert.{k}: unknown key")
            if "after_hours" not in rv and "on_ac_hours" not in rv:
                raise ConfigError(f"{base}.revert: needs after_hours and/or on_ac_hours")
            for k in ("after_hours", "on_ac_hours"):
                if k in rv:
                    _typed(f"{base}.revert.{k}", rv[k], float)
                    if rv[k] <= 0:
                        raise ConfigError(f"{base}.revert.{k}: must be > 0")
            to = rv.get("to", "previous")
            if to != "previous" and to not in profiles:
                raise ConfigError(f"{base}.revert.to: unknown profile {to!r}")
        for slot, pin in p.get("pins", {}).items():
            pb = f"{base}.pins.{slot}"
            if slot not in SLOTS:
                raise ConfigError(f"{pb}: unknown slot")
            if not isinstance(pin, dict):
                raise ConfigError(f"{pb}: must be a table")
            has_role, has_band = "role" in pin, ("start" in pin or "stop" in pin)
            if has_role == has_band:
                raise ConfigError(f"{pb}: exactly one of role or start/stop")
            if has_role:
                if set(pin) != {"role"} or pin["role"] not in ROLES:
                    raise ConfigError(f"{pb}.role: must be one of {ROLES}")
            else:
                if set(pin) != {"start", "stop"}:
                    raise ConfigError(f"{pb}: needs both start and stop")
                _band(pb, [pin["start"], pin["stop"]])
    for k in ("active_profile", "previous_profile"):
        if g[k] not in profiles:
            raise ConfigError(f"general.{k}: unknown profile {g[k]!r}")


# --------------------------------------------------------------- load / emit

def load(path=CONFIG_FILE, must_exist=False):
    path = Path(path)
    try:
        exists = path.exists()
    except OSError as e:
        # An unsearchable directory is not "no config": say so, so the
        # caller records a config_error instead of silently defaulting.
        raise ConfigError(f"{path}: {e}") from e
    if not exists:
        if must_exist:
            raise ConfigError(f"{path}: not found or unreadable")
        return default_config()
    try:
        with path.open("rb") as fh:
            cfg = tomllib.load(fh)
    except OSError as e:
        if must_exist:
            raise ConfigError(f"{path}: not found or unreadable") from e
        raise ConfigError(f"{path}: {e}") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e
    validate(cfg)
    return cfg


def load_json(path):
    """A candidate config as a JSON document with the TOML's exact shape.

    The applet's config dialog submits these so that only Python ever emits
    TOML. Same strict validation as load(); the path is named on any I/O or
    parse error so the dialog can show it."""
    path = Path(path)
    try:
        with path.open("rb") as fh:
            cfg = json.load(fh)
    except OSError as e:
        raise ConfigError(f"{path}: not found or unreadable") from e
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ConfigError(f"{path}: {e}") from e
    if not isinstance(cfg, dict):
        raise ConfigError(f"{path}: top level must be an object")
    validate(cfg)
    return cfg


def _val(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        s = repr(v)
        return s if ("." in s or "e" in s) else s + ".0"
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, list):
        return "[" + ", ".join(_val(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {_val(x)}" for k, x in v.items()) + " }"
    raise ConfigError(f"cannot emit {type(v).__name__}")


def emit(cfg):
    out = ["[general]"]
    for k in GENERAL_KEYS:
        if k in cfg["general"]:
            out.append(f"{k} = {_val(cfg['general'][k])}")
    for name in sorted(cfg["profiles"]):
        p = cfg["profiles"][name]
        out += ["", f"[profiles.{name}]"]
        for k in ("label", "description", "balancing"):
            if k in p:
                out.append(f"{k} = {_val(p[k])}")
        for k in sorted(p.get("bands", {})):
            out.append(f"bands.{k} = {_val(p['bands'][k])}")
        for k in ("after_hours", "on_ac_hours", "to"):
            if k in p.get("revert", {}):
                out.append(f"revert.{k} = {_val(p['revert'][k])}")
        for slot in sorted(p.get("pins", {})):
            out.append(f"pins.{slot} = {_val(p['pins'][slot])}")
    return "\n".join(out) + "\n"


def save(cfg, path=CONFIG_FILE):
    validate(cfg)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        # Rename the backup into place rather than opening it for writing: a
        # .bak left root-owned by a stray `sudo` run would otherwise raise
        # PermissionError for the service account on every later save.
        bak = path.with_name(path.name + ".bak")
        bak_tmp = path.with_name(path.name + ".bak.tmp")
        bak_tmp.write_bytes(path.read_bytes())
        os.replace(bak_tmp, bak)
        try:
            os.chmod(bak, 0o664)
        except OSError:
            pass
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(emit(cfg))
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o664)
    except OSError:
        pass


def _resolve_type(key):
    """Resolve the expected type for a dotted key path, or None if unknown."""
    parts = key.split(".")
    if len(parts) == 2 and parts[0] == "general":
        return GENERAL_KEYS.get(parts[1])
    if len(parts) >= 3 and parts[0] == "profiles":
        name = parts[1]
        rest = parts[2:]
        if len(rest) == 1:
            k = rest[0]
            if k == "label" or k == "description":
                return str
            elif k == "balancing":
                return bool
        elif len(rest) == 2 and rest[0] == "bands":
            return list
        elif len(rest) >= 2 and rest[0] == "revert":
            if rest[1] in ("after_hours", "on_ac_hours"):
                return float
            elif rest[1] == "to":
                return str
        elif len(rest) >= 2 and rest[0] == "pins":
            if len(rest) == 3 and rest[2] == "role":
                return str
            elif len(rest) == 3 and rest[2] in ("start", "stop"):
                return int
    return None


def _coerce_to_type(raw, target_type, key):
    """Coerce raw string to target_type, or raise ConfigError."""
    s = raw.strip()
    if target_type is str:
        return s
    elif target_type is bool:
        if s.lower() == "true":
            return True
        elif s.lower() == "false":
            return False
        else:
            raise ConfigError(f"{key}: expected true or false, got {s!r}")
    elif target_type is int:
        try:
            return int(s)
        except ValueError:
            raise ConfigError(f"{key}: expected int, got {s!r}")
    elif target_type is float:
        try:
            return float(s)
        except ValueError:
            raise ConfigError(f"{key}: expected float, got {s!r}")
    elif target_type is list:
        parts = s.split(",")
        if len(parts) != 2:
            raise ConfigError(f"{key}: expected two values as a,b, got {s!r}")
        try:
            return [int(p.strip()) for p in parts]
        except ValueError:
            raise ConfigError(f"{key}: expected two ints as a,b, got {s!r}")
    return s


OFF_WORDS = ("none", "off", "false", "")


def set_dotted(cfg, key, raw):
    parts = key.split(".")
    # Removing auto-revert is a first-class edit, not a validation trap:
    #   profiles.X.revert=none                      drops the whole table
    #   profiles.X.revert.after_hours=none (or 0)   drops that trigger; when no
    #   trigger is left the table goes too, since a revert with no trigger is
    #   meaningless (validate() rejects it).
    word = raw.strip().lower()
    if len(parts) == 3 and parts[0] == "profiles" and parts[2] == "revert" and word in OFF_WORDS:
        if parts[1] not in cfg.get("profiles", {}):
            raise ConfigError(f"{key}: no profile {parts[1]!r}")
        cfg["profiles"][parts[1]].pop("revert", None)
        return
    if (len(parts) == 4 and parts[0] == "profiles" and parts[2] == "revert"
            and parts[3] in ("after_hours", "on_ac_hours") and word in OFF_WORDS + ("0", "0.0")):
        if parts[1] not in cfg.get("profiles", {}):
            raise ConfigError(f"{key}: no profile {parts[1]!r}")
        prof = cfg["profiles"][parts[1]]
        rv = prof.get("revert")
        if rv:
            rv.pop(parts[3], None)
            if not rv.get("after_hours") and not rv.get("on_ac_hours"):
                del prof["revert"]
        return
    node = cfg
    for p in parts[:-1]:
        node = node.setdefault(p, {})
        if not isinstance(node, dict):
            raise ConfigError(f"{key}: {p} is not a table")
    target_type = _resolve_type(key)
    if target_type is not None:
        node[parts[-1]] = _coerce_to_type(raw, target_type, key)
    else:
        node[parts[-1]] = raw

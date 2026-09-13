"""Write resolved bands to firmware, only when they differ, and read back."""
from pathlib import Path

from dbb.state import add_event, now_iso
from dbb.sysfs import apply_band, read_applied_band


def read_password(cfg):
    p = cfg["general"].get("bios_password_file") or ""
    if not p:
        return None
    try:
        return Path(p).read_text().strip() or None
    except OSError:
        return None


def apply_bands(bands, state, password=None):
    result = {"written": [], "skipped": [], "errors": {}, "mismatch": []}
    fw = state.setdefault("firmware", {})
    for slot, band in bands.items():
        if band is None:
            continue
        want = [int(band[0]), int(band[1])]
        last = fw.get(slot)
        if last and last.get("observed") == want and not last.get("error"):
            result["skipped"].append(slot)
            continue
        msg, err = apply_band(slot, want[0], want[1], password, dry_run=False)
        observed = read_applied_band(slot)
        rec = {"requested": want, "observed": list(observed) if observed else None,
               "ts": now_iso(), "error": err}
        fw[slot] = rec
        if err:
            result["errors"][slot] = err
        else:
            result["written"].append(slot)
        if rec["observed"] != want:
            result["mismatch"].append(slot)
            add_event(state, "firmware",
                      f"{slot}: requested {want} observed {rec['observed']}"
                      + (f" ({err})" if err else ""))
    return result

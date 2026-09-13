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
"""Charge-ceiling policy: which pack to protect, and by how much."""

from dbb.sysfs import BATS
from dbb.wear import efc

# Charge ceilings per role. "protect" is the pack that is ahead on wear.
DEFAULT_BANDS = {
    "protect": (50, 60),
    "work": (80, 90),
    "neutral": (50, 80),
    "field": (90, 100),
}

# Divergence in equivalent full cycles that must accumulate before roles flip.
# Hysteresis: without it the policy would oscillate every sample.
DEFAULT_DEADBAND = 0.5


def decide_roles(state, deadback):
    """Assign 'protect' to the pack that is ahead on wear, 'work' to the other.

    Lowering the ceiling on a pack shifts load away from it under either EC
    behaviour: if the EC drains the fuller pack first, a lower ceiling makes it
    lose that race; if the EC drains in fixed order, a lower ceiling means it
    delivers fewer Wh before handing over. Both move cycles to the other pack.
    """
    slots = state.get("slots", {})
    present = [b for b in BATS if b in slots]
    if len(present) < 2:
        return None, "only one pack tracked so far"

    scores = {b: efc(slots[b]) for b in present}
    a, c = present[0], present[1]
    diff = scores[a] - scores[c]

    if abs(diff) < deadback:
        return {b: "neutral" for b in present}, (
            f"packs within {deadback:.2f} EFC "
            f"({scores[a]:.2f} vs {scores[c]:.2f}) - holding neutral band")

    leader = a if diff > 0 else c
    laggard = c if diff > 0 else a
    return ({leader: "protect", laggard: "work"},
            f"{leader} is ahead by {abs(diff):.2f} EFC - protecting it, "
            f"{laggard} takes the load")

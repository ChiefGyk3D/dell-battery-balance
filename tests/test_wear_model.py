import importlib.machinery, importlib.util, os, sys

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      os.pardir, "dell-battery-balance")
spec = importlib.util.spec_from_loader(
    "dbb", importlib.machinery.SourceFileLoader("dbb", SCRIPT))
dbb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dbb)

DESIGN = 4600000
def mk(ts, ac, b0, b1, s0="Discharging", s1="Discharging", t=320):
    def one(c, st):
        return dict(status=st, capacity=round(100*c/DESIGN), charge_now_uah=c,
                    charge_full_uah=DESIGN, charge_full_design_uah=DESIGN,
                    voltage_now_uv=11400000, voltage_min_design_uv=11400000,
                    current_now_ua=0, temp_dc=t)
    return dict(ts=ts, boot_id="x", ac_online=ac, bats={"BAT0": one(b0,s0), "BAT1": one(b1,s1)})

st = {"version":1,"created":"t","slots":{},"last":None,
      "discharge_first":{"BAT0":0,"BAT1":0},"sessions":0,"policy":None}

# Simulate the EC behaviour we actually observed: BAT1 drains first to a floor,
# then BAT0 takes over. Three full unplug/recharge cycles.
t = 0.0
b0, b1 = DESIGN, DESIGN
for cycle in range(3):
    while b1 > DESIGN*0.05:                      # BAT1 drains, BAT0 holds
        t += 300; b1 -= DESIGN*0.05
        dbb.integrate(st, mk(t, 0, int(b0), int(max(b1,0))))
    while b0 > DESIGN*0.30:                      # BAT0 takes over
        t += 300; b0 -= DESIGN*0.05
        dbb.integrate(st, mk(t, 0, int(b0), int(max(b1,0))))
    while b0 < DESIGN or b1 < DESIGN:            # recharge on AC
        t += 300
        if b0 < DESIGN: b0 = min(DESIGN, b0 + DESIGN*0.05)
        else: b1 = min(DESIGN, b1 + DESIGN*0.05)
        dbb.integrate(st, mk(t, 1, int(b0), int(b1), "Charging", "Charging"))

e0, e1 = dbb.efc(st["slots"]["BAT0"]), dbb.efc(st["slots"]["BAT1"])
print(f"after 3 cycles:  BAT0 EFC={e0:.2f}   BAT1 EFC={e1:.2f}")
print(f"calendar score:  BAT0={st['slots']['BAT0']['calendar_score']:.1f}  "
      f"BAT1={st['slots']['BAT1']['calendar_score']:.1f}")
print(f"drain-first tally: {st['discharge_first']}")
roles, why = dbb.decide_roles(st, 0.5)
print(f"decision: {why}")
print(f"roles: {roles}")
assert e1 > e0, "BAT1 should show more cycle wear"
assert st["discharge_first"]["BAT1"] > st["discharge_first"]["BAT0"], "BAT1 drains first"
assert roles["BAT1"] == "protect" and roles["BAT0"] == "work", "worn pack must be protected"

# Deadband: near-equal packs must hold neutral rather than flap.
st2 = {"slots":{"BAT0":dbb.blank_slot(DESIGN),"BAT1":dbb.blank_slot(DESIGN)}}
st2["slots"]["BAT0"]["discharge_uah"] = DESIGN*2.0
st2["slots"]["BAT1"]["discharge_uah"] = DESIGN*2.2
r, w = dbb.decide_roles(st2, 0.5)
print(f"\nnear-equal: {w} -> {r}")
assert set(r.values()) == {"neutral"}

# Band clamping against the firmware limits (start 50-95, stop 55-100).
print("\nclamp checks:", dbb.clamp_band(50,60), dbb.clamp_band(10,200), dbb.clamp_band(95,55))
assert dbb.clamp_band(10,200) == (50,100)
assert dbb.clamp_band(95,55)[0] < 55
print("\nALL ASSERTIONS PASSED")

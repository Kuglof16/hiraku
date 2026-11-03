"""Tyre force laws: the tangential force at the contact point and its deflection state.

Sim computes the contact (normal force, contact point, slip velocities in the
tyre frame: vsl along the wheel heading, vst across it) and asks the corner's
tyre for the force. Every law has the same two calls:

    force(dx, dy, vsl, vst, vroll, ntot) -> (fl, ft, sliding, lim)
    relax(dx, dy, vsl, vst, vroll, lim, h) -> (dx, dy)   # deflection state after the step

vroll is the rolling speed |omega| r_e of the wheel, ntot the normal force,
lim the friction limit mu N the force was held to.
"""

from __future__ import annotations

import math

from .units import G0

LOW_SPEED_DAMPING_RATIO = 0.7   # tyre deflection-state damping at standstill (fades out above V_LOW)
V_LOW = 1.0                     # m/s


class BristleTyre:
    """Linear tyre with relaxation length sigma, held to the friction circle mu N.

    The deflection state d follows dd/dt = v_slip - (|w r|/sigma) d; the force is
    F = -k d - c_ls fade (v_slip - lam d) with k = C / sigma, so a standing tyre is
    a spring and does not creep. c_ls damps the state at standstill and fades out
    above V_LOW. When |F| exceeds mu N the force is scaled onto the circle (sliding)
    and so is the deflection after the step.
    """

    __slots__ = ("sigma", "kx", "ky", "mu", "cx_ls", "cy_ls")

    def __init__(self, c_long: float, c_lat: float, sigma: float, mu: float, static_load: float):
        self.sigma = float(sigma)
        self.kx = float(c_long / sigma)
        self.ky = float(c_lat / sigma)
        self.mu = float(mu)
        m = static_load / G0
        self.cx_ls = 2.0 * LOW_SPEED_DAMPING_RATIO * math.sqrt(self.kx * m)
        self.cy_ls = 2.0 * LOW_SPEED_DAMPING_RATIO * math.sqrt(self.ky * m)

    def force(self, dx: float, dy: float, vsl: float, vst: float, vroll: float, ntot: float):
        lam = vroll / self.sigma
        fade = 1.0 / (1.0 + (vroll / V_LOW) ** 2)
        fl = -self.kx * dx - self.cx_ls * fade * (vsl - lam * dx)
        ft = -self.ky * dy - self.cy_ls * fade * (vst - lam * dy)
        return _limit(fl, ft, self.mu * ntot)

    def relax(self, dx: float, dy: float, vsl: float, vst: float, vroll: float, lim: float, h: float):
        lam = vroll / self.sigma
        if lam * h < 1e-9:
            e, gg = 1.0, h
        else:
            e = math.exp(-lam * h)
            gg = (1.0 - e) / lam
        dxn = dx * e + vsl * gg
        dyn = dy * e + vst * gg
        fxe, fye = self.kx * dxn, self.ky * dyn
        fe = math.sqrt(fxe * fxe + fye * fye)
        if fe > lim:
            sc = lim / fe
            dxn, dyn = dxn * sc, dyn * sc
        return dxn, dyn


class CasterTyre:
    """A freely swivelling caster: rolling resistance against the contact's sliding velocity, no deflection."""

    __slots__ = ("c_rr", "mu")

    def __init__(self, c_rr: float, mu: float):
        self.c_rr = float(c_rr)
        self.mu = float(mu)

    def force(self, dx: float, dy: float, vsl: float, vst: float, vroll: float, ntot: float):
        rr = self.c_rr * ntot / math.sqrt(vsl * vsl + vst * vst + 1e-4)
        return _limit(-rr * vsl, -rr * vst, self.mu * ntot)

    def relax(self, dx: float, dy: float, vsl: float, vst: float, vroll: float, lim: float, h: float):
        return 0.0, 0.0


def _limit(fl: float, ft: float, lim: float):
    """Scale (fl, ft) onto the circle of radius lim when it is outside; (fl, ft, sliding, lim)."""
    mag = math.sqrt(fl * fl + ft * ft)
    sliding = mag > lim
    if sliding:
        sc = lim / mag if mag > 0.0 else 0.0
        fl, ft = fl * sc, ft * sc
    return fl, ft, sliding, lim


def tyre_of(corner):
    """The force law of a model.Corner."""
    if corner.kind == "caster":
        return CasterTyre(corner.c_rr, corner.mu)
    return BristleTyre(corner.c_long, corner.c_lat, corner.sigma, corner.mu, corner.static_load)

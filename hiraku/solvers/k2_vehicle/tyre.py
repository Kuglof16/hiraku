"""Tyre force laws: the tangential force at the contact point, the aligning moment and the deflection state.

Sim computes the contact (normal force, contact point, slip velocities in the
tyre frame: vsl along the wheel heading, vst across it to the left) and asks the
corner's tyre for the force. Every law has the same two calls:

    force(dx, dy, vsl, vst, vroll, vcx, gamma, ntot) -> (fl, ft, mz, sliding, lim)
    relax(dx, dy, vsl, vst, vroll, vcx, lim, h) -> (dx, dy)   # deflection state after the step

vroll is the rolling speed |omega| r_e of the wheel, vcx the forward speed of the
contact centre along the heading, gamma the inclination of the wheel to the road
(rad, ISO: top to the right +), ntot the normal force; mz is the moment about the
road normal (up +), lim the friction limit the force is held to.
"""

from __future__ import annotations

import math

from .magic_formula import MF61
from .units import G0

LOW_SPEED_DAMPING_RATIO = 0.7   # tyre deflection-state damping at standstill (fades out above V_LOW)
V_LOW = 1.0                     # m/s


class BristleTyre:
    """Linear tyre with relaxation length sigma, held to the friction circle mu N.

    The deflection state d follows dd/dt = v_slip - (|w r|/sigma) d; the force is
    F = -k d - c_ls fade (v_slip - lam d) with k = C / sigma, so a standing tyre is
    a spring and does not creep. c_ls damps the state at standstill and fades out
    above V_LOW. When |F| exceeds mu N the force is scaled onto the circle (sliding)
    and so is the deflection after the step. Used for legs (a foot, not a tyre).
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

    def force(self, dx, dy, vsl, vst, vroll, vcx, gamma, ntot):
        lam = vroll / self.sigma
        fade = 1.0 / (1.0 + (vroll / V_LOW) ** 2)
        fl = -self.kx * dx - self.cx_ls * fade * (vsl - lam * dx)
        ft = -self.ky * dy - self.cy_ls * fade * (vst - lam * dy)
        fl, ft, sliding, lim = _limit(fl, ft, self.mu * ntot)
        return fl, ft, 0.0, sliding, lim

    def relax(self, dx, dy, vsl, vst, vroll, vcx, lim, h):
        lam = vroll / self.sigma
        e, gg = _decay(lam, h)
        dxn = dx * e + vsl * gg
        dyn = dy * e + vst * gg
        fxe, fye = self.kx * dxn, self.ky * dyn
        fe = math.sqrt(fxe * fxe + fye * fye)
        if fe > lim:
            sc = lim / fe
            dxn, dyn = dxn * sc, dyn * sc
        return dxn, dyn


class MFTyre:
    """A Magic Formula tyre (magic_formula.MF61) on the deflection states.

    The deflections are the transient slips: kappa' = -dx / sigma, tan(alpha') = dy / sigma
    (the single-contact-point relaxation model of Pacejka's Tire and Vehicle Dynamics; in
    steady rolling they are the slips the Magic Formula takes). They relax with the forward
    speed of the contact centre, dd/dt = v_slip - (|V_cx| / sigma) d, so a locked wheel
    settles at kappa = -1 and a standing tyre is a spring that does not creep. They are
    held inside the slip ranges the coefficient file is valid for (KPUMIN..KPUMAX,
    ALPMIN..ALPMAX). The same low-speed damping as BristleTyre is added, with the slip
    stiffness at the static load, on the rate the deflection changes at: a deflection held
    at the end of its range by a slip that pushes it further (a wheel spinning or locked at
    standstill) is not changing, so it gets none, and the damping never takes the force past
    the larger of mu N and the formula's own force. A right-hand tyre is the left-hand one mirrored: its
    lateral slip and camber go in with the sign changed and Fy and Mz come out so, so a
    mirrored vehicle stays mirrored bit for bit.
    sliding: the transient slip is past the pure-slip peak (the ellipse of the peak slips;
    a peak slip goes with 1 / B = C D / K, so it is taken at the static load and moved with
    D / K to the load of the moment).
    """

    __slots__ = ("mf", "sigma", "mirror", "cx_ls", "cy_ls", "dx_lo", "dx_hi", "dy_lo", "dy_hi", "kappa_peak",
                 "ta_peak", "mu", "rx0", "ry0")

    def __init__(self, mf: MF61, sigma: float, static_load: float, side: float):
        self.mf = mf
        self.sigma = float(sigma)
        self.mirror = side < 0
        m = static_load / G0
        self.cx_ls = 2.0 * LOW_SPEED_DAMPING_RATIO * math.sqrt(mf.kxk(static_load) / sigma * m)
        self.cy_ls = 2.0 * LOW_SPEED_DAMPING_RATIO * math.sqrt(abs(mf.kya(static_load)) / sigma * m)
        self.dx_lo, self.dx_hi = -sigma * mf.kappa_max, -sigma * mf.kappa_min
        self.dy_lo, self.dy_hi = sigma * math.tan(mf.alpha_min), sigma * math.tan(mf.alpha_max)
        self.kappa_peak = mf.peak_slip_x(static_load)
        self.ta_peak = mf.peak_slip_y(static_load)
        self.mu = max(mf.mu_x(static_load), mf.mu_y(static_load))
        self.rx0 = mf.mu_x(static_load) * static_load / mf.kxk(static_load)  # D / K at the static load
        self.ry0 = mf.mu_y(static_load) * static_load / abs(mf.kya(static_load))

    def force(self, dx, dy, vsl, vst, vroll, vcx, gamma, ntot):
        sigma = self.sigma
        v = abs(vcx)
        lam = v / sigma
        fade = 1.0 / (1.0 + (v / V_LOW) ** 2)
        kappa = -dx / sigma
        ta = dy / sigma
        if self.mirror:
            fx, fy, mz = self.mf.forces(ntot, kappa, -ta, -gamma)
            fy, mz = -fy, -mz
        else:
            fx, fy, mz = self.mf.forces(ntot, kappa, ta, gamma)
        rate_x, rate_y = vsl - lam * dx, vst - lam * dy
        if (dx <= self.dx_lo and rate_x < 0.0) or (dx >= self.dx_hi and rate_x > 0.0):
            rate_x = 0.0
        if (dy <= self.dy_lo and rate_y < 0.0) or (dy >= self.dy_hi and rate_y > 0.0):
            rate_y = 0.0
        fl = fx - self.cx_ls * fade * rate_x
        ft = fy - self.cy_ls * fade * rate_y
        # the damping does not take the force past friction: at most the larger of mu N and the formula's force
        lim = self.mu * ntot
        cap = max(lim, math.sqrt(fx * fx + fy * fy))
        mag = math.sqrt(fl * fl + ft * ft)
        if mag > cap:
            fl, ft = fl * cap / mag, ft * cap / mag
        sliding = False
        if ntot > 0.0:
            mf = self.mf
            rk = kappa / (self.kappa_peak * mf.mu_x(ntot) * ntot / mf.kxk(ntot) / self.rx0)
            ra = ta / (self.ta_peak * mf.mu_y(ntot) * ntot / abs(mf.kya(ntot)) / self.ry0)
            sliding = rk * rk + ra * ra > 1.0
        return fl, ft, mz, sliding, lim

    def relax(self, dx, dy, vsl, vst, vroll, vcx, lim, h):
        e, gg = _decay(abs(vcx) / self.sigma, h)
        dxn = dx * e + vsl * gg
        dyn = dy * e + vst * gg
        dxn = self.dx_lo if dxn < self.dx_lo else (self.dx_hi if dxn > self.dx_hi else dxn)
        dyn = self.dy_lo if dyn < self.dy_lo else (self.dy_hi if dyn > self.dy_hi else dyn)
        return dxn, dyn


class CasterTyre:
    """A freely swivelling caster: rolling resistance against the contact's sliding velocity, no deflection."""

    __slots__ = ("c_rr", "mu")

    def __init__(self, c_rr: float, mu: float):
        self.c_rr = float(c_rr)
        self.mu = float(mu)

    def force(self, dx, dy, vsl, vst, vroll, vcx, gamma, ntot):
        rr = self.c_rr * ntot / math.sqrt(vsl * vsl + vst * vst + 1e-4)
        fl, ft, sliding, lim = _limit(-rr * vsl, -rr * vst, self.mu * ntot)
        return fl, ft, 0.0, sliding, lim

    def relax(self, dx, dy, vsl, vst, vroll, vcx, lim, h):
        return 0.0, 0.0


def _decay(lam: float, h: float) -> tuple[float, float]:
    """Exact step of dd/dt = v - lam d over h: d' = d e + v gg."""
    if lam * h < 1e-9:
        return 1.0, h
    e = math.exp(-lam * h)
    return e, (1.0 - e) / lam


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
    if corner.mf is not None:
        return MFTyre(corner.mf, corner.sigma, corner.static_load, corner.side)
    return BristleTyre(corner.c_long, corner.c_lat, corner.sigma, corner.mu, corner.static_load)

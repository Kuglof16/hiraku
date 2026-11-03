"""Magic Formula 6.1 tyre (Pacejka): steady-state Fx, Fy and Mz for pure and combined slip.

Equations: H. B. Pacejka, Tire and Vehicle Dynamics, 3rd ed. (2012), section 4.3.2,
eqs (4.E1)-(4.E78), with Bt, Dt, the equivalent slip angles and s in the form of
Besselink, Schmeitz and Pacejka, Vehicle System Dynamics 48 (2010), eqs (A54)-(A60),
as the open MFeval solver (M. Furlan Tassara, BSD licence) states them. Not checked
against the book. Written here from those equations; equation numbers are in the comments.

Conditions of this implementation: nominal inflation pressure (dpi = 0, so the pressure
terms vanish), no turn slip (all zeta = 1), no speed-decaying friction (LMUV = 0, so
LMU* = LMU' = LMU), forward rolling. ISO signs (TYRESIDE LEFT): kappa > 0 drives,
tan(alpha) = V_sy / |V_cx| (slip velocity to the left), Fy < 0 for alpha > 0, Mz about
the road normal (up), gamma > 0 tilts the wheel top to the right (-y).

Inputs: Fz (N), kappa (1), tan_alpha (1; the book's alpha* = tan(alpha) sgn(V_cx)) and
gamma (rad, the inclination to the road). Scaling: lmux, lmuy (peak friction), lkx
(slip stiffness), lky (cornering stiffness), fz0 and r0 (the size); the other scaling
factors are 1.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

EPS = 1e-6           # the book's epsilon against division by zero (4.E6a)
TWO_OVER_PI = 2.0 / math.pi

DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "tyres"
DEFAULT_TYRE = "pacejka_tvd3_205_60r15"


def load_coefficients(name: str = DEFAULT_TYRE) -> dict:
    """A tyre coefficient file of data/tyres (kind mf_tyre)."""
    with (DATA_DIR / f"{name}.json").open(encoding="utf-8") as f:
        data = json.load(f)
    if data.get("kind") != "mf_tyre":
        raise ValueError(f"{name}: not an mf_tyre file")
    return data


def _sgn(x: float) -> float:
    return -1.0 if x < 0.0 else 1.0


def _mf(b: float, c: float, d: float, e: float, x: float) -> float:
    """D sin(C atan(B x - E (B x - atan(B x))))."""
    bx = b * x
    return d * math.sin(c * math.atan(bx - e * (bx - math.atan(bx))))


def _weight(b: float, c: float, e: float, x: float) -> float:
    """cos(C atan(B x - E (B x - atan(B x)))): the combined-slip weighting function."""
    bx = b * x
    return math.cos(c * math.atan(bx - e * (bx - math.atan(bx))))


class MF61:
    """One tyre: the coefficient file scaled to a size (fz0, r0) and to the wanted peak
    friction and stiffnesses (lmux, lmuy, lkx, lky)."""

    def __init__(self, coeffs: dict, *, fz0: float | None = None, r0: float | None = None, lmux: float = 1.0,
                 lmuy: float = 1.0, lkx: float = 1.0, lky: float = 1.0):
        self.coeffs = coeffs
        for group in ("longitudinal", "lateral", "aligning"):
            for k, v in coeffs[group].items():
                setattr(self, k, float(v))
        rg = coeffs["ranges"]
        self.kappa_min, self.kappa_max = float(rg["KPUMIN"]), float(rg["KPUMAX"])
        self.alpha_min, self.alpha_max = float(rg["ALPMIN"]), float(rg["ALPMAX"])
        self.fz0 = float(coeffs["FNOMIN"] if fz0 is None else fz0)
        self.r0 = float(coeffs["UNLOADED_RADIUS"] if r0 is None else r0)
        self.lmux, self.lmuy, self.lkx, self.lky = float(lmux), float(lmuy), float(lkx), float(lky)

    # ------------------------------------------------------------ pure slip
    def mu_x(self, fz: float, gamma: float = 0.0) -> float:
        dfz = (fz - self.fz0) / self.fz0                                            # (4.E2a)
        return (self.PDX1 + self.PDX2 * dfz) * (1.0 - self.PDX3 * gamma * gamma) * self.lmux  # (4.E13)

    def mu_y(self, fz: float, gamma: float = 0.0) -> float:
        dfz = (fz - self.fz0) / self.fz0
        gs = math.sin(gamma)                                                        # (4.E4)
        return (self.PDY1 + self.PDY2 * dfz) * (1.0 - self.PDY3 * gs * gs) * self.lmuy  # (4.E23)

    def kxk(self, fz: float) -> float:
        """Longitudinal slip stiffness dFx0/dkappa at kappa_x = 0 (4.E15)."""
        dfz = (fz - self.fz0) / self.fz0
        return fz * (self.PKX1 + self.PKX2 * dfz) * math.exp(self.PKX3 * dfz) * self.lkx

    def kya(self, fz: float, gamma: float = 0.0) -> float:
        """Cornering stiffness dFy0/dalpha at alpha_y = 0 (4.E25; negative in ISO signs)."""
        gs = math.sin(gamma)
        return (self.PKY1 * self.fz0 * (1.0 - self.PKY3 * abs(gs))
                * math.sin(self.PKY4 * math.atan((fz / self.fz0) / (self.PKY2 + self.PKY5 * gs * gs))) * self.lky)

    def fx0(self, fz: float, kappa: float, gamma: float = 0.0) -> tuple[float, float]:
        """Pure longitudinal force and the slip stiffness (4.E9)-(4.E18)."""
        return self._fx0(fz, kappa, gamma)[:2]

    def _fx0(self, fz: float, kappa: float, gamma: float) -> tuple[float, float, float]:
        """Fx0, Kxk and Dx."""
        dfz = (fz - self.fz0) / self.fz0
        cx = self.PCX1                                                              # (4.E11)
        dx = self.mu_x(fz, gamma) * fz                                              # (4.E12)
        kxk = self.kxk(fz)
        bx = kxk / (cx * dx + EPS * _sgn(dx))                                       # (4.E16)
        shx = self.PHX1 + self.PHX2 * dfz                                           # (4.E17)
        svx = fz * (self.PVX1 + self.PVX2 * dfz) * self.lmux                        # (4.E18)
        kx = kappa + shx                                                            # (4.E10)
        ex = min(1.0, (self.PEX1 + self.PEX2 * dfz + self.PEX3 * dfz * dfz) * (1.0 - self.PEX4 * _sgn(kx)))  # (4.E14)
        return _mf(bx, cx, dx, ex, kx) + svx, kxk, dx                              # (4.E9)

    def _fy0(self, fz: float, ta: float, gs: float) -> tuple[float, float, float, float, float, float]:
        """Pure lateral force (4.E19)-(4.E30) at gamma* = gs: Fy0, mu_y, Kya, SHy, SVy, By Cy."""
        dfz = (fz - self.fz0) / self.fz0
        kya = (self.PKY1 * self.fz0 * (1.0 - self.PKY3 * abs(gs))
               * math.sin(self.PKY4 * math.atan((fz / self.fz0) / (self.PKY2 + self.PKY5 * gs * gs))) * self.lky)  # (4.E25)
        svyg = fz * (self.PVY3 + self.PVY4 * dfz) * gs * self.lmuy                  # (4.E28)
        kyg0 = fz * (self.PKY6 + self.PKY7 * dfz)                                   # (4.E30)
        shy = (self.PHY1 + self.PHY2 * dfz) + (kyg0 * gs - svyg) / (kya + EPS * _sgn(kya))  # (4.E27)
        svy = fz * (self.PVY1 + self.PVY2 * dfz) * self.lmuy + svyg                 # (4.E29)
        ay = ta + shy                                                               # (4.E20)
        cy = self.PCY1                                                              # (4.E21)
        muy = (self.PDY1 + self.PDY2 * dfz) * (1.0 - self.PDY3 * gs * gs) * self.lmuy  # (4.E23)
        dy = muy * fz                                                               # (4.E22)
        ey = min(1.0, (self.PEY1 + self.PEY2 * dfz) * (1.0 + self.PEY5 * gs * gs - (self.PEY3 + self.PEY4 * gs) * _sgn(ay)))  # (4.E24)
        by = kya / (cy * dy + EPS * _sgn(dy))                                       # (4.E26)
        return _mf(by, cy, dy, ey, ay) + svy, muy, kya, shy, svy, by * cy          # (4.E19)

    def fy0(self, fz: float, tan_alpha: float, gamma: float = 0.0) -> float:
        return self._fy0(fz, tan_alpha, math.sin(gamma))[0]

    # ------------------------------------------------------------ combined slip
    def forces(self, fz: float, kappa: float, tan_alpha: float, gamma: float = 0.0) -> tuple[float, float, float]:
        """Fx, Fy (N) and Mz (N m) in combined slip (4.E50)-(4.E78)."""
        return self.forces_and_peaks(fz, kappa, tan_alpha, gamma)[:3]

    def forces_and_peaks(self, fz: float, kappa: float, tan_alpha: float, gamma: float = 0.0) -> tuple:
        """Fx, Fy, Mz and the peaks Dx, Dy and the slip stiffness Kxk the formula used (one evaluation)."""
        if fz <= 0.0:
            return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
        dfz = (fz - self.fz0) / self.fz0
        gs = math.sin(gamma)
        ta = tan_alpha
        cos_a = 1.0 / math.sqrt(1.0 + ta * ta)                                      # cos(alpha') (4.E6), forward rolling

        fx0, kxk, dx_peak = self._fx0(fz, kappa, gamma)
        # Fx combined (4.E50)-(4.E57)
        exa = min(1.0, self.REX1 + self.REX2 * dfz)
        shxa = self.RHX1
        bxa = (self.RBX1 + self.RBX3 * gs * gs) * math.cos(math.atan(self.RBX2 * kappa))
        gxa = _weight(bxa, self.RCX1, exa, ta + shxa) / _weight(bxa, self.RCX1, exa, shxa)
        fx = gxa * fx0

        # Fy combined (4.E58)-(4.E67)
        fy0, muy, kya, shy, svy, bycy = self._fy0(fz, ta, gs)
        eyk = min(1.0, self.REY1 + self.REY2 * dfz)
        shyk = self.RHY1 + self.RHY2 * dfz
        byk = (self.RBY1 + self.RBY4 * gs * gs) * math.cos(math.atan(self.RBY2 * (ta - self.RBY3)))
        gyk = _weight(byk, self.RCY1, eyk, kappa + shyk) / _weight(byk, self.RCY1, eyk, shyk)
        dvyk = muy * fz * (self.RVY1 + self.RVY2 * dfz + self.RVY3 * gs) * math.cos(math.atan(self.RVY4 * ta))
        svyk = dvyk * math.sin(self.RVY5 * math.atan(self.RVY6 * kappa))
        fy = gyk * fy0 + svyk

        # Mz (4.E31)-(4.E49), (4.E71)-(4.E78), (A54)-(A60)
        kya_p = kya + EPS * _sgn(kya)                                               # (4.E39)
        sht = self.QHZ1 + self.QHZ2 * dfz + (self.QHZ3 + self.QHZ4 * dfz) * gs      # (4.E35)
        alpha_t = ta + sht                                                          # (4.E34)
        alpha_r = ta + shy + svy / kya_p                                            # (4.E37), (4.E38)
        dt = ((self.QDZ1 + self.QDZ2 * dfz) * (1.0 + self.QDZ3 * gamma + self.QDZ4 * gamma * gamma)
              * fz * (self.r0 / self.fz0))                                          # (A60)
        bt = ((self.QBZ1 + self.QBZ2 * dfz + self.QBZ3 * dfz * dfz) * (1.0 + self.QBZ4 * gamma + self.QBZ5 * abs(gamma))
              * self.lky / self.lmuy)                                               # (A58)
        ct = self.QCZ1                                                              # (4.E41)
        et = min(1.0, (self.QEZ1 + self.QEZ2 * dfz + self.QEZ3 * dfz * dfz)
                 * (1.0 + (self.QEZ4 + self.QEZ5 * gs) * TWO_OVER_PI * math.atan(bt * ct * alpha_t)))  # (4.E44)
        dr = (fz * self.r0 * ((self.QDZ6 + self.QDZ7 * dfz)
                              + ((self.QDZ8 + self.QDZ9 * dfz) + (self.QDZ10 + self.QDZ11 * dfz) * abs(gs)) * gs)
              * self.lmuy * cos_a)                                                  # (4.E47), cos(alpha)
        br = self.QBZ9 * self.lky / self.lmuy + self.QBZ10 * bycy                   # (4.E45)
        k2 = (kxk / kya_p) ** 2 * kappa * kappa
        ar_eq = math.atan(math.sqrt(math.tan(alpha_r) ** 2 + k2)) * _sgn(alpha_r)   # (A54)
        at_eq = math.atan(math.sqrt(math.tan(alpha_t) ** 2 + k2)) * _sgn(alpha_t)   # (A55)
        s = self.r0 * (self.SSZ1 + self.SSZ2 * (fy / self.fz0) + (self.SSZ3 + self.SSZ4 * dfz) * gamma)  # (A56)
        mzr = dr * math.cos(math.atan(br * ar_eq))                                  # (4.E75), Cr = 1 (4.E46)
        # Fy' of gamma = 0 (4.E74): the pure force at gamma 0 weighted by Gyk
        fy0_g0 = fy0 if gs == 0.0 else self._fy0(fz, ta, 0.0)[0]
        t = dt * math.cos(ct * math.atan(bt * at_eq - et * (bt * at_eq - math.atan(bt * at_eq)))) * cos_a  # (4.E73)
        mz = -t * (gyk * fy0_g0) + mzr + s * fx                                     # (4.E71), (4.E72)
        return fx, fy, mz, dx_peak, kxk, muy * fz

    # ------------------------------------------------------------ helpers
    def trail(self, fz: float, tan_alpha: float = 0.0, gamma: float = 0.0) -> float:
        """Pneumatic trail t (m) in pure side slip (4.E33): the lateral force acts this far behind the contact."""
        dfz = (fz - self.fz0) / self.fz0
        gs = math.sin(gamma)
        at = tan_alpha + self.QHZ1 + self.QHZ2 * dfz + (self.QHZ3 + self.QHZ4 * dfz) * gs
        dt = ((self.QDZ1 + self.QDZ2 * dfz) * (1.0 + self.QDZ3 * gamma + self.QDZ4 * gamma * gamma)
              * fz * (self.r0 / self.fz0))
        bt = ((self.QBZ1 + self.QBZ2 * dfz + self.QBZ3 * dfz * dfz) * (1.0 + self.QBZ4 * gamma + self.QBZ5 * abs(gamma))
              * self.lky / self.lmuy)
        ct = self.QCZ1
        et = min(1.0, (self.QEZ1 + self.QEZ2 * dfz + self.QEZ3 * dfz * dfz)
                 * (1.0 + (self.QEZ4 + self.QEZ5 * gs) * TWO_OVER_PI * math.atan(bt * ct * at)))
        return dt * math.cos(ct * math.atan(bt * at - et * (bt * at - math.atan(bt * at)))) / math.sqrt(1.0 + tan_alpha ** 2)

    def peak_slip_x(self, fz: float) -> float:
        """kappa (> 0) of the pure-slip Fx peak at load fz (shifts left out)."""
        dfz = (fz - self.fz0) / self.fz0
        cx = self.PCX1
        bx = self.kxk(fz) / (cx * self.mu_x(fz) * fz)
        ex = min(1.0, (self.PEX1 + self.PEX2 * dfz + self.PEX3 * dfz * dfz) * (1.0 - self.PEX4))
        return _peak(bx, cx, ex)

    def peak_slip_y(self, fz: float) -> float:
        """tan(alpha) (> 0) of the pure-slip Fy peak at load fz (shifts left out)."""
        dfz = (fz - self.fz0) / self.fz0
        cy = self.PCY1
        by = abs(self.kya(fz)) / (cy * self.mu_y(fz) * fz)
        ey = min(1.0, (self.PEY1 + self.PEY2 * dfz) * (1.0 - self.PEY3))
        return _peak(by, cy, ey)


def _peak(b: float, c: float, e: float) -> float:
    """x > 0 where D sin(C atan(B x - E (B x - atan B x))) peaks: B x - E (B x - atan B x) = tan(pi / 2C)."""
    if c <= 1.0:
        return math.inf  # no peak: the curve rises to its asymptote
    target = math.tan(math.pi / (2.0 * c))
    lo, hi = 0.0, 1.0
    f = lambda x: b * x - e * (b * x - math.atan(b * x)) - target
    while f(hi) < 0.0:
        hi *= 2.0
        if hi > 1e6:
            return math.inf
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if f(mid) < 0.0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)

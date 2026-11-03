"""Time integration of the K2 vehicle.

Generalised coordinates: sprung body position p (world) and attitude q
(unit quaternion, body -> world), and the travel s of every sprung corner
along its K&C path. Generalised speeds: v (world), w (body frame), ds/dt.
Each sprung corner carries a point mass m_u at the wheel centre
r_i = p + R c_i(s_i); the mass matrix is built from these kinematics, so the
unsprung inertia is coupled exactly (Kane's equations):

    M(q) du/dt = Q - sum_i m_u J_i^T (dJ_i/dt u) - [0, w x I_s w, 0] ,  plus g on p.

Uniform gravity is added as the exact acceleration (g, 0, ...) of the
generalised speeds (M times that vector is the gravity force), so free fall
is exactly the integrator's own sum. The step is semi-implicit Euler:
speeds first, then positions with the new speeds.

Wheel spin and the tyre deflection states are separate first-order states.
Brakes and rolling resistance are Coulomb torques applied implicitly (a held
wheel stays exactly at zero spin). The tyre's tangential force, its aligning
moment and its deflection state are the corner's force law (tyre.py; a Magic
Formula 6.1 tyre for every wheel).

One step: corner kinematics -> road contacts -> per corner (wheel axis, normal
contact, slip in the tyre frame, tyre force, wheel spin, generalised forces)
-> body forces -> mass matrix and solve -> semi-implicit Euler -> tyre states.

Left/right symmetry: corners are ordered in mirror pairs, sums over corners
run in that order (a pair cancels exactly), and the linear solve runs on the
pair sums and differences, so a symmetric vehicle in a symmetric state stays
bit-for-bit symmetric. The per-corner arithmetic is plain Python floats
(faster than numpy for four corners, and every corner is computed by the
same operations).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .model import Vehicle
from .road import Road
from .tyre import tyre_of
from .units import G0


def quat_to_r(q) -> np.ndarray:
    return np.array(_r_tuple(q)).reshape(3, 3)


def _r_tuple(q):
    w, x, y, z = q
    return (1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - w * z), 2.0 * (x * z + w * y),
            2.0 * (x * y + w * z), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - w * x),
            2.0 * (x * z - w * y), 2.0 * (y * z + w * x), 1.0 - 2.0 * (x * x + y * y))


def quat_mul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return [aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw]


def quat_from_euler(roll: float, pitch: float, yaw: float) -> list:
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    return [cr * cp * cy + sr * sp * sy, sr * cp * cy - cr * sp * sy,
            cr * sp * cy + sr * cp * sy, cr * cp * sy - sr * sp * cy]


def euler_from_r(r) -> tuple[float, float, float]:
    """Roll, pitch, yaw (rad) with R = Rz(yaw) Ry(pitch) Rx(roll)."""
    r = np.asarray(r).reshape(3, 3)
    pitch = -math.asin(max(-1.0, min(1.0, float(r[2, 0]))))
    roll = math.atan2(float(r[2, 1]), float(r[2, 2]))
    yaw = math.atan2(float(r[1, 0]), float(r[0, 0]))
    return roll, pitch, yaw


def _to_world(rot, x, y, z):
    r00, r01, r02, r10, r11, r12, r20, r21, r22 = rot
    return (r00 * x + r01 * y + r02 * z,
            r10 * x + r11 * y + r12 * z,
            r20 * x + r21 * y + r22 * z)


def _to_body(rot, x, y, z):
    r00, r01, r02, r10, r11, r12, r20, r21, r22 = rot
    return (r00 * x + r10 * y + r20 * z,
            r01 * x + r11 * y + r21 * z,
            r02 * x + r12 * y + r22 * z)


@dataclass
class Controls:
    steer: list   # per corner (rad)
    drive: list   # per corner wheel torque (N m), + forward
    brake: list   # per corner brake torque capacity (N m), >= 0


class Sim:
    def __init__(self, veh: Vehicle, road: Road, dt: float = 1e-3, gravity=None, lock: str | None = None):
        self.veh = veh
        self.road = road
        self.dt = dt
        self.set_gravity([0.0, 0.0, -G0] if gravity is None else gravity)
        cs = veh.corners
        self.n = n = len(cs)
        self.hard = [tuple(float(x) for x in (c.pos - veh.cg_s)) for c in cs]
        f = float
        self.dof = [c.dof for c in cs]
        self.dofidx = [i for i in range(n) if self.dof[i]]
        self.m = len(self.dofidx)
        self.mu_ = [f(c.m_u if c.dof else 0.0) for c in cs]
        self.side = [f(c.side) for c in cs]
        self.radius = [f(c.radius) for c in cs]
        self.k_z = [f(c.k_z) for c in cs]
        self.c_z = [f(c.c_z) for c in cs]
        self.tyres = [tyre_of(c) for c in cs]
        self.c_rr = [f(c.c_rr) for c in cs]
        self.j_w = [f(max(c.j_w, 1e-6)) for c in cs]
        self.spins = [c.kind == "wheel" for c in cs]
        self.k_w = [f(c.k_w) for c in cs]
        self.c_w = [f(c.c_w) for c in cs]
        self.preload = [f(c.preload) for c in cs]
        self.s_min = [f(c.s_min) for c in cs]
        self.s_max = [f(c.s_max) for c in cs]
        self.k_stop = [f(c.k_stop) for c in cs]
        self.i_s = [[float(veh.i_s[a, b]) for b in range(3)] for a in range(3)]
        self.m_dof = float(veh.m_s)
        for i in self.dofidx:
            self.m_dof = self.m_dof + self.mu_[i]
        self.kc = [c.path is not None and bool(np.any(c.path.coef[[0, 1, 3, 4]])) for c in cs]
        self.coef = [c.path.coef.tolist() if c.path is not None else None for c in cs]
        self.camber0 = [c.path.camber0 if c.path is not None else 0.0 for c in cs]
        self.toe0 = [c.path.toe0 if c.path is not None else 0.0 for c in cs]
        self.s_lo = [c.path.s_lo if c.path is not None else -1.0 for c in cs]
        self.s_hi = [c.path.s_hi if c.path is not None else 1.0 for c in cs]
        self.comp = [c.path.comp.tolist() if c.path is not None and c.path.comp is not None else None for c in cs]
        pos = {i: k for k, i in enumerate(self.dofidx)}
        self.pairs = []
        for i, c in enumerate(cs):
            j = c.mirror
            if j > i and c.dof and cs[j].dof:
                self.pairs.append((6 + pos[i], 6 + pos[j]))
        self.groups = []
        seen = set()
        for i, c in enumerate(cs):
            if i in seen:
                continue
            j = c.mirror
            if 0 <= j != i:
                self.groups.append((i, j))
                seen.update((i, j))
            else:
                self.groups.append((i,))
                seen.add(i)
        self.lock = lock  # None, or "heave" (only vertical translation and strokes)
        self.reset()

    def set_gravity(self, g):
        self.g = [float(x) for x in g]

    # ----------------------------------------------------------------- state
    def reset(self):
        n = self.n
        veh = self.veh
        self.t = 0.0
        self.p = [float(x) for x in veh.cg_s]
        self.q = [1.0, 0.0, 0.0, 0.0]
        self.v = [0.0, 0.0, 0.0]
        self.w = [0.0, 0.0, 0.0]
        self.s = [0.0] * n
        self.sd = [0.0] * n
        self.om = [0.0] * n
        self.dx = [0.0] * n
        self.dy = [0.0] * n
        self.ct = [0.0] * n  # toe and camber from the compliance (rad), from the forces of the step before
        self.cc = [0.0] * n
        self.out = {}
        defl = [c.static_load / c.k_z for c in veh.corners]
        self.p[2] -= sum(defl) / n

    _STATE = ("t", "p", "q", "v", "w", "s", "sd", "om", "dx", "dy", "ct", "cc")

    def state(self) -> dict:
        return {k: (list(getattr(self, k)) if isinstance(getattr(self, k), list) else getattr(self, k)) for k in self._STATE}

    def restore(self, st: dict):
        for k, v in st.items():
            setattr(self, k, list(v) if isinstance(v, list) else v)

    def no_controls(self) -> Controls:
        return Controls([0.0] * self.n, [0.0] * self.n, [0.0] * self.n)

    # ------------------------------------------------------------- geometry
    def _path(self, i: int, s: float):
        """Body-frame wheel centre, its first and second travel derivatives, the
        knuckle rotation per travel (x and z), camber and toe of corner i (on a K&C
        table, with the compliance's toe and camber of the forces of the step before)."""
        hx, hy, hz = self.hard[i]
        if not self.dof[i]:
            return hx, hy, hz, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, self.camber0[i], self.toe0[i]
        if not self.kc[i]:
            return hx, hy, hz + s, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
        lo, hi = self.s_lo[i], self.s_hi[i]
        sc = lo if s < lo else (hi if s > hi else s)
        ext = s - sc
        rows = self.coef[i]
        vals = []
        for row in rows[:2]:  # wheel centre x and y: value, first and second derivative
            v, d1, d2 = row[0], 0.0, 0.0
            for ck in row[1:]:
                d2 = d2 * sc + 2.0 * d1
                d1 = d1 * sc + v
                v = v * sc + ck
            if ext != 0.0:
                v = v + d1 * ext
                d2 = 0.0
            vals.append((v, d1, d2))
        # dz is the travel itself (PathPoly.from_table): sc, 1, 0, as its polynomial evaluates
        vals.append((sc + 1.0 * ext if ext != 0.0 else sc, 1.0, 0.0))
        for row in rows[3:]:  # camber and toe: value and first derivative
            v, d1 = row[0], 0.0
            for ck in row[1:]:
                d1 = d1 * sc + v
                v = v * sc + ck
            if ext != 0.0:
                v = v + d1 * ext
            vals.append((v, d1, 0.0))
        sd_ = -1.0 if self.side[i] < 0 else 1.0
        (x, x1, x2), (y, y1, y2), (z, z1, z2), (cb, cb1, _), (te, te1, _) = vals
        side = self.side[i]
        return (hx + x, hy + sd_ * y, hz + z, x1, sd_ * y1, z1, x2, sd_ * y2, z2,
                -side * cb1, -side * te1, self.camber0[i] + cb + self.cc[i], self.toe0[i] + te + self.ct[i])

    def _kinematics(self, rot):
        """Per corner: (path values, wheel-centre position and velocity in world axes), and the centres."""
        r00, r01, r02, r10, r11, r12, r20, r21, r22 = rot
        px, py, pz = self.p
        vx, vy, vz = self.v
        wx, wy, wz = self.w
        s_ = self.s
        sd_ = self.sd
        geo = []
        centres = []
        for i in range(self.n):
            g = self._path(i, s_[i])
            cx, cy, cz, dcx, dcy, dcz = g[0], g[1], g[2], g[3], g[4], g[5]
            sdi = sd_[i]
            bx = wy * cz - wz * cy + dcx * sdi
            by = wz * cx - wx * cz + dcy * sdi
            bz = wx * cy - wy * cx + dcz * sdi
            Cx = px + r00 * cx + r01 * cy + r02 * cz
            Cy = py + r10 * cx + r11 * cy + r12 * cz
            Cz = pz + r20 * cx + r21 * cy + r22 * cz
            Vx = vx + r00 * bx + r01 * by + r02 * bz
            Vy = vy + r10 * bx + r11 * by + r12 * bz
            Vz = vz + r20 * bx + r21 * by + r22 * bz
            geo.append((g, Cx, Cy, Cz, Vx, Vy, Vz))
            centres.append((Cx, Cy, Cz))
        return geo, centres

    # ----------------------------------------------------------------- step
    def step(self, ctl: Controls, ext=None):
        """Advance one step. ext: list of (force, point) in body axes, the point relative
        to the sprung CG (e.g. aerodynamic drag and side force at their centres of pressure)."""
        h = self.dt
        n = self.n
        rot = _r_tuple(self.q)
        W = _to_world(rot, *self.w)
        geo, centres = self._kinematics(rot)
        contacts = self.road.contacts(centres, self.radius)

        rows = [None] * n          # per-corner contributions, summed in mirror pairs below
        qs = [0.0] * n
        o_ntot = [0.0] * n
        o_fx = [0.0] * n
        o_fy = [0.0] * n
        o_f = [None] * n
        o_p = [None] * n
        o_n = [None] * n
        o_slide = [False] * n
        o_spring = [0.0] * n
        o_mz = [0.0] * n
        o_gamma = [0.0] * n
        keep = [None] * n
        om_new = [0.0] * n
        for i in range(n):
            (rows[i], qs[i], o_spring[i], om_new[i], keep[i], o_ntot[i], o_fx[i], o_fy[i], o_f[i], o_p[i], o_n[i],
             o_slide[i], o_mz[i], o_gamma[i]) = self._corner(i, geo[i], contacts[i], rot, W, ctl, h)

        rhs6, sm, jj = self._body_forces(rows, rot, ext)
        mm, rhs = self._mass_matrix(geo, rot, sm, jj, rhs6, qs)
        sol, (gx, gy, gz) = self._solve(mm, rhs)
        self._integrate(sol, gx, gy, gz, h)
        self.om = om_new
        self._relax_tyres(geo, keep, o_ntot, rot, h)
        self._comply(keep, o_fx, o_fy, o_mz, rot)
        self.t += h

        self.out = {"ntot": o_ntot, "fx": o_fx, "fy": o_fy, "mz": o_mz, "gamma": o_gamma, "f": o_f, "p": o_p,
                    "n": o_n, "sliding": o_slide, "spring": o_spring, "steer": list(ctl.steer), "centres": centres,
                    "camber": [geo[i][0][11] for i in range(n)], "toe": [geo[i][0][12] for i in range(n)],
                    "acc": (sol[0] + gx, sol[1] + gy, sol[2] + gz)}

    def _corner(self, i, gi, contacts, rot, W, ctl, h):
        """Forces of corner i: its generalised-force row and travel force, spring force, new wheel
        spin, the tyre frame kept for the state update, and the outputs."""
        g, Cx, Cy, Cz, Vx, Vy, Vz = gi
        camber, toe = g[11], g[12]
        ax, ay, az = self._wheel_axis(i, rot, ctl.steer[i], camber, toe)
        (fnx, fny, fnz, ntot, pex, pey, pez, nex, ney, nez, mnx, mny, mnz) = self._normal(
            i, contacts, Cx, Cy, Cz, Vx, Vy, Vz, ax, ay, az)
        (lx, ly, lz, tx, ty, tz, rhx, rhy, rhz, arx, ary, arz, vsl, vst, re, arl) = self._slip(
            i, W, Cx, Cy, Cz, Vx, Vy, Vz, ax, ay, az, pex, pey, pez, nex, ney, nez)
        om = self.om[i]
        vroll = abs(om) * re
        vcx = vsl - om * arl  # forward speed of the contact centre (the spin's part taken out)
        sa = ax * nex + ay * ney + az * nez
        gamma = math.asin(-1.0 if sa < -1.0 else (1.0 if sa > 1.0 else sa))  # inclination to the road, ISO
        fl, ft_, mz, sliding, lim = self.tyres[i].force(self.dx[i], self.dy[i], vsl, vst, vroll, vcx, gamma, ntot)
        ftx = fl * lx + ft_ * tx
        fty = fl * ly + ft_ * ty
        ftz = fl * lz + ft_ * tz
        fx_ = fnx + ftx
        fy_ = fny + fty
        fz_ = fnz + ftz
        # the aligning moment acts about the road normal, on the wheel
        mnx, mny, mnz = mnx + mz * nex, mny + mz * ney, mnz + mz * nez
        on, mbx, mby, mbz = self._spin(i, ctl, h, ax, ay, az, rhx, rhy, rhz, ftx, fty, ftz, mnx, mny, mnz, ntot, re)
        row, qsi, spring = self._generalised(i, rot, g, fx_, fy_, fz_, mbx, mby, mbz, rhx, rhy, rhz)
        keep = (lx, ly, lz, tx, ty, tz, rhx, rhy, rhz, arx, ary, arz, re, lim, arl)
        return (row, qsi, spring, on, keep, ntot, fl, ft_, (fx_, fy_, fz_), (pex, pey, pez), (nex, ney, nez),
                sliding, mz, gamma)

    def _wheel_axis(self, i, rot, steer, camber, toe):
        """Spin axis of corner i in world axes from the steer angle and the path's camber and toe."""
        side = self.side[i]
        psi = steer - side * toe
        phi = -side * camber
        cphi = math.cos(phi)
        abx, aby, abz = -cphi * math.sin(psi), cphi * math.cos(psi), math.sin(phi)
        return _to_world(rot, abx, aby, abz)

    def _normal(self, i, contacts, Cx, Cy, Cz, Vx, Vy, Vz, ax, ay, az):
        """Normal contact of the wheel disc on the road contact planes: total force (world), its
        size, the mean contact point, the unit normal and the moment of the normal forces about
        the wheel centre."""
        kz, cz_ = self.k_z[i], self.c_z[i]
        fnx = fny = fnz = 0.0
        ntot = 0.0
        pex = pey = pez = 0.0
        rad = self.radius[i]
        mnx = mny = mnz = 0.0
        for (qx_, qy_, qz_, nx, ny, nz, dep) in contacts:
            if dep <= 0.0:
                continue
            # the wheel is a disc: its lowest point toward this contact plane
            sa = ax * nx + ay * ny + az * nz
            cg2 = 1.0 - sa * sa
            cgam = math.sqrt(cg2) if cg2 > 0.0 else 0.0
            dc = rad - dep
            dep = rad * cgam - dc
            if dep <= 0.0:
                continue
            fk = kz * dep - cz_ * (Vx * nx + Vy * ny + Vz * nz)
            if fk <= 0.0:
                continue
            if cgam > 1e-12:
                ux, uy, uz = (nx - sa * ax) / cgam, (ny - sa * ay) / cgam, (nz - sa * az) / cgam
            else:
                ux, uy, uz = nx, ny, nz
            qx_ = Cx - rad * ux + dep * nx
            qy_ = Cy - rad * uy + dep * ny
            qz_ = Cz - rad * uz + dep * nz
            fnx += fk * nx
            fny += fk * ny
            fnz += fk * nz
            ntot += fk
            pex += fk * qx_
            pey += fk * qy_
            pez += fk * qz_
            # moment of this normal force about the wheel centre
            qcx, qcy, qcz = qx_ - Cx, qy_ - Cy, qz_ - Cz
            mnx += fk * (qcy * nz - qcz * ny)
            mny += fk * (qcz * nx - qcx * nz)
            mnz += fk * (qcx * ny - qcy * nx)
        if ntot > 0.0:
            pex, pey, pez = pex / ntot, pey / ntot, pez / ntot
            fm = math.sqrt(fnx * fnx + fny * fny + fnz * fnz)
            nex, ney, nez = fnx / fm, fny / fm, fnz / fm
        else:
            pex, pey, pez = Cx, Cy, Cz - rad
            nex, ney, nez = 0.0, 0.0, 1.0
        return fnx, fny, fnz, ntot, pex, pey, pez, nex, ney, nez, mnx, mny, mnz

    def _slip(self, i, W, Cx, Cy, Cz, Vx, Vy, Vz, ax, ay, az, pex, pey, pez, nex, ney, nez):
        """Tyre frame (l along the heading, t across, both in the contact plane), the contact
        point from the wheel centre, the spin's velocity per rad/s there, the slip velocities
        along l and t, the effective rolling radius, and the spin's velocity per rad/s along l."""
        Wx, Wy, Wz = W
        lx = ay * nez - az * ney
        ly = az * nex - ax * nez
        lz = ax * ney - ay * nex
        ln = math.sqrt(lx * lx + ly * ly + lz * lz)
        if ln < 1e-12:
            lx, ly, lz, ln = 1.0, 0.0, 0.0, 1.0
        lx, ly, lz = lx / ln, ly / ln, lz / ln
        tx = ney * lz - nez * ly
        ty = nez * lx - nex * lz
        tz = nex * ly - ney * lx
        rhx, rhy, rhz = pex - Cx, pey - Cy, pez - Cz
        arx = ay * rhz - az * rhy
        ary = az * rhx - ax * rhz
        arz = ax * rhy - ay * rhx
        om = self.om[i]
        vsx_ = Vx + (Wy * rhz - Wz * rhy) + om * arx
        vsy_ = Vy + (Wz * rhx - Wx * rhz) + om * ary
        vsz_ = Vz + (Wx * rhy - Wy * rhx) + om * arz
        vsl = vsx_ * lx + vsy_ * ly + vsz_ * lz
        vst = vsx_ * tx + vsy_ * ty + vsz_ * tz
        re = math.sqrt(rhx * rhx + rhy * rhy + rhz * rhz)
        arl = arx * lx + ary * ly + arz * lz
        return lx, ly, lz, tx, ty, tz, rhx, rhy, rhz, arx, ary, arz, vsl, vst, re, arl

    def _spin(self, i, ctl, h, ax, ay, az, rhx, rhy, rhz, ftx, fty, ftz, mnx, mny, mnz, ntot, re):
        """New wheel spin (implicit Coulomb torque of the brake and the rolling resistance) and the
        moment the corner passes to the body (world axes)."""
        mtx = rhy * ftz - rhz * fty + mnx
        mty = rhz * ftx - rhx * ftz + mny
        mtz = rhx * fty - rhy * ftx + mnz
        tau = mtx * ax + mty * ay + mtz * az
        if not self.spins[i]:
            return 0.0, mtx, mty, mtz
        om = self.om[i]
        jw = self.j_w[i]
        drive = ctl.drive[i]
        om_free = om + h * (drive + tau) / jw
        brake = ctl.brake[i]
        cap = brake + self.c_rr[i] * ntot * re
        dlim = h * cap / jw
        if abs(om_free) <= dlim:
            on = 0.0
        elif om_free > 0.0:
            on = om_free - dlim
        else:
            on = om_free + dlim
        t_res = (on - om_free) * jw / h
        t_brake = t_res * (brake / cap) if cap > 0.0 else 0.0
        react = drive + t_brake
        mbx = mtx - tau * ax - react * ax
        mby = mty - tau * ay - react * ay
        mbz = mtz - tau * az - react * az
        return on, mbx, mby, mbz

    def _generalised(self, i, rot, g, fx_, fy_, fz_, mbx, mby, mbz, rhx, rhy, rhz):
        """Row of corner i for the body sums (force, moment, unsprung terms of the mass matrix),
        the generalised force on its travel, and the spring force."""
        cx, cy, cz, dcx, dcy, dcz, ddx_, ddy_, ddz_, thx, thz = g[:11]
        fbx, fby, fbz = _to_body(rot, fx_, fy_, fz_)
        # one contribution per corner and quantity, so mirror pairs cancel exactly
        crx = cy * fbz - cz * fby
        cry = cz * fbx - cx * fbz
        crz = cx * fby - cy * fbx
        if not self.dof[i]:
            return (fx_, fy_, fz_, crx, cry, crz, mbx, mby, mbz) + (0.0,) * 12, 0.0, 0.0
        wx, wy, wz = self.w
        rbx, rby, rbz = _to_body(rot, rhx, rhy, rhz)
        lvx = dcx + (0.0 * rbz - thz * rby)
        lvy = dcy + (thz * rbx - thx * rbz)
        lvz = dcz + (thx * rby - 0.0 * rbx)
        si_, sdi = self.s[i], self.sd[i]
        spring = self.preload[i] + self.k_w[i] * si_ + self.c_w[i] * sdi
        if si_ > self.s_max[i]:
            spring += self.k_stop[i] * (si_ - self.s_max[i])
        elif si_ < self.s_min[i]:
            spring += self.k_stop[i] * (si_ - self.s_min[i])
        # centripetal / Coriolis of the unsprung point
        ux = wy * cz - wz * cy
        uy = wz * cx - wx * cz
        uz = wx * cy - wy * cx
        ex = wy * dcz - wz * dcy
        ey = wz * dcx - wx * dcz
        ez = wx * dcy - wy * dcx
        sd2 = sdi * sdi
        acx_i = (wy * uz - wz * uy) + 2.0 * ex * sdi + ddx_ * sd2
        acy_i = (wz * ux - wx * uz) + 2.0 * ey * sdi + ddy_ * sd2
        acz_i = (wx * uy - wy * ux) + 2.0 * ez * sdi + ddz_ * sd2
        mu_ = self.mu_[i]
        crx -= mu_ * (cy * acz_i - cz * acy_i)
        cry -= mu_ * (cz * acx_i - cx * acz_i)
        crz -= mu_ * (cx * acy_i - cy * acx_i)
        qsi = (fbx * lvx + fby * lvy + fbz * lvz) - spring - mu_ * (dcx * acx_i + dcy * acy_i + dcz * acz_i)
        c2 = cx * cx + cy * cy + cz * cz
        row = (fx_, fy_, fz_, crx, cry, crz, mbx, mby, mbz,
               mu_ * acx_i, mu_ * acy_i, mu_ * acz_i, mu_ * cx, mu_ * cy, mu_ * cz,
               mu_ * (c2 - cx * cx), mu_ * (c2 - cy * cy), mu_ * (c2 - cz * cz),
               mu_ * cx * cy, mu_ * cx * cz, mu_ * cy * cz)
        return row, qsi, spring

    def _body_forces(self, rows, rot, ext):
        """Generalised forces on the body (world force, body moment) with the gyroscopic term and
        the external loads; the unsprung first moment and inertia sums for the mass matrix."""
        r00, r01, r02, r10, r11, r12, r20, r21, r22 = rot
        (qtx, qty, qtz, qrx, qry, qrz, mbx, mby, mbz, acx, acy, acz, smx, smy, smz,
         j00, j11, j22, j01, j02, j12) = self._psum(rows)
        # body torques from the corners (world -> body) and gyroscopic term
        qrx += r00 * mbx + r10 * mby + r20 * mbz
        qry += r01 * mbx + r11 * mby + r21 * mbz
        qrz += r02 * mbx + r12 * mby + r22 * mbz
        wx, wy, wz = self.w
        i_s = self.i_s
        iwx = i_s[0][0] * wx + i_s[0][1] * wy + i_s[0][2] * wz
        iwy = i_s[1][0] * wx + i_s[1][1] * wy + i_s[1][2] * wz
        iwz = i_s[2][0] * wx + i_s[2][1] * wy + i_s[2][2] * wz
        qrx -= wy * iwz - wz * iwy
        qry -= wz * iwx - wx * iwz
        qrz -= wx * iwy - wy * iwx
        qtx -= r00 * acx + r01 * acy + r02 * acz
        qty -= r10 * acx + r11 * acy + r12 * acz
        qtz -= r20 * acx + r21 * acy + r22 * acz
        for (fex, fey, fez), (ptx, pty, ptz) in (ext or ()):
            qtx += r00 * fex + r01 * fey + r02 * fez
            qty += r10 * fex + r11 * fey + r12 * fez
            qtz += r20 * fex + r21 * fey + r22 * fez
            qrx += pty * fez - ptz * fey
            qry += ptz * fex - ptx * fez
            qrz += ptx * fey - pty * fex
        return [qtx, qty, qtz, qrx, qry, qrz], (smx, smy, smz), (j00, j11, j22, j01, j02, j12)

    def _mass_matrix(self, geo, rot, sm, jj, rhs6, qs):
        """The mass matrix M(q) and the right-hand side (body forces, then the travel forces)."""
        r00, r01, r02, r10, r11, r12, r20, r21, r22 = rot
        smx, smy, smz = sm
        j00, j11, j22, j01, j02, j12 = jj
        i_s = self.i_s
        nd = 6 + self.m
        mm = np.zeros((nd, nd))
        md = self.m_dof
        mm[0, 0] = mm[1, 1] = mm[2, 2] = md
        # M_tr = -R [S]x
        sk = ((0.0, -smz, smy), (smz, 0.0, -smx), (-smy, smx, 0.0))
        rr = ((r00, r01, r02), (r10, r11, r12), (r20, r21, r22))
        for a_ in range(3):
            for b_ in range(3):
                val = -(rr[a_][0] * sk[0][b_] + rr[a_][1] * sk[1][b_] + rr[a_][2] * sk[2][b_])
                mm[a_, 3 + b_] = val
                mm[3 + b_, a_] = val
        i00 = i_s[0][0] + j00
        i11 = i_s[1][1] + j11
        i22 = i_s[2][2] + j22
        i01 = i_s[0][1] - j01
        i02 = i_s[0][2] - j02
        i12 = i_s[1][2] - j12
        rhs = list(rhs6)
        for k, i in enumerate(self.dofidx):
            g = geo[i][0]
            cx, cy, cz, dcx, dcy, dcz = g[0], g[1], g[2], g[3], g[4], g[5]
            mu_ = self.mu_[i]
            col = 6 + k
            tx_ = mu_ * (r00 * dcx + r01 * dcy + r02 * dcz)
            ty_ = mu_ * (r10 * dcx + r11 * dcy + r12 * dcz)
            tz_ = mu_ * (r20 * dcx + r21 * dcy + r22 * dcz)
            mm[0, col] = mm[col, 0] = tx_
            mm[1, col] = mm[col, 1] = ty_
            mm[2, col] = mm[col, 2] = tz_
            crx = mu_ * (cy * dcz - cz * dcy)
            cry = mu_ * (cz * dcx - cx * dcz)
            crz = mu_ * (cx * dcy - cy * dcx)
            mm[3, col] = mm[col, 3] = crx
            mm[4, col] = mm[col, 4] = cry
            mm[5, col] = mm[col, 5] = crz
            mm[col, col] = mu_ * (dcx * dcx + dcy * dcy + dcz * dcz)
            rhs.append(qs[i])
        i10, i20, i21 = i01, i02, i12
        mm[3, 3], mm[3, 4], mm[3, 5] = i00, i01, i02
        mm[4, 3], mm[4, 4], mm[4, 5] = i10, i11, i12
        mm[5, 3], mm[5, 4], mm[5, 5] = i20, i21, i22
        return mm, np.array(rhs)

    def _solve(self, mm, rhs):
        """Generalised accelerations (gravity not included) and the gravity acceleration to add."""
        nd = 6 + self.m
        if self.lock == "heave":
            keep_idx = [2] + list(range(6, nd))
            sol = np.zeros(nd)
            sol[keep_idx] = np.linalg.solve(mm[np.ix_(keep_idx, keep_idx)], rhs[keep_idx])
            g = (0.0, 0.0, self.g[2])
        else:
            sol = self._solve_sym(mm, rhs)
            g = tuple(self.g)
        return sol.tolist(), g

    def _integrate(self, sol, gx, gy, gz, h):
        """Semi-implicit Euler: speeds first, then positions, attitude and travels with the new speeds."""
        vx, vy, vz = self.v
        wx, wy, wz = self.w
        px, py, pz = self.p
        vx += h * (sol[0] + gx)
        vy += h * (sol[1] + gy)
        vz += h * (sol[2] + gz)
        wx += h * sol[3]
        wy += h * sol[4]
        wz += h * sol[5]
        self.v = [vx, vy, vz]
        self.w = [wx, wy, wz]
        self.p = [px + h * vx, py + h * vy, pz + h * vz]
        ang = math.sqrt(wx * wx + wy * wy + wz * wz) * h
        if ang > 0.0:
            sh = math.sin(ang / 2.0) * h / ang
            qn = quat_mul(self.q, (math.cos(ang / 2.0), wx * sh, wy * sh, wz * sh))
            nq = math.sqrt(qn[0] * qn[0] + qn[1] * qn[1] + qn[2] * qn[2] + qn[3] * qn[3])
            self.q = [qn[0] / nq, qn[1] / nq, qn[2] / nq, qn[3] / nq]
        s_, sd_ = self.s, self.sd
        new_sd = list(sd_)
        new_s = list(s_)
        for k, i in enumerate(self.dofidx):
            new_sd[i] = sd_[i] + h * sol[6 + k]
            new_s[i] = s_[i] + h * new_sd[i]
        self.sd = new_sd
        self.s = new_s

    def _relax_tyres(self, geo, keep, ntot, rot, h):
        """Tyre deflection states with the new speeds and spins (geometry and tyre frame of this step)."""
        r00, r01, r02, r10, r11, r12, r20, r21, r22 = rot
        vx, vy, vz = self.v
        wx, wy, wz = self.w
        Wx, Wy, Wz = _to_world(rot, wx, wy, wz)
        n = self.n
        new_dx = [0.0] * n
        new_dy = [0.0] * n
        for i in range(n):
            if ntot[i] <= 0.0:
                continue
            g = geo[i][0]
            cx, cy, cz, dcx, dcy, dcz = g[0], g[1], g[2], g[3], g[4], g[5]
            lx, ly, lz, tx, ty, tz, rhx, rhy, rhz, arx, ary, arz, re, lim, arl = keep[i]
            sdi = self.sd[i]
            bx = wy * cz - wz * cy + dcx * sdi
            by = wz * cx - wx * cz + dcy * sdi
            bz = wx * cy - wy * cx + dcz * sdi
            on = self.om[i]
            vsx_ = vx + r00 * bx + r01 * by + r02 * bz + (Wy * rhz - Wz * rhy) + on * arx
            vsy_ = vy + r10 * bx + r11 * by + r12 * bz + (Wz * rhx - Wx * rhz) + on * ary
            vsz_ = vz + r20 * bx + r21 * by + r22 * bz + (Wx * rhy - Wy * rhx) + on * arz
            vsl = vsx_ * lx + vsy_ * ly + vsz_ * lz
            vst = vsx_ * tx + vsy_ * ty + vsz_ * tz
            new_dx[i], new_dy[i] = self.tyres[i].relax(self.dx[i], self.dy[i], vsl, vst, abs(on) * re,
                                                       vsl - on * arl, lim, h)
        self.dx = new_dx
        self.dy = new_dy

    def _comply(self, keep, fl, ft, mz, rot):
        """Toe and camber of the K&C compliance for the next step: the table's compliance at the
        travel times this step's tyre force at the contact (body x, y) and aligning moment. The
        tables are for a left wheel, so a right wheel's Fy and Mz go in mirrored."""
        for i in range(self.n):
            rows = self.comp[i]
            if rows is None or keep[i] is None:
                continue
            lo, hi = self.s_lo[i], self.s_hi[i]
            s = self.s[i]
            sc = lo if s < lo else (hi if s > hi else s)
            vals = []
            for row in rows:
                v = row[0]
                for ck in row[1:]:
                    v = v * sc + ck
                vals.append(v)
            lx, ly, lz, tx, ty, tz = keep[i][:6]
            lbx, lby, _ = _to_body(rot, lx, ly, lz)
            tbx, tby, _ = _to_body(rot, tx, ty, tz)
            fx = fl[i] * lbx + ft[i] * tbx
            fy = fl[i] * lby + ft[i] * tby
            m = mz[i]
            if self.side[i] < 0:
                fy, m = -fy, -m
            # COMPLIANCE_KEYS: toe and camber per fx, per fy, per mz
            self.ct[i] = vals[0] * fx + vals[2] * fy + vals[4] * m
            self.cc[i] = vals[1] * fx + vals[3] * fy + vals[5] * m

    def _psum(self, rows: list) -> list:
        """Column sums of the per-corner rows: each mirror pair (L + R) first, then the pairs
        in order, so a mirrored state gives exactly mirrored sums."""
        tot = None
        for grp in self.groups:
            if len(grp) == 2:
                part = [a + b for a, b in zip(rows[grp[0]], rows[grp[1]])]
            else:
                part = list(rows[grp[0]])
            tot = part if tot is None else [a + b for a, b in zip(tot, part)]
        return tot

    def _solve_sym(self, mm: np.ndarray, rhs: np.ndarray) -> np.ndarray:
        """Solve M x = b on mirror-pair sums/differences (exact zeros stay zero)."""
        if not self.pairs:
            return np.linalg.solve(mm, rhs)
        m2 = mm
        b2 = rhs
        for i, j in self.pairs:
            ci, cj = m2[:, i].copy(), m2[:, j].copy()
            m2[:, i], m2[:, j] = ci + cj, ci - cj
        for i, j in self.pairs:
            ri, rj = m2[i, :].copy(), m2[j, :].copy()
            m2[i, :], m2[j, :] = ri + rj, ri - rj
            bi, bj = b2[i], b2[j]
            b2[i], b2[j] = bi + bj, bi - bj
        y = np.linalg.solve(m2, b2)
        x = y.copy()
        for i, j in self.pairs:
            x[i], x[j] = y[i] + y[j], y[i] - y[j]
        return x

    # -------------------------------------------------------------- helpers
    def rotation(self) -> np.ndarray:
        return quat_to_r(self.q)

    def attitude(self) -> tuple[float, float, float]:
        return euler_from_r(self.rotation())

    def design_origin(self) -> np.ndarray:
        """World position of the body point that is the design origin at the design pose."""
        return np.array(self.p) + self.rotation() @ (-self.veh.cg_s)

    def wheel_centres(self) -> np.ndarray:
        r = self.rotation()
        out = []
        for i in range(self.n):
            g = self._path(i, self.s[i])
            out.append(np.array(self.p) + r @ np.array(g[:3]))
        return np.array(out)

    def cg_world(self) -> np.ndarray:
        """Whole-vehicle centre of mass (sprung + unsprung)."""
        cw = self.wheel_centres()
        mu = np.array(self.mu_)
        m = self.veh.m_s + float(mu.sum())
        return (self.veh.m_s * np.array(self.p) + (mu[:, None] * cw).sum(axis=0)) / m

    def speed(self) -> float:
        return math.sqrt(sum(x * x for x in self.v))

    def forward_speed(self) -> float:
        r = self.rotation()
        return float(np.array(self.v) @ r[:, 0])

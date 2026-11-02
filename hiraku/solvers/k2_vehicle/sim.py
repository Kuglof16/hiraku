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
wheel stays exactly at zero spin). The tyre's tangential force comes from a
deflection state d with relaxation length sigma: dd/dt = v_slip - (|w r|/sigma) d,
F = -k d, k = C / sigma, so a standing tyre is a spring and does not creep.

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
from .units import G0

LOW_SPEED_DAMPING_RATIO = 0.7   # tyre deflection-state damping at standstill (fades out above V_LOW)
V_LOW = 1.0                     # m/s


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
        self.sigma = [f(c.sigma) for c in cs]
        self.kx = [f(c.c_long / c.sigma) for c in cs]
        self.ky = [f(c.c_lat / c.sigma) for c in cs]
        self.mu = [f(c.mu) for c in cs]
        self.c_rr = [f(c.c_rr) for c in cs]
        self.j_w = [f(max(c.j_w, 1e-6)) for c in cs]
        self.spins = [c.kind == "wheel" for c in cs]
        self.caster = [c.kind == "caster" for c in cs]
        self.k_w = [f(c.k_w) for c in cs]
        self.c_w = [f(c.c_w) for c in cs]
        self.preload = [f(c.preload) for c in cs]
        self.s_min = [f(c.s_min) for c in cs]
        self.s_max = [f(c.s_max) for c in cs]
        self.k_stop = [f(c.k_stop) for c in cs]
        m_corner = [c.static_load / G0 for c in cs]
        self.cx_ls = [2.0 * LOW_SPEED_DAMPING_RATIO * math.sqrt(k * m) for k, m in zip(self.kx, m_corner)]
        self.cy_ls = [2.0 * LOW_SPEED_DAMPING_RATIO * math.sqrt(k * m) for k, m in zip(self.ky, m_corner)]
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
        self.out = {}
        defl = [c.static_load / c.k_z for c in veh.corners]
        self.p[2] -= sum(defl) / n

    _STATE = ("t", "p", "q", "v", "w", "s", "sd", "om", "dx", "dy")

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
        knuckle rotation per travel (x and z), camber and toe of corner i."""
        hx, hy, hz = self.hard[i]
        if not self.dof[i]:
            return hx, hy, hz, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, self.camber0[i], self.toe0[i]
        if not self.kc[i]:
            return hx, hy, hz + s, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
        lo, hi = self.s_lo[i], self.s_hi[i]
        sc = lo if s < lo else (hi if s > hi else s)
        ext = s - sc
        vals = []
        for row in self.coef[i]:
            v, d1, d2 = row[0], 0.0, 0.0
            for ck in row[1:]:
                d2 = d2 * sc + 2.0 * d1
                d1 = d1 * sc + v
                v = v * sc + ck
            if ext != 0.0:
                v = v + d1 * ext
                d2 = 0.0
            vals.append((v, d1, d2))
        sd_ = -1.0 if self.side[i] < 0 else 1.0
        (x, x1, x2), (y, y1, y2), (z, z1, z2), (cb, cb1, _), (te, te1, _) = vals
        side = self.side[i]
        return (hx + x, hy + sd_ * y, hz + z, x1, sd_ * y1, z1, x2, sd_ * y2, z2,
                -side * cb1, -side * te1, self.camber0[i] + cb, self.toe0[i] + te)

    # ----------------------------------------------------------------- step
    def step(self, ctl: Controls, f_ext_body=None, f_ext_point_body=None):
        """Advance one step. f_ext_body: an external force (body axes) at body point
        f_ext_point_body (relative to the sprung CG), e.g. aerodynamics."""
        h = self.dt
        n = self.n
        r00, r01, r02, r10, r11, r12, r20, r21, r22 = _r_tuple(self.q)
        px, py, pz = self.p
        vx, vy, vz = self.v
        wx, wy, wz = self.w
        Wx = r00 * wx + r01 * wy + r02 * wz
        Wy = r10 * wx + r11 * wy + r12 * wz
        Wz = r20 * wx + r21 * wy + r22 * wz
        s_ = self.s
        sd_ = self.sd

        geo = []
        centres = []
        for i in range(n):
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
        keep = [None] * n
        om_new = [0.0] * n
        steer = ctl.steer
        for i in range(n):
            g, Cx, Cy, Cz, Vx, Vy, Vz = geo[i]
            cx, cy, cz, dcx, dcy, dcz, ddx_, ddy_, ddz_, thx, thz, camber, toe = g
            side = self.side[i]
            psi = steer[i] - side * toe
            phi = -side * camber
            cphi = math.cos(phi)
            abx, aby, abz = -cphi * math.sin(psi), cphi * math.cos(psi), math.sin(phi)
            ax = r00 * abx + r01 * aby + r02 * abz
            ay = r10 * abx + r11 * aby + r12 * abz
            az = r20 * abx + r21 * aby + r22 * abz

            # normal contact
            kz, cz_ = self.k_z[i], self.c_z[i]
            fnx = fny = fnz = 0.0
            ntot = 0.0
            pex = pey = pez = 0.0
            rad = self.radius[i]
            mnx = mny = mnz = 0.0
            for (qx_, qy_, qz_, nx, ny, nz, dep) in contacts[i]:
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

            # tyre frame and slip
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
            vroll = abs(om) * re
            lam = vroll / self.sigma[i]
            fade = 1.0 / (1.0 + (vroll / V_LOW) ** 2)
            dxi, dyi = self.dx[i], self.dy[i]
            if self.caster[i]:
                rr = self.c_rr[i] * ntot / math.sqrt(vsl * vsl + vst * vst + 1e-4)
                fl, ft_ = -rr * vsl, -rr * vst
            else:
                fl = -self.kx[i] * dxi - self.cx_ls[i] * fade * (vsl - lam * dxi)
                ft_ = -self.ky[i] * dyi - self.cy_ls[i] * fade * (vst - lam * dyi)
            lim = self.mu[i] * ntot
            mag = math.sqrt(fl * fl + ft_ * ft_)
            sliding = mag > lim
            if sliding:
                sc = lim / mag if mag > 0.0 else 0.0
                fl, ft_ = fl * sc, ft_ * sc
            ftx = fl * lx + ft_ * tx
            fty = fl * ly + ft_ * ty
            ftz = fl * lz + ft_ * tz
            fx_ = fnx + ftx
            fy_ = fny + fty
            fz_ = fnz + ftz

            # spin: implicit Coulomb torque of the brake and the rolling resistance
            mtx = rhy * ftz - rhz * fty + mnx
            mty = rhz * ftx - rhx * ftz + mny
            mtz = rhx * fty - rhy * ftx + mnz
            tau = mtx * ax + mty * ay + mtz * az
            if self.spins[i]:
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
            else:
                on = 0.0
                mbx, mby, mbz = mtx, mty, mtz
            om_new[i] = on

            # generalised forces of this corner
            fbx = r00 * fx_ + r10 * fy_ + r20 * fz_
            fby = r01 * fx_ + r11 * fy_ + r21 * fz_
            fbz = r02 * fx_ + r12 * fy_ + r22 * fz_
            # one contribution per corner and quantity, so mirror pairs cancel exactly
            crx = cy * fbz - cz * fby
            cry = cz * fbx - cx * fbz
            crz = cx * fby - cy * fbx
            if self.dof[i]:
                rbx = r00 * rhx + r10 * rhy + r20 * rhz
                rby = r01 * rhx + r11 * rhy + r21 * rhz
                rbz = r02 * rhx + r12 * rhy + r22 * rhz
                lvx = dcx + (0.0 * rbz - thz * rby)
                lvy = dcy + (thz * rbx - thx * rbz)
                lvz = dcz + (thx * rby - 0.0 * rbx)
                si_, sdi = s_[i], sd_[i]
                spring = self.preload[i] + self.k_w[i] * si_ + self.c_w[i] * sdi
                if si_ > self.s_max[i]:
                    spring += self.k_stop[i] * (si_ - self.s_max[i])
                elif si_ < self.s_min[i]:
                    spring += self.k_stop[i] * (si_ - self.s_min[i])
                o_spring[i] = spring
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
                qs[i] = (fbx * lvx + fby * lvy + fbz * lvz) - spring - mu_ * (dcx * acx_i + dcy * acy_i + dcz * acz_i)
                c2 = cx * cx + cy * cy + cz * cz
                rows[i] = (fx_, fy_, fz_, crx, cry, crz, mbx, mby, mbz,
                           mu_ * acx_i, mu_ * acy_i, mu_ * acz_i, mu_ * cx, mu_ * cy, mu_ * cz,
                           mu_ * (c2 - cx * cx), mu_ * (c2 - cy * cy), mu_ * (c2 - cz * cz),
                           mu_ * cx * cy, mu_ * cx * cz, mu_ * cy * cz)
            else:
                rows[i] = (fx_, fy_, fz_, crx, cry, crz, mbx, mby, mbz) + (0.0,) * 12

            o_ntot[i] = ntot
            o_fx[i] = fl
            o_fy[i] = ft_
            o_f[i] = (fx_, fy_, fz_)
            o_p[i] = (pex, pey, pez)
            o_n[i] = (nex, ney, nez)
            o_slide[i] = sliding
            keep[i] = (lx, ly, lz, tx, ty, tz, rhx, rhy, rhz, arx, ary, arz, re, lim)

        (qtx, qty, qtz, qrx, qry, qrz, mbx, mby, mbz, acx, acy, acz, smx, smy, smz,
         j00, j11, j22, j01, j02, j12) = self._psum(rows)
        # body torques from the corners (world -> body) and gyroscopic term
        qrx += r00 * mbx + r10 * mby + r20 * mbz
        qry += r01 * mbx + r11 * mby + r21 * mbz
        qrz += r02 * mbx + r12 * mby + r22 * mbz
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
        if f_ext_body is not None:
            fex, fey, fez = f_ext_body
            qtx += r00 * fex + r01 * fey + r02 * fez
            qty += r10 * fex + r11 * fey + r12 * fez
            qtz += r20 * fex + r21 * fey + r22 * fez
            ptx, pty, ptz = f_ext_point_body if f_ext_point_body is not None else (0.0, 0.0, 0.0)
            qrx += pty * fez - ptz * fey
            qry += ptz * fex - ptx * fez
            qrz += ptx * fey - pty * fex

        # mass matrix
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
        rhs = [qtx, qty, qtz, qrx, qry, qrz]
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
        rhs = np.array(rhs)

        if self.lock == "heave":
            keep_idx = [2] + list(range(6, nd))
            sol = np.zeros(nd)
            sol[keep_idx] = np.linalg.solve(mm[np.ix_(keep_idx, keep_idx)], rhs[keep_idx])
            gx, gy, gz = 0.0, 0.0, self.g[2]
        else:
            sol = self._solve_sym(mm, rhs)
            gx, gy, gz = self.g
        sol = sol.tolist()

        # semi-implicit Euler
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
        new_sd = list(sd_)
        new_s = list(s_)
        for k, i in enumerate(self.dofidx):
            new_sd[i] = sd_[i] + h * sol[6 + k]
            new_s[i] = s_[i] + h * new_sd[i]
        self.sd = new_sd
        self.s = new_s
        self.om = om_new

        # tyre deflection states with the new speeds (geometry of this step)
        Wx = r00 * wx + r01 * wy + r02 * wz
        Wy = r10 * wx + r11 * wy + r12 * wz
        Wz = r20 * wx + r21 * wy + r22 * wz
        new_dx = [0.0] * n
        new_dy = [0.0] * n
        for i in range(n):
            if self.caster[i] or o_ntot[i] <= 0.0:
                continue
            g = geo[i][0]
            cx, cy, cz, dcx, dcy, dcz = g[0], g[1], g[2], g[3], g[4], g[5]
            lx, ly, lz, tx, ty, tz, rhx, rhy, rhz, arx, ary, arz, re, lim = keep[i]
            sdi = new_sd[i]
            bx = wy * cz - wz * cy + dcx * sdi
            by = wz * cx - wx * cz + dcy * sdi
            bz = wx * cy - wy * cx + dcz * sdi
            on = om_new[i]
            vsx_ = vx + r00 * bx + r01 * by + r02 * bz + (Wy * rhz - Wz * rhy) + on * arx
            vsy_ = vy + r10 * bx + r11 * by + r12 * bz + (Wz * rhx - Wx * rhz) + on * ary
            vsz_ = vz + r20 * bx + r21 * by + r22 * bz + (Wx * rhy - Wy * rhx) + on * arz
            vsl = vsx_ * lx + vsy_ * ly + vsz_ * lz
            vst = vsx_ * tx + vsy_ * ty + vsz_ * tz
            lam = abs(on) * re / self.sigma[i]
            if lam * h < 1e-9:
                e, gg = 1.0, h
            else:
                e = math.exp(-lam * h)
                gg = (1.0 - e) / lam
            dxn = self.dx[i] * e + vsl * gg
            dyn = self.dy[i] * e + vst * gg
            fxe, fye = self.kx[i] * dxn, self.ky[i] * dyn
            fe = math.sqrt(fxe * fxe + fye * fye)
            if fe > lim:
                sc = lim / fe
                dxn, dyn = dxn * sc, dyn * sc
            new_dx[i] = dxn
            new_dy[i] = dyn
        self.dx = new_dx
        self.dy = new_dy
        self.t += h

        self.out = {"ntot": o_ntot, "fx": o_fx, "fy": o_fy, "f": o_f, "p": o_p, "n": o_n, "sliding": o_slide,
                    "spring": o_spring, "steer": list(steer), "centres": centres,
                    "camber": [geo[i][0][11] for i in range(n)], "toe": [geo[i][0][12] for i in range(n)],
                    "acc": (sol[0] + gx, sol[1] + gy, sol[2] + gz)}

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

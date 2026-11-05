"""The explicit time step of the beam frame: co-rotational beams with plastic hinges, a rigid wall.

advance() is advance_py() compiled by numba (hiraku.solvers.k2_vehicle.jit); both run the same source.

Per step (central differences, the velocities at the half steps):
1. each element that is not broken: its co-rotated frame from the two node positions and the mean of the
   nodes' y axes (Crisfield's co-rotational beam, as written by Battini and Pacoste: r1 along the chord,
   r3 = r1 x q / |r1 x q| with q the mean of the two nodes' local y axes, r2 = r3 x r1); the local end
   rotations the logarithm of each node's triad seen from that frame; the local axial stretch l - l0;
2. the local forces from the linear Euler-Bernoulli element on those small local deformations, with
   the plastic parts taken out: N = EA / l0 (u - u_p), T = GJ / l0 (phi - phi_p), the end moments
   EI / l0 [4 2; 2 4] (theta - theta_p) about local y and z;
3. return mapping, elastic-perfectly plastic: |N| <= Np = A fy (the excess goes into u_p); |T| <= Tp;
   the end moment about each local axis <= its plastic moment reduced by the axial force,
   Mp (1 - (N / Np)^2) (the interaction of a thin tube, applied about each axis on its own); the
   excess rotation stays as the hinge's plastic rotation (no spring back);
4. an element whose plastic hinge rotation at either end, sqrt(theta_p,y^2 + theta_p,z^2), passes
   hinge_lim breaks: it is removed and carries nothing from then on;
5. the shear forces from the moments (V = (M_i + M_j) / l), the forces and moments to the nodes through
   the co-rotated frame (they balance exactly: the momentum and angular momentum of the frame are kept);
6. the wall, a rigid plane x = xw: a node past it gets the penalty force kpen (x - xw) back, and a
   Coulomb friction force against its sliding velocity, at most mu times that force (and at most the
   force that stops the sliding in half a step);
7. the velocities (mass-proportional damping alpha, 0 in a crash) and the positions; the node rotations
   R <- exp([w dt]x) R with the spin w in the design frame (isotropic rotational inertia: no gyroscopic term).
"""

from __future__ import annotations

import math

import numpy as np

from ..jit import njit


def _advance(nsteps, dt, x, v, R, w, m, J, con, l0, e0, prop, up, tp, thp, alive, yielded, fix, vpre, fext,
             alpha, wall_on, xw, kpen, mu, hinge_lim, fint, imp):
    n = x.shape[0]
    ne = con.shape[0]
    Ti = np.empty((3, 3))
    Tj = np.empty((3, 3))
    Rr = np.empty((3, 3))
    Lm = np.empty((3, 3))
    th = np.empty((2, 3))
    Mi = np.empty(2)
    Mj = np.empty(2)
    fl = np.empty(6)
    ml = np.empty(6)
    fc = np.empty(3)
    damp_a = 1.0 - 0.5 * alpha * dt
    damp_b = 1.0 / (1.0 + 0.5 * alpha * dt)
    for _ in range(nsteps):
        for i in range(n):
            for c in range(6):
                fint[i, c] = 0.0
        # -- elements
        for e in range(ne):
            if alive[e] == 0:
                continue
            a = con[e, 0]
            b = con[e, 1]
            dx = x[b, 0] - x[a, 0]
            dy = x[b, 1] - x[a, 1]
            dz = x[b, 2] - x[a, 2]
            ln = math.sqrt(dx * dx + dy * dy + dz * dz)
            r1x = dx / ln
            r1y = dy / ln
            r1z = dz / ln
            for p in range(3):
                for q in range(3):
                    sa = 0.0
                    sb = 0.0
                    for k in range(3):
                        sa += R[a, p, k] * e0[e, k, q]
                        sb += R[b, p, k] * e0[e, k, q]
                    Ti[p, q] = sa
                    Tj[p, q] = sb
            qx = 0.5 * (Ti[0, 1] + Tj[0, 1])
            qy = 0.5 * (Ti[1, 1] + Tj[1, 1])
            qz = 0.5 * (Ti[2, 1] + Tj[2, 1])
            r3x = r1y * qz - r1z * qy
            r3y = r1z * qx - r1x * qz
            r3z = r1x * qy - r1y * qx
            s3 = math.sqrt(r3x * r3x + r3y * r3y + r3z * r3z)
            r3x /= s3
            r3y /= s3
            r3z /= s3
            Rr[0, 0] = r1x
            Rr[1, 0] = r1y
            Rr[2, 0] = r1z
            Rr[0, 2] = r3x
            Rr[1, 2] = r3y
            Rr[2, 2] = r3z
            Rr[0, 1] = r3y * r1z - r3z * r1y
            Rr[1, 1] = r3z * r1x - r3x * r1z
            Rr[2, 1] = r3x * r1y - r3y * r1x
            # local end rotations: log(Rr^T T)
            for end in range(2):
                for p in range(3):
                    for q in range(3):
                        s = 0.0
                        for k in range(3):
                            s += Rr[k, p] * (Ti[k, q] if end == 0 else Tj[k, q])
                        Lm[p, q] = s
                vx = Lm[2, 1] - Lm[1, 2]
                vy = Lm[0, 2] - Lm[2, 0]
                vz = Lm[1, 0] - Lm[0, 1]
                sn = 0.5 * math.sqrt(vx * vx + vy * vy + vz * vz)
                cs = 0.5 * (Lm[0, 0] + Lm[1, 1] + Lm[2, 2] - 1.0)
                ang = math.atan2(sn, cs)
                f = 0.5 * ang / sn if sn > 1e-300 else 0.5
                th[end, 0] = f * vx
                th[end, 1] = f * vy
                th[end, 2] = f * vz
            L0 = l0[e]
            ea = prop[e, 0]
            gj = prop[e, 1]
            npl = prop[e, 4]
            tpl = prop[e, 7]
            # axial
            ka = ea / L0
            N = ka * (ln - L0 - up[e])
            if abs(N) > npl:
                sg = 1.0 if N > 0.0 else -1.0
                up[e] += (abs(N) - npl) / ka * sg
                N = sg * npl
                yielded[e] = 1
            # torsion
            kt = gj / L0
            T = kt * (th[1, 0] - th[0, 0] - tp[e])
            if abs(T) > tpl:
                sg = 1.0 if T > 0.0 else -1.0
                tp[e] += (abs(T) - tpl) / kt * sg
                T = sg * tpl
                yielded[e] = 1
            red = N / npl
            fac = 1.0 - red * red
            if fac < 0.0:
                fac = 0.0
            # bending about local y (plane 0) and z (plane 1), return mapping on the two end moments
            for pl in range(2):
                kb = prop[e, 2 + pl] / L0
                cap = prop[e, 5 + pl] * fac
                ei = th[0, 1 + pl] - thp[e, 0, pl]
                ej = th[1, 1 + pl] - thp[e, 1, pl]
                mi0 = kb * (4.0 * ei + 2.0 * ej)
                mj0 = kb * (2.0 * ei + 4.0 * ej)
                mi = mi0
                mj = mj0
                yi = abs(mi) > cap
                yj = abs(mj) > cap
                if yi and not yj:
                    mic = cap if mi > 0.0 else -cap
                    d = (mi - mic) / (4.0 * kb)
                    mj = mj - 2.0 * kb * d
                    if abs(mj) > cap:
                        yj = True
                    else:
                        thp[e, 0, pl] += d
                        mi = mic
                elif yj and not yi:
                    mjc = cap if mj > 0.0 else -cap
                    d = (mj - mjc) / (4.0 * kb)
                    mi = mi - 2.0 * kb * d
                    if abs(mi) > cap:
                        yi = True
                    else:
                        thp[e, 1, pl] += d
                        mj = mjc
                if yi and yj:
                    mic = cap if mi > 0.0 else -cap
                    mjc = cap if mj > 0.0 else -cap
                    ri = mi0 - mic
                    rj = mj0 - mjc
                    thp[e, 0, pl] += (4.0 * ri - 2.0 * rj) / (12.0 * kb)
                    thp[e, 1, pl] += (-2.0 * ri + 4.0 * rj) / (12.0 * kb)
                    mi = mic
                    mj = mjc
                if yi or yj:
                    yielded[e] = 1
                Mi[pl] = mi
                Mj[pl] = mj
            hi = math.sqrt(thp[e, 0, 0] * thp[e, 0, 0] + thp[e, 0, 1] * thp[e, 0, 1])
            hj = math.sqrt(thp[e, 1, 0] * thp[e, 1, 0] + thp[e, 1, 1] * thp[e, 1, 1])
            if hi > hinge_lim or hj > hinge_lim:
                alive[e] = 0
                continue
            vy_ = (Mi[1] + Mj[1]) / ln
            vz_ = -(Mi[0] + Mj[0]) / ln
            # local end forces and moments (the element's internal force vector)
            fl[0] = -N
            fl[1] = vy_
            fl[2] = vz_
            fl[3] = N
            fl[4] = -vy_
            fl[5] = -vz_
            ml[0] = -T
            ml[1] = Mi[0]
            ml[2] = Mi[1]
            ml[3] = T
            ml[4] = Mj[0]
            ml[5] = Mj[1]
            for c in range(3):
                fint[a, c] += Rr[c, 0] * fl[0] + Rr[c, 1] * fl[1] + Rr[c, 2] * fl[2]
                fint[b, c] += Rr[c, 0] * fl[3] + Rr[c, 1] * fl[4] + Rr[c, 2] * fl[5]
                fint[a, 3 + c] += Rr[c, 0] * ml[0] + Rr[c, 1] * ml[1] + Rr[c, 2] * ml[2]
                fint[b, 3 + c] += Rr[c, 0] * ml[3] + Rr[c, 1] * ml[4] + Rr[c, 2] * ml[5]
        # -- nodes: wall, velocities, positions, rotations
        for i in range(n):
            fc[0] = 0.0
            fc[1] = 0.0
            fc[2] = 0.0
            if wall_on != 0:
                g = x[i, 0] - xw
                if g > 0.0:
                    fn = kpen[i] * g
                    fc[0] = -fn
                    vt = math.sqrt(v[i, 1] * v[i, 1] + v[i, 2] * v[i, 2])
                    if vt > 0.0 and mu > 0.0:
                        ft = mu * fn
                        stop = 0.5 * m[i] * vt / dt
                        if stop < ft:
                            ft = stop
                        fc[1] = -ft * v[i, 1] / vt
                        fc[2] = -ft * v[i, 2] / vt
                    for c in range(3):
                        imp[c] += fc[c] * dt
            for c in range(3):
                if fix[i, c] != 0:
                    v[i, c] = vpre[i, c]
                else:
                    v[i, c] = (damp_a * v[i, c] + dt * (fext[i, c] - fint[i, c] + fc[c]) / m[i]) * damp_b
                x[i, c] += dt * v[i, c]
                if fix[i, 3 + c] != 0:
                    w[i, c] = vpre[i, 3 + c]
                else:
                    w[i, c] = (damp_a * w[i, c] + dt * (fext[i, 3 + c] - fint[i, 3 + c]) / J[i]) * damp_b
            ax = w[i, 0] * dt
            ay = w[i, 1] * dt
            az = w[i, 2] * dt
            t2 = ax * ax + ay * ay + az * az
            if t2 > 0.0:
                t = math.sqrt(t2)
                if t < 1e-4:
                    s1 = 1.0 - t2 / 6.0
                    s2 = 0.5 - t2 / 24.0
                else:
                    s1 = math.sin(t) / t
                    s2 = (1.0 - math.cos(t)) / t2
                # exp([a]x) = I + s1 [a]x + s2 [a]x^2, applied on the left
                for q in range(3):
                    r0 = R[i, 0, q]
                    r1_ = R[i, 1, q]
                    r2 = R[i, 2, q]
                    c0 = ay * r2 - az * r1_
                    c1 = az * r0 - ax * r2
                    c2 = ax * r1_ - ay * r0
                    d0 = ay * c2 - az * c1
                    d1 = az * c0 - ax * c2
                    d2 = ax * c1 - ay * c0
                    R[i, 0, q] = r0 + s1 * c0 + s2 * d0
                    R[i, 1, q] = r1_ + s1 * c1 + s2 * d1
                    R[i, 2, q] = r2 + s1 * c2 + s2 * d2


advance_py = _advance
advance = njit(_advance)

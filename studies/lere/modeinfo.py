"""Mode shape / strain digest (library): per mode, the members with the largest motion and strain share.
strain share = sum over the member's elements of (relative von Mises)^2 x element area, over the total (a proxy of
where the mode's strain energy is; t not weighted)."""
import numpy as np


def areas(X, E):
    a = X[E[:, 2]] - X[E[:, 0]]
    b = X[E[:, 3]] - X[E[:, 1]]
    return 0.5 * np.linalg.norm(np.cross(a, b), axis=1)


def digest(X, E, member, shape, strain, top=6):
    member = np.asarray(member)
    A = areas(X, E)
    w = strain ** 2 * A
    tot = float(w.sum()) or 1.0
    um = np.linalg.norm(shape, axis=1)[E].mean(axis=1)
    out_s, out_u = {}, {}
    for m in np.unique(member):
        k = member == m
        out_s[str(m)] = float(w[k].sum()) / tot
        out_u[str(m)] = float(um[k].mean())
    s = sorted(out_s.items(), key=lambda x: -x[1])[:top]
    u = sorted(out_u.items(), key=lambda x: -x[1])[:top]
    i = int(np.argmax(strain))
    d = np.abs(shape).sum(axis=0)
    return {"strain_share": [(m, round(v, 3)) for m, v in s], "motion": [(m, round(v, 2)) for m, v in u],
            "hot_member": str(member[i]), "hot_xyz": X[E[i]].mean(axis=0).round(0).tolist(),
            "motion_xyz_share": (d / d.sum()).round(2).tolist()}


def group_share(member, X, E, strain, groups):
    A = areas(X, E)
    w = strain ** 2 * A
    tot = float(w.sum()) or 1.0
    member = np.asarray(member)
    out = {}
    for g, pre in groups.items():
        k = np.array([str(m).startswith(pre) for m in member])
        out[g] = round(float(w[k].sum()) / tot, 3)
    return out


GROUPS = {"rollbar": ("rollbar_", "rops_"), "front_tower": ("tower_post_LA", "tower_post_RA", "tower_arm_LA",
          "tower_arm_RA", "tower_LA", "tower_RA"), "rear_tower": ("tower_post_LB", "tower_post_RB", "tower_arm_LB",
          "tower_arm_RB", "tower_brace", "tower_LB", "tower_RB"), "dash": ("dash_",), "rails": ("rail_",),
          "cross": ("cross_",)}

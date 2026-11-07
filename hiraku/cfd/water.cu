// MLS-MPM water for hiraku.cfd.water: Hu et al. 2018 (DOI 10.1145/3197517.3201293), quadratic B-splines, APIC.
//
// One time step, in this order:
//   grid_clear
//   p2g          particles to nodes, with atomic adds (mass, momentum, pressure)
//   grid_update  momentum to velocity, gravity, then the vehicle, the road and the walls; each change of a
//                node's velocity is summed in double as the impulse given to the water
//   g2p          nodes to particles (APIC), positions, volume ratio
//
// Units are metres, seconds and kilograms. "Kernel coordinates": node (i, j, k) stands at (i, j, k) dx.
// A particle is 16 floats: x y z, vx vy vz, C (9, by rows), e = J - 1. The pressure comes from e, which keeps the
// small volume changes that a float32 J near one would lose. Must match hiraku/cfd/water.py.

typedef unsigned int u32;

struct Params {
    int nx, ny, nz, np;          // nodes per axis, particles
    int pad, vehicle, project, ngroups;  // wall layers; the vehicle is on; particles are moved out of the shapes
    float dx, inv_dx, dt, gravity;
    float vol0, mass, k7, e_max;  // rest volume and mass of a particle, K / 7, largest e kept (no tension above e = 0)
    float e_min, px, py, pz;      // smallest e kept; vehicle origin in kernel coordinates
    float pc, ps, pvx, pvz;       // cos and sin of the pitch, velocity of the origin
    float pw, lox, loy, loz;      // pitch rate; box of the table in vehicle axes (bands included)
    float hix, hiy, hiz, bp_inv;  // and the inverse size of its lookup cells
    int bpx, bpy, bpz;            // lookup cells per axis
    float mass_limit;             // the node mass a particle sees at the density limit (see g2p)
    float crowd;                  // how much of the excess over the limit becomes compression
    int open_ends;                // 1: water leaves through the ends of the box along x (see g2p); 0: walls there
};

// The vehicle's shapes in vehicle axes: the same objects as the drawing, packed.
// shapes[4 s]: first plane, or the cylinder when the plane count is 0; [4 s + 1]: plane count; [4 s + 2]: group;
// [4 s + 3]: 1 when water at a node takes the surface velocity (a tyre), 0 when it only cannot enter (slides).
// planes[4 p]: outward normal and offset, n . x <= offset inside. cyl[12 c]: centre, axis, radius, half width, spin.
// bands[s]: a node this near a shape thinner than two cells also counts, so that nodes on both sides hold it.
struct Tables {
    const float* planes;
    const int* shapes;
    const float* bands;
    const float* cyl;
    const u32* start;
    const u32* items;
};

// The shape nearest the inside at a point in vehicle axes: its signed distance and outward normal.
// With exact, only the shapes themselves count (no bands). Returns -1 when no shape holds the point.
__device__ inline int vehicle_query(const Tables& t, const Params& p, float x, float y, float z, bool exact,
                                    float& out_d, float& out_x, float& out_y, float& out_z) {
    if (x < p.lox || y < p.loy || z < p.loz || x >= p.hix || y >= p.hiy || z >= p.hiz) return -1;
    const int cx = min((int)((x - p.lox) * p.bp_inv), p.bpx - 1);
    const int cy = min((int)((y - p.loy) * p.bp_inv), p.bpy - 1);
    const int cz = min((int)((z - p.loz) * p.bp_inv), p.bpz - 1);
    const int cell = cx + p.bpx * (cy + p.bpy * cz);
    int best = -1;
    float best_d = 1e9f;
    const u32 end = t.start[cell + 1];
    for (u32 l = t.start[cell]; l < end; l++) {
        const int s = (int)t.items[l];
        const float band = exact ? 0.0f : t.bands[s];
        const int count = t.shapes[4 * s + 1];
        float d = -1e9f, ax = 0.0f, ay = 0.0f, az = 1.0f;
        if (count > 0) {
            const int first = t.shapes[4 * s];
            for (int q = first; q < first + count; q++) {
                const float* n = t.planes + 4 * q;
                const float di = n[0] * x + n[1] * y + n[2] * z - n[3];
                if (di > d) {
                    d = di;
                    ax = n[0];
                    ay = n[1];
                    az = n[2];
                    if (d > band) break;
                }
            }
        } else {
            const float* c = t.cyl + 12 * t.shapes[4 * s];
            const float rx = x - c[0], ry = y - c[1], rz = z - c[2];
            const float along = rx * c[3] + ry * c[4] + rz * c[5];
            const float qx = rx - along * c[3], qy = ry - along * c[4], qz = rz - along * c[5];
            const float rad = sqrtf(qx * qx + qy * qy + qz * qz);
            const float side = rad - c[6];
            const float cap = fabsf(along) - c[7];
            if (side > cap && rad > 1e-6f) {
                d = side;
                ax = qx / rad;
                ay = qy / rad;
                az = qz / rad;
            } else {
                d = cap;
                const float sign = along >= 0.0f ? 1.0f : -1.0f;
                ax = sign * c[3];
                ay = sign * c[4];
                az = sign * c[5];
            }
        }
        if (exact ? d < 0.0f : d < band) {
            if (d < best_d) {
                best_d = d;
                best = s;
                out_x = ax;
                out_y = ay;
                out_z = az;
            }
        }
    }
    out_d = best_d;
    return best;
}

extern "C" __global__ void grid_clear(float* grid, Params p) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= p.nx) return;
    const int n = 4 * (i + p.nx * (blockIdx.y + p.ny * blockIdx.z));
    grid[n] = 0.0f;
    grid[n + 1] = 0.0f;
    grid[n + 2] = 0.0f;
    grid[n + 3] = 0.0f;
}

extern "C" __global__ void p2g(const float* __restrict__ part, float* __restrict__ grid, Params p) {
    const int id = blockIdx.x * blockDim.x + threadIdx.x;
    if (id >= p.np) return;
    const float* q = part + 16 * id;
    const float xn = q[0] * p.inv_dx, yn = q[1] * p.inv_dx, zn = q[2] * p.inv_dx;
    const int bi = (int)(xn - 0.5f), bj = (int)(yn - 0.5f), bk = (int)(zn - 0.5f);
    const float fx = xn - (float)bi, fy = yn - (float)bj, fz = zn - (float)bk;
    const float wx[3] = {0.5f * (1.5f - fx) * (1.5f - fx), 0.75f - (fx - 1.0f) * (fx - 1.0f), 0.5f * (fx - 0.5f) * (fx - 0.5f)};
    const float wy[3] = {0.5f * (1.5f - fy) * (1.5f - fy), 0.75f - (fy - 1.0f) * (fy - 1.0f), 0.5f * (fy - 0.5f) * (fy - 0.5f)};
    const float wz[3] = {0.5f * (1.5f - fz) * (1.5f - fz), 0.75f - (fz - 1.0f) * (fz - 1.0f), 0.5f * (fz - 0.5f) * (fz - 0.5f)};

    // Tait: pressure = K / 7 (J^-7 - 1), no tension. With y = 1/J - 1 the power is written out, so nothing cancels.
    const float e = q[15];
    const float y = -e / (1.0f + e);
    const float tait = y * (7.0f + y * (21.0f + y * (35.0f + y * (35.0f + y * (21.0f + y * (7.0f + y))))));
    const float pressure = fmaxf(p.k7 * tait, 0.0f);
    // the Kirchhoff stress J sigma = -J pressure, through the MLS-MPM force
    const float stress = p.dt * p.vol0 * 4.0f * p.inv_dx * p.inv_dx * (1.0f + e) * pressure;
    const float m = p.mass;
    const float a00 = m * q[6] + stress, a01 = m * q[7], a02 = m * q[8];
    const float a10 = m * q[9], a11 = m * q[10] + stress, a12 = m * q[11];
    const float a20 = m * q[12], a21 = m * q[13], a22 = m * q[14] + stress;
    const float px = m * q[3], py = m * q[4], pz = m * q[5];

    for (int c = 0; c < 3; c++) {
        const float dz = ((float)c - fz) * p.dx;
        for (int b = 0; b < 3; b++) {
            const float dy = ((float)b - fy) * p.dx;
            const float wyz = wy[b] * wz[c];
            const int row = bi + p.nx * ((bj + b) + p.ny * (bk + c));
            for (int a = 0; a < 3; a++) {
                const float dx = ((float)a - fx) * p.dx;
                const float w = wx[a] * wyz;
                float* g = grid + 4 * (row + a);
                atomicAdd(g, w * m);
                atomicAdd(g + 1, w * (px + a00 * dx + a01 * dy + a02 * dz));
                atomicAdd(g + 2, w * (py + a10 * dx + a11 * dy + a12 * dz));
                atomicAdd(g + 3, w * (pz + a20 * dx + a21 * dy + a22 * dz));
            }
        }
    }
}

extern "C" __global__ void grid_update(float* __restrict__ grid, const float* __restrict__ gz, const float* __restrict__ gs,
                                       const float* planes, const int* shapes, const float* bands, const float* cyl,
                                       const u32* start, const u32* items, double* imp, Params p) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= p.nx) return;
    const int j = blockIdx.y, k = blockIdx.z;
    const int n = 4 * (i + p.nx * (j + p.ny * k));
    const float m = grid[n];
    if (m <= 0.0f) return;
    const float im = 1.0f / m;
    float vx = grid[n + 1] * im, vy = grid[n + 2] * im, vz = grid[n + 3] * im - p.gravity * p.dt;
    const float x = (float)i * p.dx, y = (float)j * p.dx, z = (float)k * p.dx;

    if (p.vehicle) {
        const Tables t = {planes, shapes, bands, cyl, start, items};
        const float rx = x - p.px, ry = y - p.py, rz = z - p.pz;
        const float xl = p.pc * rx - p.ps * rz, zl = p.ps * rx + p.pc * rz;
        float d, nx, ny, nz;
        const int s = vehicle_query(t, p, xl, ry, zl, false, d, nx, ny, nz);
        if (s >= 0) {
            // the rigid vehicle's velocity at this node, then a tyre's own spin
            float bx = p.pvx + p.pw * rz, by = 0.0f, bz = p.pvz - p.pw * rx;
            const float x0 = vx, y0 = vy, z0 = vz;
            if (shapes[4 * s + 3]) {
                if (shapes[4 * s + 1] == 0) {
                    const float* c = cyl + 12 * shapes[4 * s];
                    const float ax = xl - c[0], ay = ry - c[1], az = zl - c[2];
                    const float sx = c[9] * az - c[10] * ay, sy = c[10] * ax - c[8] * az, sz = c[8] * ay - c[9] * ax;
                    bx += p.pc * sx + p.ps * sz;
                    by += sy;
                    bz += -p.ps * sx + p.pc * sz;
                }
                vx = bx;
                vy = by;
                vz = bz;
            } else {
                const float wx = p.pc * nx + p.ps * nz, wz = -p.ps * nx + p.pc * nz;
                const float ux = vx - bx, uy = vy - by, uz = vz - bz;
                const float un = ux * wx + uy * ny + uz * wz;
                if (un < 0.0f) {
                    vx = bx + ux - un * wx;
                    vy = by + uy - un * ny;
                    vz = bz + uz - un * wz;
                }
            }
            double* out = imp + 3 * shapes[4 * s + 2];
            atomicAdd(out, (double)m * ((double)vx - (double)x0));
            atomicAdd(out + 1, (double)m * ((double)vy - (double)y0));
            atomicAdd(out + 2, (double)m * ((double)vz - (double)z0));
        }
    }

    if (z <= gz[i]) {
        // the road slides: only the part of the velocity into it goes, along its normal (-slope, 0, 1)
        const float slope = gs[i];
        const float inv = 1.0f / sqrtf(1.0f + slope * slope);
        const float wx = -slope * inv, wz = inv;
        const float un = vx * wx + vz * wz;
        if (un < 0.0f) {
            double* out = imp + 3 * p.ngroups;
            atomicAdd(out, -(double)m * (double)un * (double)wx);
            atomicAdd(out + 2, -(double)m * (double)un * (double)wz);
            vx -= un * wx;
            vz -= un * wz;
        }
    }

    double* walls = imp + 3 * (p.ngroups + 1);
    if (!p.open_ends && ((i <= p.pad && vx < 0.0f) || (i >= p.nx - 1 - p.pad && vx > 0.0f))) {
        atomicAdd(walls, -(double)m * (double)vx);
        vx = 0.0f;
    }
    if ((j <= p.pad && vy < 0.0f) || (j >= p.ny - 1 - p.pad && vy > 0.0f)) {
        atomicAdd(walls + 1, -(double)m * (double)vy);
        vy = 0.0f;
    }
    if (k >= p.nz - 1 - p.pad && vz > 0.0f) {
        atomicAdd(walls + 2, -(double)m * (double)vz);
        vz = 0.0f;
    }

    grid[n + 1] = vx;
    grid[n + 2] = vy;
    grid[n + 3] = vz;
}

extern "C" __global__ void g2p(float* __restrict__ part, const float* __restrict__ grid, const float* __restrict__ gz,
                               const float* planes, const int* shapes, const float* bands, const float* cyl,
                               const u32* start, const u32* items, double* imp, Params p) {
    const int id = blockIdx.x * blockDim.x + threadIdx.x;
    if (id >= p.np) return;
    float* q = part + 16 * id;
    float x = q[0], y = q[1], z = q[2];
    const float xn = x * p.inv_dx, yn = y * p.inv_dx, zn = z * p.inv_dx;
    const int bi = (int)(xn - 0.5f), bj = (int)(yn - 0.5f), bk = (int)(zn - 0.5f);
    const float fx = xn - (float)bi, fy = yn - (float)bj, fz = zn - (float)bk;
    const float wx[3] = {0.5f * (1.5f - fx) * (1.5f - fx), 0.75f - (fx - 1.0f) * (fx - 1.0f), 0.5f * (fx - 0.5f) * (fx - 0.5f)};
    const float wy[3] = {0.5f * (1.5f - fy) * (1.5f - fy), 0.75f - (fy - 1.0f) * (fy - 1.0f), 0.5f * (fy - 0.5f) * (fy - 0.5f)};
    const float wz[3] = {0.5f * (1.5f - fz) * (1.5f - fz), 0.75f - (fz - 1.0f) * (fz - 1.0f), 0.5f * (fz - 0.5f) * (fz - 0.5f)};

    float vx = 0.0f, vy = 0.0f, vz = 0.0f, seen = 0.0f;
    float c00 = 0.0f, c01 = 0.0f, c02 = 0.0f, c10 = 0.0f, c11 = 0.0f, c12 = 0.0f, c20 = 0.0f, c21 = 0.0f, c22 = 0.0f;
    for (int c = 0; c < 3; c++) {
        const float dz = (float)c - fz;
        for (int b = 0; b < 3; b++) {
            const float dy = (float)b - fy;
            const float wyz = wy[b] * wz[c];
            const int row = bi + p.nx * ((bj + b) + p.ny * (bk + c));
            for (int a = 0; a < 3; a++) {
                const float dx = (float)a - fx;
                const float w = wx[a] * wyz;
                const float* g = grid + 4 * (row + a);
                const float ux = w * g[1], uy = w * g[2], uz = w * g[3];
                seen += w * g[0];
                vx += ux;
                vy += uy;
                vz += uz;
                c00 += ux * dx;
                c01 += ux * dy;
                c02 += ux * dz;
                c10 += uy * dx;
                c11 += uy * dy;
                c12 += uy * dz;
                c20 += uz * dx;
                c21 += uz * dy;
                c22 += uz * dz;
            }
        }
    }
    const float scale = 4.0f * p.inv_dx;
    c00 *= scale; c01 *= scale; c02 *= scale;
    c10 *= scale; c11 *= scale; c12 *= scale;
    c20 *= scale; c21 *= scale; c22 *= scale;

    x += p.dt * vx;
    y += p.dt * vy;
    z += p.dt * vz;

    if (p.open_ends && (x < (float)p.pad * p.dx || x > (float)(p.nx - 1 - p.pad) * p.dx)) {
        // It has left along the road. It waits at rest in the corner of the outer nodes, which no water reaches, and the
        // momentum it carried goes to the walls' row of the budget.
        double* out = imp + 3 * (p.ngroups + 1);
        atomicAdd(out, -(double)p.mass * (double)vx);
        atomicAdd(out + 1, -(double)p.mass * (double)vy);
        atomicAdd(out + 2, -(double)p.mass * (double)vz);
        q[0] = p.dx;
        q[1] = p.dx;
        q[2] = p.dx;
        for (int k = 3; k < 16; k++) q[k] = 0.0f;
        return;
    }

    float e = q[15];
    e += (1.0f + e) * p.dt * (c00 + c11 + c22);
    // e follows the velocity field, which cannot see particles crowding onto a boundary. Where the grid's own mass says the
    // water is denser than the limit, e is held at least this compressed, so the pressure pushes the crowd apart. It goes
    // through the same stress as any pressure, so momentum stays exact.
    if (seen > p.mass_limit) e = fminf(e, p.crowd * (p.mass_limit / seen - 1.0f));
    e = fminf(fmaxf(e, p.e_min), p.e_max);

    if (p.project) {
        // a particle inside a shape goes back out to the nearest face; its momentum is left alone
        const Tables t = {planes, shapes, bands, cyl, start, items};
        const float rx = x - p.px, ry = y - p.py, rz = z - p.pz;
        const float xl = p.pc * rx - p.ps * rz, zl = p.ps * rx + p.pc * rz;
        float d, nx, ny, nz;
        if (vehicle_query(t, p, xl, ry, zl, true, d, nx, ny, nz) >= 0) {
            const float wnx = p.pc * nx + p.ps * nz, wnz = -p.ps * nx + p.pc * nz;
            const float out = 1e-4f - d;
            x += out * wnx;
            y += out * ny;
            z += out * wnz;
        }
    }

    // never below the road, never out of the grid (positions only: the nodes have already answered)
    const float top = (float)(p.nz - 2) * p.dx;
    const float hx = (float)(p.nx - 2) * p.dx, hy = (float)(p.ny - 2) * p.dx;
    x = fminf(fmaxf(x, p.dx), hx);
    y = fminf(fmaxf(y, p.dx), hy);
    const int gi = (int)(x * p.inv_dx);
    const float gt = x * p.inv_dx - (float)gi;
    const float road = (1.0f - gt) * gz[gi] + gt * gz[gi + 1];
    z = fminf(fmaxf(z, road), top);

    q[0] = x;
    q[1] = y;
    q[2] = z;
    q[3] = vx;
    q[4] = vy;
    q[5] = vz;
    q[6] = c00;
    q[7] = c01;
    q[8] = c02;
    q[9] = c10;
    q[10] = c11;
    q[11] = c12;
    q[12] = c20;
    q[13] = c21;
    q[14] = c22;
    q[15] = e;
}

// The table at points in vehicle axes, for the checks: out[2 n] signed distance (1e9 outside all shapes), out[2 n + 1] shape.
extern "C" __global__ void probe(const float* __restrict__ pts, float* __restrict__ out, const float* planes,
                                 const int* shapes, const float* bands, const float* cyl, const u32* start,
                                 const u32* items, int count, Params p) {
    const int id = blockIdx.x * blockDim.x + threadIdx.x;
    if (id >= count) return;
    const Tables t = {planes, shapes, bands, cyl, start, items};
    float d = 1e9f, nx = 0.0f, ny = 0.0f, nz = 0.0f;
    const int s = vehicle_query(t, p, pts[3 * id], pts[3 * id + 1], pts[3 * id + 2], true, d, nx, ny, nz);
    out[2 * id] = s >= 0 ? d : 1e9f;
    out[2 * id + 1] = (float)s;
}

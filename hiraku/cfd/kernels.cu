// D3Q19 lattice Boltzmann kernels for hiraku.cfd.
//
// Two-lattice pull scheme in shifted storage h_i = f_i - w_i (32-bit floats). One time step:
//   links_compute(src)  every cut link: reflected value and momentum exchange, reading src only
//   ground_fill(src)    the moving ground, written into the ghost layer below the first row
//   links_write(src)    the reflected values go into the slots the pull below will read
//   stream_collide(src -> dst)
// The ghost layer around the interior holds the far field (fill_far) and the ground. Solid and ghost
// nodes are never updated; their slots are only read through links_write and the ghost fills.
//
// Lattice order: 0 rest; 1,2 +-x; 3,4 +-y; 5,6 +-z; 7(1,1,0) 8(-1,-1,0) 9(1,-1,0) 10(-1,1,0);
// 11(1,0,1) 12(-1,0,-1) 13(1,0,-1) 14(-1,0,1); 15(0,1,1) 16(0,-1,-1) 17(0,1,-1) 18(0,-1,1).
// Must match hiraku/cfd/lattice.py.

typedef unsigned int u32;
typedef unsigned char u8;
typedef unsigned short u16;

struct Grid {
    int nx, ny, nz;  // interior nodes
    int X, Y;        // strides with the ghost layer: X = nx + 2, Y = ny + 2
    int N;           // nodes with the ghost layer
    float tau0;      // molecular relaxation time
    float cs2;       // Smagorinsky constant squared (0: no model)
    float ux, uy, uz;  // far-field velocity at this step
    float ug, vg;    // ground velocity at this step
    float ramp;      // factor on the wall velocities of the links at this step
    float cf;        // ground friction coefficient at the first row
};

#define W0 (1.0f / 3.0f)
#define W1 (1.0f / 18.0f)
#define W2 (1.0f / 36.0f)
#define H(q) h[(q) * N + n]

#define PAIR(wt, cu, Qpi, a, b)                                   \
    {                                                             \
        const float cu_ = (cu);                                   \
        const float even_ = (wt) * (base + 4.5f * rho * cu_ * cu_ + t * ((Qpi) - tr3)); \
        const float odd_ = (wt) * 3.0f * rho * cu_;               \
        a = even_ + odd_;                                         \
        b = even_ - odd_;                                         \
    }

extern "C" __global__ void stream_collide(const float* __restrict__ src, float* __restrict__ dst,
                                          const u8* __restrict__ mask, const float* __restrict__ sx,
                                          const float* __restrict__ sy, const float* __restrict__ sz,
                                          Grid g) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= g.nx) return;
    const int j = blockIdx.y, k = blockIdx.z;
    const int X = g.X, XY = g.X * g.Y, N = g.N;
    const int n = (i + 1) + (j + 1) * X + (k + 1) * XY;
    if (mask[n]) return;

    const float f0 = src[n];
    const float f1 = src[N + n - 1];
    const float f2 = src[2 * N + n + 1];
    const float f3 = src[3 * N + n - X];
    const float f4 = src[4 * N + n + X];
    const float f5 = src[5 * N + n - XY];
    const float f6 = src[6 * N + n + XY];
    const float f7 = src[7 * N + n - 1 - X];
    const float f8 = src[8 * N + n + 1 + X];
    const float f9 = src[9 * N + n - 1 + X];
    const float f10 = src[10 * N + n + 1 - X];
    const float f11 = src[11 * N + n - 1 - XY];
    const float f12 = src[12 * N + n + 1 + XY];
    const float f13 = src[13 * N + n - 1 + XY];
    const float f14 = src[14 * N + n + 1 - XY];
    const float f15 = src[15 * N + n - X - XY];
    const float f16 = src[16 * N + n + X + XY];
    const float f17 = src[17 * N + n - X + XY];
    const float f18 = src[18 * N + n + X - XY];

    const float dr = f0 + f1 + f2 + f3 + f4 + f5 + f6 + f7 + f8 + f9 + f10 + f11 + f12 + f13 + f14 + f15 + f16 + f17 + f18;
    const float rho = 1.0f + dr;
    const float ux = (f1 - f2 + f7 - f8 + f9 - f10 + f11 - f12 + f13 - f14) / rho;
    const float uy = (f3 - f4 + f7 - f8 - f9 + f10 + f15 - f16 + f17 - f18) / rho;
    const float uz = (f5 - f6 + f11 - f12 - f13 + f14 + f15 - f16 - f17 + f18) / rho;

    // nonequilibrium momentum flux Pi = sum h c c - dr/3 I - rho u u
    const float Pxx = f1 + f2 + f7 + f8 + f9 + f10 + f11 + f12 + f13 + f14 - dr / 3.0f - rho * ux * ux;
    const float Pyy = f3 + f4 + f7 + f8 + f9 + f10 + f15 + f16 + f17 + f18 - dr / 3.0f - rho * uy * uy;
    const float Pzz = f5 + f6 + f11 + f12 + f13 + f14 + f15 + f16 + f17 + f18 - dr / 3.0f - rho * uz * uz;
    const float Pxy = f7 + f8 - f9 - f10 - rho * ux * uy;
    const float Pxz = f11 + f12 - f13 - f14 - rho * ux * uz;
    const float Pyz = f15 + f16 - f17 - f18 - rho * uy * uz;

    float tau = g.tau0;
    if (g.cs2 > 0.0f) {
        const float pp = Pxx * Pxx + Pyy * Pyy + Pzz * Pzz + 2.0f * (Pxy * Pxy + Pxz * Pxz + Pyz * Pyz);
        tau = 0.5f * (g.tau0 + sqrtf(g.tau0 * g.tau0 + 18.0f * g.cs2 * sqrtf(2.0f * pp) / rho));
    }
    const float t = (1.0f - 1.0f / tau) * 4.5f;
    const float base = dr - 1.5f * rho * (ux * ux + uy * uy + uz * uz);
    const float tr3 = (Pxx + Pyy + Pzz) / 3.0f;

    float h[19];
    h[0] = W0 * (base - t * tr3);
    PAIR(W1, ux, Pxx, h[1], h[2]);
    PAIR(W1, uy, Pyy, h[3], h[4]);
    PAIR(W1, uz, Pzz, h[5], h[6]);
    PAIR(W2, ux + uy, Pxx + Pyy + 2.0f * Pxy, h[7], h[8]);
    PAIR(W2, ux - uy, Pxx + Pyy - 2.0f * Pxy, h[9], h[10]);
    PAIR(W2, ux + uz, Pxx + Pzz + 2.0f * Pxz, h[11], h[12]);
    PAIR(W2, ux - uz, Pxx + Pzz - 2.0f * Pxz, h[13], h[14]);
    PAIR(W2, uy + uz, Pyy + Pzz + 2.0f * Pyz, h[15], h[16]);
    PAIR(W2, uy - uz, Pyy + Pzz - 2.0f * Pyz, h[17], h[18]);

    const float sigma = sx[i] + sy[j] + sz[k];
    if (sigma > 0.0f) {
        // absorbing layer: relax towards the far-field equilibrium (density one, far-field velocity)
        const float vx = g.ux, vy = g.uy, vz = g.uz;
        const float b0 = -1.5f * (vx * vx + vy * vy + vz * vz);
#define SPONGE(wt, cu, a, b)                                   \
        {                                                      \
            const float cu_ = (cu);                            \
            const float even_ = (wt) * (b0 + 4.5f * cu_ * cu_); \
            const float odd_ = (wt) * 3.0f * cu_;              \
            a += sigma * (even_ + odd_ - a);                   \
            b += sigma * (even_ - odd_ - b);                   \
        }
        h[0] += sigma * (W0 * b0 - h[0]);
        SPONGE(W1, vx, h[1], h[2]);
        SPONGE(W1, vy, h[3], h[4]);
        SPONGE(W1, vz, h[5], h[6]);
        SPONGE(W2, vx + vy, h[7], h[8]);
        SPONGE(W2, vx - vy, h[9], h[10]);
        SPONGE(W2, vx + vz, h[11], h[12]);
        SPONGE(W2, vx - vz, h[13], h[14]);
        SPONGE(W2, vy + vz, h[15], h[16]);
        SPONGE(W2, vy - vz, h[17], h[18]);
    }

    for (int q = 0; q < 19; q++) dst[q * N + n] = h[q];
}

// Cut links, by chunks of the same group: thread t handles links chunk_start[t] .. chunk_start[t+1]-1.
// The reflected value is a h[slot_a] + b h[slot_b] + ramp dh; a screen link (r < 1) lets (1-r) pass.
// Momentum exchange on the body, in lattice units: F = -r c_i (reflected + h[slot_a]). The rest-state weights
// 2 w_i are left out: they are the uniform base pressure, zero over a closed surface, but a false load of
// 1/3 (lattice) times the contact area on a wheel standing on the ground, whose surface is not closed.
extern "C" __global__ void links_compute(const float* __restrict__ h, const u32* __restrict__ slot_a,
                                         const u32* __restrict__ slot_b, const u32* __restrict__ slot_d,
                                         const float* __restrict__ a, const float* __restrict__ b,
                                         const float* __restrict__ dh, const float* __restrict__ r,
                                         const u8* __restrict__ dir, const float* __restrict__ arm,
                                         const u32* __restrict__ chunk_start, const u16* __restrict__ chunk_group,
                                         float* __restrict__ v, double* accum, u32 nchunks, float ramp) {
    const u32 tid = blockIdx.x * blockDim.x + threadIdx.x;
    if (tid >= nchunks) return;
    const signed char CX[19] = {0, 1, -1, 0, 0, 0, 0, 1, -1, 1, -1, 1, -1, 1, -1, 0, 0, 0, 0};
    const signed char CY[19] = {0, 0, 0, 1, -1, 0, 0, 1, -1, -1, 1, 0, 0, 0, 0, 1, -1, 1, -1};
    const signed char CZ[19] = {0, 0, 0, 0, 0, 1, -1, 0, 0, 0, 0, 1, -1, -1, 1, 1, -1, -1, 1};
    double fx = 0.0, fy = 0.0, fz = 0.0, mx = 0.0, my = 0.0, mz = 0.0;
    const u32 end = chunk_start[tid + 1];
    for (u32 l = chunk_start[tid]; l < end; l++) {
        const int q = dir[l];
        const float ha = h[slot_a[l]];
        const float refl = a[l] * ha + b[l] * h[slot_b[l]] + ramp * dh[l];
        const float rr = r[l];
        v[l] = rr * refl + (1.0f - rr) * h[slot_d[l]];
        const double mom = -(double)rr * ((double)refl + (double)ha);
        const double lx = CX[q] * mom, ly = CY[q] * mom, lz = CZ[q] * mom;
        const double ax = arm[3 * l], ay = arm[3 * l + 1], az = arm[3 * l + 2];
        fx += lx;
        fy += ly;
        fz += lz;
        mx += ay * lz - az * ly;
        my += az * lx - ax * lz;
        mz += ax * ly - ay * lx;
    }
    double* out = accum + 6 * (int)chunk_group[tid];
    atomicAdd(out + 0, fx);
    atomicAdd(out + 1, fy);
    atomicAdd(out + 2, fz);
    atomicAdd(out + 3, mx);
    atomicAdd(out + 4, my);
    atomicAdd(out + 5, mz);
}

extern "C" __global__ void links_write(float* __restrict__ h, const u32* __restrict__ slot_d,
                                       const float* __restrict__ v, u32 nlinks) {
    const u32 l = blockIdx.x * blockDim.x + threadIdx.x;
    if (l >= nlinks) return;
    h[slot_d[l]] = v[l];
}

// The ground, halfway below the first row (k = 0): a specular wall with the stress of a wall function.
// A no-slip wall moving at the air's speed grew a numerical instability at relaxation times near 1/2: from an
// exact uniform flow, within hundreds of steps, into a reversed layer many cells thick. The specular wall
// has no such mode. The stress tau = 1/2 cf |du| du, with du = ground - first row, goes in as +-tau/2 on the
// diagonal slots: no mass, momentum tau per node and step.
extern "C" __global__ void ground_fill(float* __restrict__ h, Grid g) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= g.nx) return;
    const int j = blockIdx.y;
    const int X = g.X, XY = g.X * g.Y, N = g.N;
    const int gh = (i + 1) + (j + 1) * X;  // ghost node (i, j, -1)
    const int n = gh + XY;                 // the node above it
    float dr = 0.0f;
    for (int q = 0; q < 19; q++) dr += h[q * N + n];
    const float rho = 1.0f + dr;
    const float ux = (H(1) - H(2) + H(7) - H(8) + H(9) - H(10) + H(11) - H(12) + H(13) - H(14)) / rho;
    const float uy = (H(3) - H(4) + H(7) - H(8) - H(9) + H(10) + H(15) - H(16) + H(17) - H(18)) / rho;
    const float dux = g.ug - ux, duy = g.vg - uy;
    const float s = 0.25f * g.cf * sqrtf(dux * dux + duy * duy);
    h[5 * N + gh] = H(6);
    h[11 * N + gh] = H(13) + s * dux;
    h[14 * N + gh] = H(12) - s * dux;
    h[15 * N + gh] = H(17) + s * duy;
    h[18 * N + gh] = H(16) - s * duy;
}

// The whole ghost layer at the far-field equilibrium (density one). The ground overwrites its slots.
extern "C" __global__ void fill_far(float* __restrict__ h, const u8* __restrict__ mask, Grid g) {
    const u32 n = blockIdx.x * blockDim.x + threadIdx.x + ((u32)blockIdx.y) * 65536u * blockDim.x;
    if (n >= (u32)g.N) return;
    if (mask[n] != 2) return;
    const float CXF[19] = {0, 1, -1, 0, 0, 0, 0, 1, -1, 1, -1, 1, -1, 1, -1, 0, 0, 0, 0};
    const float CYF[19] = {0, 0, 0, 1, -1, 0, 0, 1, -1, -1, 1, 0, 0, 0, 0, 1, -1, 1, -1};
    const float CZF[19] = {0, 0, 0, 0, 0, 1, -1, 0, 0, 0, 0, 1, -1, -1, 1, 1, -1, -1, 1};
    const float b0 = -1.5f * (g.ux * g.ux + g.uy * g.uy + g.uz * g.uz);
    h[n] = W0 * b0;
    for (int q = 1; q < 19; q++) {
        const float cu = CXF[q] * g.ux + CYF[q] * g.uy + CZF[q] * g.uz;
        h[q * g.N + n] = (q <= 6 ? W1 : W2) * (b0 + 3.0f * cu + 4.5f * cu * cu);
    }
}

// Adds one sample of density perturbation and velocity at every fluid node (post-collision moments).
extern "C" __global__ void mean_sample(const float* __restrict__ h, const u8* __restrict__ mask,
                                       float* __restrict__ m_dr, float* __restrict__ m_ux,
                                       float* __restrict__ m_uy, float* __restrict__ m_uz, Grid g) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= g.nx) return;
    const int j = blockIdx.y, k = blockIdx.z;
    const int n = (i + 1) + (j + 1) * g.X + (k + 1) * g.X * g.Y, N = g.N;
    if (mask[n]) return;
    float dr = 0.0f;
    for (int q = 0; q < 19; q++) dr += h[q * N + n];
    const float rho = 1.0f + dr;
    const float jx = H(1) - H(2) + H(7) - H(8) + H(9) - H(10) + H(11) - H(12) + H(13) - H(14);
    const float jy = H(3) - H(4) + H(7) - H(8) - H(9) + H(10) + H(15) - H(16) + H(17) - H(18);
    const float jz = H(5) - H(6) + H(11) - H(12) - H(13) + H(14) + H(15) - H(16) - H(17) + H(18);
    m_dr[n] += dr;
    m_ux[n] += jx / rho;
    m_uy[n] += jy / rho;
    m_uz[n] += jz / rho;
}

// Diagnostics over the fluid nodes, one row of x per thread. out_d: sum dr, sum jx, sum jy, sum jz.
// out_i: max |u|^2 x 1e7, max dr x 1e7, -min dr x 1e7, count of nonfinite nodes, count of negative f.
extern "C" __global__ void stats(const float* __restrict__ h, const u8* __restrict__ mask, Grid g,
                                 double* out_d, int* out_i) {
    const int t = blockIdx.x * blockDim.x + threadIdx.x;
    if (t >= g.ny * g.nz) return;
    const int j = t % g.ny, k = t / g.ny;
    const int N = g.N;
    double sdr = 0.0, sjx = 0.0, sjy = 0.0, sjz = 0.0;
    float umax = 0.0f, drmax = 0.0f, drmin = 0.0f;
    int bad = 0, negative = 0;
    for (int i = 0; i < g.nx; i++) {
        const int n = (i + 1) + (j + 1) * g.X + (k + 1) * g.X * g.Y;
        if (mask[n]) continue;
        float dr = 0.0f;
        for (int q = 0; q < 19; q++) {
            const float wq = q == 0 ? W0 : (q <= 6 ? W1 : W2);
            const float v = h[q * N + n];
            dr += v;
            if (v + wq < 0.0f) negative++;
        }
        if (!(dr == dr) || dr > 1e6f || dr < -1e6f) {
            bad++;
            continue;
        }
        const float jx = H(1) - H(2) + H(7) - H(8) + H(9) - H(10) + H(11) - H(12) + H(13) - H(14);
        const float jy = H(3) - H(4) + H(7) - H(8) - H(9) + H(10) + H(15) - H(16) + H(17) - H(18);
        const float jz = H(5) - H(6) + H(11) - H(12) - H(13) + H(14) + H(15) - H(16) - H(17) + H(18);
        const float rho = 1.0f + dr;
        const float u2 = (jx * jx + jy * jy + jz * jz) / (rho * rho);
        if (u2 > umax) umax = u2;
        if (dr > drmax) drmax = dr;
        if (dr < drmin) drmin = dr;
        sdr += dr;
        sjx += jx;
        sjy += jy;
        sjz += jz;
    }
    atomicAdd(out_d + 0, sdr);
    atomicAdd(out_d + 1, sjx);
    atomicAdd(out_d + 2, sjy);
    atomicAdd(out_d + 3, sjz);
    atomicMax(out_i + 0, (int)fminf(umax * 1e7f, 2e9f));
    atomicMax(out_i + 1, (int)fminf(drmax * 1e7f, 2e9f));
    atomicMax(out_i + 2, (int)fminf(-drmin * 1e7f, 2e9f));
    atomicAdd(out_i + 3, bad);
    atomicAdd(out_i + 4, negative);
}

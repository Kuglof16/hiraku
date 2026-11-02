// The surroundings of a test, drawn only from the record (never placed by hand).
// When the tool writes the analysis' `scene` (contract scene_object: step,
// road_profile, slope, tilt_table, cone, gate, wall, ramp, ground), that is
// drawn: position_mm is taken as the centre of the object's box (size_mm), as
// for a part's box, turned by rpy_deg. Without a `scene`, the surroundings are
// read off the load case's params and the series:
//   step         params.step_height; where its edge is: the reactions that push on
//                the step's corner (a still) or the first time a wheel's contact
//                point leaves the road (a motion)
//   tilt table   the table_angle channel (the table turns with the world)
//   side slope   params.side_slope and params.slope_up (the road frame is the slope)
//   rough road   the wheels' contact points over time (the road surface under the
//                two wheel tracks; between and beside them it is drawn straight)
//   wind         the wind_speed channel and params.wind_from
//   course       the path_y channel along cg_x (the line the driver model follows)
//   wall         a crash test: the wall stands at the design's front end
// The record does not hold cones, a stop line or the wall's own size, so they
// are not drawn (the wall's size is only made big enough to read as a wall).

import * as THREE from "three";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import { ANGLE, LENGTH, lerpAt, nearestAt } from "./series.js";

const PAPER = "#f4f1ea";
const PAPER_DARK = "#e6e0d3";
const WIND = "#2fa7d6";

// A length or angle param in mm / rad ({value, unit}, or a bare number in SI).
function lengthParam(p) {
  if (typeof p === "number") return p * 1000;
  if (p && typeof p.value === "number" && LENGTH[p.unit]) return p.value * LENGTH[p.unit];
  return null;
}
function angleParam(p) {
  if (typeof p === "number") return p;
  if (p && typeof p.value === "number" && ANGLE[p.unit]) return p.value * ANGLE[p.unit];
  return null;
}

function channelMm(motion, name) {
  const s = motion?.channels.get(name);
  return s && LENGTH[s.unit] ? { s, k: LENGTH[s.unit] } : null;
}

// The design's front end (largest x of nodes, part boxes and wheels), ISO mm.
function frontOf(design) {
  let x = -Infinity;
  for (const n of design?.nodes ?? []) x = Math.max(x, n.xyz_mm[0]);
  for (const p of design?.parts ?? []) x = Math.max(x, p.box.center_mm[0] + p.box.size_mm[0] / 2);
  for (const s of design?.supports ?? []) x = Math.max(x, s.position_mm[0] + (s.radius_mm ?? 0));
  return Number.isFinite(x) ? x : null;
}

function halfWidth(design) {
  let y = 600;
  for (const s of design?.supports ?? []) y = Math.max(y, Math.abs(s.position_mm[1]) + (s.width_mm ?? 200) / 2);
  for (const p of design?.parts ?? []) y = Math.max(y, Math.abs(p.box.center_mm[1]) + p.box.size_mm[1] / 2);
  return y;
}

function stepOf(lc, ana, motion, design) {
  const h = lengthParam(lc?.params?.step_height);
  if (!(h > 0)) return null;
  let edge = null;
  const ys = [];
  // A still: the reactions on the corner. With a motion the step must sit where the
  // animated wheels meet it, so the contact points are read instead (a motion's
  // vectors may come from another moment of the tool's work).
  for (const v of motion ? [] : ana?.vectors ?? []) {
    if (v.kind !== "reaction" || !Array.isArray(v.origin_mm) || Math.abs(v.origin_mm[2] - h) > 1) continue;
    edge = Math.min(edge ?? Infinity, v.origin_mm[0]);
    ys.push(v.origin_mm[1]);
  }
  if (motion) {
    for (const id of motion.wheelIds) {
      const cx = channelMm(motion, `contact_x.${id}`);
      const cy = channelMm(motion, `contact_y.${id}`);
      const cz = channelMm(motion, `contact_z.${id}`);
      if (!cx || !cy || !cz) continue;
      const i = cz.s.values.findIndex((z) => z * cz.k > 0.02 * h);
      if (i < 0) continue;
      const t = cz.s.t_s[i];
      edge = Math.min(edge ?? Infinity, lerpAt(cx.s, t) * cx.k);
      ys.push(lerpAt(cy.s, t) * cy.k);
    }
  }
  if (edge === null) return null;
  const hw = halfWidth(design) + 500;
  // Both sides step: the step spans the road; one side only: a block under that side.
  const both = ys.some((y) => y > 0) && ys.some((y) => y < 0);
  const y0 = both ? -hw : ys[0] > 0 ? 0 : -hw;
  const y1 = both ? hw : ys[0] > 0 ? hw : 0;
  const far = Math.max(edge + 2500, (motion?.path?.maxX ?? 0) + frontOf(design) + 1500);
  return { h, x0: edge, x1: far, y0, y1 };
}

// Road heights under the two wheel tracks, from the contact points of every
// sample (front and rear wheels of a side run on the same track).
function surfaceOf(motion) {
  if (!motion) return null;
  const tracks = { left: [], right: [] };
  let zmax = 0;
  for (const id of motion.wheelIds) {
    const cx = channelMm(motion, `contact_x.${id}`);
    const cy = channelMm(motion, `contact_y.${id}`);
    const cz = channelMm(motion, `contact_z.${id}`);
    if (!cx || !cy || !cz) continue;
    for (let i = 0; i < cz.s.values.length; i += 1) {
      const t = cz.s.t_s[i];
      const y = lerpAt(cy.s, t) * cy.k;
      const z = cz.s.values[i] * cz.k;
      zmax = Math.max(zmax, Math.abs(z));
      (y >= 0 ? tracks.left : tracks.right).push([lerpAt(cx.s, t) * cx.k, y, z]);
    }
  }
  if (zmax < 5 || !tracks.left.length || !tracks.right.length) return null;
  for (const k of ["left", "right"]) tracks[k].sort((a, b) => a[0] - b[0]);
  const meanY = (pts) => pts.reduce((s, p) => s + p[1], 0) / pts.length;
  const zAt = (pts, x) => {
    let lo = 0;
    let hi = pts.length - 1;
    if (x <= pts[0][0]) return pts[0][2];
    if (x >= pts[hi][0]) return pts[hi][2];
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (pts[mid][0] <= x) lo = mid;
      else hi = mid;
    }
    const span = pts[hi][0] - pts[lo][0];
    return span > 0 ? pts[lo][2] + ((pts[hi][2] - pts[lo][2]) * (x - pts[lo][0])) / span : pts[lo][2];
  };
  const x0 = Math.min(tracks.left[0][0], tracks.right[0][0]) - 2000;
  const x1 = Math.max(tracks.left.at(-1)[0], tracks.right.at(-1)[0]) + 2000;
  return { x0, x1, yL: meanY(tracks.left), yR: meanY(tracks.right), zL: (x) => zAt(tracks.left, x), zR: (x) => zAt(tracks.right, x) };
}

function courseOf(motion) {
  const py = channelMm(motion, "path_y");
  const cx = channelMm(motion, "cg_x");
  if (!py || !cx) return null;
  const pts = py.s.t_s.map((t, i) => [lerpAt(cx.s, t) * cx.k, py.s.values[i] * py.k]);
  const ys = pts.map((p) => p[1]);
  return Math.max(...ys) - Math.min(...ys) > 100 ? pts : null;
}

// Whether a wheel leaving the road means a limit was passed: not on a step
// test, where the wheel leaves the lower road to climb.
export function liftIsLimit(lc) {
  return !lc?.params?.step_height;
}

const deg = (v) => (v ?? 0) * (Math.PI / 180);

// A recorded road_profile as heights under the wheel tracks: one line per key
// (a support id gives its track's y; left / right take the outermost wheels' y),
// or one line for the whole width.
function profileSurface(obj, design) {
  const p = obj.profile;
  const off = obj.position_mm ?? [0, 0, 0];
  const xs = (p?.x_mm ?? []).map((x) => x + off[0]);
  if (xs.length < 2) return null;
  const supports = Object.fromEntries((design?.supports ?? []).map((s) => [s.id, s.position_mm[1]]));
  const ysAll = Object.values(supports);
  const yLeft = ysAll.length ? Math.max(...ysAll) : 550;
  const yRight = ysAll.length ? Math.min(...ysAll) : -550;
  const lines = Array.isArray(p.z_mm)
    ? [[yLeft, p.z_mm], [yRight, p.z_mm]]
    : Object.entries(p.z_mm ?? {}).map(([k, zs]) => [supports[k] ?? (/^(l|left)$/i.test(k) ? yLeft : /^(r|right)$/i.test(k) ? yRight : 0), zs]);
  const left = lines.filter(([y]) => y >= 0);
  const right = lines.filter(([y]) => y < 0);
  if (!left.length || !right.length) return null;
  const zAt = (zs, x) => {
    if (x <= xs[0]) return zs[0] + off[2];
    for (let i = 1; i < xs.length; i += 1) {
      if (x <= xs[i]) return zs[i - 1] + ((zs[i] - zs[i - 1]) * (x - xs[i - 1])) / (xs[i] - xs[i - 1] || 1) + off[2];
    }
    return zs.at(-1) + off[2];
  };
  const mean = (ls, x) => ls.reduce((s, [, zs]) => s + zAt(zs, x), 0) / ls.length;
  return { x0: xs[0], x1: xs.at(-1), yL: left[0][0], yR: right[0][0], zL: (x) => mean(left, x), zR: (x) => mean(right, x) };
}

function fromRecord(objects, design) {
  const out = { objects: [], slope: null, table: false, surface: null };
  for (const o of objects) {
    if (o.kind === "slope") out.slope = deg(o.rpy_deg?.[0]);
    else if (o.kind === "road_profile") out.surface = profileSurface(o, design);
    else {
      if (o.kind === "tilt_table") out.table = true;
      if (Array.isArray(o.position_mm) && Array.isArray(o.size_mm)) out.objects.push(o);
    }
  }
  return out;
}

// What surrounds this test (see the top of the file); every field may be null.
export function sceneOf(lc, ana, motion, design) {
  const params = lc?.params ?? {};
  const wind = motion?.channels.has("wind_speed") ? { from: String(params.wind_from ?? "left") === "right" ? "right" : "left", s: motion.channels.get("wind_speed") } : null;
  const base = { wind, course: courseOf(motion), halfWidth: halfWidth(design), objects: [], step: null, wall: null };
  if (Array.isArray(ana?.scene) && ana.scene.length) {
    const r = fromRecord(ana.scene, design);
    return { ...base, ...r, table: r.table || !!motion?.hasTable, recorded: true };
  }
  const slope = angleParam(params.side_slope);
  const crash = ana?.tool === "crash" || lc?.domain === "crash";
  return {
    ...base,
    step: stepOf(lc, ana, motion, design),
    table: !!motion?.hasTable,
    slope: slope === null ? null : String(params.slope_up ?? "left") === "right" ? -slope : slope,
    surface: params.step_height ? null : surfaceOf(motion),
    wall: crash ? { x: frontOf(design) } : null,
    recorded: false,
  };
}

// ---- drawing --------------------------------------------------------------

function inkLines(points, mat) {
  return new LineSegments2(new LineSegmentsGeometry().setPositions(points), mat);
}

function edgesOf(geo, out, matrix = null, threshold = 25) {
  const e = new THREE.EdgesGeometry(geo, threshold);
  const pos = e.getAttribute("position");
  const p = new THREE.Vector3();
  for (let i = 0; i < pos.count; i += 1) {
    p.fromBufferAttribute(pos, i);
    if (matrix) p.applyMatrix4(matrix);
    out.push(p.x, p.y, p.z);
  }
  e.dispose();
}

// A solid with the look's fill and (in the diagram look) ink edges.
function solid(geos, look, ink, realSpec) {
  const g = new THREE.Group();
  const pts = [];
  for (const geo of geos) {
    const mat = look.real ? look.standard(realSpec) : new THREE.MeshLambertMaterial({ color: PAPER_DARK });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.castShadow = mesh.receiveShadow = look.real;
    g.add(mesh);
    if (!look.real) edgesOf(geo, pts);
  }
  if (pts.length) g.add(inkLines(pts, ink));
  return g;
}

// A solid block in ISO mm.
function block(x0, x1, y0, y1, z0, z1, look, ink, realSpec) {
  const geo = new THREE.BoxGeometry(x1 - x0, y1 - y0, z1 - z0);
  geo.translate((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2);
  return solid([geo], look, ink, realSpec);
}

const REAL_OF = {
  step: { color: "#9c9a92", roughness: 0.95, metalness: 0 },
  wall: { color: "#a5a29a", roughness: 0.9, metalness: 0 },
  ramp: { color: "#9c9a92", roughness: 0.95, metalness: 0 },
  ground: { color: "#8a7a62", roughness: 1, metalness: 0 },
  tilt_table: { color: "#9aa0a6", roughness: 0.45, metalness: 0.75, bump: "plate" },
  cone: { color: "#e2682a", roughness: 0.6, metalness: 0 },
  gate: { color: "#e2682a", roughness: 0.6, metalness: 0 },
};

// One object of the record's scene, in its own box (centre position_mm, size_mm, turned by rpy_deg).
function recordedObject(o, look, ink) {
  const [sx, sy, sz] = o.size_mm;
  let geos;
  if (o.kind === "cone") {
    geos = [new THREE.ConeGeometry(Math.min(sx, sy) / 2, sz, 24).rotateX(Math.PI / 2)];
  } else if (o.kind === "ramp") {
    // a wedge rising toward +x
    const shape = new THREE.Shape([new THREE.Vector2(-sx / 2, -sz / 2), new THREE.Vector2(sx / 2, -sz / 2), new THREE.Vector2(sx / 2, sz / 2)]);
    geos = [new THREE.ExtrudeGeometry(shape, { depth: sy, bevelEnabled: false }).rotateX(Math.PI / 2).translate(0, sy / 2, 0)];
  } else if (o.kind === "gate") {
    // two posts at the gate's sides
    const r = Math.max(20, Math.min(sx, 60) / 2);
    geos = [-1, 1].map((s) => new THREE.CylinderGeometry(r, r, sz, 12).rotateX(Math.PI / 2).translate(0, (s * sy) / 2, 0));
  } else {
    geos = [new THREE.BoxGeometry(sx, sy, sz)];
  }
  const g = solid(geos, look, ink, REAL_OF[o.kind] ?? REAL_OF.step);
  g.position.set(...o.position_mm);
  const r = o.rpy_deg ?? [0, 0, 0];
  g.rotation.set(deg(r[0]), deg(r[1]), deg(r[2]), "ZYX");
  return g;
}

export class SceneProps {
  constructor(look, ink, muted) {
    this.look = look;
    this.ink = ink;
    this.muted = muted;
    this.world = new THREE.Group(); // tilts with the world (ISO mm)
    this.fixed = new THREE.Group(); // stays level (ISO mm)
    this.windMat = new LineMaterial({ color: WIND, linewidth: 2.2 });
    this.courseMat = new LineMaterial({ color: "#f4f1ea", linewidth: 3 });
    this.shared = new Set([ink, muted, this.windMat, this.courseMat]);
    this.desc = null;
    this.wind = null;
  }

  resize(w, h) {
    for (const m of [this.windMat, this.courseMat]) m.resolution.set(w, h);
  }

  clear() {
    for (const g of [this.world, this.fixed]) {
      g.traverse((o) => {
        o.geometry?.dispose?.();
        if (o.material && !this.shared.has(o.material)) o.material.dispose?.();
      });
      g.clear();
    }
    this.wind = null;
  }

  build(desc, bbox) {
    this.clear();
    this.desc = desc;
    if (!desc) return;
    const look = this.look;
    for (const o of desc.objects ?? []) this.world.add(recordedObject(o, look, this.ink));
    if (desc.step) {
      const s = desc.step;
      this.world.add(block(s.x0, s.x1, s.y0, s.y1, 0, s.h, look, this.ink, { color: "#9c9a92", roughness: 0.95, metalness: 0 }));
    }
    if (desc.table && !desc.recorded) {
      const hw = desc.halfWidth + 700;
      const x0 = bbox.min.x - 700;
      const x1 = bbox.max.x + 700;
      this.world.add(block(x0, x1, -hw, hw, -90, -2, look, this.ink, { color: "#9aa0a6", roughness: 0.45, metalness: 0.75, bump: "plate" }));
      // the pivot post and the floor stay level
      const floorZ = -hw * Math.sin(0.75) - 300;
      this.fixed.add(block(-200 + (x0 + x1) / 2, 200 + (x0 + x1) / 2, -200, 200, floorZ, -90, look, this.ink, { color: "#55595e", roughness: 0.5, metalness: 0.6 }));
      this.fixed.add(block(x0 - 800, x1 + 800, -hw - 800, hw + 800, floorZ - 40, floorZ, look, this.ink, { color: "#8a8780", roughness: 0.95, metalness: 0 }));
    }
    if (desc.surface) this.world.add(this.surfaceMesh(desc.surface, desc.halfWidth));
    if (desc.course) {
      const pts = [];
      const c = desc.course;
      for (let i = 0; i + 1 < c.length; i += 2) pts.push(c[i][0], c[i][1], 4, c[i + 1][0], c[i + 1][1], 4);
      this.world.add(inkLines(pts, look.real ? this.courseMat : this.muted));
    }
    if (desc.wall) {
      const hw = desc.halfWidth + 900;
      this.world.add(block(desc.wall.x, desc.wall.x + 400, -hw, hw, 0, 1600, look, this.ink, { color: "#a5a29a", roughness: 0.9, metalness: 0 }));
    }
    if (desc.wind) this.buildWind(desc, bbox);
  }

  // The road surface: rows along x, columns across (beside, on and between the tracks).
  surfaceMesh(s, hw) {
    const look = this.look;
    const side = look.real ? 6000 : 1500; // the real look shows more of the hillside
    const ys = [-(hw + side), s.yR, s.yR + (s.yL - s.yR) / 3, s.yR + (2 * (s.yL - s.yR)) / 3, s.yL, hw + side];
    const zAt = (x, y) => {
      if (y <= s.yR) return s.zR(x);
      if (y >= s.yL) return s.zL(x);
      const f = (y - s.yR) / (s.yL - s.yR);
      return s.zR(x) * (1 - f) + s.zL(x) * f;
    };
    const step = 80;
    const nx = Math.max(2, Math.ceil((s.x1 - s.x0) / step) + 1);
    const pos = [];
    const uv = [];
    for (let i = 0; i < nx; i += 1) {
      const x = s.x0 + ((s.x1 - s.x0) * i) / (nx - 1);
      for (const y of ys) {
        pos.push(x, y, zAt(x, y));
        uv.push(x / 2500, y / 2500);
      }
    }
    const idx = [];
    const m = ys.length;
    for (let i = 0; i + 1 < nx; i += 1) {
      for (let j = 0; j + 1 < m; j += 1) {
        const a = i * m + j;
        idx.push(a, a + m, a + 1, a + 1, a + m, a + m + 1);
      }
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    geo.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
    geo.setIndex(idx);
    geo.computeVertexNormals();
    const g = new THREE.Group();
    if (look.real) {
      const tex = look.texture("earth").clone();
      tex.needsUpdate = true;
      tex.repeat.set(1, 1);
      const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ map: tex, roughness: 1, metalness: 0 }));
      mesh.receiveShadow = true;
      g.add(mesh);
    } else {
      g.add(new THREE.Mesh(geo, new THREE.MeshLambertMaterial({ color: PAPER, side: THREE.DoubleSide })));
      // ruled lines across the road every 500 mm and along each column
      const pts = [];
      for (let x = Math.ceil(s.x0 / 500) * 500; x <= s.x1; x += 500) {
        for (let j = 0; j + 1 < m; j += 1) pts.push(x, ys[j], zAt(x, ys[j]) + 2, x, ys[j + 1], zAt(x, ys[j + 1]) + 2);
      }
      for (const y of ys) {
        for (let i = 0; i + 1 < nx; i += 1) {
          const xa = s.x0 + ((s.x1 - s.x0) * i) / (nx - 1);
          const xb = s.x0 + ((s.x1 - s.x0) * (i + 1)) / (nx - 1);
          pts.push(xa, y, zAt(xa, y) + 2, xb, y, zAt(xb, y) + 2);
        }
      }
      g.add(inkLines(pts, this.muted));
    }
    return g;
  }

  // Wind: rows of arrows on the windward side, as long as the recorded wind speed at the time.
  buildWind(desc, bbox) {
    const side = desc.wind.from === "left" ? 1 : -1;
    const max = Math.max(1e-9, ...desc.wind.s.values.map(Math.abs));
    const rows = [];
    const g = new THREE.Group();
    for (const z of [350, 750, 1150]) {
      for (const f of [0.15, 0.5, 0.85]) {
        const x = bbox.min.x + (bbox.max.x - bbox.min.x) * f;
        const line = inkLines([0, 0, 0, 0, 0, 0], this.windMat);
        g.add(line);
        rows.push({ line, x, z });
      }
    }
    this.world.add(g);
    this.wind = { rows, side, max, s: desc.wind.s, y0: side * (desc.halfWidth + 1500) };
  }

  // Per frame: the wind arrows follow the vehicle and the recorded wind speed.
  update(frame) {
    const w = this.wind;
    if (!w) return;
    const v = frame ? nearestAt(w.s, frame.t) : 0;
    const len = 1200 * Math.min(1, Math.abs(v) / w.max);
    const dx = frame?.body?.[0] ?? 0;
    const dy = frame?.body?.[1] ?? 0;
    for (const r of w.rows) {
      r.line.visible = len > 50;
      if (!r.line.visible) continue;
      const x = r.x + dx;
      const y0 = w.y0 + dy;
      const y1 = y0 - w.side * len;
      const head = Math.min(180, len * 0.3);
      const pts = [x, y0, r.z, x, y1, r.z, x, y1, r.z, x + head * 0.5, y1 + w.side * head, r.z, x, y1, r.z, x - head * 0.5, y1 + w.side * head, r.z];
      r.line.geometry.dispose();
      r.line.geometry = new LineSegmentsGeometry().setPositions(pts);
    }
  }
}

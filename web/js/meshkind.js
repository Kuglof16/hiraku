// The elements of a contract mesh record by kind: what to draw of each (faces, edges),
// which sides are free, and how many there are of each kind. From the contract
// (hiraku.schema.json mesh.elements / element_kind):
//   quad4, tri3  shell elements, nodes counter-clockwise seen from the outer side
//   hex8         nodes 0-3 counter-clockwise seen from +zeta (the bottom face), 4-7 above them
//   hex20        the 8 corners, then the mid-edge nodes of edges 0-1, 1-2, 2-3, 3-0, 4-5, 5-6,
//                6-7, 7-4, 0-4, 1-5, 2-6, 3-7
// element_kind is required for a mixed mesh; otherwise the kind follows from the node count.

export const KINDS = ["quad4", "tri3", "hex8", "hex20"];
export const KIND_JA = { quad4: "四角", tri3: "三角", hex8: "六面体 8 節点", hex20: "六面体 20 節点" };
const BY_COUNT = { 3: "tri3", 4: "quad4", 8: "hex8", 20: "hex20" };
const COUNT = { tri3: 3, quad4: 4, hex8: 8, hex20: 20 };

// The six faces of a hexahedron by its corner numbers, counter-clockwise seen from outside
// (so their normals point out): bottom, top, then the four sides.
export const HEX_FACES = [
  [0, 3, 2, 1],
  [4, 5, 6, 7],
  [0, 1, 5, 4],
  [1, 2, 6, 5],
  [2, 3, 7, 6],
  [3, 0, 4, 7],
];
// hex20: the mid-edge node of each corner pair, in the contract's order (nodes 8..19).
const HEX20_EDGES = [[0, 1], [1, 2], [2, 3], [3, 0], [4, 5], [5, 6], [6, 7], [7, 4], [0, 4], [1, 5], [2, 6], [3, 7]];
const HEX20_MID = new Map(HEX20_EDGES.flatMap(([a, b], i) => [[`${a}-${b}`, 8 + i], [`${b}-${a}`, 8 + i]]));

const isShell = (k) => k === "quad4" || k === "tri3";
export const isSolidKind = (k) => k === "hex8" || k === "hex20";

// The kind of element i: element_kind when given and its node count fits, else by node count (null if neither).
export function elementKind(rec, i) {
  const e = rec.elements[i];
  const k = rec.element_kind?.[i];
  if (KINDS.includes(k) && e.length === COUNT[k]) return k;
  return BY_COUNT[e.length] ?? null;
}

// How many elements of each kind (kinds with none left out; "other" for an element of no known kind).
export function kindCounts(rec) {
  const out = {};
  (rec.elements ?? []).forEach((_, i) => {
    const k = elementKind(rec, i) ?? "other";
    out[k] = (out[k] ?? 0) + 1;
  });
  return out;
}

// The face of a hexahedron as mesh node numbers along its boundary: 4 corners (hex8), or
// corner, mid-edge node, corner, ... (8 nodes, hex20).
function hexFaceLoop(e, kind, face) {
  if (kind === "hex8") return face.map((c) => e[c]);
  const loop = [];
  for (let i = 0; i < 4; i += 1) loop.push(e[face[i]], e[HEX20_MID.get(`${face[i]}-${face[(i + 1) % 4]}`)]);
  return loop;
}

// Triangles of a face loop: a polygon fan for 3 or 4 nodes; for an 8-node face, a corner
// triangle at each corner and the inner quad of the mid-edge nodes (so the face follows them).
function loopTriangles(loop) {
  if (loop.length === 8) {
    const [c0, m0, c1, m1, c2, m2, c3, m3] = loop;
    return [[c0, m0, m3], [m0, c1, m1], [m1, c2, m2], [m2, c3, m3], [m0, m1, m2], [m0, m2, m3]];
  }
  const out = [];
  for (let t = 1; t + 1 < loop.length; t += 1) out.push([loop[0], loop[t], loop[t + 1]]);
  return out;
}

const faceKey = (loop, kind) => (kind === "hex20" ? loop.filter((_, i) => i % 2 === 0) : loop).slice().sort((a, b) => a - b).join(",");

// What is seen of the mesh: every shell element, and the faces of the solids that no other
// solid shares (the outer surface). Each face: { elem, loop (node numbers round it), tris }.
export function outerFaces(rec) {
  const faces = [];
  const solid = new Map(); // corner key -> face, or null once a second solid has it
  rec.elements.forEach((e, ei) => {
    const kind = elementKind(rec, ei);
    if (isSolidKind(kind)) {
      for (const f of HEX_FACES) {
        const loop = hexFaceLoop(e, kind, f);
        const key = faceKey(loop, kind);
        solid.set(key, solid.has(key) ? null : { elem: ei, loop, tris: loopTriangles(loop) });
      }
    } else if (e.length >= 3) {
      faces.push({ elem: ei, loop: e.slice(), tris: loopTriangles(e) });
    }
  });
  for (const f of solid.values()) if (f) faces.push(f);
  return faces;
}

// The lines to draw as element edges, each node pair once: the sides of the shell elements
// and the edges round the outer faces of the solids (a hex20 edge through its mid-edge node).
export function edgePairs(rec, faces = outerFaces(rec)) {
  const n = rec.nodes_mm.length;
  const seen = new Set();
  const pairs = [];
  for (const f of faces) {
    const L = f.loop;
    for (let i = 0; i < L.length; i += 1) {
      const a = L[i];
      const b = L[(i + 1) % L.length];
      const key = Math.min(a, b) * n + Math.max(a, b);
      if (seen.has(key)) continue;
      seen.add(key);
      pairs.push(a, b);
    }
  }
  return pairs;
}

// Sides used by one shell element only (quad4 / tri3), as [a, b] node pairs. A solid has no
// free edges in this sense: each of its edges is shared by two of its own faces.
export function shellFreeEdges(rec) {
  const n = rec.nodes_mm.length;
  const uses = new Map();
  rec.elements.forEach((e, ei) => {
    if (!isShell(elementKind(rec, ei))) return;
    for (let i = 0; i < e.length; i += 1) {
      const a = e[i];
      const b = e[(i + 1) % e.length];
      const key = Math.min(a, b) * n + Math.max(a, b);
      uses.set(key, (uses.get(key) ?? 0) + 1);
    }
  });
  const out = [];
  for (const [key, c] of uses) if (c === 1) out.push([Math.floor(key / n), key % n]);
  return out;
}

// "四角 12・六面体 8 節点 4": the counts of each kind in KINDS order, then any other.
export function kindCountText(rec) {
  const c = kindCounts(rec);
  return [...KINDS, "other"]
    .filter((k) => c[k])
    .map((k) => `${KIND_JA[k] ?? "そのほか"} ${c[k]}`)
    .join("・");
}

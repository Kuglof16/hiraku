// The 3D view of the workbench (A3): frame members, wheels, part boxes, the
// centre-of-gravity mark, stress contour, force arrows and the driving
// animation. Everything is placed in design / road coordinates (mm, ISO 8855:
// x forward, y left, z up) inside one group whose matrix maps them to
// three.js (m, y up).
//
//   scene
//     world            turned about x by the tilt table's or the side slope's angle (gravity stays down on screen)
//       ground         the road grid (diagram look) or earth (real look)
//       iso            ISO mm -> three.js m
//         props.world  the test's surroundings that tilt with the road (scene.js)
//         body         frame members, parts, names, stress ghosts (moves with the body pose)
//         wheels       one group per support (centre, steer, spin)
//         links        support -> frame node lines
//         arrows       vectors of the analysis (a still)
//         motionArrows reaction / grip per wheel from the series (animated)
//         ghost        translucent red copy at the pose beyond the limit
//         cg, markers
//     fixedIso         ISO mm, level: props.fixed (e.g. a tilt table's post and floor)
//
// Parts are shaped by kind inside their boxes (shapes.js); the look (?look=)
// picks materials and light (look.js).
//
// Drawing (render): the picture (in the real look on its own renderer: through
// realpost.js, antialiased, with ambient occlusion and tone mapping), then the
// overlay (layer OVERLAY), drawn after it and outside it:
//   - the contour's colours: an unlit twin (contourTwin) of each coloured mesh, its
//     colour not tone mapped, so a pixel of the contour is the legend's colour; it is
//     tested against the depth of the picture's solid things (drawn again, depth only)
//   - what is drawn over everything (arrows, the setup's marks, free edges, the centre
//     of gravity), and the diagram's ink lines again over the twins

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import { GRAY, OVER_COLOR, colorOf } from "./colormap.js";
import { overOf } from "./fields.js";
import { FIX_R_COLOR, FIX_T_COLOR, FREE_EDGE_COLOR, LOAD_COLOR } from "./quality.js";
import { esc, fmt, fmtVU, vectorName } from "./labels.js";
import { GROUND_MM, Look, cutLookOf } from "./look.js";
import { RealPost, sharedPost, skipsDepth } from "./realpost.js";
import { SceneProps, liftIsLimit, sceneOf } from "./scene.js";
import { edgePairs, outerFaces } from "./meshkind.js";
import { ANGLE, lerpAt, wheelName } from "./series.js";
import { mergePieces, occupantSegmentPieces, partPieces, personOf, segmentOf, wheelPieces } from "./shapes.js";

const INK = "#1d1d1f";
const MUTED = "#6b665c";
const RULE = "#e2dccf";
const RULE_MAJOR = "#c9c3b6";
const LIMIT_RED = "#c8402a";
// What a rule hit lights (setRuleFocus): a see-through fill and a thick outline, over everything.
const LIT_FILL = "#ffc400";
const LIT_LINE = "#ff6a00";
// joint: the force the body takes at a suspension mounting point (at "<support>.<point>")
const VECTOR_COLORS = { reaction: "#2b59c3", grip: "#8e6bbf", load: "#c8402a", wind: "#2fa7d6", inertia: "#e9893a", joint: "#1f6f6b" };
const UP = new THREE.Vector3(0, 1, 0);
// The layer of what is drawn after the picture (see the top of this file).
export const OVERLAY = 1;
// The depth of the picture's solid things for the overlay to test against: pushed back a
// little (more than a shell's faces are, polygonOffset 1), so a twin on its own surface passes.
const DEPTH_ONLY = new THREE.MeshBasicMaterial({ colorWrite: false, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 2, polygonOffsetUnits: 2 });

// Draw an object (and what is under it) only in the overlay.
function overlayOnly(obj) {
  obj.traverse((o) => o.layers.set(OVERLAY));
  return obj;
}

// The unlit copy of a mesh in the overlay, on the mesh's own geometry (so it moves and
// bends with it): its colour is drawn as it is (not lit, not tone mapped).
function contourTwin(mesh) {
  let t = mesh.userData.twin;
  if (!t) {
    const src = mesh.material;
    t = new THREE.Mesh(
      mesh.geometry,
      new THREE.MeshBasicMaterial({
        toneMapped: false,
        fog: false, // the legend's colour, not hazed (a stage's haze)
        side: src.side,
        polygonOffset: src.polygonOffset,
        polygonOffsetFactor: src.polygonOffsetFactor,
        polygonOffsetUnits: src.polygonOffsetUnits,
      }),
    );
    t.layers.set(OVERLAY);
    t.matrixAutoUpdate = false; // the mesh's own place
    t.visible = false;
    mesh.add(t);
    mesh.userData.twin = t;
  }
  return t;
}

// Show a mesh's twin in one colour (hex), in its geometry's vertex colours ("vertex"), or hide it (null).
function paintTwin(mesh, color) {
  const t = color === null && !mesh.userData.twin ? null : contourTwin(mesh);
  if (!t) return;
  t.visible = color !== null;
  if (color === null) return;
  const vertex = color === "vertex";
  if (t.material.vertexColors !== vertex) {
    t.material.vertexColors = vertex;
    t.material.needsUpdate = true;
  }
  t.material.color.set(vertex ? "#ffffff" : color);
}
// Camera directions (ISO, from the target) for the scenes that need their own view.
export const VIEWS = {
  default: [0.85, 0.95, 0.62],
  table: [1, 0.14, 0.2], // a tilt table from the front, low: the table's turn reads as a turn
  front: [1, 0.08, 0.32], // a lane change from the front
  side: [0.06, 1, 0.3], // braking from the side
};

// ISO 8855 mm -> three.js m: X = x, Y = z, Z = -y (a proper rotation, then a scale).
export const ISO_TO_THREE = new THREE.Matrix4()
  .set(1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1)
  .multiply(new THREE.Matrix4().makeScale(0.001, 0.001, 0.001));

export function isoToThree(p) {
  return new THREE.Vector3(p[0], p[1], p[2]).applyMatrix4(ISO_TO_THREE);
}

function v3(p) {
  return new THREE.Vector3(p[0], p[1], p[2]);
}

// Local frame of a member: x along the member, z toward ISO up (or ISO x when vertical).
function memberBasis(a, b) {
  const x = b.clone().sub(a).normalize();
  let up = new THREE.Vector3(0, 0, 1);
  if (Math.abs(x.dot(up)) > 0.95) up = new THREE.Vector3(1, 0, 0);
  const z = up.sub(x.clone().multiplyScalar(up.dot(x))).normalize();
  const y = z.clone().cross(x);
  return new THREE.Matrix4().makeBasis(x, y, z);
}

function sectionSize(section) {
  const d = section?.dims_mm ?? {};
  const shape = section?.shape ?? "custom";
  if (shape === "round_tube" || shape === "round_solid") {
    const dia = d.d ?? d.D ?? 30;
    return { round: true, width: dia, height: dia };
  }
  return { round: false, width: d.b ?? d.w ?? d.h ?? 30, height: d.h ?? d.b ?? 30 };
}

function memberGeometry(design, m, nodes) {
  const a = nodes[m.node_a];
  const b = nodes[m.node_b];
  if (!a || !b) return null;
  const length = a.distanceTo(b);
  if (length < 1e-6) return null;
  const { round, width, height } = sectionSize(design.sections?.[m.section]);
  const geo = round ? new THREE.CylinderGeometry(width / 2, width / 2, length, 20).rotateZ(-Math.PI / 2) : new THREE.BoxGeometry(length, width, height);
  const mid = a.clone().add(b).multiplyScalar(0.5);
  const matrix = memberBasis(a, b).setPosition(mid);
  return { geo, matrix, length, mid, width, height, round };
}

function supportGeometry(s) {
  const r = s.radius_mm ?? 100;
  const w = s.width_mm ?? Math.max(40, r * 0.4);
  if (s.kind === "leg") return { geo: new THREE.CylinderGeometry(w / 2, w / 2, Math.max(r, 50), 16).rotateX(Math.PI / 2), r, w };
  if (s.kind === "caster") return { geo: new THREE.SphereGeometry(r, 20, 12), r, w };
  return { geo: new THREE.CylinderGeometry(r, r, w, 48), r, w };
}

// How a view looks at one element of a shell mesh (ISO mm): its centre, the face's
// outward normal, the axis of the member it belongs to and the direction the camera
// stands in, plus the radius of the neighbourhood to take in.
//   normal: the cross product of a quad's diagonals (of two sides for a triangle); the
//     contract lists corners counter-clockwise seen from the outer side, so it points out.
//   axis: node_a -> node_b of the element's member (element_member), made square to the
//     normal; an element of a part with no member (e.g. a plate) takes its first side.
//   direction: the normal turned FOCUS_TILT toward the member's axis, with a little of
//     ISO up. Straight along the normal the wall's colours read but its bend along the
//     member is flat; turned toward the axis, the wall runs away from the eye, so the
//     change of colour along the member (where a wall bends near a joint) is seen
//     while the face keeps cos(FOCUS_TILT) of its width. The axis' sign is the one on
//     the default view's side, so the camera does not swing round to the far side.
//   radius: 1.5 times the larger side of the member's section (the whole tube around
//     the element), at least 4 element edges.
const FOCUS_TILT = (35 * Math.PI) / 180;
const FOCUS_UP = 0.25;
export function maxElementFocus(rec, elem, design) {
  const ids = rec.elements[elem];
  const pts = ids.map((i) => v3(rec.nodes_mm[i]));
  const center = pts.reduce((a, p) => a.add(p), new THREE.Vector3()).multiplyScalar(1 / pts.length);
  const normal =
    pts.length === 4
      ? pts[2].clone().sub(pts[0]).cross(pts[3].clone().sub(pts[1]))
      : pts[1].clone().sub(pts[0]).cross(pts[2].clone().sub(pts[0]));
  normal.normalize();
  const memberId = rec.element_member?.[elem];
  const m = (design?.members ?? []).find((x) => x.id === memberId);
  const nodes = Object.fromEntries((design?.nodes ?? []).map((n) => [n.id, v3(n.xyz_mm)]));
  const axis = m && nodes[m.node_a] && nodes[m.node_b] ? nodes[m.node_b].clone().sub(nodes[m.node_a]) : pts[1].clone().sub(pts[0]);
  axis.addScaledVector(normal, -axis.dot(normal));
  if (axis.lengthSq() < 1e-12) axis.set(1, 0, 0).addScaledVector(normal, -normal.x);
  axis.normalize();
  if (axis.dot(v3(VIEWS.default)) < 0) axis.negate();
  const dir = normal.clone().multiplyScalar(Math.cos(FOCUS_TILT)).addScaledVector(axis, Math.sin(FOCUS_TILT));
  dir.z += FOCUS_UP;
  dir.normalize();
  let edge = 0;
  for (let i = 0; i < pts.length; i += 1) edge = Math.max(edge, pts[i].distanceTo(pts[(i + 1) % pts.length]));
  const sec = m ? sectionSize(design.sections?.[m.section]) : null;
  const radius = Math.max(sec ? 1.5 * Math.max(sec.width, sec.height) : 0, 4 * edge);
  return { elem, member: memberId ?? null, center, normal, axis, dir, radius };
}

function pushEdges(out, geometry, matrix, threshold = 20) {
  const edges = new THREE.EdgesGeometry(geometry, threshold);
  const pos = edges.getAttribute("position");
  const p = new THREE.Vector3();
  for (let i = 0; i < pos.count; i += 1) {
    p.fromBufferAttribute(pos, i);
    if (matrix) p.applyMatrix4(matrix);
    out.push(p.x, p.y, p.z);
  }
  edges.dispose();
}

function cgTexture() {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d");
  const r = 56;
  g.translate(64, 64);
  g.fillStyle = "#fbf9f4";
  g.beginPath();
  g.arc(0, 0, r, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = INK;
  for (const start of [0, Math.PI]) {
    g.beginPath();
    g.moveTo(0, 0);
    g.arc(0, 0, r, start - Math.PI / 2, start);
    g.closePath();
    g.fill();
  }
  g.lineWidth = 7;
  g.strokeStyle = INK;
  g.beginPath();
  g.arc(0, 0, r, 0, Math.PI * 2);
  g.stroke();
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function label(text, cls) {
  const el = document.createElement("div");
  el.className = `lbl ${cls ?? ""}`;
  el.innerHTML = text;
  return new CSS2DObject(el);
}

// A label that says one of the view's main values (the largest value, the picked wheel's
// force): it is laid out outside the vehicle with a leader line to its point
// (placeOutLabels), and counted as a label (data-kind) by the present mode.
function keyLabel(text, cls) {
  const tag = label(text, `${cls ?? ""} lbl-key lbl-out`);
  tag.element.dataset.kind = "label";
  return tag;
}

const WHEEL_FORCES = new Set(["reaction", "grip"]);

// Distance (px) from a point {x, y} to the segment (ax, ay)-(bx, by).
function segmentDistance(p, ax, ay, bx, by) {
  const dx = bx - ax;
  const dy = by - ay;
  const L = dx * dx + dy * dy;
  const u = L > 0 ? Math.max(0, Math.min(1, ((p.x - ax) * dx + (p.y - ay) * dy) / L)) : 0;
  return Math.hypot(p.x - (ax + u * dx), p.y - (ay + u * dy));
}
const LEADER_GAP_PX = 18; // between the vehicle's outline on screen and a label laid outside it
const LABEL_MARGIN = { top: 52, bottom: 124, side: 10 }; // the view's edges kept for its buttons and bars

// A drawn solid of the picture (not a line, not an overlay twin, not a lit copy).
function isSolid(x) {
  return x.isMesh && !x.isLineSegments2 && x.layers.isEnabled(0) && !x.userData.lit;
}

// The matrix of obj in root's frame (its local matrices up to root).
function relMatrix(obj, root) {
  const m = new THREE.Matrix4();
  for (let o = obj; o && o !== root; o = o.parent) {
    if (o.matrixAutoUpdate) o.updateMatrix();
    m.premultiply(o.matrix);
  }
  return m;
}

function disposeTree(obj) {
  obj.traverse((o) => {
    o.geometry?.dispose?.();
    if (o.material && !o.material.userData?.shared) o.material.dispose?.();
    if (o.isCSS2DObject) o.element.remove();
  });
  obj.clear();
}

// Arrow along +y of its group: a unit shaft and cone, stretched by placeArrow.
function makeArrow(color) {
  const mat = new THREE.MeshBasicMaterial({ color, depthTest: false, transparent: true });
  const g = new THREE.Group();
  const shaft = new THREE.Mesh(new THREE.CylinderGeometry(9, 9, 1, 12), mat);
  const cone = new THREE.Mesh(new THREE.ConeGeometry(26, 1, 16), mat);
  g.add(shaft, cone);
  for (const o of [g, shaft, cone]) o.renderOrder = 8;
  return overlayOnly(g);
}

function placeArrow(g, start, dir, len) {
  const head = Math.min(90, len * 0.4);
  const [shaft, cone] = g.children;
  shaft.scale.y = len - head;
  shaft.position.y = (len - head) / 2;
  cone.scale.y = head;
  cone.position.y = len - head / 2;
  g.quaternion.setFromUnitVectors(UP, dir);
  g.position.copy(start);
}

// R = Rz(yaw) Ry(pitch) Rx(roll), the K2 convention.
function bodyMatrix(body, rpy) {
  const m = new THREE.Matrix4().makeRotationFromEuler(new THREE.Euler(rpy[0], rpy[1], rpy[2], "ZYX"));
  return m.setPosition(body[0], body[1], body[2]);
}

function wheelMatrix(base, steer, spin) {
  return base
    .clone()
    .multiply(new THREE.Matrix4().makeRotationZ(steer))
    .multiply(new THREE.Matrix4().makeRotationY(spin));
}

export class Workbench3D {
  // opts.renderer: a WebGLRenderer shared with other views (the cut pages draw many
  // views through one); such a view has no labels and no mouse control, and is
  // drawn with renderInto. opts.ground: "shadow" for a ground that only takes the
  // shadow (a vehicle standing on the page's own background). opts.quality: the real
  // look's cost (look.js QUALITY; ?quality=). opts.stage: a cut page's view (look.js: the
  // sky and its haze behind, the gravel road, the sun behind the vehicle); in the real look
  // it is drawn through the renderer's shared post (realpost.js sharedPost: antialiasing,
  // ambient occlusion) when its quality has them.
  constructor(host, lookName = "zu", opts = {}) {
    this.host = host;
    this.look = new Look(lookName, opts.quality, { stage: !!opts.stage });
    this.ownRenderer = !opts.renderer;
    this.groundStyle = opts.ground ?? null;
    this.renderer = opts.renderer ?? new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(28, 1, 0.05, 200);
    this.camera.position.set(4, 3, 4);
    if (this.ownRenderer) {
      this.renderer.setClearColor(0x000000, 0);
      this.renderer.domElement.className = "gl";
      host.appendChild(this.renderer.domElement);
      this.labelRenderer = new CSS2DRenderer();
      this.labelRenderer.domElement.className = "gl-labels";
      host.appendChild(this.labelRenderer.domElement);
      // leader lines from the labels laid outside the vehicle to their points
      const box = document.createElement("div");
      box.innerHTML = `<svg class="gl-leaders"></svg>`; // parsed as SVG
      this.leaders = box.firstChild;
      host.appendChild(this.leaders);
      this.controls = new OrbitControls(this.camera, this.renderer.domElement);
      this.controls.enableDamping = false;
      this.controls.screenSpacePanning = true;
    } else {
      this.labelRenderer = null;
      this.controls = { target: new THREE.Vector3(), update: () => this.camera.lookAt(this.controls.target) };
    }

    this.sun = this.look.setUp(this.renderer, this.scene);
    // The lights are in the overlay's layer too: a pass that counts other lights than the
    // pass before makes three.js choose every lit material's shader again (each frame).
    this.scene.traverse((o) => o.isLight && o.layers.enable(OVERLAY));
    // the real look on its own renderer: antialiasing and occlusion in post (none at the low quality)
    const q = this.look.q;
    this.post = this.ownRenderer && this.look.real && (q.ao || q.msaa) ? new RealPost(this.renderer, this.scene, this.camera, q) : null;
    this.sharedPost = !this.ownRenderer && this.look.stage && !!(q.ao || q.msaa);

    this.world = new THREE.Group();
    this.scene.add(this.world);
    this.ground = new THREE.Group();
    this.iso = new THREE.Group();
    this.iso.matrixAutoUpdate = false;
    this.iso.matrix.copy(ISO_TO_THREE);
    this.world.add(this.ground, this.iso);

    this.body = new THREE.Group();
    this.body.matrixAutoUpdate = false;
    this.model = new THREE.Group();
    this.parts = new THREE.Group();
    this.overGhosts = new THREE.Group();
    this.names = new THREE.Group();
    this.shells = new THREE.Group(); // shell meshes (one per mesh_id, built when a contour needs it)
    this.lit = new THREE.Group(); // a rule hit's lit parts, nodes and the design's box (setRuleFocus); shown with the parts off too
    this.body.add(this.model, this.shells, this.parts, this.overGhosts, this.names, this.lit);

    this.wheels = new THREE.Group();
    this.arrows = new THREE.Group();
    this.motionArrows = new THREE.Group();
    this.ghost = new THREE.Group();
    this.markers = new THREE.Group();
    this.iso.add(this.body, this.wheels, this.arrows, this.motionArrows, this.ghost, this.markers);

    this.inkMat = new LineMaterial({ color: INK, linewidth: 1.7 });
    this.mutedMat = new LineMaterial({ color: MUTED, linewidth: 1.1 });
    this.freeEdgeMat = new LineMaterial({ color: FREE_EDGE_COLOR, linewidth: 4, depthTest: false, transparent: true });
    this.litLine = new LineMaterial({ color: LIT_LINE, linewidth: 3.5, depthTest: false, transparent: true });
    this.litFill = new THREE.MeshBasicMaterial({ color: LIT_FILL, transparent: true, opacity: 0.45, depthTest: false, depthWrite: false, toneMapped: false, fog: false, side: THREE.DoubleSide });
    for (const m of [this.inkMat, this.mutedMat, this.freeEdgeMat, this.litLine, this.litFill]) m.userData.shared = true;
    this.litObjs = []; // what setRuleFocus added (overlay only), taken away by clearLit
    this.litIds = [];
    this.litShown = [];
    this.links = new LineSegments2(new LineSegmentsGeometry(), this.mutedMat);
    this.iso.add(this.links);

    this.props = new SceneProps(this.look, this.inkMat, this.mutedMat);
    this.iso.add(this.props.world);
    this.fixedIso = new THREE.Group();
    this.fixedIso.matrixAutoUpdate = false;
    this.fixedIso.matrix.copy(ISO_TO_THREE);
    this.fixedIso.add(this.props.fixed);
    this.scene.add(this.fixedIso);
    this.sceneDesc = null;
    this.sceneKey = null;

    this.cg = new THREE.Sprite(new THREE.SpriteMaterial({ map: cgTexture(), depthTest: false, transparent: true }));
    this.cg.renderOrder = 10;
    overlayOnly(this.cg);
    this.cg.scale.setScalar(150);
    this.cg.visible = false;
    this.iso.add(this.cg);

    this.design = null;
    this.members = {}; // id -> { mesh, length, node_a, node_b, mid, width, height }
    this.shellObjs = {}; // mesh_id -> shellObj()
    this.wheelObjs = {}; // support id -> { group, base (design position), ring, tag, r, frameNode }
    this.partObjs = {};
    this.motion = null;
    this.frame = null;
    this.view = null; // camera preset in use (a key of VIEWS)
    this.toggles = {};
    this.fitted = false;
    this.keys = { contour: null, vectors: null };
    this.size = { w: 1, h: 1 };
  }

  resize(w, h, pixelRatio) {
    this.size = { w, h };
    if (this.ownRenderer) {
      this.renderer.setPixelRatio(pixelRatio);
      this.renderer.setSize(w, h, true);
      this.labelRenderer.setSize(w, h);
      this.leaders.setAttribute("width", String(w));
      this.leaders.setAttribute("height", String(h));
      this.post?.setSize(w, h, this.renderer.getPixelRatio());
    }
    this.applyAspect();
    for (const m of [this.inkMat, this.mutedMat, this.freeEdgeMat, this.litLine]) m.resolution.set(w, h);
    this.props.resize(w, h);
  }

  setDesign(design) {
    if (design === this.design) return;
    this.design = design;
    this.clearLit(); // before the meshes it hangs on go
    for (const g of [this.model, this.shells, this.parts, this.names, this.overGhosts, this.markers, this.wheels]) disposeTree(g);
    this.shellObjs = {};
    this.model.visible = true;
    this.members = {};
    this.wheelObjs = {};
    this.partObjs = {};
    this.keys.contour = null;
    this.motion = null;
    this.frame = undefined;
    this.sceneKey = null;
    this.sceneDesc = null;
    this.props.clear();
    disposeTree(this.ghost);
    disposeTree(this.motionArrows);
    if (!design) return;

    const nodes = Object.fromEntries((design.nodes ?? []).map((n) => [n.id, v3(n.xyz_mm)]));
    this.nodes = nodes;
    const ink = [];
    const box = new THREE.Box3();

    for (const m of design.members ?? []) {
      const g = memberGeometry(design, m, nodes);
      if (!g) continue;
      const mesh = new THREE.Mesh(g.geo, this.look.memberMaterial(design.materials?.[m.material] ?? null, m.part ?? null));
      mesh.material.userData.baseColor = "#" + mesh.material.color.getHexString();
      mesh.castShadow = mesh.receiveShadow = this.look.real;
      mesh.matrixAutoUpdate = false;
      mesh.matrix.copy(g.matrix);
      mesh.userData = { id: m.id };
      this.model.add(mesh);
      // Edges in the member's own frame, so a mode shape can move them with it.
      const localEdges = [];
      pushEdges(localEdges, g.geo, null, g.round ? 40 : 20);
      pushEdges(ink, g.geo, mesh.matrix, g.round ? 40 : 20);
      this.members[m.id] = { mesh, node_a: m.node_a, node_b: m.node_b, localEdges, ...g };
      box.expandByPoint(nodes[m.node_a]).expandByPoint(nodes[m.node_b]);
      if (this.labelRenderer) {
        const tag = label(esc(m.id), "lbl-id");
        tag.position.copy(g.mid);
        this.names.add(tag);
      }
    }
    // the names of members and nodes only where labels are drawn (not on a shared renderer)
    for (const [id, p] of this.labelRenderer ? Object.entries(nodes) : []) {
      const tag = label(esc(id), "lbl-node");
      tag.position.copy(p);
      this.names.add(tag);
    }
    this.restInk = ink;
    this.inkLines = new LineSegments2(new LineSegmentsGeometry().setPositions(ink.length ? ink : [0, 0, 0, 0, 0, 0]), this.inkMat);
    this.inkLines.visible = ink.length > 0 && !this.look.real;
    this.inkLines.layers.enable(OVERLAY); // again over the contour's twins
    this.inkLines.renderOrder = 1;
    this.model.add(this.inkLines);
    this.modeShape = null;

    for (const s of design.supports ?? []) {
      const c = v3(s.position_mm);
      const { geo, r, w } = supportGeometry(s);
      const group = new THREE.Group();
      group.matrixAutoUpdate = false;
      const lines = [];
      if (s.kind === "wheel") {
        // A tyre, rim, hub and spokes (the spokes show the wheel turning); one mesh per material.
        const pieces = wheelPieces(r, w);
        for (const pc of pieces) if (pc.sub !== "rim") pushEdges(lines, pc.geo, null, 35);
        for (const pc of mergePieces(pieces)) {
          const mesh = new THREE.Mesh(pc.geo, this.look.partMaterial("wheel", pc.sub));
          mesh.castShadow = mesh.receiveShadow = this.look.real;
          group.add(mesh);
        }
        for (const side of [-1, 1]) lines.push(0, side * w * 0.36, 0, r * 0.6, side * w * 0.36, 0);
        geo.dispose();
      } else {
        group.add(new THREE.Mesh(geo, this.look.partMaterial("wheel", "tyre")));
        pushEdges(lines, geo, null, 30);
      }
      if (!this.look.real) group.add(new LineSegments2(new LineSegmentsGeometry().setPositions(lines), this.inkMat));
      const ring = new THREE.Mesh(
        new THREE.TorusGeometry(r * 1.06, Math.max(12, r * 0.07), 8, 48).rotateX(Math.PI / 2),
        new THREE.MeshBasicMaterial({ color: LIMIT_RED, transparent: true, opacity: 0.55, depthWrite: false }),
      );
      ring.visible = false;
      group.add(ring);
      const tag = label(esc(s.id), "lbl-id");
      tag.position.set(0, 0, r + 60);
      if (this.labelRenderer) group.add(tag); // a shared renderer draws no labels: kept out of its scene
      this.wheels.add(group);
      this.wheelObjs[s.id] = { group, base: c, ring, tag, r, w, frameNode: s.frame_node ?? null };
      box.expandByPoint(c.clone().add(v3([r, w / 2, r]))).expandByPoint(c.clone().sub(v3([r, w / 2, r])));
    }

    // Wheels listed as parts are drawn from the supports instead.
    const wheelAt = (c) => (design.supports ?? []).some((sp) => v3(sp.position_mm).distanceTo(c) < 50);
    // A seat per person, not per occupant part: an occupant may be given in body segments
    // ("<person>" the torso, "<person>.<segment>"), and a person's parts board together, in
    // the order the persons first come in design.parts (hiraku/contract/loading.py).
    const occupants = (design.parts ?? []).filter((p) => p.kind === "occupant");
    const seatOf = new Map();
    for (const p of occupants) if (!seatOf.has(personOf(p))) seatOf.set(personOf(p), seatOf.size);
    // A person with a head part is drawn segment by segment; a person without one (an occupant
    // given as one box, or a torso box that still holds the head) keeps the torso drawn as a
    // capsule with a ball for the head, as before.
    const headed = new Set(occupants.filter((p) => segmentOf(p) === "head").map(personOf));
    for (const p of design.parts ?? []) {
      const c = v3(p.box.center_mm);
      const sz = p.box.size_mm;
      box.expandByPoint(c.clone().add(v3(sz).multiplyScalar(0.5))).expandByPoint(c.clone().sub(v3(sz).multiplyScalar(0.5)));
      if (p.kind === "wheel" && wheelAt(c)) continue;
      const g = new THREE.Group();
      let edges = null;
      if (p.kind === "occupant" && (segmentOf(p) !== null || headed.has(personOf(p)))) {
        // one rounded shape inside the segment's box, turned with it (rpy_deg as for any part)
        const holder = new THREE.Group();
        holder.position.copy(c);
        const rpy = p.box.rpy_deg ?? [0, 0, 0];
        holder.rotation.set((rpy[0] * Math.PI) / 180, (rpy[1] * Math.PI) / 180, (rpy[2] * Math.PI) / 180, "ZYX");
        const mat = this.look.partMaterial("occupant");
        for (const pc of occupantSegmentPieces(p)) {
          const mesh = new THREE.Mesh(pc.geo, mat);
          mesh.castShadow = this.look.real;
          holder.add(mesh);
        }
        g.add(holder);
      } else if (p.kind === "occupant") {
        const mat = this.look.partMaterial("occupant");
        const r = Math.min(sz[0], sz[1]) * 0.26;
        const head = Math.min(sz[0], sz[1]) * 0.2;
        const torsoLen = Math.max(10, sz[2] - 2 * head - 2 * r);
        const torso = new THREE.Mesh(new THREE.CapsuleGeometry(r, torsoLen, 6, 16), mat);
        torso.rotation.x = Math.PI / 2; // capsule axis (local y) -> ISO z
        torso.position.set(c.x, c.y, c.z - sz[2] / 2 + r + torsoLen / 2);
        const ball = new THREE.Mesh(new THREE.SphereGeometry(head, 20, 14), mat);
        ball.position.set(c.x, c.y, c.z + sz[2] / 2 - head);
        for (const m of [torso, ball]) m.castShadow = this.look.real;
        g.add(torso, ball);
      } else {
        // The part's shape for its kind inside its box, or the box itself (its edges
        // rounded in the real look only).
        const pieces = partPieces(p, { round: this.look.real, design });
        const holder = new THREE.Group();
        holder.position.copy(c);
        const rpy = p.box.rpy_deg ?? [0, 0, 0];
        holder.rotation.set((rpy[0] * Math.PI) / 180, (rpy[1] * Math.PI) / 180, (rpy[2] * Math.PI) / 180, "ZYX");
        holder.updateMatrix();
        const lines = [];
        for (const pc of pieces) pushEdges(lines, pc.geo, holder.matrix, 35);
        // one mesh per material (the pieces of the same sub merged)
        for (const pc of mergePieces(pieces)) {
          const mesh = new THREE.Mesh(pc.geo, this.look.partMaterial(p, pc.sub, design.materials ?? null));
          mesh.material.userData.sub = pc.sub;
          mesh.renderOrder = 2;
          mesh.castShadow = mesh.receiveShadow = this.look.real;
          holder.add(mesh);
        }
        g.add(holder);
        if (!this.look.real) {
          edges = new LineSegments2(new LineSegmentsGeometry().setPositions(lines.length ? lines : [0, 0, 0, 0, 0, 0]), this.mutedMat);
          edges.layers.enable(OVERLAY);
          edges.renderOrder = 1;
          g.add(edges);
        }
      }
      // Short name on the tag (the part before a bracket); the full name is the tooltip.
      const short = String(p.name ?? p.id).replace(/\s*[（(].*$/, "") || p.id;
      const tag = label(`${esc(short)}<small>${fmt(p.mass_kg)} kg</small>`, "lbl-part");
      tag.element.title = p.name ?? p.id;
      tag.position.copy(c).add(new THREE.Vector3(0, 0, sz[2] / 2 + 40));
      if (this.labelRenderer) g.add(tag); // a shared renderer draws no labels: kept out of its scene
      this.parts.add(g);
      this.partObjs[p.id] = { group: g, edges, tag, kind: p.kind, seat: p.kind === "occupant" ? seatOf.get(personOf(p)) : null, onBoard: true, center: c, size: sz, short };
    }

    this.bboxIso = box.isEmpty() ? new THREE.Box3(new THREE.Vector3(-1000, -1000, 0), new THREE.Vector3(1000, 1000, 1000)) : box;
    this.loading = undefined;
    this.setLoading(null);
    this.buildGround();
    this.buildAxes();
    this.setFrame(null);
    if (!this.fitted) {
      this.fit();
      this.fitted = true;
    }
    this.lightAll(); // a rule hit pointed at before stays lit on the new version
  }

  // Light what a rule hit names (the person points at it in A2): ids of parts, frame members,
  // frame nodes, "support:<id>" (that wheel and its suspension's mounting points),
  // "<support>.<point>" (one mounting point) and "design" (the whole design: a box round it).
  // Only the look changes: a see-through fill and a thick outline drawn over everything in the
  // overlay, on the objects' own geometry (no material, number or shape of the design is
  // touched, so the running glow of animate() and the contour stay as they are). An id with
  // nothing in the 3D is left out. [] puts everything back. Returns the ids that were lit.
  setRuleFocus(ids) {
    const list = [...new Set((Array.isArray(ids) ? ids : []).filter((x) => typeof x === "string" && x))];
    if (list.join("\n") === this.litIds.join("\n")) return this.litShown;
    this.litIds = list;
    this.lightAll();
    return this.litShown;
  }

  lightAll() {
    this.clearLit();
    this.litShown = this.design ? this.litIds.filter((id) => this.lightOne(id)) : [];
  }

  clearLit() {
    for (const o of this.litObjs) {
      o.parent?.remove(o);
      if (o.userData.ownGeometry) o.geometry.dispose(); // a twin's geometry is its mesh's own
    }
    this.litObjs = [];
    this.litShown = [];
  }

  lightOne(id) {
    const d = this.design;
    if (id === "design") return this.litBox(this.bboxIso);
    if (id.startsWith("support:")) {
      const sid = id.slice("support:".length);
      const s = (d.supports ?? []).find((x) => x.id === sid);
      if (!s) return false;
      let hit = this.litWheel(sid);
      for (const p of Object.values(s.suspension?.hardpoints_mm ?? {})) hit = this.litDot(p) || hit;
      return hit;
    }
    let hit = false;
    const part = this.partObjs[id];
    if (part) {
      // under the body (not the parts' group), so it shows with the parts switched off too
      part.group.traverse((x) => {
        if (isSolid(x)) hit = this.litTwin(x, this.lit, relMatrix(x, this.body)) || hit;
      });
    }
    // a wheel listed as a part is drawn from its support
    const asPart = (d.parts ?? []).find((p) => p.id === id);
    if (!part && asPart?.kind === "wheel") {
      const c = v3(asPart.box.center_mm);
      const s = (d.supports ?? []).find((sp) => v3(sp.position_mm).distanceTo(c) < 50);
      if (s) hit = this.litWheel(s.id) || hit;
    }
    // a frame member, or the members of a part (member.part)
    for (const m of d.members ?? []) {
      if ((m.id === id || m.part === id) && this.members[m.id]) hit = this.litTwin(this.members[m.id].mesh, this.members[m.id].mesh, null) || hit;
    }
    if (!hit && this.nodes?.[id]) hit = this.litDot(this.nodes[id].toArray());
    if (!hit) {
      // "<support>.<point>": a suspension mounting point
      const dot = id.indexOf(".");
      const s = dot > 0 ? (d.supports ?? []).find((x) => x.id === id.slice(0, dot)) : null;
      const p = s?.suspension?.hardpoints_mm?.[id.slice(dot + 1)];
      if (p) hit = this.litDot(p);
    }
    return hit;
  }

  // A lit copy of a mesh on its own geometry: the fill, and the outline of its edges.
  litTwin(mesh, parent, matrix, ownGeometry = false) {
    const fill = new THREE.Mesh(mesh.geometry, this.litFill);
    fill.userData.ownGeometry = ownGeometry;
    fill.matrixAutoUpdate = false;
    if (matrix) fill.matrix.copy(matrix);
    fill.renderOrder = 7;
    const lines = [];
    pushEdges(lines, mesh.geometry, matrix, 35);
    const edges = new LineSegments2(new LineSegmentsGeometry().setPositions(lines.length ? lines : [0, 0, 0, 0, 0, 0]), this.litLine);
    edges.userData.ownGeometry = true;
    edges.renderOrder = 8;
    for (const o of [fill, edges]) {
      overlayOnly(o);
      o.userData.lit = true;
      parent.add(o);
      this.litObjs.push(o);
    }
    return true;
  }

  litWheel(sid) {
    const w = this.wheelObjs[sid];
    if (!w) return false;
    let hit = false;
    for (const x of [...w.group.children]) if (x !== w.ring && isSolid(x)) hit = this.litTwin(x, w.group, null) || hit;
    return hit;
  }

  // A point (ISO mm, on the body): a ball sized to the design.
  litDot(p) {
    if (!Array.isArray(p) || p.length !== 3 || !p.every(Number.isFinite)) return false;
    const size = this.bboxIso ? this.bboxIso.getSize(new THREE.Vector3()).length() : 2000;
    const ball = new THREE.Mesh(new THREE.SphereGeometry(Math.max(20, size * 0.012), 16, 10), this.litFill);
    ball.position.copy(v3(p));
    ball.updateMatrix();
    return this.litTwin(ball, this.lit, ball.matrix, true); // the ball itself is not drawn: its lit copy owns the geometry
  }

  // A box round the whole design (the outline only).
  litBox(box) {
    if (!box || box.isEmpty()) return false;
    const pad = box.getSize(new THREE.Vector3()).length() * 0.02;
    const geo = new THREE.BoxGeometry(...box.getSize(new THREE.Vector3()).addScalar(2 * pad).toArray());
    const lines = [];
    pushEdges(lines, geo, new THREE.Matrix4().makeTranslation(box.getCenter(new THREE.Vector3())), 35);
    geo.dispose();
    const edges = new LineSegments2(new LineSegmentsGeometry().setPositions(lines), this.litLine);
    edges.userData.ownGeometry = true;
    edges.userData.lit = true;
    edges.renderOrder = 8;
    overlayOnly(edges);
    this.lit.add(edges);
    this.litObjs.push(edges);
    return true;
  }

  // Who is on board for a load case's params.loading (empty / driver / two /
  // two_cargo; none means two_cargo). Occupants board in the order they are
  // listed (a person's segments with the person, o.seat) and cargo rides only
  // with two_cargo, as the solvers do. Parts that
  // stay off are drawn faint and left out of the centre of gravity.
  setLoading(loading) {
    if (loading === this.loading) return;
    this.loading = loading;
    const seats = { empty: 0, driver: 1, two: 2, two_cargo: 2 }[loading ?? "two_cargo"] ?? 2;
    const cargo = (loading ?? "two_cargo") === "two_cargo";
    for (const o of Object.values(this.partObjs)) {
      o.onBoard = o.kind === "occupant" ? o.seat < seats : o.kind === "cargo" ? cargo : true;
      if (o.edges) o.edges.visible = o.onBoard;
      o.tag.element.classList.toggle("off", !o.onBoard);
    }
    this.applyPartLook();
    this.cgDesign = this.design ? this.displayCg(this.design) : null;
    this.placeCg();
  }

  // A part's colour and opacity: off board is faint; a part with a contour value
  // (e.g. its temperature) takes the contour colour; otherwise its own colour.
  // In the real look a part that stays off is not drawn at all.
  applyPartLook() {
    const ct = this.keys.contour;
    const real = this.look.real;
    for (const [id, o] of Object.entries(this.partObjs)) {
      const v = ct?.parts?.[id];
      if (real) o.group.visible = o.onBoard;
      o.group.traverse((x) => {
        const m = x.isMesh ? x.material : null;
        if (!m || m.userData.baseColor === undefined) return;
        const own = !m.userData.sub; // pieces like a spring keep their colour
        const valued = typeof v === "number" && own;
        const hex = valued ? (overOf(ct, id, v) ? OVER_COLOR : colorOf(v, ct.max, ct.min)) : null;
        m.color.set(hex ?? m.userData.baseColor);
        paintTwin(x, hex);
        // A part with a value is often inside the vehicle (a battery under the floor and the
        // seats). The diagram look shows it through what is in front; in the real look its
        // colour is drawn over what is in front, so it can still be read.
        const twin = x.userData.twin;
        if (twin && twin.material.depthTest !== !(real && valued)) {
          twin.material.depthTest = !(real && valued);
          twin.renderOrder = real && valued ? 3 : 0;
        }
        if (!real && m.userData.baseOpacity !== undefined) m.opacity = o.onBoard ? (valued ? 0.85 : m.userData.baseOpacity) : 0.07;
      });
    }
  }

  // Centre of gravity used only to place the mark when the analysis gives none:
  // mass-weighted centre of the parts on board and the frame members (density x
  // section area x length). Members are left out when the design lists the
  // frame as a part with a mass.
  displayCg(design) {
    let m = 0;
    const sum = new THREE.Vector3();
    for (const p of design.parts ?? []) {
      if (!(p.mass_kg > 0) || this.partObjs[p.id]?.onBoard === false) continue;
      sum.addScaledVector(v3(p.box.center_mm), p.mass_kg);
      m += p.mass_kg;
    }
    const nodes = this.nodes ?? {};
    const frameAsPart = (design.parts ?? []).some((p) => p.kind === "frame" && p.mass_kg > 0);
    for (const mem of frameAsPart ? [] : design.members ?? []) {
      const a = nodes[mem.node_a];
      const b = nodes[mem.node_b];
      const sec = design.sections?.[mem.section];
      const mat = design.materials?.[mem.material];
      if (!a || !b || !sec || !(mat?.density_kg_m3 > 0)) continue;
      const area = sec.A_mm2 ?? sectionArea(sec);
      if (!(area > 0)) continue;
      const mass = mat.density_kg_m3 * area * 1e-6 * a.distanceTo(b) * 1e-3;
      sum.addScaledVector(a.clone().add(b).multiplyScalar(0.5), mass);
      m += mass;
    }
    return m > 0 ? sum.multiplyScalar(1 / m) : null;
  }

  // The recorded centre of mass when the frame has one, else the design's, carried by the body.
  placeCg() {
    const f = this.frame;
    if (f?.cg) this.cgPos = v3(f.cg);
    else if (this.cgDesign) this.cgPos = this.cgDesign.clone().applyMatrix4(this.body.matrix);
    else this.cgPos = null;
    if (this.cgPos) this.cg.position.copy(this.cgPos);
    this.cg.visible = !!this.toggles.cg && !!this.cgPos && !this.closeUp;
  }

  // The ground under the vehicle: a ruled grid (diagram look) or earth (real look).
  // A rough road's own surface replaces it; on a tilt table it is the table top.
  buildGround() {
    disposeTree(this.ground);
    const desc = this.sceneDesc;
    if (desc?.surface || !this.bboxIso) return;
    const b = this.bboxIso.clone();
    const p = this.motion?.path;
    // the vehicle anywhere along its path (the path is the design origin's)
    if (p) b.expandByPoint(new THREE.Vector3(p.minX + b.min.x, p.minY + b.min.y, 0)).expandByPoint(new THREE.Vector3(p.maxX + b.max.x, p.maxY + b.max.y, 0));
    const pad = desc?.table ? 700 : 1500;
    const x0 = Math.floor((b.min.x - pad) / 100) * 100;
    const x1 = Math.ceil((b.max.x + pad) / 100) * 100;
    const y0 = desc?.table ? -(desc.halfWidth + 700) : Math.floor((b.min.y - pad) / 1000) * 1000;
    const y1 = desc?.table ? desc.halfWidth + 700 : Math.ceil((b.max.y + pad) / 1000) * 1000;
    if (this.look.real || this.groundStyle === "shadow") {
      if (desc?.table) return; // the table's own plate
      const holder = new THREE.Group();
      holder.matrixAutoUpdate = false;
      holder.matrix.copy(ISO_TO_THREE);
      // on a stage the road runs out to where the haze has hidden it (look.js GROUND_MM)
      const far = this.look.stage ? GROUND_MM : 20000;
      holder.add(this.groundStyle === "shadow" ? this.look.shadowCatcher(x0 - 5000, x1 + 5000, y0 - 5000, y1 + 5000) : this.look.earth(x0 - far, x1 + far, y0 - far, y1 + far));
      this.ground.add(holder);
      return;
    }
    const minor = [];
    const major = [];
    for (let x = x0; x <= x1; x += 100) (x % 1000 === 0 ? major : minor).push(x, y0, 0, x, y1, 0);
    for (let y = y0; y <= y1; y += 100) (y % 1000 === 0 ? major : minor).push(x0, y, 0, x1, y, 0);
    const mk = (arr, color) => {
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(arr, 3));
      const l = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.9 }));
      l.matrixAutoUpdate = false;
      l.matrix.copy(ISO_TO_THREE);
      return l;
    };
    this.ground.add(mk(minor, RULE), mk(major, RULE_MAJOR));
  }

  buildAxes() {
    const L = 300;
    const pts = [0, 0, 0, L, 0, 0, 0, 0, 0, 0, L, 0, 0, 0, 0, 0, 0, L];
    this.markers.add(new LineSegments2(new LineSegmentsGeometry().setPositions(pts), this.mutedMat));
    for (const [txt, p] of [["x 前", [L + 60, 0, 0]], ["y 左", [0, L + 60, 0]], ["z 上", [0, 0, L + 60]]]) {
      const t = label(txt, "lbl-axis");
      t.position.set(...p);
      this.markers.add(t);
    }
  }

  // The test's surroundings (scene.js) for a load case and its shown analysis;
  // also picks the camera view the scene needs.
  setScene(loadCase, analysis) {
    const key = `${loadCase?.load_case_id ?? ""}|${analysis?.analysis_id ?? ""}|${this.design?.design_version ?? ""}`;
    if (key === this.sceneKey && this.sceneMotion === this.motion) return;
    this.sceneKey = key;
    this.sceneMotion = this.motion;
    if (!this.design) return;
    this.sceneDesc = sceneOf(loadCase, analysis, this.motion, this.design);
    this.props.build(this.sceneDesc, this.bboxIso ?? new THREE.Box3());
    this.buildGround();
    const d = this.sceneDesc;
    // a step, a wall or a stop reads best from the side; a lane change from the front
    const has = (...kinds) => (d?.objects ?? []).some((o) => kinds.includes(o.kind));
    const side = d && (d.step || d.wall || has("step", "wall", "ramp") || analysis?.tool === "braking");
    const front = d && (d.course || has("cone", "gate"));
    const view = !d ? "default" : d.table ? "table" : front ? "front" : side ? "side" : "default";
    this.liftIsLimit = liftIsLimit(loadCase);
    this.ghost.visible = !!this.frame && !!this.toggles.limit && this.liftIsLimit;
    if (view !== this.view && this.design) {
      this.fit(VIEWS[view]);
      this.view = view;
    }
    this.setFrame(this.frame ?? null);
  }

  // Frame the design from a direction (VIEWS).
  fit(view = VIEWS.default) {
    const b = this.bboxIso;
    const center = isoToThree(b.getCenter(new THREE.Vector3()).toArray());
    const radius = b.getSize(new THREE.Vector3()).length() * 0.0005;
    const fov = (this.camera.fov * Math.PI) / 180;
    const dist = (radius / Math.sin(fov / 2)) * 1.0;
    const dir = isoToThree(view).normalize();
    this.controls.target.copy(center);
    this.camera.position.copy(center).addScaledVector(dir, dist);
    this.camera.near = dist / 50;
    this.camera.far = dist * 20;
    this.camera.updateProjectionMatrix();
    this.controls.update();
    this.followBase = null;
    this.followApplied = null;
  }

  // A mesh (the contract's mesh record: shells, solids or both) as faces and element edges,
  // built once per mesh_id. What is drawn is each shell element and the outer faces of the
  // solids (meshkind.js); each triangle corner has its own vertex, so an element takes one
  // colour; vNode / vElem say which mesh node and element each vertex is. The edges are each
  // shell side and each edge round a solid's outer face once, on the node positions.
  shellObj(rec) {
    const had = this.shellObjs[rec.mesh_id];
    if (had) return had;
    const P = rec.nodes_mm;
    const seen = outerFaces(rec);
    let corners = 0;
    for (const f of seen) corners += 3 * f.tris.length;
    const pos = new Float32Array(corners * 3);
    const vNode = new Uint32Array(corners);
    const vElem = new Uint32Array(corners);
    let k = 0;
    for (const f of seen) {
      for (const tri of f.tris) {
        for (const n of tri) {
          vNode[k] = n;
          vElem[k] = f.elem;
          pos.set(P[n], k * 3);
          k += 1;
        }
      }
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geo.setAttribute("color", new THREE.BufferAttribute(new Float32Array(corners * 3), 3));
    geo.computeVertexNormals();
    const faces = new THREE.Mesh(
      geo,
      new THREE.MeshStandardMaterial({ vertexColors: true, side: THREE.DoubleSide, roughness: 0.75, metalness: 0.05, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 }),
    );
    faces.castShadow = faces.receiveShadow = this.look.real;
    const nodePos = new Float32Array(P.length * 3);
    P.forEach((p, i) => nodePos.set(p, i * 3));
    const pairs = edgePairs(rec, seen);
    const eg = new THREE.BufferGeometry();
    eg.setAttribute("position", new THREE.BufferAttribute(nodePos, 3));
    eg.setIndex(pairs);
    // the element edges are drawn over the faces' twin, in the overlay
    const edges = overlayOnly(new THREE.LineSegments(eg, new THREE.LineBasicMaterial({ color: INK, transparent: true, opacity: 0.3, toneMapped: false })));
    edges.renderOrder = 1;
    paintTwin(faces, "vertex");
    const group = new THREE.Group();
    group.add(faces, edges);
    group.visible = false;
    this.shells.add(group);
    const o = { rec, group, faces, edges, vNode, vElem, rest: pos.slice(), nodeRest: nodePos.slice() };
    this.shellObjs[rec.mesh_id] = o;
    return o;
  }

  // Colour a shell's faces by element values (one colour per element) or by node values,
  // or by colours given per element (m.colors: the mesh's own views, quality.js).
  paintShell(o, m, contour) {
    const col = o.faces.geometry.getAttribute("color");
    const rgb = new Map(); // band colour -> [r, g, b] (linear)
    const c = new THREE.Color();
    for (let k = 0; k < o.vNode.length; k += 1) {
      const hex = m.colors ? m.colors[o.vElem[k]] : colorOf(m.elem ? m.elem[o.vElem[k]] : m.node[o.vNode[k]], contour.max, contour.min);
      let t = rgb.get(hex);
      if (!t) rgb.set(hex, (t = c.set(hex).toArray()));
      col.array.set(t, k * 3);
    }
    col.needsUpdate = true;
  }

  // contour (fields.js contourOf): values per member / node / part on [min, max],
  // modes (node displacement vectors) for a mode shape; null for plain gray. A contour
  // on a shell mesh draws that mesh in place of the beams.
  setContour(contour) {
    if (contour === this.keys.contour) return;
    this.keys.contour = contour;
    disposeTree(this.overGhosts);
    const shell = contour?.mesh ? this.shellObj(contour.mesh.rec) : null;
    for (const o of Object.values(this.shellObjs)) o.group.visible = o === shell;
    this.model.visible = !shell;
    this.setModeShape(contour?.modes || contour?.mesh?.modes ? contour : null);
    this.applyPartLook();
    this.freeEdgeLines = null;
    this.setupMarks = null;
    if (shell) {
      this.paintShell(shell, contour.mesh, contour);
      // the largest value on its element (or node), as recorded; the worst element of a metric
      const P = contour.mesh.rec.nodes_mm;
      const at = contour.mesh.maxAt;
      if (at) {
        const ids = at.elem !== undefined ? contour.mesh.rec.elements[at.elem] : [at.node];
        const where = new THREE.Vector3();
        for (const n of ids) where.add(v3(P[n]));
        where.multiplyScalar(1 / ids.length);
        const tag = keyLabel(esc(contour.mesh.tag ?? `最大 ${fmtVU(contour.frameMax ?? contour.fieldMax, contour.field.unit)}`), "lbl-max");
        tag.position.copy(where);
        this.overGhosts.add(tag);
      }
      if (contour.freeEdges?.length) {
        const pos = [];
        for (const [a, b] of contour.freeEdges) pos.push(...P[a], ...P[b]);
        this.freeEdgeLines = new LineSegments2(new LineSegmentsGeometry().setPositions(pos), this.freeEdgeMat);
        this.freeEdgeLines.renderOrder = 6;
        overlayOnly(this.freeEdgeLines);
        this.overGhosts.add(this.freeEdgeLines);
      }
      if (contour.setup) this.buildSetup(contour.setup, contour.mesh.rec);
      return;
    }
    for (const [id, info] of Object.entries(this.members)) {
      const mat = info.mesh.material;
      const geo = info.mesh.geometry;
      mat.vertexColors = false;
      const plain = mat.userData.baseColor ?? GRAY;
      if (!contour) {
        mat.color.set(plain);
        mat.needsUpdate = true;
        paintTwin(info.mesh, null);
        continue;
      }
      const v = contour.values?.[id];
      const va = contour.nodes?.[info.node_a];
      const vb = contour.nodes?.[info.node_b];
      if (typeof v === "number") {
        mat.color.set(colorOf(v, contour.max, contour.min));
        paintTwin(info.mesh, colorOf(v, contour.max, contour.min));
      } else if (typeof va === "number" && typeof vb === "number") {
        const pos = geo.getAttribute("position");
        const cols = new Float32Array(pos.count * 3);
        const c = new THREE.Color();
        for (let i = 0; i < pos.count; i += 1) {
          const u = pos.getX(i) / info.length + 0.5;
          c.set(colorOf(va + (vb - va) * u, contour.max, contour.min));
          cols.set([c.r, c.g, c.b], i * 3);
        }
        geo.setAttribute("color", new THREE.BufferAttribute(cols, 3));
        mat.color.set("#ffffff");
        mat.vertexColors = true;
        paintTwin(info.mesh, "vertex");
      } else {
        mat.color.set(plain);
        paintTwin(info.mesh, null);
      }
      mat.needsUpdate = true;
      const over = typeof v === "number" ? v > contour.max : Math.max(va ?? -Infinity, vb ?? -Infinity) > contour.max;
      if (contour.limitKnown && over) {
        const s = Math.max(info.width, info.height) * 2.2;
        const ghost = new THREE.Mesh(
          new THREE.BoxGeometry(info.length + s * 0.4, s, s),
          new THREE.MeshBasicMaterial({ color: LIMIT_RED, transparent: true, opacity: 0.22, depthWrite: false }),
        );
        ghost.matrixAutoUpdate = false;
        ghost.matrix.copy(info.mesh.matrix);
        this.overGhosts.add(ghost);
      }
    }
    // Parts with a value (temperatures): the recorded value on a tag, and red when over the limit.
    for (const [id, v] of Object.entries(contour?.parts ?? {})) {
      const o = this.partObjs[id];
      if (!o) continue;
      const over = overOf(contour, id, v);
      const tag = label(`${esc(o.short)}<small>${esc(fmtVU(v, contour.field.unit))}</small>`, `lbl-val${over ? " lbl-over" : ""}`);
      tag.position.copy(o.center).add(new THREE.Vector3(0, 0, o.size[2] / 2 + 40));
      this.overGhosts.add(tag);
      if (over) {
        const ghost = new THREE.Mesh(
          new THREE.BoxGeometry(o.size[0] * 1.15, o.size[1] * 1.15, o.size[2] * 1.15),
          new THREE.MeshBasicMaterial({ color: LIMIT_RED, transparent: true, opacity: 0.22, depthWrite: false }),
        );
        ghost.position.copy(o.center);
        this.overGhosts.add(ghost);
      }
    }
    if (contour?.where && this.members[contour.where] && contour.result) {
      const tag = keyLabel(`最大 ${esc(fmtVU(contour.result.value, contour.result.unit))}`, "lbl-max");
      tag.position.copy(this.members[contour.where].mid);
      this.overGhosts.add(tag);
    }
  }

  // What the solver put on the model (quality.js setupContourOf), on its mesh's nodes:
  // a cone pointing at the node along each stopped translation, a ring round each
  // stopped rotation's axis; each load an arrow with its head at origin_mm (lengths as
  // setVectors), a turning arrow round a moment's axis, and a thin line to each node the
  // load was spread to, darker the larger its weight.
  buildSetup(st, rec) {
    const g = new THREE.Group();
    const P = rec.nodes_mm;
    const AX = { x: new THREE.Vector3(1, 0, 0), y: new THREE.Vector3(0, 1, 0), z: new THREE.Vector3(0, 0, 1) };
    const Z = new THREE.Vector3(0, 0, 1);
    const mat = (color, opacity = 1) => new THREE.MeshBasicMaterial({ color, depthTest: false, transparent: true, opacity });
    const tMat = mat(FIX_T_COLOR);
    const rMat = mat(FIX_R_COLOR);
    for (const c of st.constraints) {
      const p = v3(P[c.node]);
      for (const d of c.dofs) {
        const axis = AX[d[1]];
        if (!axis) continue;
        let o;
        if (d[0] === "u") {
          o = new THREE.Mesh(new THREE.ConeGeometry(28, 80, 14), tMat);
          o.quaternion.setFromUnitVectors(UP, axis);
          o.position.copy(p).addScaledVector(axis, -48);
          o.userData.kind = "fix-t";
        } else {
          o = new THREE.Mesh(new THREE.TorusGeometry(60, 6, 8, 32), rMat);
          o.quaternion.setFromUnitVectors(Z, axis);
          o.position.copy(p);
          o.userData.kind = "fix-r";
        }
        o.userData.node = c.node;
        o.userData.dof = d;
        o.renderOrder = 9;
        g.add(o);
      }
    }
    const mags = st.loads.map((l) => Math.hypot(...l.vector_N));
    const fmax = Math.max(1e-9, ...mags);
    st.loads.forEach((l, i) => {
      const origin = v3(l.origin_mm);
      const mag = mags[i];
      if (mag > 0) {
        const dir = v3(l.vector_N).normalize();
        const len = 160 + 640 * (mag / fmax);
        const start = origin.clone().addScaledVector(dir, -len);
        const a = makeArrow(LOAD_COLOR);
        placeArrow(a, start, dir, len);
        a.userData.kind = "load";
        g.add(a);
        const tag = label(`${esc(l.label ?? `荷重 ${i + 1}`)}<small>|F| ${fmt(mag / 1000, 3)} kN</small>`, "lbl-vec lbl-load");
        tag.position.copy(start.clone().addScaledVector(dir, -40));
        g.add(tag);
      }
      const mo = Array.isArray(l.moment_Nmm) ? v3(l.moment_Nmm) : null;
      if (mo && mo.lengthSq() > 0) {
        // three quarters of a ring round the moment's axis, the cone at its end turning
        // the right-hand way
        const r = 150;
        const arc = 1.5 * Math.PI;
        const turn = new THREE.Group();
        const m = mat(LOAD_COLOR);
        turn.add(new THREE.Mesh(new THREE.TorusGeometry(r, 9, 8, 40, arc), m));
        const cone = new THREE.Mesh(new THREE.ConeGeometry(26, 70, 14), m);
        cone.position.set(r * Math.cos(arc), r * Math.sin(arc), 0);
        cone.quaternion.setFromUnitVectors(UP, new THREE.Vector3(-Math.sin(arc), Math.cos(arc), 0));
        turn.add(cone);
        turn.quaternion.setFromUnitVectors(Z, mo.clone().normalize());
        turn.position.copy(origin);
        turn.userData.kind = "moment";
        turn.traverse((o) => (o.renderOrder = 9));
        g.add(turn);
      }
      const spread = (l.spread_to ?? []).filter((s) => st.okNode(s.node) && Number.isFinite(s.weight));
      const wmax = Math.max(1e-12, ...spread.map((s) => Math.abs(s.weight)));
      for (const s of spread) {
        const geo = new THREE.BufferGeometry().setFromPoints([origin, v3(P[s.node])]);
        const line = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: LOAD_COLOR, transparent: true, depthTest: false, opacity: 0.2 + 0.8 * (Math.abs(s.weight) / wmax) }));
        line.userData = { kind: "spread", node: s.node, weight: s.weight };
        line.renderOrder = 7;
        g.add(line);
      }
    });
    this.setupMarks = overlayOnly(g);
    this.overGhosts.add(g);
  }

  // The focus (maxElementFocus) of a shell contour's largest element, or null.
  maxFocus(contour) {
    const at = contour?.mesh?.maxAt?.elem;
    return at === undefined ? null : maxElementFocus(contour.mesh.rec, at, this.design);
  }

  // Where the camera stands to see a focus with its neighbourhood (radius, the diameter
  // across) taking `fill` of the view's smaller side, or of its height with
  // side "height": { target, position, near, far } in three.js metres.
  focusView(f, fill = 0.5, side = "smaller") {
    const target = isoToThree(f.center.toArray());
    const d = isoToThree(f.dir.toArray()).normalize();
    const tv = Math.tan((this.camera.fov * Math.PI) / 360);
    const t = side === "height" ? tv : Math.min(tv, tv * this.camera.aspect);
    const dist = (f.radius * 0.001) / (fill * t);
    const span = this.bboxIso ? this.bboxIso.getSize(new THREE.Vector3()).length() * 0.001 : dist * 20;
    return { target, position: target.clone().addScaledVector(d, dist), near: dist / 50, far: dist + span * 3 };
  }

  // The camera at a view ({ target, position, near?, far? } in three.js metres).
  lookFrom(v) {
    this.controls.target.copy(v.target);
    this.camera.position.copy(v.position);
    if (v.near) this.camera.near = v.near;
    if (v.far) this.camera.far = v.far;
    this.camera.updateProjectionMatrix();
    this.controls.update();
  }

  cameraView() {
    return { target: this.controls.target.clone(), position: this.camera.position.clone(), near: this.camera.near, far: this.camera.far };
  }

  // Look at a shell contour's largest element up close (fill as in focusView), or, with
  // null, go back to the view from before the first focus.
  setFocus(contour, fill = 0.5) {
    const f = this.maxFocus(contour);
    const key = f ? `${contour.key}|${f.elem}` : null;
    if (key === this.focusKey) return;
    this.focusKey = key;
    if (f) {
      this.focusSaved ??= this.cameraView();
      this.lookFrom(this.focusView(f, fill));
      this.focused = f;
    } else if (this.focusSaved) {
      this.lookFrom(this.focusSaved);
      this.focusSaved = null;
      this.focused = null;
    }
    this.setCloseUp(!!this.focused);
  }

  // Close up (at a focus) the part boxes and the centre-of-gravity mark are left out, the
  // toggles' state kept: the boxes stand between the camera and the frame's walls and,
  // see-through, still tint the stress colours behind them, and the mark is a fixed 150 mm
  // so near it fills the view. They come back as the toggles say when the view leaves.
  setCloseUp(on) {
    if (this.closeUp === on) return;
    this.closeUp = on;
    this.setToggles(this.toggles);
  }

  // Keep `right` pixels at the right of the view clear (e.g. under a legend laid over it):
  // the camera's frustum is the free part's, laid over the whole canvas with a view
  // offset, so fitting and focusing centre in the free part and nothing is cut at the left.
  setReserve(right) {
    this.reserveRight = Math.max(0, right || 0);
    this.applyAspect();
  }

  applyAspect() {
    const { w, h } = this.size;
    const free = Math.max(1, w - (this.reserveRight ?? 0));
    this.camera.aspect = free / h;
    if (free < w) this.camera.setViewOffset(free, h, 0, 0, w, h);
    else this.camera.clearViewOffset();
    this.camera.updateProjectionMatrix();
  }

  // A mode shape: each node moves along its recorded vector, scaled so the
  // largest one is 8 % of the design's size (the shape, not its amplitude, is
  // the result). It swings once a second of replay, so a frame at a given ?t=
  // is always the same.
  setModeShape(contour) {
    this.modeShape = null;
    this.poseMembers(0);
    for (const o of Object.values(this.shellObjs)) this.poseShell(o, null, 0);
    const shellModes = contour?.mesh?.modes ?? null;
    if (!(contour?.modes || shellModes) || !this.bboxIso) return;
    let dmax = 0;
    for (const d of shellModes ?? Object.values(contour.modes)) dmax = Math.max(dmax, Math.hypot(...d));
    if (!(dmax > 0)) return;
    const size = this.bboxIso.getSize(new THREE.Vector3()).length();
    const scale = (0.08 * size) / dmax;
    this.modeShape = shellModes ? { shell: this.shellObj(contour.mesh.rec), shellModes, scale } : { modes: contour.modes, scale };
  }

  // Move a shell's nodes by k times their scaled vectors (k = 0: at rest).
  poseShell(o, modes, k) {
    const fp = o.faces.geometry.getAttribute("position");
    const ep = o.edges.geometry.getAttribute("position");
    if (!modes || k === 0) {
      fp.array.set(o.rest);
      ep.array.set(o.nodeRest);
    } else {
      for (let i = 0; i < modes.length; i += 1) {
        const d = modes[i];
        for (let j = 0; j < 3; j += 1) ep.array[i * 3 + j] = o.nodeRest[i * 3 + j] + d[j] * k;
      }
      for (let v = 0; v < o.vNode.length; v += 1) {
        const n = o.vNode[v];
        for (let j = 0; j < 3; j += 1) fp.array[v * 3 + j] = ep.array[n * 3 + j];
      }
    }
    fp.needsUpdate = true;
    ep.needsUpdate = true;
    o.faces.geometry.computeBoundingSphere();
  }

  // A mode shape (or displacement) at k of its swing, on the beams or on a shell.
  poseShape(k) {
    const ms = this.modeShape;
    if (ms?.shell) this.poseShell(ms.shell, ms.shellModes, k * ms.scale);
    else this.poseMembers(k);
  }

  poseMembers(k) {
    const ms = this.modeShape;
    const ink = [];
    const p = new THREE.Vector3();
    for (const info of Object.values(this.members)) {
      let m = info.matrix;
      if (ms && k !== 0) {
        const at = (id) => {
          const d = ms.modes[id] ?? [0, 0, 0];
          return this.nodes[id].clone().add(new THREE.Vector3(...d).multiplyScalar(k * ms.scale));
        };
        const a = at(info.node_a);
        const b = at(info.node_b);
        m = memberBasis(a, b)
          .scale(new THREE.Vector3(a.distanceTo(b) / info.length, 1, 1))
          .setPosition(a.clone().add(b).multiplyScalar(0.5));
      }
      info.mesh.matrix.copy(m);
      info.mesh.matrixWorldNeedsUpdate = true;
      const e = info.localEdges;
      for (let i = 0; i < e.length; i += 3) {
        p.set(e[i], e[i + 1], e[i + 2]).applyMatrix4(m);
        ink.push(p.x, p.y, p.z);
      }
    }
    if (this.inkLines && ink.length) {
      this.inkLines.geometry.dispose();
      this.inkLines.geometry = new LineSegmentsGeometry().setPositions(ink);
    }
  }

  // The analysis' vectors (a still: one moment). Hidden while a motion is shown.
  // A wheel's forces (reaction, grip) carry words only at the picked wheel (pickWheel, else
  // the one with the largest recorded force); the other wheels show their arrows alone.
  setVectors(vectors, keepPick = false) {
    if (vectors === this.keys.vectors) return;
    if (!keepPick && vectors) this.pickedWheel = null; // another analysis: its largest force again
    this.keys.vectors = vectors;
    disposeTree(this.arrows);
    this.vecTips = [];
    const list = (vectors ?? []).filter((v) => Array.isArray(v.vector_N) && Array.isArray(v.origin_mm));
    const mags = list.map((v) => Math.hypot(...v.vector_N));
    const fmax = Math.max(1e-9, ...mags);
    let auto = null;
    list.forEach((v, i) => {
      if (WHEEL_FORCES.has(v.kind) && v.at && (auto === null || mags[i] > auto.mag)) auto = { at: v.at, mag: mags[i] };
    });
    this.autoWheel = this.motion ? this.autoWheel : auto?.at ?? null;
    const picked = this.pickedWheel ?? auto?.at ?? null;
    list.forEach((v, i) => {
      const mag = mags[i];
      if (!(mag > 0)) return;
      const dir = v3(v.vector_N).normalize();
      const len = 160 + 640 * (mag / fmax);
      const origin = v3(v.origin_mm);
      // Loads and reactions push on their point (head at the origin: a reaction at a tyre's
      // contact comes from below the road, so it does not lie over the wheel); grip and the
      // other forces start there.
      const pushes = v.kind === "load" || v.kind === "reaction";
      const start = pushes ? origin.clone().addScaledVector(dir, -len) : origin;
      const g = makeArrow(VECTOR_COLORS[v.kind] ?? INK);
      placeArrow(g, start, dir, len);
      this.arrows.add(g);
      const wheelForce = WHEEL_FORCES.has(v.kind) && v.at;
      if (wheelForce) this.vecTips.push({ id: v.at, at: start.clone().addScaledVector(dir, len / 2) });
      if (wheelForce && v.at !== picked) return; // the arrow alone
      const text = `${esc(vectorName(v.kind))}${v.at ? ` ${esc(v.at)}` : ""}<small>|F| ${fmt(mag / 1000, 3)} kN</small>`;
      const tag = wheelForce ? keyLabel(text, `lbl-vec lbl-${v.kind}`) : label(text, `lbl-vec lbl-${v.kind}`);
      // a wheel's force: its label is led from the arrow near the contact, a quarter of the way
      // from it (the free end below the road can be out of the view); placeOutLabels lays it out
      tag.position.copy(wheelForce ? origin.clone().addScaledVector(dir, pushes ? -len / 4 : len / 4) : start.clone().addScaledVector(dir, len + 40));
      this.arrows.add(tag);
    });
  }

  // The wheel whose forces carry words (null: the largest one). Returns true when it changed.
  pickWheel(id) {
    if ((id ?? null) === (this.pickedWheel ?? null)) return false;
    this.pickedWheel = id ?? null;
    const v = this.keys.vectors;
    this.keys.vectors = undefined;
    this.setVectors(v, true);
    this.setToggles(this.toggles);
    for (const o of Object.values(this.motionArrowObjs ?? {})) o.text = null;
    this.placeMotionArrows(this.frame);
    return true;
  }

  // The wheel whose forces carry words now.
  wheelShown() {
    return this.pickedWheel ?? this.autoWheel ?? null;
  }

  // The wheel whose arrows are nearest to a point of the view (px from its top left), within
  // 60 px, picked; returns its id or null.
  pickAt(x, y) {
    const cands = this.frame
      ? this.frame.wheels.filter((w) => w.contact && this.motionArrowObjs?.[w.id]).map((w) => ({ id: w.id, at: v3(w.contact) }))
      : this.vecTips ?? [];
    let best = null;
    for (const c of cands) {
      const p = this.screenOf(c.at);
      if (!p) continue;
      const d = Math.hypot(p.x - x, p.y - y);
      if (d <= 60 && (!best || d < best.d)) best = { id: c.id, d };
    }
    if (best) this.pickWheel(best.id);
    return best?.id ?? null;
  }

  // A point in the vehicle's frame of the iso group (ISO mm) on screen (px), or null behind the camera.
  screenOf(isoPoint, parent = this.iso) {
    const p = isoPoint.clone().applyMatrix4(parent.matrixWorld).project(this.camera);
    if (p.z > 1) return null;
    return { x: (p.x * 0.5 + 0.5) * this.size.w, y: (-p.y * 0.5 + 0.5) * this.size.h };
  }

  // Labels marked lbl-out go outside the vehicle's outline on screen: above it when their
  // point is in its upper half, below it otherwise (so a leader line does not cross the
  // wheels from a point low on the vehicle), at their point's x, kept inside the view and
  // apart from each other; a leader line joins each label to its point.
  placeOutLabels() {
    if (!this.leaders) return;
    const outs = [];
    this.scene.traverseVisible((o) => {
      if (o.isCSS2DObject && o.element.classList.contains("lbl-out") && o.element.style.display !== "none") outs.push(o);
    });
    if (!outs.length || !this.design) {
      if (this.leadersHtml) this.leaders.innerHTML = this.leadersHtml = "";
      return;
    }
    if (this.outline?.design !== this.design) this.outline = { design: this.design, pts: this.contentPoints() };
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const q of this.outline.pts) {
      const p = this.screenOf(q.clone().applyMatrix4(this.body.matrix));
      if (!p) continue;
      x0 = Math.min(x0, p.x);
      x1 = Math.max(x1, p.x);
      y0 = Math.min(y0, p.y);
      y1 = Math.max(y1, p.y);
    }
    const W = this.size.w;
    const H = this.size.h;
    // the wheels on screen, as discs: a leader line or a label should not lie over them
    const discs = [];
    for (const w of Object.values(this.wheelObjs)) {
      const c = new THREE.Vector3().setFromMatrixPosition(w.group.matrix);
      const p = this.screenOf(c);
      const q = this.screenOf(c.clone().add(new THREE.Vector3(0, 0, w.r)));
      if (p && q) discs.push({ x: p.x, y: p.y, r: Math.hypot(q.x - p.x, q.y - p.y) });
    }
    // the panels over the view (the graph, the legend, the bars, the notes): a label is not put under them
    const panels = [];
    for (const el of this.host.children) {
      if (el === this.renderer.domElement || el === this.labelRenderer.domElement || el === this.leaders || el.classList.contains("cmp-view")) continue;
      if (!el.offsetParent || el.hidden || Number(getComputedStyle(el).opacity) === 0) continue;
      panels.push({ x: el.offsetLeft - (this.host.clientWidth - W), y: el.offsetTop, w: el.offsetWidth, h: el.offsetHeight });
    }
    const placed = [];
    const lines = [];
    const at = new THREE.Vector3();
    const gap = LEADER_GAP_PX;
    for (const o of outs) {
      at.setFromMatrixPosition(o.matrixWorld).project(this.camera);
      if (at.z > 1) continue;
      const ax = (at.x * 0.5 + 0.5) * W;
      const ay = (-at.y * 0.5 + 0.5) * H;
      const w = o.element.offsetWidth;
      const h = o.element.offsetHeight;
      const cx = (x) => Math.max(w / 2 + LABEL_MARGIN.side, Math.min(W - w / 2 - LABEL_MARGIN.side, x));
      const cy = (y) => Math.max(LABEL_MARGIN.top + h / 2, Math.min(H - LABEL_MARGIN.bottom - h / 2, y));
      // places around the vehicle's outline: above and below it (at the point's x and at the
      // outline's corners), and at its sides (at the point's y and the outline's top and bottom)
      const cands = [];
      for (const x of [ax, x0 + w / 2, x1 - w / 2, x0 - gap - w / 2, x1 + gap + w / 2]) {
        cands.push([x, y0 - gap - h / 2], [x, y1 + gap + h / 2]);
      }
      for (const y of [ay, y0 + h / 2, y1 - h / 2]) cands.push([x0 - gap - w / 2, y], [x1 + gap + w / 2, y]);
      let best = null;
      for (const [px, py] of cands) {
        const lx = cx(px);
        const ly = cy(py);
        const ex = Math.max(lx - w / 2, Math.min(lx + w / 2, ax)); // the label's edge nearest the point
        const ey = Math.max(ly - h / 2, Math.min(ly + h / 2, ay));
        let score = Math.hypot(ex - ax, ey - ay);
        for (const d of discs) {
          // the leader line across a wheel (not the wheel the point is on), the label over a wheel
          if (Math.hypot(ax - d.x, ay - d.y) > d.r && segmentDistance(d, ax, ay, ex, ey) < d.r) score += 1000;
          if (Math.abs(d.x - lx) < w / 2 + d.r && Math.abs(d.y - ly) < h / 2 + d.r) score += 400;
        }
        if (lx + w / 2 > x0 && lx - w / 2 < x1 && ly + h / 2 > y0 && ly - h / 2 < y1) score += 300; // over the vehicle
        if (placed.some((r) => Math.abs(r.x - lx) < (r.w + w) / 2 + 6 && Math.abs(r.y - ly) < (r.h + h) / 2 + 4)) score += 2000;
        if (panels.some((r) => lx + w / 2 > r.x && lx - w / 2 < r.x + r.w && ly + h / 2 > r.y && ly - h / 2 < r.y + r.h)) score += 1500;
        if (!best || score < best.score) best = { score, lx, ly, ex, ey };
      }
      const { lx, ly, ex, ey } = best;
      placed.push({ x: lx, y: ly, w, h });
      o.element.style.transform = `translate(-50%,-50%) translate(${lx}px,${ly}px)`;
      if (Math.hypot(ex - ax, ey - ay) > 4) lines.push(`<line x1="${ax.toFixed(1)}" y1="${ay.toFixed(1)}" x2="${ex.toFixed(1)}" y2="${ey.toFixed(1)}"/><circle cx="${ax.toFixed(1)}" cy="${ay.toFixed(1)}" r="3.5"/>`);
    }
    const html = lines.join("");
    if (html !== this.leadersHtml) this.leaders.innerHTML = this.leadersHtml = html;
  }

  // A motion from series.js (or null). Builds the arrow set, the ghost at the
  // last pose when a wheel lifted (the limit was passed), and widens the ground
  // to the path.
  setMotion(motion) {
    if (motion === this.motion) return;
    this.motion = motion;
    this.autoWheel = null;
    if (motion) this.pickedWheel = null; // another motion: its largest wheel again
    disposeTree(this.ghost);
    disposeTree(this.motionArrows);
    this.motionArrowObjs = {};
    // Take back the follow shift of the previous motion before starting a new one.
    if (this.followApplied) {
      this.camera.position.sub(this.followApplied);
      this.controls.target.sub(this.followApplied);
      this.controls.update();
    }
    this.followBase = null;
    this.followApplied = null;
    this.buildGround();
    if (!motion || !this.design) {
      this.setFrame(null);
      return;
    }
    for (const id of motion.wheelIds) {
      const reaction = makeArrow(VECTOR_COLORS.reaction);
      const grip = makeArrow(VECTOR_COLORS.grip);
      const tag = keyLabel("", "lbl-vec lbl-reaction");
      this.motionArrows.add(reaction, grip, tag);
      this.motionArrowObjs[id] = { reaction, grip, tag, text: null };
    }
    // the wheel whose label is shown when none is picked: the largest recorded normal force
    // over the motion (at the series' samples), so the label does not jump between wheels
    let best = null;
    const n = 60;
    for (let k = 0; k <= n; k += 1) {
      for (const w of motion.frame(motion.t0 + (motion.duration * k) / n).wheels) {
        if (w.load !== null && w.load > 0 && motion.wheelIds.includes(w.id) && (!best || w.load > best.load)) best = { id: w.id, load: w.load };
      }
    }
    this.autoWheel = best?.id ?? null;
    if (motion.liftT !== null) this.buildGhost(motion.frame(motion.t1));
  }

  buildGhost(frame) {
    const design = this.design;
    // Frame and wheels only: big part boxes would turn the ghost into a red blob.
    const mat = new THREE.MeshBasicMaterial({ color: LIMIT_RED, transparent: true, opacity: 0.14, depthWrite: false });
    const body = new THREE.Group();
    body.matrixAutoUpdate = false;
    body.matrix.copy(bodyMatrix(frame.body, frame.rpy));
    for (const m of design.members ?? []) {
      const g = memberGeometry(design, m, this.nodes);
      if (!g) continue;
      const mesh = new THREE.Mesh(g.geo, mat);
      mesh.matrixAutoUpdate = false;
      mesh.matrix.copy(g.matrix);
      body.add(mesh);
    }
    this.ghost.add(body);
    for (const s of design.supports ?? []) {
      const w = frame.wheels.find((x) => x.id === s.id);
      const mesh = new THREE.Mesh(supportGeometry(s).geo, mat);
      mesh.matrixAutoUpdate = false;
      mesh.matrix.copy(this.wheelPose(s.id, w, body.matrix));
      this.ghost.add(mesh);
    }
  }

  wheelPose(id, w, bodyM) {
    const base = this.wheelObjs[id]?.base ?? new THREE.Vector3();
    const rot = new THREE.Matrix4().extractRotation(bodyM);
    const at = w?.center ? v3(w.center) : base.clone().applyMatrix4(bodyM);
    return wheelMatrix(rot.setPosition(at), w?.steer ?? 0, w?.spin ?? 0);
  }

  // Pose everything for one frame of the motion, or the design pose (frame = null).
  setFrame(frame) {
    this.frame = frame;
    const bodyM = frame ? bodyMatrix(frame.body, frame.rpy) : new THREE.Matrix4();
    this.body.matrix.copy(bodyM);
    this.body.matrixWorldNeedsUpdate = true;
    const byId = Object.fromEntries((frame?.wheels ?? []).map((w) => [w.id, w]));
    const links = [];
    for (const [id, o] of Object.entries(this.wheelObjs)) {
      const w = byId[id];
      o.group.matrix.copy(this.wheelPose(id, w, bodyM));
      o.group.matrixWorldNeedsUpdate = true;
      const lifted = !!(frame && w && w.load !== null && w.load <= 0 && this.motion?.liftAt?.[id] !== undefined && frame.t >= this.motion.liftAt[id]);
      o.ring.visible = lifted;
      o.tag.element.innerHTML = lifted ? `${esc(id)} 浮いた` : esc(id);
      o.tag.element.classList.toggle("lbl-lift", lifted);
      const fn = o.frameNode && this.nodes?.[o.frameNode];
      if (fn) {
        const c = new THREE.Vector3().setFromMatrixPosition(o.group.matrix);
        const n = fn.clone().applyMatrix4(bodyM);
        links.push(c.x, c.y, c.z, n.x, n.y, n.z);
      }
    }
    this.links.geometry.dispose();
    this.links.geometry = new LineSegmentsGeometry().setPositions(links.length ? links : [0, 0, 0, 0, 0, 0]);
    this.links.visible = links.length > 0;
    this.world.rotation.x = this.worldAngle(frame);
    this.props.update(frame);
    this.placeCg();
    this.placeMotionArrows(frame);
    this.ghost.visible = !!frame && !!this.toggles.limit && this.liftIsLimit !== false;
  }

  placeMotionArrows(frame) {
    const objs = this.motionArrowObjs ?? {};
    const fref = Math.max(1e-9, this.motion?.fref ?? 0);
    const show = !!frame && !!this.toggles.arrows;
    this.motionArrows.visible = show;
    if (!show) return;
    for (const w of frame.wheels) {
      const o = objs[w.id];
      if (!o) continue;
      for (const kind of ["reaction", "grip"]) {
        const f = w[kind];
        const mag = f ? Math.hypot(...f) : 0;
        const g = o[kind];
        g.visible = !!w.contact && mag > fref * 1e-3;
        if (!g.visible) continue;
        const dir = v3(f).normalize();
        const len = 120 + 680 * Math.min(1, mag / fref);
        // the road's reaction pushes up into the contact from below (not across the wheel); grip starts there
        placeArrow(g, kind === "reaction" ? v3(w.contact).addScaledVector(dir, -len) : v3(w.contact), dir, len);
      }
      // The picked wheel's label shows the recorded normal force (wheel_load) at the nearest
      // sample; the other wheels show their arrows without words.
      o.tag.visible = w.id === this.wheelShown() && !!w.contact && w.load !== null && w.load > 0;
      if (!o.tag.visible) {
        if (o.text !== "") o.tag.element.innerHTML = o.text = "";
      } else {
        const text = `${esc(wheelName(w.id))}<small>${fmt(w.load / 1000, 3)} kN</small>`;
        if (text !== o.text) o.tag.element.innerHTML = o.text = text;
        const f = w.reaction ?? [0, 0, 1];
        const len = 120 + 680 * Math.min(1, Math.hypot(...f) / fref);
        o.tag.position.copy(v3(w.contact)).addScaledVector(v3(f).normalize(), -len / 4); // on the reaction arrow, near the contact
      }
    }
  }

  // The world's turn about x: the tilt table's angle at the frame, else the angle channel
  // the scene names (a slope tilted in time), else the road's side slope.
  worldAngle(frame) {
    if (frame?.table != null) return frame.table;
    const ch = this.sceneDesc?.angleChannel ? this.motion?.channels.get(this.sceneDesc.angleChannel) : null;
    if (ch && frame && ANGLE[ch.unit]) return lerpAt(ch, frame.t) * ANGLE[ch.unit];
    return this.sceneDesc?.slope ?? 0;
  }

  // Where a frame's centre of mass (or body origin) is on screen space, in three.js metres.
  anchorOf(frame) {
    const p = v3(frame.cg ?? frame.body).applyMatrix4(ISO_TO_THREE);
    return p.applyAxisAngle(new THREE.Vector3(1, 0, 0), this.worldAngle(frame));
  }

  // Keep the camera on the moving vehicle: camera and target are shifted by how
  // far the anchor is from where it was at the motion's first time. The shift
  // depends on that time only, so any seek order gives the same view.
  follow() {
    if (!this.frame || !this.motion) return;
    this.followBase ??= this.anchorOf(this.motion.frame(this.motion.t0));
    const want = this.anchorOf(this.frame).sub(this.followBase);
    const d = want.clone().sub(this.followApplied ?? new THREE.Vector3());
    if (d.lengthSq() < 1e-12) return;
    this.camera.position.add(d);
    this.controls.target.add(d);
    this.followApplied = want;
    this.controls.update();
  }

  setToggles(t) {
    this.toggles = { ...t };
    this.parts.visible = !!t.parts && !this.closeUp;
    this.arrows.visible = !!t.arrows && !this.frame;
    this.names.visible = !!t.names;
    this.markers.visible = !!t.names;
    for (const o of Object.values(this.partObjs)) o.tag.visible = !!t.names;
    for (const o of Object.values(this.wheelObjs)) o.tag.visible = !!t.names || o.ring.visible;
    this.overGhosts.visible = !!t.contour;
    this.ghost.visible = !!this.frame && !!t.limit && this.liftIsLimit !== false;
    this.cg.visible = !!t.cg && !!this.cgPos && !this.closeUp;
    this.placeMotionArrows(this.frame);
  }

  // Time-driven effects use the playhead only, so a frozen ?t= frame is repeatable.
  animate(playhead, running) {
    const k = running ? 0.18 * (1 + Math.sin(playhead * Math.PI * 2)) : 0;
    for (const info of Object.values(this.members)) info.mesh.material.emissive?.setScalar(k * 0.5);
    if (this.modeShape) this.poseShape(Math.sin(playhead * Math.PI * 2));
  }

  // Draw the frame, the wheels and the scene as one see-through colour (an overlay of
  // another case on a view, e.g. the translucent red of the case past the limit). Parts
  // and ink lines are left out: big part boxes would turn it into one red blob.
  tint(color, opacity) {
    const mat = new THREE.MeshBasicMaterial({ color, transparent: true, opacity, depthWrite: false });
    mat.userData.shared = true;
    this.parts.visible = false;
    for (const g of [this.model, this.wheels, this.props.world, this.props.fixed]) {
      g.traverse((o) => {
        if (o.isLineSegments2 || o.isLine) o.visible = false;
        else if (o.isMesh) o.material = mat;
      });
    }
    this.ground.visible = false;
    this.cg.visible = false;
    this.tinted = true;
  }

  // Look through another view's camera (an overlay drawn over that view).
  copyCamera(other) {
    this.camera.position.copy(other.camera.position);
    this.camera.quaternion.copy(other.camera.quaternion);
    this.camera.fov = other.camera.fov;
    this.camera.near = other.camera.near;
    this.camera.far = other.camera.far;
    // the other view's frustum, a reserved strip (setReserve) included
    this.camera.aspect = other.camera.aspect;
    const ov = other.camera.view;
    if (ov?.enabled) this.camera.setViewOffset(ov.fullWidth, ov.fullHeight, ov.offsetX, ov.offsetY, ov.width, ov.height);
    else this.camera.clearViewOffset();
    this.camera.updateProjectionMatrix();
    this.controls.target.copy(other.controls.target);
    this.noFollow = true;
  }

  render() {
    if (!this.noFollow && !this.focused) this.follow();
    // the stage: the sky, the haze and the sun behind the vehicle (none on a see-through overlay)
    this.camera.updateMatrixWorld();
    const sunDir = this.look.stage ? this.look.stageFrame(this.scene, this.camera, this.controls.target, !!this.tinted) : null;
    if (this.sun && this.bboxIso) {
      this.iso.updateWorldMatrix(true, false); // shadowPoints reads iso's world matrix (the renderer updates the rest)
      this.look.aimSun(this.sun, this.controls.target, this.shadowPoints(), sunDir);
    }
    if (!this.ownRenderer) {
      // views of both looks share the renderer: each sets the renderer's look for itself
      this.renderer.toneMapping = this.look.real ? THREE.ACESFilmicToneMapping : THREE.NoToneMapping;
      this.renderer.shadowMap.enabled = this.look.real;
      if (this.look.real) this.renderer.shadowMap.type = this.look.q.shadow.type === "pcfsoft" ? THREE.PCFSoftShadowMap : THREE.PCFShadowMap;
    }
    if (this.post) this.post.render();
    else if (this.sharedPost && !this.tinted) sharedPost(this.renderer, this.scene, this.camera, this.size.w, this.size.h, this.look.quality, this.look.q).render();
    else this.renderer.render(this.scene, this.camera);
    this.renderOverlay();
    if (this.labelRenderer) {
      this.labelRenderer.render(this.scene, this.camera);
      this.placeOutLabels();
    }
  }

  // The corners of the vehicle's box (100 mm round it) where it stands now, in three.js
  // world metres: what the sun's shadow box is fitted to.
  shadowPoints() {
    const b = this.bboxIso;
    const m = new THREE.Matrix4().multiplyMatrices(this.iso.matrixWorld, this.body.matrix);
    const pts = [];
    for (const x of [b.min.x - 100, b.max.x + 100]) for (const y of [b.min.y - 100, b.max.y + 100]) for (const z of [b.min.z - 100, b.max.z + 100]) pts.push(new THREE.Vector3(x, y, z).applyMatrix4(m));
    return pts;
  }

  // The overlay (layer OVERLAY) over the picture. With a contour, first the depth of the
  // picture's solid things (lines, sprites and see-through things are left out), so the
  // twins are hidden where something stands in front of them.
  renderOverlay() {
    const r = this.renderer;
    const autoClear = r.autoClear;
    const autoShadow = r.shadowMap.autoUpdate;
    // the sky is in the picture already: the overlay's passes leave it out
    const background = this.scene.background;
    this.scene.background = null;
    r.autoClear = false;
    r.shadowMap.autoUpdate = false;
    // the picture's pass has brought every world matrix up to date
    const autoWorld = this.scene.matrixWorldAutoUpdate;
    this.scene.matrixWorldAutoUpdate = false;
    if (this.keys.contour) {
      r.clearDepth();
      const hidden = [];
      this.scene.traverse((o) => {
        if (o.visible && skipsDepth(o)) {
          o.visible = false;
          hidden.push(o);
        }
      });
      this.scene.overrideMaterial = DEPTH_ONLY;
      r.render(this.scene, this.camera);
      this.scene.overrideMaterial = null;
      for (const o of hidden) o.visible = true;
    }
    // the overlay's own pass only when something is shown in it (each pass walks the whole scene)
    let shown = !!this.keys.contour;
    if (!shown) this.scene.traverseVisible((o) => (shown ||= (o.layers.mask & (1 << OVERLAY)) !== 0 && !o.isGroup && !o.isScene && !o.isCSS2DObject));
    const mask = this.camera.layers.mask;
    this.camera.layers.set(OVERLAY);
    if (shown) r.render(this.scene, this.camera);
    this.camera.layers.mask = mask;
    this.scene.matrixWorldAutoUpdate = autoWorld;
    this.scene.background = background;
    r.autoClear = autoClear;
    r.shadowMap.autoUpdate = autoShadow;
  }

  // A view on a shared renderer: draw into the lower left w x h pixels of its canvas
  // and copy them onto a 2D canvas (the renderer's canvas must be at least that big).
  // scale: device pixels per stage pixel, so ink lines keep their stage width.
  renderInto(ctx, w, h, scale = 1) {
    if (this.size.w !== w || this.size.h !== h) this.resize(w, h);
    for (const m of [this.inkMat, this.mutedMat, this.litLine]) m.resolution.set(w / scale, h / scale);
    this.props.resize(w / scale, h / scale);
    const r = this.renderer;
    const H = r.domElement.height;
    r.setScissorTest(true);
    r.setViewport(0, 0, w, h);
    r.setScissor(0, 0, w, h);
    r.setClearColor(0x000000, 0);
    r.clear();
    this.render();
    r.setScissorTest(false);
    ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
    ctx.drawImage(r.domElement, 0, H - h, w, h, 0, 0, ctx.canvas.width, ctx.canvas.height);
    this.canvas2d = ctx.canvas; // the canvas it was last copied to (the tests read it)
  }

  // Points that outline the design at rest (ISO mm): frame nodes, the corners of the
  // part boxes and points around each wheel's rims.
  contentPoints() {
    const pts = Object.values(this.nodes ?? {}).map((n) => n.clone());
    for (const p of this.design?.parts ?? []) {
      const c = p.box.center_mm;
      const h = p.box.size_mm.map((v) => v / 2);
      for (const sx of [-1, 1]) for (const sy of [-1, 1]) for (const sz of [-1, 1]) pts.push(v3([c[0] + sx * h[0], c[1] + sy * h[1], c[2] + sz * h[2]]));
    }
    for (const o of Object.values(this.wheelObjs)) {
      for (let k = 0; k < 8; k += 1) {
        const a = (k / 8) * Math.PI * 2;
        for (const side of [-1, 1]) pts.push(o.base.clone().add(v3([o.r * Math.cos(a), (side * o.w) / 2, o.r * Math.sin(a)])));
      }
    }
    return pts;
  }

  // Frame a box (ISO mm) from a direction so it fills the view's width or height,
  // whichever binds (a wide tile is filled across, not by a sphere around the box).
  fitBox(box, dir, margin = 1.08) {
    const pts = [];
    for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z]) pts.push(v3([x, y, z]));
    this.fitPoints(pts, dir, margin);
  }

  // Frame points (ISO mm) from a direction: the camera stands as close as it can with
  // every point inside the view, looking at the points' middle.
  fitPoints(points, dir, margin = 1.08) {
    const box = new THREE.Box3().setFromPoints(points);
    const center = isoToThree(box.getCenter(new THREE.Vector3()).toArray());
    const d = isoToThree(dir).normalize();
    // Each corner p of the box, seen from distance D along d, stays inside the view when
    // D >= |p across| / tan(half fov) + p along d; the camera stands at the largest D.
    const up = new THREE.Vector3(0, 1, 0);
    const right = new THREE.Vector3().crossVectors(up, d).normalize();
    const camUp = new THREE.Vector3().crossVectors(d, right).normalize();
    const tv = Math.tan((this.camera.fov * Math.PI) / 360);
    const th = tv * this.camera.aspect;
    let dist = 0;
    for (const q of points) {
      const p = isoToThree(q.toArray()).sub(center);
      const along = p.dot(d);
      dist = Math.max(dist, (margin * Math.abs(p.dot(right))) / th + along, (margin * Math.abs(p.dot(camUp))) / tv + along);
    }
    this.controls.target.copy(center);
    this.camera.position.copy(center).addScaledVector(d, dist);
    this.camera.near = dist / 50;
    this.camera.far = dist * 20;
    this.camera.updateProjectionMatrix();
    this.controls.update();
    this.followBase = null;
    this.followApplied = null;
  }
}

// A 3D view of a cut page (cut*.js): drawn through the cut's one renderer (cut.renderer) as a
// stage, in the look the layout gives the cut (look.js cutLookOf: its `look` key, "real" when
// it has none; ?look= over both). opts: more of the constructor's (e.g. quality).
export function cutView(cut, name, opts = {}) {
  const look = cutLookOf(cut.layout, name, new URLSearchParams(location.search));
  const view = new Workbench3D(null, look, { renderer: cut.renderer, stage: true, ...opts });
  CUT_VIEWS.push(view);
  return view;
}

// The cut page's views, in the order they were made (window.hiraku.views(): the tests read
// their looks and the canvases they were last drawn into, canvas2d).
const CUT_VIEWS = [];
export const cutViews = () => [...CUT_VIEWS];

function sectionArea(sec) {
  const d = sec.dims_mm ?? {};
  switch (sec.shape) {
    case "rect_tube":
      return d.b * d.h - Math.max(0, d.b - 2 * d.t) * Math.max(0, d.h - 2 * d.t);
    case "rect_solid":
      return d.b * d.h;
    case "round_tube":
      return (Math.PI / 4) * (d.d ** 2 - Math.max(0, d.d - 2 * d.t) ** 2);
    case "round_solid":
      return (Math.PI / 4) * d.d ** 2;
    default:
      return NaN;
  }
}

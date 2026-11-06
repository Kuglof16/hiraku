// ④ 解析結果: the 3D picture of each domain's tile, drawn as the workbench draws the design in
// its real look (app.js passes "real" whichever look the workbench has; the domain's contour on
// it) and small. One renderer for every tile (one WebGL
// context); a picture is drawn once per what it shows (its key: the version, the analysis, the
// contour, the size) and kept, so the grid drawn again (each new event) only copies it.
//
// The views have no labels, arrows, centre-of-gravity mark or joints: the tile says the rest in
// words. The real look is drawn without its sky (the tile's background is the panel) and at the
// cheapest quality (look.js QUALITY low).

import * as THREE from "three";
import { VIEWS, Workbench3D } from "./workbench3d.js";

const TOGGLES = { parts: true, contour: true, arrows: false, cg: false, names: false, limit: false, graph: false, joints: false, modules: false, spaces: false, heat: false };
const QUALITY = "low";
const FIT_MARGIN = 1.04;
const CACHE_MAX = 48; // pictures kept (eight tiles over a few versions and sizes)

export class TileViews {
  // onReady: called when a look's files (the real look's ground) have come in after a picture
  // was drawn without them, so the grid asks for its pictures again
  constructor(look, onReady = () => {}) {
    this.lookName = look;
    this.onReady = onReady;
    this.renderer = null;
    this.views = new Map(); // domain -> Workbench3D
    this.cache = new Map(); // key -> canvas with the picture
    this.waiting = false;
  }

  view(domain) {
    if (!this.renderer) {
      this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
      this.renderer.setPixelRatio(1);
    }
    let v = this.views.get(domain);
    if (!v) {
      v = new Workbench3D(null, this.lookName, { renderer: this.renderer, quality: QUALITY, sky: false });
      this.views.set(domain, v);
      if (v.look.real) {
        this.waiting = true;
        v.look.assetsReady.then(() => {
          this.cache.clear();
          this.onReady();
        });
      }
    }
    return v;
  }

  // Draw into canvas (its CSS size times scale px) the design with the contour ct (null: none)
  // for the load case lc and analysis ana (either may be null). key: what the picture shows.
  // cachedOnly: copy the picture only when it is kept (false when it is not: draw it later)
  draw(canvas, domain, { key, design, ct, lc, ana, scale }, cachedOnly = false) {
    const w = Math.max(1, Math.round(canvas.clientWidth * scale));
    const h = Math.max(1, Math.round(canvas.clientHeight * scale));
    if (!design || canvas.clientWidth < 2 || canvas.clientHeight < 2) return false;
    const full = `${key}|${w}x${h}`;
    if (canvas.dataset.key === full) return true; // (already shows it)
    let pic = this.cache.get(full);
    if (!pic && cachedOnly) return false;
    canvas.width = w;
    canvas.height = h;
    if (!pic) {
      const v = this.view(domain);
      v.setDesign(design);
      v.setLoading(lc?.params?.loading ?? null);
      v.setMotion(null);
      v.setScene(lc ?? null, ana ?? null);
      v.setContour(ct ?? null);
      v.setVectors(null);
      v.setToggles(TOGGLES);
      v.resize(w / scale, h / scale);
      // the camera of the scene (a crash from the side of its wall, as cut_grid.js makeView) and what
      // the scene puts beside the vehicle (the wall's face) in the frame
      const pts = v.contentPoints();
      const d = v.sceneDesc;
      if (d?.wall) pts.push(new THREE.Vector3(d.wall.x + 300, 0, 1200));
      for (const o of d?.objects ?? []) if (o.kind === "wall") pts.push(new THREE.Vector3(o.position_mm[0] - o.size_mm[0] / 2, o.position_mm[1], o.position_mm[2]));
      v.fitPoints(pts, VIEWS[v.view ?? "default"] ?? VIEWS.default, FIT_MARGIN);
      v.animate(0, false);
      const r = this.renderer;
      const size = r.getSize(new THREE.Vector2());
      if (size.x < w || size.y < h) r.setSize(Math.max(size.x, w), Math.max(size.y, h), false);
      pic = document.createElement("canvas");
      pic.width = w;
      pic.height = h;
      v.renderInto(pic.getContext("2d"), w, h, scale);
      if (this.cache.size >= CACHE_MAX) this.cache.delete(this.cache.keys().next().value); // the oldest
      this.cache.set(full, pic);
    }
    const g = canvas.getContext("2d");
    g.clearRect(0, 0, w, h);
    g.drawImage(pic, 0, 0);
    canvas.dataset.key = full;
    return true;
  }
}

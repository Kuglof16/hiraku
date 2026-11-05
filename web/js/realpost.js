// The real look's picture on its own renderer: drawn into a multisampled render
// target (the antialiasing), ambient occlusion (GTAO) laid over it, then tone
// mapping and sRGB (OutputPass) onto the canvas. What must keep its own colour (the
// contour) is drawn after this, outside it (workbench3d.js renderOverlay).

import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { GTAOPass } from "three/addons/postprocessing/GTAOPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";

// Objects left out of the occlusion's normal / depth pass: lines, sprites and
// see-through things (they would cast occlusion they do not have).
export function skipsDepth(o) {
  if (o.isLine || o.isPoints || o.isSprite || o.isLineSegments2 || o.isLine2) return true;
  const m = o.material;
  return !!m && !Array.isArray(m) && (m.transparent || m.depthWrite === false || m.colorWrite === false);
}

// GTAO at a fraction of the view's size; its normal pass skips what skipsDepth says and
// does not draw the shadow maps again (the picture's pass drew them this frame).
class AOPass extends GTAOPass {
  constructor(scene, camera, w, h, scale) {
    super(scene, camera, w, h);
    this.scale = scale;
    this.output = GTAOPass.OUTPUT.Default;
  }

  setSize(w, h) {
    super.setSize(Math.max(1, Math.round(w * this.scale)), Math.max(1, Math.round(h * this.scale)));
  }

  // the normal pass draws no sky behind (a stage's background)
  overrideVisibility() {
    const cache = this._visibilityCache;
    this.scene.traverse((o) => {
      cache.set(o, o.visible);
      if (skipsDepth(o)) o.visible = false;
    });
    this._background = this.scene.background;
    this.scene.background = null;
  }

  restoreVisibility() {
    super.restoreVisibility();
    this.scene.background = this._background;
  }

  render(renderer, writeBuffer, readBuffer, dt, mask) {
    const auto = renderer.shadowMap.autoUpdate;
    renderer.shadowMap.autoUpdate = false;
    super.render(renderer, writeBuffer, readBuffer, dt, mask);
    renderer.shadowMap.autoUpdate = auto;
  }
}

export class RealPost {
  // quality: an entry of look.js QUALITY. size: { x, y } device pixels (default: the renderer's
  // drawing buffer)
  constructor(renderer, scene, camera, quality, size = null) {
    this.renderer = renderer;
    size ??= renderer.getDrawingBufferSize(new THREE.Vector2());
    const target = new THREE.WebGLRenderTarget(Math.max(1, size.x), Math.max(1, size.y), { type: THREE.HalfFloatType, samples: quality.msaa });
    this.composer = new EffectComposer(renderer, target);
    this.renderPass = new RenderPass(scene, camera);
    this.composer.addPass(this.renderPass);
    if (quality.ao) {
      this.ao = new AOPass(scene, camera, size.x, size.y, quality.ao.scale);
      // the vehicle is 1 to 4 m across: occlusion within 0.5 m (contacts, corners, and the ground
      // under the body, whose floor stands a few hundred mm up: 0.25 m left it lit as in the open)
      this.ao.updateGtaoMaterial({ radius: 0.5, distanceExponent: 1, thickness: 1, scale: 1, samples: 16 });
      this.ao.updatePdMaterial({ lumaPhi: 10, depthPhi: 2, normalPhi: 3, radius: 4, rings: 2, samples: 16 });
      this.composer.addPass(this.ao);
    }
    this.composer.addPass(new OutputPass());
  }

  setSize(w, h, pixelRatio) {
    this.composer.setPixelRatio(pixelRatio);
    this.composer.setSize(w, h);
  }

  render() {
    this.composer.render();
  }

  // Draw another view's scene and camera with this post (a shared renderer's views take turns).
  use(scene, camera) {
    this.renderPass.scene = scene;
    this.renderPass.camera = camera;
    if (this.ao) {
      this.ao.scene = scene;
      this.ao.camera = camera;
    }
  }

  dispose() {
    this.composer.dispose();
  }
}

// The cut pages draw many views through one renderer, each into the lower left w x h pixels
// of its canvas (Workbench3D.renderInto): one post per renderer, view size and quality,
// made the first time and used by every view of that size in turn. Its last pass draws into
// the renderer's viewport (the view's corner).
const SHARED = new WeakMap(); // renderer -> Map(key -> RealPost)

export function sharedPost(renderer, scene, camera, w, h, qualityName, quality) {
  let posts = SHARED.get(renderer);
  if (!posts) SHARED.set(renderer, (posts = new Map()));
  const key = `${w}x${h}:${qualityName}`;
  let post = posts.get(key);
  if (!post) {
    post = new RealPost(renderer, scene, camera, quality, { x: w, y: h });
    post.composer.setPixelRatio(1); // w and h are device pixels already
    post.composer.setSize(w, h);
    posts.set(key, post);
  }
  post.use(scene, camera);
  return post;
}

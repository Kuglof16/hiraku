// The flooded road (viewer/src/water.py, data key water): a replay of the stored water record, no calculation on the page.
// The vehicle stays where it is on the page and the road and the water move past it (the camera follows the vehicle). The vehicle is
// opaque. The page always says that this is for showing and not a judgement of what can be crossed (D.statement).
window.HV = window.HV || {};
HV.fields = HV.fields || {};
(function (HV) {
  'use strict';
  const start = HV.main;
  HV.main = function (...args) {   // the place in the field switch: after aero
    const at = HV.FIELD_ORDER.indexOf('aero');
    if (!HV.FIELD_ORDER.includes('water')) HV.FIELD_ORDER.splice(at < 0 ? HV.FIELD_ORDER.length : at + 1, 0, 'water');
    return start.apply(this, args);
  };

  const LOOK = { body: [0.55, 0.57, 0.6], frame: [0.1, 0.11, 0.13], occupant: [0.28, 0.3, 0.33], cargo: [0.17, 0.22, 0.29], other: [0.19, 0.2, 0.22], tyre: 0x17181a, back: 0x101318, road: 0x34373c, water: 0x3a7ea8, spray: 0xd8ecf5 };
  const lookOf = p => (['body', 'fender', 'rollbar'].includes(p.kind) ? LOOK.body : p.kind === 'occupant' || p.module === 'occupant' ? LOOK.occupant : p.kind === 'cargo' ? LOOK.cargo : LOOK.other);
  const DRY_MM = 4;   // a column with less water than this shows the road
  // this data's bytes are hex (viewer/src/water.py says why): back to the page's packed form
  function unpackHex(p) {
    let bin = '';
    for (let i = 0; i < p.hex.length; i += 2) bin += String.fromCharCode(parseInt(p.hex.substr(i, 2), 16));
    return HV.unpack(Object.assign({}, p, { b64: btoa(bin) }));
  }

  HV.fields.water = {
    label: '冠水路', script: 'js/water.js',
    async init(D, ctx) {
      const T = window.THREE, view = ctx.view;
      const beams = await ctx.addBeams();
      beams.colourMembers(() => LOOK.frame);
      const boxes = ctx.addParts(D.parts, { opacity: 1 });
      boxes.mat.transparent = false; boxes.mat.depthWrite = true; boxes.mat.needsUpdate = true;
      const paintParts = () => { D.parts.forEach((p, i) => boxes.colour(i, lookOf(p))); boxes.mesh.instanceColor.needsUpdate = true; };
      D.parts.forEach((p, i) => { boxes.show[i] = !!D.on_board[i] && p.kind !== 'side_net' && p.kind !== 'wheel'; });
      paintParts(); boxes.update();
      const extras = new T.Group(); ctx.root.add(extras);
      const tyre = view.material(new T.MeshLambertMaterial({ color: LOOK.tyre }));
      for (const w of D.wheels) { const m = new T.Mesh(new T.CylinderGeometry(w.r, w.r, w.width, 48), tyre); m.position.set(...w.c); m.userData.kind = 'tyre'; extras.add(m); }
      const backdrop = new T.Mesh(new T.SphereGeometry(120000, 24, 12), new T.MeshBasicMaterial({ color: LOOK.back, side: T.BackSide, depthWrite: false }));
      backdrop.renderOrder = -10; backdrop.frustumCulled = false; backdrop.userData.kind = 'ground'; extras.add(backdrop);

      // everything of the road's frame goes in one group: its matrix per frame is the vehicle's pose
      const road = new T.Group(); road.matrixAutoUpdate = false; extras.add(road);
      const { nx, ny, x0_mm, y0_mm, step_mm } = D.columns;
      const xAt = i => x0_mm + (i + 0.5) * step_mm, yAt = j => y0_mm + (j + 0.5) * step_mm;
      const surface = await unpackHex(D.surface), spray = await unpackHex(D.spray), sprayStart = await unpackHex(D.spray_start);
      // the road: a strip over the columns
      const rp = new Float32Array(nx * 2 * 3), ri = [];
      for (let i = 0; i < nx; i++) {
        rp.set([xAt(i), y0_mm, D.road_mm[i], xAt(i), -y0_mm, D.road_mm[i]], i * 6);
        if (i + 1 < nx) { const a = i * 2; ri.push(a, a + 2, a + 1, a + 1, a + 2, a + 3); }
      }
      const rg = new T.BufferGeometry(); rg.setAttribute('position', new T.BufferAttribute(rp, 3)); rg.setIndex(ri); rg.computeVertexNormals();
      const roadMesh = new T.Mesh(rg, view.material(new T.MeshLambertMaterial({ color: LOOK.road, side: T.DoubleSide }))); roadMesh.frustumCulled = false; roadMesh.userData.kind = 'ground'; road.add(roadMesh);
      // the water: a height field over the columns, dry columns just under the road
      const wp = new Float32Array(nx * ny * 3), wi = [];
      for (let i = 0; i < nx; i++) for (let j = 0; j < ny; j++) {
        const k = (i * ny + j) * 3; wp[k] = xAt(i); wp[k + 1] = yAt(j);
        if (i + 1 < nx && j + 1 < ny) { const a = i * ny + j; wi.push(a, a + ny, a + 1, a + 1, a + ny, a + ny + 1); }
      }
      const wg = new T.BufferGeometry(); wg.setAttribute('position', new T.BufferAttribute(wp, 3).setUsage(T.DynamicDrawUsage)); wg.setIndex(wi);
      const waterMesh = new T.Mesh(wg, view.material(new T.MeshLambertMaterial({ color: LOOK.water, side: T.DoubleSide }))); waterMesh.frustumCulled = false; waterMesh.userData.kind = 'water'; road.add(waterMesh);
      // the flying water: the sample stored for each frame
      let most = 0; for (let f = 0; f + 1 < sprayStart.length; f++) most = Math.max(most, sprayStart[f + 1] - sprayStart[f]);
      const sp = new Float32Array(most * 3), sg = new T.BufferGeometry(); sg.setAttribute('position', new T.BufferAttribute(sp, 3).setUsage(T.DynamicDrawUsage));
      const points = new T.Points(sg, new T.PointsMaterial({ color: LOOK.spray, size: 22, sizeAttenuation: true })); points.frustumCulled = false; points.userData.kind = 'water'; road.add(points);

      const S = { f: 0 };
      function show(f) {
        S.f = f;
        // road frame -> page: the vehicle's local x points backwards along the travel (-x of the road frame is forward), and the page has x forward
        const [x0, z0, pitch] = D.poses[f], c = Math.cos(pitch), s = Math.sin(pitch), m = D.centre_mm;
        road.matrix.set(-c, 0, s, m[0] + c * x0 * 1000 - s * z0 * 1000,
                        0, -1, 0, m[1],
                        s, 0, c, m[2] - s * x0 * 1000 - c * z0 * 1000,
                        0, 0, 0, 1);
        road.matrixWorldNeedsUpdate = true;
        const base = f * nx * ny;
        for (let i = 0; i < nx; i++) for (let j = 0; j < ny; j++) {
          const h = surface[base + i * ny + j], g = D.road_mm[i];
          wp[(i * ny + j) * 3 + 2] = h - g < DRY_MM ? g - 30 : h;
        }
        wg.attributes.position.needsUpdate = true; wg.computeVertexNormals();
        const a = sprayStart[f], b = sprayStart[f + 1];
        sp.set(spray.subarray(a * 3, b * 3)); sg.setDrawRange(0, b - a); sg.attributes.position.needsUpdate = true;
      }
      const times = D.records.map(r => r.t);
      const f0 = Math.max(0, times.findIndex(t => t >= 1.0));
      show(f0);
      const rec = () => D.records[S.f];
      const ctl = {
        nT: D.frames, t0: f0, times, speed: 0.25, partMode: 'field', ground: null, viewDir: [1.25, 2.1, 0.55],
        timeNote: '保存した記録の再生。車を止めて見せ、道路と水が流れる',
        scale: { min: 5, max: 60, step: 1, value: 22, label: '飛ぶ水の点の大きさ (mm)', note: '見せるための大きさで、粒子の大きさではない' },
        setScale(v) { points.material.size = +v; },
        setTime(i) { show(i); if (ctx.partMode === 'field') paintParts(); },
        timeText(i) { const r = D.records[i]; return `時刻 ${r.t.toFixed(2)} 秒   床下の水の深さ 平均 ${r.depth_mean_mm.toFixed(0)} mm   水が届いた床下の列 ${r.reached_pct.toFixed(0)} %`; },
        hud() { return [D.statement, '水面は粒子の体積から求めた高さ、点は飛ぶ水の粒子の一部。波の大きさは実験と比べていない']; },
        contour: () => null,
        plot() {
          const mk = () => [{ x: rec().t, label: `${rec().t.toFixed(2)} 秒`, col: '#555', w: 2 }];
          const x = times;
          return [{ title: '床下の水の深さ (平均)', unit: 'mm', xlabel: '時刻 [s]', x, ys: [{ name: '平均', v: D.records.map(r => r.depth_mean_mm), col: '#2b59c3' }], marks: mk() },
                  { title: '水が届いた床下の列', unit: '%', xlabel: '時刻 [s]', x, ys: [{ name: '列の割合', v: D.records.map(r => r.reached_pct), col: '#4cbfa6' }], marks: mk() }];
        },
        plotClick(_, x) { let b = 0, bd = Infinity; times.forEach((t, i) => { const d = Math.abs(t - x); if (d < bd) { bd = d; b = i; } }); return b; },
        pickables: () => [beams.mesh, boxes.mesh],
        info(hit) {
          if (hit.object === boxes.mesh) return `<b>${HV.esc(D.parts[hit.instanceId].id)}</b> ${HV.esc(D.parts[hit.instanceId].name)}`;
          const m = beams.memberOf(hit);
          return `<b>${HV.esc(D.frame.member_ids[m])}</b> ${HV.esc(D.frame.member_names[m])}<br>水の中では形だけ`;
        },
        panel(div) {
          const h = document.createElement('h3'); h.textContent = 'この計算について'; div.appendChild(h);
          const st = document.createElement('div'); st.id = 'waterstatement'; st.textContent = D.statement; st.style.fontWeight = '600'; div.appendChild(st);
          const h1 = document.createElement('h3'); h1.textContent = '通過の間の最大'; div.appendChild(h1);
          const u = D.underfloor;
          for (const line of [`床下の水の深さの平均 ${u.max_depth_mean_mm.toFixed(0)} mm`, `水が届いた床下の列 ${u.max_reached_pct.toFixed(0)} %`, `いちばん低い部品 ${u.lowest_part_mm.toFixed(0)} mm`, `水のいちばん高い所 ${D.top_of_water_m.toFixed(2)} m`]) {
            const d = document.createElement('div'); d.textContent = line; div.appendChild(d);
          }
          const h2 = document.createElement('h3'); h2.textContent = '通した確かめ'; div.appendChild(h2);
          for (const c of D.checks) { const d = document.createElement('div'); d.textContent = c.name; div.appendChild(d); }
        },
        state: () => ({ frame: S.f, water_vertices: nx * ny, spray_shown: sprayStart[S.f + 1] - sprayStart[S.f] }),
      };
      return ctl;
    },
  };
})(window.HV);

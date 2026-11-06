// R4 roll bar push-over (viewer/src/rops.py: the run's own tool re-run, the shape at every hinge event).
// One version at a time (the camera stays when the version or the case changes). The slider runs over the push:
// state 0 (unloaded), each hinge event, the final; SUB steps per segment, the shape linear between states (the
// tool's event-to-event solution is linear there), the clearance at each step from src/rops.py (not interpolated).
window.HV = window.HV || {};
HV.fields = HV.fields || {};
HV.fields.rops = {
  label: HV.FIELD_LABEL.rops,   // js/names.js: the contract's domain (強度信頼性) + (ROPS)
  async init(D, ctx) { return HV.rops(D, ctx); },
};

HV.rops = async function (D, ctx) {
  const T = window.THREE, view = ctx.view, $ = id => document.getElementById(id);
  const V = D.versions, CJ = D.cases_ja || {};
  const AXIS = { x: 'ねじり', y: '曲げ (局所 y 軸まわり)', z: '曲げ (局所 z 軸まわり)' };
  const PURPLE = 0x8e6bbf, RED = 0xc8402a, BLUE = 0x2b59c3;
  const S = { vi: Math.max(0, V.findIndex(v => v.v === D.default_v)), cname: null, i: 0, scale: 1, nums: true };
  S.cname = V[S.vi].worst.case;
  const arrows = ctx.addArrows(), arrow = arrows.add('#e9893a');
  const grow = p => { for (let a = 0; a < 3; a++) { ctx.lo[a] = Math.min(ctx.lo[a], p[a]); ctx.hi[a] = Math.max(ctx.hi[a], p[a]); } };

  // a number on a small disc (a sprite in the scene: it turns with the view)
  const numTex = {};
  const numSprite = n => {
    if (!numTex[n]) {
      const c = document.createElement('canvas'); c.width = c.height = 64; const g = c.getContext('2d');
      g.fillStyle = '#fffdf8'; g.strokeStyle = '#8e6bbf'; g.lineWidth = 6; g.beginPath(); g.arc(32, 32, 27, 0, 2 * Math.PI); g.fill(); g.stroke();
      g.fillStyle = '#1d1d1f'; g.font = 'bold 34px "BIZ UDPGothic", "Yu Gothic", sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(String(n), 32, 34);
      numTex[n] = new T.CanvasTexture(c);
    }
    const s = new T.Sprite(new T.SpriteMaterial({ map: numTex[n], depthTest: false, transparent: true }));
    s.scale.set(70, 70, 1); s.renderOrder = 9; return s;
  };

  // per version: its group (beams, part boxes, occupant spaces, hinge rings), built once
  for (const ver of V) {
    ver.g = new T.Group(); ver.g.visible = false; ctx.root.add(ver.g);
    ver.nodes0 = await HV.unpack(ver.frame.nodes);
    for (let i = 0; i < ver.nodes0.length; i += 3) grow([ver.nodes0[i], ver.nodes0[i + 1], ver.nodes0[i + 2]]);
    ver.beams = new HV.Beams(view, ver.frame, ver.nodes0); ver.g.add(ver.beams.group);
    ver.boxes = new HV.Boxes(view, ver.parts, { opacity: 0.16 }); ver.g.add(ver.boxes.group);
    for (const p of ver.parts) { grow(p.c.map((c, a) => c - p.s[a] / 2)); grow(p.c.map((c, a) => c + p.s[a] / 2)); }
    ver.spaces = ver.spaces.map(s => {
      const size = [0, 1, 2].map(a => s.hi[a] - s.lo[a]), c = [0, 1, 2].map(a => (s.hi[a] + s.lo[a]) / 2);
      const geo = new T.BoxGeometry(...size);
      const mat = view.material(new T.MeshLambertMaterial({ color: BLUE, transparent: true, opacity: 0.12, depthWrite: false, side: T.DoubleSide }));
      const mesh = new T.Mesh(geo, mat); mesh.position.set(...c); mesh.userData = { kind: 'space', occupant: s.occupant };
      const lm = view.material(new T.LineBasicMaterial({ color: BLUE, transparent: true, opacity: 0.8 }));
      const edges = new T.LineSegments(new T.EdgesGeometry(geo), lm); edges.position.set(...c);
      ver.g.add(mesh, edges);
      return Object.assign({}, s, { mesh, mat, lm });
    });
    ver.hg = new T.Group(); ver.g.add(ver.hg);
    ver.U = {};
  }
  ctx.has.parts = true;   // the part boxes are the shown version's (ctx.boxes is set by show())
  const ver = () => V[S.vi], cs = () => ver().cases[S.cname];
  async function U(v, name) { if (!v.U[name]) v.U[name] = await HV.unpack(v.cases[name].U); return v.U[name]; }

  // the hinge rings of the shown case: a ring at the element end, a little in along the element, numbered in order
  let rings = [];
  function buildRings() {
    const v = ver(), c = cs(); v.hg.clear(); rings = [];
    let n = 0, lastState = -1;
    c.hinges.forEach((h, k) => {
      if (h.state !== lastState) lastState = h.state;
      n = k + 1;
      const r = v.frame.radius_mm[h.elem] * 1.45 + 6;
      const ring = new T.Mesh(new T.TorusGeometry(r, 5, 8, 28), view.material(new T.MeshBasicMaterial({ color: PURPLE })));
      ring.userData = { kind: 'hinge', k };
      const num = numSprite(n);
      v.hg.add(ring, num); rings.push({ h, ring, num, r });
    });
  }

  // the displaced positions at step i (state s + fraction t), times the display scale
  const cur = { pos: null };
  function at(i) {
    const c = cs(), sub = c.sub, nS = c.n_states, s = Math.min(Math.floor(i / sub), nS - 2), t = (i - s * sub) / sub;
    return { s, t, F: c.F[s] + t * (c.F[s + 1] - c.F[s]), d: c.d[s] + t * (c.d[s + 1] - c.d[s]),
             E: c.energy_J[s] + (c.energy_J[s + 1] - c.energy_J[s]) * (t * c.F[s] + 0.5 * t * t * (c.F[s + 1] - c.F[s])) / (0.5 * (c.F[s] + c.F[s + 1]) || 1),
             state: i / sub };
  }
  let Uc = null;
  function update() {
    const v = ver(), c = cs(), n0 = v.nodes0, n = n0.length / 3, a = at(S.i), nn = n * 3;
    const p = cur.pos && cur.pos.length === nn ? cur.pos : (cur.pos = new Float32Array(nn));
    const u = new Float32Array(nn);
    for (let j = 0; j < nn; j++) { u[j] = Uc[a.s * nn + j] + a.t * (Uc[(a.s + 1) * nn + j] - Uc[a.s * nn + j]); p[j] = n0[j] + S.scale * u[j]; }
    cur.u = u;
    v.beams.setNodes(p);
    const spec = contour();
    v.beams.colourNodes(k => HV.colour(spec, Math.hypot(u[3 * k], u[3 * k + 1], u[3 * k + 2])));
    // the occupant spaces: red only for the one the bar is inside (clearance < 0 at this step)
    v.spaces.forEach((s, o) => {
      const cl = c.sub_clear[o][S.i], ng = cl < D.criterion.limit;
      s.mat.color.setHex(ng ? RED : BLUE); s.mat.opacity = ng ? 0.42 : 0.12; s.lm.color.setHex(ng ? RED : BLUE);
    });
    // the push: the arrow's tip at the load point (moved), its length by the force
    const L = c.load_node, tip = [p[3 * L], p[3 * L + 1], p[3 * L + 2]], Fmax = Math.max(...c.F, 1);
    const len = a.F > 0 ? 250 + 450 * a.F / Fmax : 0;
    arrows.set(arrow, tip.map((x, k) => x - c.dir[k] * len), c.dir.map(x => x * Math.max(a.F, 1e-9)), len / Math.max(a.F, 1e-9));
    // rings: shown from their event on
    const T3 = new T.Vector3(), A = new T.Vector3(), B = new T.Vector3();
    for (const R of rings) {
      const on = a.state >= R.h.state - 1e-9;
      R.ring.visible = on; R.num.visible = on && S.nums;
      if (!on) continue;
      const [ia, ib] = v.frame.members[R.h.elem], e0 = R.h.end ? ib : ia, e1 = R.h.end ? ia : ib;
      A.set(p[3 * e0], p[3 * e0 + 1], p[3 * e0 + 2]); B.set(p[3 * e1], p[3 * e1 + 1], p[3 * e1 + 2]);
      T3.subVectors(B, A); const Lm = T3.length(); T3.normalize();
      const off = Math.min(0.3 * Lm, R.r * 1.6 + 25);
      R.ring.position.copy(A).addScaledVector(T3, off);
      R.ring.quaternion.setFromUnitVectors(new T.Vector3(0, 0, 1), T3);
      R.num.position.copy(R.ring.position).add(new T.Vector3(0, 0, R.r + 55));
    }
  }
  function contour() {
    const c = cs(); const m = Math.max(1e-6, ...(c.umax ? [c.umax] : [0]));
    return { min: 0, max: m, log: false, unit: 'mm', title: '変位の大きさ |u| (実寸)', note: `範囲はこの押し方の最大 ${HV.fmt(m)} mm` };
  }
  async function show(vi, cname, keep) {
    const frac = ctl.nT > 1 ? S.i / (ctl.nT - 1) : 1;
    for (const v of V) v.g.visible = false;
    S.vi = vi; S.cname = cname in V[vi].cases ? cname : V[vi].worst.case;
    const v = ver(), c = cs();
    v.g.visible = true;
    Uc = await U(v, S.cname);
    if (c.umax === undefined) { let m = 0; for (let j = 0; j < Uc.length; j += 3) m = Math.max(m, Math.hypot(Uc[j], Uc[j + 1], Uc[j + 2])); c.umax = m; }
    ctl.nT = (c.n_states - 1) * c.sub + 1;
    S.i = keep === 'end' ? ctl.nT - 1 : Math.round(frac * (ctl.nT - 1));
    ctx.boxes = v.boxes; ctx.ledger = new HV.Ledger({ ledger: v.ledger });
    // the header's source line names the shown version's record (main.js wrote the first one's)
    if (HV.state && HV.state.ctl === ctl && $('source')) $('source').textContent = `v${v.v}  ${D.source.run}  ${v.analysis_id}  ${v.load_case_id}`;
    buildRings(); update();
    // the shared controls follow (main.js reads ctl.nT; its slider's range is set here)
    if ($('time')) { $('time').max = ctl.nT - 1; $('time').value = S.i; }
    if (HV.state && HV.state.ctl === ctl) { HV.state.t = S.i; ctx.applyLayers(); $('ledgerLegend').innerHTML = ctx.ledger.legendHtml(); $('ledgerLists').innerHTML = ctx.ledger.listsHtml(); $('ledgerTable').innerHTML = ctx.ledger.tableHtml(); }
    paintPanel();
    if (HV.refresh) HV.refresh();
  }

  const okng = ok => `<span class="${ok ? 'ok' : 'ng'}">${ok ? 'OK' : 'NG'}</span>`;
  const sgn = x => `${x >= 0 ? '+' : ''}${x.toFixed(2)}`;
  const tag = (cls, text) => { const s = document.createElement('span'); s.className = cls; s.textContent = text; return s; };
  let panelDiv = null;
  function paintPanel() {
    if (!panelDiv) return;
    for (const v of V) { const b = $('ropsv_' + v.v); if (b) b.classList.toggle('on', v === ver()); }
    for (const k of Object.keys(ver().cases)) { const b = $('ropsc_' + k); if (b) { b.classList.toggle('on', k === S.cname); b.replaceChildren((CJ[k] || k) + ' ', tag(ver().case_ok[k] ? 'ok' : 'ng', sgn(ver().record[k]))); } }
    const c = cs(), v = ver(), list = $('ropsHinges');
    if (list) list.innerHTML = c.hinges.length ? c.hinges.map((h, k) => {
      const [ia, ib] = v.frame.members[h.elem], node = v.frame.node_ids[h.end ? ib : ia];
      return `<div data-k="${k}">${k + 1}. ${HV.esc(v.frame.member_ids[h.elem])} の ${HV.esc(node)} 側、${HV.esc(AXIS[h.axis] || h.axis)}、${HV.fmt(h.F / 1000)} kN</div>`;
    }).join('') : '<div class="muted">ヒンジは入らない (弾性のまま最後の力まで)</div>';
    if (list) list.querySelectorAll('div[data-k]').forEach(d => { d.onclick = () => { const h = c.hinges[+d.dataset.k]; S.i = h.state * c.sub; if (HV.state) HV.state.t = S.i; $('time').value = S.i; update(); HV.refresh(); }; });
  }

  const ctl = {
    nT: 1, t0: 0, playMs: 70, viewDir: [-1.25, 0.55, 0.5],
    timeNote: 'ヒンジが入る瞬間ごとの形と、そのあいだ (直線、計算そのもの)',
    scale: { min: 0, max: 5, step: 0.5, value: 1, label: '変形の倍率', note: '1 = 実寸 (すき間の数字はいつも実寸)' },
    setScale(s) { S.scale = s; update(); },
    setTime(i) { S.i = i; update(); },
    timeText(i) {
      const c = cs(), a = at(i), last = i === ctl.nT - 1, ev = i % c.sub === 0 && a.state > 0 && !last ? a.state : 0;
      const nh = c.hinges.filter(h => a.state >= h.state - 1e-9).length;
      return `押す力 ${HV.fmt(a.F / 1000)} kN   たわみ ${a.d.toFixed(1)} mm   吸ったエネルギー ${a.E.toFixed(0)} J` +
             (c.kind === 'energy' ? ` / ${HV.fmt(c.target)} J` : '') + `   ヒンジ ${nh} / ${c.hinges.length}` +
             (ev ? `   ← ${ev} 回目のヒンジの瞬間` : '') + (last ? '   ← 最大の荷重 (試験の終わり)' : '');
    },
    hud() {
      const c = cs(), v = ver(), cl = Math.min(...c.sub_clear.map(x => x[S.i])), o = c.sub_clear.findIndex(x => x[S.i] === cl);
      const ok = cl >= D.criterion.limit;
      return [`v${v.v} ${CJ[S.cname] || S.cname}: 乗員の空間とのすき間 ${sgn(cl)} mm ${ok ? 'OK' : 'NG'} (${c.sub_member[S.i]} と ${v.spaces[o] ? v.spaces[o].occupant : ''})` +
              `   最後 ${sgn(c.clear[c.n_states - 1])} mm (記録 ${sgn(c.record_clear)} mm)`,
              `変形 ×${S.scale}${S.scale === 1 ? ' (実寸)' : '、実寸ではない'}   橙の矢印: 押す力   紫の輪: 塑性ヒンジ (番号は入った順)   青の箱: 乗員の空間 (中に入ると赤)`];
    },
    contour,
    plot() {
      const c = cs(), a = at(S.i);
      const sx = []; for (let s = 0; s < c.n_states - 1; s++) for (let j = 0; j < c.sub + (s === c.n_states - 2 ? 1 : 0); j++) sx.push(c.d[s] + (j / c.sub) * (c.d[s + 1] - c.d[s]));
      // one mark per group of events closer than 2 % of the travel (their labels would overlap): ヒンジ 1〜4
      const groups = [], span = Math.max(1e-9, c.d[c.n_states - 1]);
      c.hinges.forEach((h, k) => { const g = groups[groups.length - 1], x = c.d[h.state]; if (g && x - g.x < 0.02 * span) g.k1 = k + 1; else groups.push({ x, k0: k + 1, k1: k + 1 }); });
      const mk = groups.map(g => ({ x: g.x, label: g.k0 === g.k1 ? `ヒンジ ${g.k0}` : `ヒンジ ${g.k0}〜${g.k1}`, col: '#8e6bbf' }));
      const cols = ['#2b59c3', '#2f7d4f', '#6b665c'];
      return [
        { title: '押す力とたわみ', code: 'F-d', unit: 'kN', xlabel: '押す点のたわみ [mm]', x: c.d, ys: [{ v: c.F.map(f => f / 1000), col: '#e9893a' }], marks: mk, cursor: a.d },
        { title: '乗員の空間とのすき間', code: 'rops_occupant_clearance', unit: 'mm', xlabel: '押す点のたわみ [mm]', x: sx,
          ys: c.sub_clear.map((v, o) => ({ name: ver().spaces[o].occupant, v, col: cols[o % 3] })), marks: mk, cursor: a.d },
      ];
    },
    plotClick(panel, x) {
      const c = cs(); let b = 0, bd = Infinity;
      for (let i = 0; i < ctl.nT; i++) { const d = Math.abs(at(i).d - x); if (d < bd) { bd = d; b = i; } }
      return b;
    },
    pickables: () => [ver().beams.mesh, ver().boxes.mesh, ...ver().spaces.map(s => s.mesh), ...rings.map(r => r.ring)],
    info(hit) {
      const v = ver(), c = cs();
      if (hit.object.userData.kind === 'space') {
        const o = v.spaces.findIndex(s => s.mesh === hit.object), s = v.spaces[o], cl = c.sub_clear[o][S.i];
        return `<b>乗員の空間 ${HV.esc(s.occupant)}</b><br>x ${HV.fmt(s.lo[0])}〜${HV.fmt(s.hi[0])}、y ${HV.fmt(s.lo[1])}〜${HV.fmt(s.hi[1])}、z ${HV.fmt(s.lo[2])}〜${HV.fmt(s.hi[2])} mm<br>` +
               `いまのすき間 ${sgn(cl)} mm ${okng(cl >= D.criterion.limit)}<details><summary>空間の決め方</summary>${HV.esc(v.space_basis)}</details>`;
      }
      if (hit.object.userData.kind === 'hinge') {
        const h = c.hinges[hit.object.userData.k], [ia, ib] = v.frame.members[h.elem];
        return `<b>ヒンジ ${hit.object.userData.k + 1}</b><br>${HV.esc(v.frame.member_ids[h.elem])} の ${HV.esc(v.frame.node_ids[h.end ? ib : ia])} 側の端、${AXIS[h.axis] || h.axis}<br>入った力 ${HV.fmt(h.F / 1000)} kN`;
      }
      if (hit.object === v.boxes.mesh) { const p = v.parts[hit.instanceId]; return `<b>${HV.esc(p.id)}</b> ${HV.esc(p.name)}<br>動かさない (押す計算に部品は入らない)`; }
      const m = v.beams.memberOf(hit), [na, nb] = v.frame.members[m], ids = v.frame.node_ids, u = cur.u;
      const du = k => Math.hypot(u[3 * k], u[3 * k + 1], u[3 * k + 2]);
      const hs = c.hinges.map((h, k) => h.elem === m ? `${k + 1} (${HV.esc(ids[h.end ? nb : na])} 側、${AXIS[h.axis] || h.axis})` : null).filter(Boolean);
      return `<b>${HV.esc(v.frame.member_ids[m])}</b> ${HV.esc(v.frame.member_names[m])}<br>いまの変位: ${HV.esc(ids[na])} ${HV.fmt(du(na))} mm、${HV.esc(ids[nb])} ${HV.fmt(du(nb))} mm (実寸)` +
             (hs.length ? `<br>ヒンジ: ${hs.join('、')}` : '');
    },
    extraInputs() {
      const rows = V.map(v => `<tr><td>v${v.v} (${HV.esc(v.analysis_id)})</td><td>${Object.keys(v.cases).map(k => `${HV.esc(CJ[k] || k)} ${sgn(v.cases[k].rerun_clear)}`).join('、')} mm</td><td>${okng(v.ok)}</td></tr>`).join('');
      const chk = V.map(v => Object.keys(v.cases).map(k => Math.abs(v.cases[k].rerun_clear - v.cases[k].record_clear))).flat();
      return `<div class="h" style="margin-top:4px">判定 (${HV.qhtml(D.criterion.quantity)} ${HV.esc(D.criterion.op)} ${HV.fmt(D.criterion.limit)} ${HV.esc(D.criterion.unit)})</div><table>${rows}</table>` +
             `<div class="muted">呼び直したすき間と記録の差: 最大 ${Math.max(...chk).toExponential(1)} mm (${D.check_mm} mm 以内で一致を確かめてから描く)</div>`;
    },
    panel(div) {
      panelDiv = div;
      const h = t => { const e = document.createElement('h3'); e.textContent = t; div.appendChild(e); };
      h('版');
      const r1 = document.createElement('div'); r1.className = 'row';
      for (const [vi, v] of V.entries()) {
        const b = document.createElement('button'); b.id = 'ropsv_' + v.v; b.replaceChildren(`v${v.v} `, tag(v.ok ? 'ok' : 'ng', v.ok ? 'OK' : 'NG'));
        b.onclick = () => show(vi, S.cname); r1.appendChild(b);
      }
      div.appendChild(r1);
      const m = document.createElement('div'); m.className = 'muted'; m.textContent = '版を変えても視点はそのまま。数字は最後 (最大の荷重) のすき間'; div.appendChild(m);
      h('押す向き');
      for (const k of Object.keys(ver().cases)) {
        const b = document.createElement('button'); b.id = 'ropsc_' + k; b.style.margin = '2px';
        b.onclick = () => show(S.vi, k); div.appendChild(b);
      }
      const r2 = document.createElement('div'); r2.className = 'row';
      const end = document.createElement('button'); end.id = 'ropsEnd'; end.textContent = '最大の荷重へ';
      end.onclick = () => { S.i = ctl.nT - 1; HV.state.t = S.i; $('time').value = S.i; update(); HV.refresh(); };
      const l = document.createElement('label'); const cb = document.createElement('input'); cb.type = 'checkbox'; cb.id = 'ropsNums'; cb.checked = S.nums;
      cb.onchange = () => { S.nums = cb.checked; update(); HV.refresh(); }; l.append(cb, ' ヒンジの番号');
      r2.append(end, l); div.appendChild(r2);
      h('ヒンジ (入った順)');
      const list = document.createElement('div'); list.className = 'list'; list.id = 'ropsHinges'; div.appendChild(list);
      paintPanel();
    },
  };
  await show(S.vi, S.cname, 'end');
  ctl.t0 = ctl.nT - 1;
  return ctl;
};

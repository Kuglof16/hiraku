// The page: field switch, shared controls (scale, time, contour, clipping, layers), HUD, notes,
// inputs panel, plot pane and click info. Each field (js/<field>.js) registers
// HV.fields[name] = {label, script, async init(D, ctx) -> controller}; the controller gives:
//   nT, timeText(i), setTime(i), scale {min, max, step, value, label, note}, setScale(s),
//   contour() -> spec or null, plot() -> panels, plotClick(panel, x), hud() -> [lines],
//   info(hit) -> html, pickables() -> [objects], panel(div), playMs
window.HV = window.HV || {};
(function (HV) {
  'use strict';
  HV.fields = HV.fields || {};
  HV.FIELD_ORDER = ['strength', 'fatigue', 'nvh', 'crash', 'handling', 'powertrain', 'aero', 'thermal', 'assembly'];
  const $ = id => document.getElementById(id);

  function loadData(field) {
    return new Promise(resolve => {
      if (window.VIEW_DATA && window.VIEW_DATA[field]) return resolve(window.VIEW_DATA[field]);
      const s = document.createElement('script');
      s.src = `data/${field}.js`;
      s.onload = () => resolve((window.VIEW_DATA || {})[field] || null);
      s.onerror = () => resolve(null);
      document.head.appendChild(s);
    });
  }

  HV.main = async function () {
    const view = new HV.View($('view'));
    const T = window.THREE;
    const st = { field: null, ctl: null, t: 0, playing: false, timer: null, ctx: null };
    const plot = new HV.Plot($('plot'), (i, x) => { if (st.ctl && st.ctl.plotClick) { const t = st.ctl.plotClick(i, x); if (t !== undefined && t !== null) setTime(t); refresh(); } });
    HV.state = st; HV.view = view;

    // field switch
    for (const f of HV.FIELD_ORDER) {
      const b = document.createElement('button'); b.textContent = (HV.fields[f] || { label: f }).label; b.id = 'sw_' + f;
      b.onclick = () => open(f); $('switch').appendChild(b);
    }

    // clipping
    for (const k of ['x', 'y', 'z']) {
      const d = document.createElement('div'); d.className = 'row';
      d.innerHTML = `<label><input type="checkbox" id="c${k}on"> ${k.toUpperCase()}</label>
        <input type="range" id="c${k}" step="1"><span id="c${k}val" class="val"></span><button id="c${k}flip" title="切った反対側を残す">反対側</button>`;
      $('clips').appendChild(d);
      const c = view.clipState[k];
      $(`c${k}on`).onchange = e => { c.on = e.target.checked; view.updateClip(); };
      $(`c${k}`).oninput = e => { c.v = +e.target.value; $(`c${k}val`).textContent = c.v + ' mm'; view.updateClip(); };
      $(`c${k}flip`).onclick = e => { c.flip = !c.flip; e.target.classList.toggle('on', c.flip); view.updateClip(); };
    }

    // time and scale
    $('time').oninput = e => { setTime(+e.target.value); };
    $('prev').onclick = () => setTime(st.t - 1);
    $('next').onclick = () => setTime(st.t + 1);
    // a field with real times (ctl.times, s) plays at a chosen fraction of real time; others step frames
    $('play').onclick = () => {
      st.playing = !st.playing; $('play').textContent = st.playing ? '❚❚ 停止' : '▶ 再生';
      stopPlay();
      if (!st.playing || !st.ctl) return;
      const tt = st.ctl.times;
      if (!tt) { st.timer = setInterval(() => setTime((st.t + 1) % st.ctl.nT), st.ctl.playMs || 40); return; }
      let sim = st.t >= st.ctl.nT - 1 ? tt[0] : tt[st.t], last = performance.now();
      const step = now => {
        if (!st.playing || st.ctl.times !== tt) return;
        sim += (now - last) / 1000 * (+$('speed').value || 0.01); last = now;
        if (sim > tt[tt.length - 1]) sim = tt[0];
        let i = st.t; if (tt[i] > sim) i = 0;
        while (i + 1 < tt.length && tt[i + 1] <= sim) i++;
        if (i !== st.t) setTime(i);
        st.raf = requestAnimationFrame(step);
      };
      st.raf = requestAnimationFrame(step);
    };
    function stopPlay() { clearInterval(st.timer); cancelAnimationFrame(st.raf); }
    const setScale = v => {
      const s = st.ctl.scale; v = Math.max(s.min, Math.min(s.max, +v || s.min));
      s.value = v; $('scale').value = v; $('scaleNum').value = v; st.ctl.setScale(v); refresh();
    };
    $('scale').oninput = e => setScale(e.target.value);
    $('scaleNum').onchange = e => setScale(e.target.value);
    $('logc').onchange = () => { if (st.ctl && st.ctl.setLog) st.ctl.setLog($('logc').checked); refresh(); };

    // layers
    const layer = (id, key) => { $(id).onchange = e => { if (st.ctx) { st.ctx.layers[key] = e.target.checked; st.ctx.applyLayers(); } refresh(); }; };
    layer('lyFrame', 'frame'); layer('lyParts', 'parts'); layer('lyGhost', 'ghost'); layer('lyScene', 'scene'); layer('lyArrows', 'arrows');

    // picking
    let down = null;
    view.renderer.domElement.addEventListener('pointerdown', e => { down = [e.clientX, e.clientY]; });
    view.renderer.domElement.addEventListener('pointerup', e => {
      if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 4 || e.button !== 0 || !st.ctl) return;
      const own = st.ctl.pickables(), boxes = st.ctx && st.ctx.boxes;
      const hit = view.pick(e, boxes && !own.includes(boxes.mesh) ? own.concat([boxes.mesh]) : own);
      showInfo(hit ? infoOf(hit, own) : null);
    });
    // a part box: the field's own text (if it picks boxes) and how the part enters this calculation
    function infoOf(hit, own) {
      const boxes = st.ctx && st.ctx.boxes;
      if (boxes && hit.object === boxes.mesh) {
        const i = hit.instanceId;
        window.__lastPick = { part: st.ctx.D.parts[i].id, cat: st.ctx.ledger.cat(i) };
        return (own.includes(boxes.mesh) ? st.ctl.info(hit) + '<hr>' : `<b>${HV.esc(st.ctx.D.parts[i].id)}</b> ${HV.esc(st.ctx.D.parts[i].name)}<br>`) +
               st.ctx.ledger.info(i);
      }
      window.__lastPick = { member: true };
      return st.ctl.info(hit);
    }
    function showInfo(html) { const b = $('info'); $('infohint').style.display = html ? 'none' : ''; if (!html) { b.style.display = 'none'; return; } b.innerHTML = html; b.style.display = 'block'; }
    $('ledgerMode').onchange = () => { if (st.ctx) { st.ctx.partMode = $('ledgerMode').value; st.ctl.setTime(st.t); } refresh(); };

    function setTime(i) {
      if (!st.ctl) return;
      st.t = ((i % st.ctl.nT) + st.ctl.nT) % st.ctl.nT; $('time').value = st.t;
      st.ctl.setTime(st.t); refresh();
    }
    function refresh() {
      const c = st.ctl; if (!c) return;
      if (st.ctx && st.ctx.boxes && st.ctx.ledger.paint(st.ctx.boxes, st.ctx.partMode)) st.ctx.boxes.update();
      $('tline').textContent = c.timeText(st.t);
      $('hudlines').replaceChildren(...c.hud().map(l => { const d = document.createElement('div'); d.textContent = l; return d; }));
      HV.drawContourBar($('legend'), c.contour());
      plot.draw(c.plot());
      view.render();
    }
    HV.refresh = refresh;
    window.addEventListener('resize', () => { plot.draw(); });

    // field: a field name; want: one of its data variants (HV.fields[f].variants [{key, label}], e.g. the NVH
    // field's beam (data/nvh.js) and shell (data/nvh_shell.js) records); the first that exists by default
    async function open(field, want) {
      stopPlay(); st.playing = false; $('play').textContent = '▶ 再生';
      document.querySelectorAll('#switch button').forEach(b => b.classList.toggle('on', b.id === 'sw_' + field));
      if (st.ctx) { view.scene.remove(st.ctx.root); if (st.ctx.fixed) view.scene.remove(st.ctx.fixed); }
      view.materials.length = 0; showInfo(null);
      st.field = field; st.ctl = null; st.ctx = null;
      const got = [];
      for (const v of (HV.fields[field] || {}).variants || [{ key: field }]) { const d = await loadData(v.key); if (d) got.push(Object.assign({ D: d }, v)); }
      const cur = got.find(g => g.key === want) || got[0];
      const D = cur ? cur.D : null;
      st.variant = cur ? cur.key : null;
      if (!D) {
        $('title').textContent = `${(HV.fields[field] || { label: field }).label}: データがない`;
        $('source').textContent = 'この分野の結果はまだ書き出されていない';
        $('tline').textContent = ''; for (const id of ['hudlines', 'inputs', 'fieldpanel', 'ledgerLegend', 'ledgerLists', 'ledgerTable']) $(id).replaceChildren();
        HV.drawContourBar($('legend'), null); plot.draw([]); view.render();
        return;
      }
      const ctx = makeCtx(view, D);
      st.ctx = ctx; view.scene.add(ctx.root);
      $('fieldpanel').innerHTML = '';
      const ctl = await HV.fields[field].init(D, ctx);
      st.ctl = ctl;
      if (ctl.panel) ctl.panel($('fieldpanel'));
      // two records of one field (beam and shell): a switch above the field's own panel, only for the same design
      const same = got.length > 1 && got.every(g => g.D.source.design_key && g.D.source.design_key === D.source.design_key);
      if (got.length > 1) {
        const box = document.createElement('div'); box.id = 'variants';
        const h = document.createElement('h3'); h.textContent = 'モデル'; box.appendChild(h);
        const row = document.createElement('div'); row.className = 'row';
        for (const g of got) {
          const b = document.createElement('button'); b.id = 'var_' + g.key; b.textContent = g.label;
          b.classList.toggle('on', g === cur); b.disabled = !same && g !== cur;
          b.onclick = () => { if (g !== cur) open(field, g.key); };
          row.appendChild(b);
        }
        box.appendChild(row);
        if (!same) { const m = document.createElement('div'); m.className = 'muted'; m.textContent = 'ほかの記録は別の設計 (比べられない)'; box.appendChild(m); }
        $('fieldpanel').prepend(box);
      }
      // every part of the design as its box (the field's own boxes, or grey ones added here) and how it enters
      // this field's calculation (D.ledger)
      if (!ctx.boxes) ctx.addParts(D.parts, { opacity: 0.2 });
      ctx.layers.parts = true;
      // the ground under the vehicle (js/look.js): the field's own (ctl.ground {z, from, x, y, holes, fixed}; null: the
      // field draws its road itself) or, by default, from the design's supports (HV.groundOf)
      const gs = ctl.ground !== undefined ? ctl.ground : ctx.groundOverride !== undefined ? ctx.groundOverride : HV.groundOf(D);
      ctx.groundSpec = gs || null;
      if (gs) {
        const m = gs.margin ?? 2500, gx = gs.x || [ctx.lo[0] - m, ctx.hi[0] + m], gy = gs.y || [ctx.lo[1] - m, ctx.hi[1] + m];
        ctx.ground = HV.Look.ground(gx[0], gx[1], gy[0], gy[1], gs.z, gs.holes || []);
        if (gs.fixed) { ctx.fixed = ctx.fixed || new T.Group(); ctx.fixed.add(ctx.ground); view.scene.add(ctx.fixed); } else root(ctx).add(ctx.ground);
        ctx.has.scene = true;
      }
      // the sun's shadow: the opaque members, tyres and scene items cast it (see-through part boxes do not)
      ctx.root.traverse(o => { if (o.isMesh && !o.material.transparent && o.userData.kind !== 'ground' && !(ctx.ground && isIn(o, ctx.ground))) o.castShadow = true; });
      HV.Look.aimSun(view, ctx.lo, ctx.hi);
      ctx.ledger = new HV.Ledger(D);
      ctx.partMode = ctl.partMode || 'category';
      $('ledgerMode').value = ctx.partMode;
      $('ledgerMode').querySelector('option[value=field]').disabled = !ctl.partMode;
      $('ledgerLegend').innerHTML = ctx.ledger.legendHtml();
      $('ledgerLists').innerHTML = ctx.ledger.listsHtml();
      $('ledgerTable').innerHTML = ctx.ledger.tableHtml();
      // header: short; the run by its name only (never a path)
      $('title').textContent = D.title + (cur.label ? `  [${cur.label}]` : '');
      const s = D.source;
      $('source').textContent = `v${s.design_version ?? '?'}  ${s.run}  ${s.analysis_id}  ${s.load_case_id}`;
      $('source').title = `${s.run} ${s.version} ${s.analysis_id} | ${s.tool} ${s.load_case_id}`;
      // inputs; the longer explanations in closed panels
      const rows = (D.inputs || []).map(r => `<tr><td><b>${HV.esc(r.label)}</b></td><td>${HV.esc(r.value)}</td><td class="muted">${HV.esc(r.source || '')}</td></tr>`).join('');
      const res = (D.results || []).map(r => `<tr><td>${HV.esc(r.q)}</td><td>${HV.fmt(r.v)} ${HV.esc(r.u)}</td><td class="muted">${HV.esc(r.where || '')}</td></tr>`).join('');
      const asm = (D.assumptions || []).map(a => `<li>${HV.esc(a)}</li>`).join('');
      $('inputs').innerHTML = `<div class="h">入力 (条件と出どころ)</div><table>${rows}</table>` +
        (ctl.extraInputs ? ctl.extraInputs() : '') + surroundHtml(D, ctx, ctl) +
        `<details id="notesBox"><summary>説明 (${(D.notes || []).length})</summary><div id="notes">${(D.notes || []).map(t => `<div>${HV.esc(t)}</div>`).join('')}</div></details>` +
        `<details><summary>記録の結果 (${(D.results || []).length})</summary><table>${res}</table></details>` +
        (asm ? `<details><summary>記録の前提 (${D.assumptions.length})</summary><ul>${asm}</ul></details>` : '');
      // controls
      $('time').max = ctl.nT - 1; st.t = Math.min(ctl.t0 || 0, ctl.nT - 1);
      $('scaleLabel').textContent = ctl.scale.label; $('scaleNote').textContent = ctl.scale.note || '';
      for (const id of ['scale', 'scaleNum']) { $(id).min = ctl.scale.min; $(id).max = ctl.scale.max; $(id).step = ctl.scale.step; $(id).value = ctl.scale.value; }
      $('timeNote').textContent = ctl.timeNote || '';
      $('speedrow').style.display = ctl.times ? '' : 'none';
      if (ctl.times) $('speed').value = String(ctl.speed || 0.01);   // the field's own default (vehicles: real time)
      $('logc').checked = !!(ctl.contour() || {}).log; $('logrow').style.display = ctl.setLog ? '' : 'none';
      ['x', 'y', 'z'].forEach((k, a) => {
        const lo = Math.floor(ctx.lo[a] - 20), hi = Math.ceil(ctx.hi[a] + 20), c = view.clipState[k];
        c.on = false; c.flip = false; c.v = Math.round((ctx.lo[a] + ctx.hi[a]) / 2);
        $(`c${k}`).min = lo; $(`c${k}`).max = hi; $(`c${k}`).value = c.v; $(`c${k}val`).textContent = c.v + ' mm';
        $(`c${k}on`).checked = false; $(`c${k}flip`).classList.remove('on');
      });
      view.updateClip();
      for (const [id, key] of [['lyFrame', 'frame'], ['lyParts', 'parts'], ['lyGhost', 'ghost'], ['lyScene', 'scene'], ['lyArrows', 'arrows']]) {
        $(id).checked = ctx.layers[key]; $(id).parentElement.style.display = ctx.has[key] ? '' : 'none';
      }
      ctx.applyLayers();
      view.fit(ctx.lo, ctx.hi, ctl.viewDir);
      view.resize();
      ctl.setScale(ctl.scale.value);
      setTime(st.t);
      window.__field = field;
    }
    HV.open = open;

    const first = (location.hash || '').slice(1);
    $('loading').style.display = 'none';
    const byVariant = HV.FIELD_ORDER.find(f => ((HV.fields[f] || {}).variants || []).some(v => v.key === first));
    await open(HV.FIELD_ORDER.includes(first) ? first : byVariant || HV.FIELD_ORDER[0], byVariant ? first : undefined);
    window.__ready = true;
  };

  const root = ctx => ctx.root;
  const isIn = (o, g) => { for (let p = o; p; p = p.parent) if (p === g) return true; return false; };

  // the surroundings and where each comes from (a closed panel in the inputs pane)
  function surroundHtml(D, ctx, ctl) {
    const rows = [], mm = v => `${HV.fmt(v)} mm`;
    const gs = ctx.groundSpec;
    if (gs) rows.push(['地面', `高さ z = ${mm(gs.z)}`, gs.from || '']);
    for (const s of D.scene || []) {
      const what = { wall: '壁', step: '段差', tilt_table: '傾斜台', ground: '路面', gate: '門', cone: 'コーン', road_profile: '路面の凹凸', slope: '斜面' }[s.kind] || s.kind;
      const size = s.s && (s.s[0] || s.s[1] || s.s[2]) ? `、大きさ ${s.s.map(v => HV.fmt(v)).join(' × ')} mm` : '';
      rows.push([what, s.c ? `中心 (${s.c.map(v => HV.fmt(v)).join(', ')}) mm${size}` : '', `記録の scene: ${s.label || s.kind}`]);
    }
    for (const r of (ctl.surround || ctx.surround || [])) rows.push(r);
    if (!rows.length) return '';
    return `<details id="surroundBox"><summary>周りの物と出どころ (${rows.length})</summary><table>` +
      rows.map(r => `<tr><td><b>${HV.esc(r[0])}</b></td><td>${HV.esc(r[1])}</td><td class="muted">${HV.esc(r[2])}</td></tr>`).join('') + '</table></details>';
  }

  // The shared scene objects of one field: frame beams, part boxes, scene boxes, arrows.
  function makeCtx(view, D) {
    const T = window.THREE;
    const root = new T.Group();
    const ctx = { view, D, root, layers: { frame: true, parts: true, ghost: false, scene: true, arrows: true },
                  has: { frame: false, parts: false, ghost: false, scene: false, arrows: false } };
    ctx.lo = [Infinity, Infinity, Infinity]; ctx.hi = [-Infinity, -Infinity, -Infinity];
    const grow = p => { for (let a = 0; a < 3; a++) { ctx.lo[a] = Math.min(ctx.lo[a], p[a]); ctx.hi[a] = Math.max(ctx.hi[a], p[a]); } };
    ctx.addBeams = async () => {
      const nodes = await HV.unpack(D.frame.nodes);
      for (let i = 0; i < nodes.length; i += 3) grow([nodes[i], nodes[i + 1], nodes[i + 2]]);
      ctx.beams = new HV.Beams(view, D.frame, nodes); root.add(ctx.beams.group); ctx.has.frame = ctx.has.ghost = true;
      return ctx.beams;
    };
    ctx.addParts = (parts, opts) => {
      for (const p of parts) { grow(p.c.map((c, a) => c - p.s[a] / 2)); grow(p.c.map((c, a) => c + p.s[a] / 2)); }
      ctx.boxes = new HV.Boxes(view, parts, opts); root.add(ctx.boxes.group); ctx.has.parts = true;
      return ctx.boxes;
    };
    ctx.addScene = () => {
      if (!(D.scene || []).length) return null;
      ctx.sceneGroup = HV.sceneBoxes(view, D.scene); root.add(ctx.sceneGroup); ctx.has.scene = true;
      return ctx.sceneGroup;
    };
    ctx.addArrows = () => { ctx.arrows = new HV.Arrows(view); root.add(ctx.arrows.group); ctx.has.arrows = true; return ctx.arrows; };
    ctx.applyLayers = () => {
      if (ctx.beams) { ctx.beams.mesh.visible = ctx.layers.frame; ctx.beams.ghost.visible = ctx.layers.ghost; }
      if (ctx.boxes) ctx.boxes.group.visible = ctx.layers.parts;
      if (ctx.sceneGroup) ctx.sceneGroup.visible = ctx.layers.scene;
      if (ctx.ground) ctx.ground.visible = ctx.layers.scene;
      if (ctx.arrows) ctx.arrows.group.visible = ctx.layers.arrows;
      view.render();
    };
    return ctx;
  }
})(window.HV);

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
  HV.FIELD_ORDER = ['nvh', 'crash', 'assembly'];
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
        <input type="range" id="c${k}" step="1"><span id="c${k}val" class="val"></span><button id="c${k}flip" title="keep the other side">flip</button>`;
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
    $('play').onclick = () => {
      st.playing = !st.playing; $('play').textContent = st.playing ? '❚❚ pause' : '▶ play';
      clearInterval(st.timer);
      if (st.playing && st.ctl) st.timer = setInterval(() => setTime((st.t + 1) % st.ctl.nT), st.ctl.playMs || 40);
    };
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
      const hit = view.pick(e, st.ctl.pickables());
      showInfo(hit ? st.ctl.info(hit) : null);
    });
    function showInfo(html) { const b = $('info'); if (!html) { b.style.display = 'none'; return; } b.innerHTML = html; b.style.display = 'block'; }

    function setTime(i) {
      if (!st.ctl) return;
      st.t = ((i % st.ctl.nT) + st.ctl.nT) % st.ctl.nT; $('time').value = st.t;
      st.ctl.setTime(st.t); refresh();
    }
    function refresh() {
      const c = st.ctl; if (!c) return;
      $('tline').textContent = c.timeText(st.t);
      $('hudlines').replaceChildren(...c.hud().map(l => { const d = document.createElement('div'); d.textContent = l; return d; }));
      HV.drawContourBar($('legend'), c.contour());
      plot.draw(c.plot());
      view.render();
    }
    HV.refresh = refresh;
    window.addEventListener('resize', () => { plot.draw(); });

    async function open(field) {
      clearInterval(st.timer); st.playing = false; $('play').textContent = '▶ play';
      document.querySelectorAll('#switch button').forEach(b => b.classList.toggle('on', b.id === 'sw_' + field));
      if (st.ctx) view.scene.remove(st.ctx.root);
      view.materials.length = 0; showInfo(null);
      st.field = field; st.ctl = null; st.ctx = null;
      const D = await loadData(field);
      if (!D) {
        $('title').textContent = `${field}: no data file`;
        $('source').textContent = `viewer/data/${field}.js is missing: python -m viewer.src.${field} <run dir>`;
        $('tline').textContent = ''; for (const id of ['hudlines', 'notes', 'inputs', 'fieldpanel']) $(id).replaceChildren();
        HV.drawContourBar($('legend'), null); plot.draw([]); view.render();
        return;
      }
      const ctx = makeCtx(view, D);
      st.ctx = ctx; view.scene.add(ctx.root);
      $('fieldpanel').innerHTML = '';
      const ctl = await HV.fields[field].init(D, ctx);
      st.ctl = ctl;
      if (ctl.panel) ctl.panel($('fieldpanel'));
      // header and notes
      $('title').textContent = D.title;
      const s = D.source;
      $('source').textContent = `${s.design_name} | ${s.version} ${s.analysis_id} (${s.tool}, ${s.load_case_id}) | run ${s.run}`;
      $('notes').innerHTML = (D.not_computed || []).map(t => `<div class="nc">NOT COMPUTED: ${HV.esc(t)}</div>`).join('') +
        (D.notes || []).map(t => `<div class="nt">${HV.esc(t)}</div>`).join('');
      // inputs
      const rows = (D.inputs || []).map(r => `<tr><td><b>${HV.esc(r.label)}</b></td><td>${HV.esc(r.value)}</td><td class="muted">${HV.esc(r.source || '')}</td></tr>`).join('');
      const res = (D.results || []).map(r => `<tr><td>${HV.esc(r.q)}</td><td>${HV.fmt(r.v)} ${HV.esc(r.u)}</td><td class="muted">${HV.esc(r.where || '')}</td></tr>`).join('');
      const asm = (D.assumptions || []).map(a => `<li>${HV.esc(a)}</li>`).join('');
      $('inputs').innerHTML = `<div class="h">Inputs (the case and where they come from)</div><table>${rows}</table>` +
        (ctl.extraInputs ? ctl.extraInputs() : '') +
        `<details><summary>Results in the record (${(D.results || []).length})</summary><table>${res}</table></details>` +
        (asm ? `<details><summary>Assumptions in the record (${D.assumptions.length})</summary><ul>${asm}</ul></details>` : '');
      // controls
      $('time').max = ctl.nT - 1; st.t = Math.min(ctl.t0 || 0, ctl.nT - 1);
      $('scaleLabel').textContent = ctl.scale.label; $('scaleNote').textContent = ctl.scale.note || '';
      for (const id of ['scale', 'scaleNum']) { $(id).min = ctl.scale.min; $(id).max = ctl.scale.max; $(id).step = ctl.scale.step; $(id).value = ctl.scale.value; }
      $('timeNote').textContent = ctl.timeNote || '';
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
    await open(HV.FIELD_ORDER.includes(first) ? first : HV.FIELD_ORDER[0]);
    window.__ready = true;
  };

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
      if (ctx.arrows) ctx.arrows.group.visible = ctx.layers.arrows;
      view.render();
    };
    return ctx;
  }
})(window.HV);

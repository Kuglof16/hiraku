// NVH (modal): frame beams moved by a mode shape, x = x0 + scale * shape / |shape|max * sin(phase),
// coloured by |shape| / |shape|max; mode list; frequency response plot.
window.HV = window.HV || {};
HV.fields = HV.fields || {};
HV.fields.nvh = {
  label: 'NVH (modes)',
  async init(D, ctx) {
    const beams = await ctx.addBeams();
    const nodes0 = beams.nodes0, n = nodes0.length / 3;
    const shapes = await Promise.all(D.modes.map(m => HV.unpack(m.shape)));
    const frf = D.frf ? { x: await HV.unpack(D.frf.x), y: await HV.unpack(D.frf.y) } : null;
    const NT = 48;
    const S = { mode: 0, scale: 150, t: 0, log: false };
    const cur = new Float32Array(nodes0.length);
    const amp = i => { const s = shapes[S.mode], a = D.modes[S.mode].amp_max || 1; return Math.hypot(s[3 * i], s[3 * i + 1], s[3 * i + 2]) / a; };
    const spec = () => ({ min: S.log ? 1e-3 : 0, max: 1, log: S.log, unit: '1', title: 'mode amplitude |u| / |u|max',
                          note: 'normalised shape, no physical size' });
    function update() {
      const s = shapes[S.mode], a = D.modes[S.mode].amp_max || 1, f = S.scale / a * Math.sin(2 * Math.PI * S.t / NT);
      for (let i = 0; i < nodes0.length; i++) cur[i] = nodes0[i] + f * s[i];
      beams.setNodes(cur);
      const c = spec();
      beams.colourNodes(i => HV.colour(c, amp(i)));
    }
    const ctl = {
      nT: NT, t0: Math.round(NT / 4), playMs: 35,
      timeNote: 'Phase of the mode, one period over the slider (0 to 360 deg). Not a time: modes have no time history.',
      scale: { min: 0, max: 600, step: 5, value: S.scale, label: 'Mode shape amplitude (mm on screen)', note: 'The largest node moves this far at the peak of the period. Not a physical amplitude.' },
      setScale(v) { S.scale = v; update(); },
      setTime(i) { S.t = i; update(); },
      setLog(on) { S.log = on; update(); },
      timeText(i) { const m = D.modes[S.mode]; return `${m.label}: ${m.f_Hz.toFixed(2)} Hz   phase ${Math.round(360 * i / NT)} deg`; },
      hud() {
        const m = D.modes[S.mode];
        return [`shape shown x ${S.scale} mm at the largest node (not to scale)` +
                (m.share_bending !== null && m.share_bending !== undefined ? `; share of the shape that bends the frame ${(100 * m.share_bending).toFixed(1)} %` : ''),
                `${D.modes.length} modes stored: ` + D.modes.map((x, i) => `${i + 1}: ${x.f_Hz.toFixed(2)} Hz`).join(', ')];
      },
      contour: spec,
      plot() {
        if (!frf) return [];
        const marks = D.modes.map((m, i) => ({ x: m.f_Hz, label: `mode ${i + 1}`, col: i === S.mode ? '#d00' : '#999', w: i === S.mode ? 2 : 1, dash: i === S.mode ? [] : [3, 3] }))
          .concat((D.lines || []).map(l => ({ x: l.f_Hz, label: l.label, col: '#2a6', dash: [6, 3] })));
        return [{ title: `frequency response ${D.frf.channel} (click: nearest mode)`, unit: D.frf.y_unit, xlabel: `frequency [${D.frf.x_unit}]`,
                  x: Array.from(frf.x), ys: [{ name: '', v: Array.from(frf.y), col: '#1f4e9c' }], marks, cursor: null, logy: true }];
      },
      plotClick(_, x) { let b = 0, bd = Infinity; D.modes.forEach((m, i) => { const d = Math.abs(m.f_Hz - x); if (d < bd) { bd = d; b = i; } }); pick(b); },
      pickables: () => [beams.mesh],
      info(hit) {
        const m = beams.memberOf(hit), [a, b] = D.frame.members[m], ids = D.frame.node_ids;
        return `<b>${HV.esc(D.frame.member_ids[m])}</b> ${HV.esc(D.frame.member_names[m])}<br>nodes ${HV.esc(ids[a])} (|u|/|u|max ${amp(a).toFixed(3)}), ` +
               `${HV.esc(ids[b])} (${amp(b).toFixed(3)}) in ${HV.esc(D.modes[S.mode].label)}`;
      },
      extraInputs() { return D.frf && D.frf.note ? `<div class="muted">${HV.esc(D.frf.note)}</div>` : ''; },
      panel(div) {
        const h = document.createElement('h3'); h.textContent = 'Modes'; div.appendChild(h);
        const list = document.createElement('div'); list.className = 'list'; list.id = 'modelist';
        D.modes.forEach((m, i) => { const d = document.createElement('div'); d.textContent = `${i + 1}. ${m.f_Hz.toFixed(2)} Hz  ${m.label}`; d.onclick = () => pick(i); list.appendChild(d); });
        div.appendChild(list);
        mark();
      },
      selectMode: i => pick(i),
    };
    function mark() { document.querySelectorAll('#modelist div').forEach((d, i) => d.classList.toggle('on', i === S.mode)); }
    function pick(i) { S.mode = i; mark(); update(); HV.refresh(); }
    ctx.layers.parts = false;
    update();
    return ctl;
  },
};

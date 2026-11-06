// NVH (modal): frame beams moved by a mode shape, x = x0 + scale * shape / |shape|max * sin(phase),
// coloured by member strain / the mode's largest (src/nvh.py member_strain) or, switched, |shape| / |shape|max;
// mode list with the top strain-energy members; frequency response plot.
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
    const S = { mode: 0, scale: 150, t: 0, log: false, by: 'strain' };
    const cur = new Float32Array(nodes0.length);
    const amp = i => { const s = shapes[S.mode], a = D.modes[S.mode].amp_max || 1; return Math.hypot(s[3 * i], s[3 * i + 1], s[3 * i + 2]) / a; };
    const rel = m => { const st = D.modes[S.mode].strain; return st.max > 0 ? st.total[m] / st.max : 0; };
    const spec = () => S.by === 'strain'
      ? { min: S.log ? 1e-3 : 0, max: 1, log: S.log, unit: '1', title: 'member strain / the mode\'s largest (axial + bending)',
          note: 'relative: the mode has no physical size; bending estimated from node translations (see the method under Inputs)' }
      : { min: S.log ? 1e-3 : 0, max: 1, log: S.log, unit: '1', title: 'mode amplitude |u| / |u|max', note: 'normalised shape, no physical size' };
    function update() {
      const s = shapes[S.mode], a = D.modes[S.mode].amp_max || 1, f = S.scale / a * Math.sin(2 * Math.PI * S.t / NT);
      for (let i = 0; i < nodes0.length; i++) cur[i] = nodes0[i] + f * s[i];
      beams.setNodes(cur);
      const c = spec();
      if (S.by === 'strain') beams.colourMembers(m => HV.colour(c, rel(m)));
      else beams.colourNodes(i => HV.colour(c, amp(i)));
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
        const top = m.energy.top.map(x => `${x.member} ${(100 * x.share).toFixed(1)} %`).join(', ');
        return [`shape shown x ${S.scale} mm at the largest node (not to scale)` +
                (m.share_bending !== null && m.share_bending !== undefined ? `; share of the shape that bends the frame ${(100 * m.share_bending).toFixed(1)} %` : ''),
                `colour: ${S.by === 'strain' ? 'member strain relative to this mode\'s largest' : 'node displacement |u| / |u|max'}; largest strain ${HV.fmt(m.strain.per_mm)} per 1 mm of the largest node's motion (compare modes by this; a body-on-springs mode is small)`,
                `strain energy, top members: ${top || 'no section data'}`,
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
        const m = beams.memberOf(hit), [a, b] = D.frame.members[m], ids = D.frame.node_ids, md = D.modes[S.mode], st = md.strain, sh = md.energy.share[m];
        return `<b>${HV.esc(D.frame.member_ids[m])}</b> ${HV.esc(D.frame.member_names[m])}<br>nodes ${HV.esc(ids[a])} (|u|/|u|max ${amp(a).toFixed(3)}), ` +
               `${HV.esc(ids[b])} (${amp(b).toFixed(3)}) in ${HV.esc(md.label)}<br>strain / mode's largest ${rel(m).toFixed(3)} ` +
               `(axial ${st.max > 0 ? (st.axial[m] / st.max).toFixed(3) : '-'}, bending ${st.max > 0 ? (st.bending[m] / st.max).toFixed(3) : '-'}); ` +
               `strain energy share ${sh === null ? 'unknown section' : (100 * sh).toFixed(2) + ' %'}`;
      },
      extraInputs() { return (D.frf && D.frf.note ? `<div class="muted">${HV.esc(D.frf.note)}</div>` : '') + `<div class="muted">Strain method: ${HV.esc(D.strain_method)}</div>`; },
      panel(div) {
        const h = document.createElement('h3'); h.textContent = 'Modes'; div.appendChild(h);
        const list = document.createElement('div'); list.className = 'list'; list.id = 'modelist';
        D.modes.forEach((m, i) => { const d = document.createElement('div'); d.textContent = `${i + 1}. ${m.f_Hz.toFixed(2)} Hz  ${m.label}`; d.onclick = () => pick(i); list.appendChild(d); });
        div.appendChild(list);
        const h2 = document.createElement('h3'); h2.textContent = 'Colour'; div.appendChild(h2);
        for (const [k, lab] of [['strain', 'member strain (relative)'], ['disp', 'displacement |u| / |u|max']]) {
          const l = document.createElement('label'); l.style.display = 'block';
          const r = document.createElement('input'); r.type = 'radio'; r.name = 'nvhcol'; r.id = 'nvhcol_' + k; r.checked = S.by === k;
          r.onchange = () => { S.by = k; update(); HV.refresh(); };
          l.append(r, ' ' + lab); div.appendChild(l);
        }
        const h3 = document.createElement('h3'); h3.textContent = 'Strain energy, top members'; div.appendChild(h3);
        const tl = document.createElement('div'); tl.id = 'nvhtop'; tl.className = 'muted'; div.appendChild(tl);
        mark();
      },
      selectMode: i => pick(i),
    };
    function mark() {
      document.querySelectorAll('#modelist div').forEach((d, i) => d.classList.toggle('on', i === S.mode));
      const tl = document.getElementById('nvhtop'); if (!tl) return;
      const md = D.modes[S.mode];
      tl.replaceChildren(...md.energy.top.map((x, j) => { const d = document.createElement('div');
        d.textContent = `${j + 1}. ${x.member}: ${(100 * x.share).toFixed(1)} % (axial ${(100 * x.axial_part).toFixed(0)} % of it)`; return d; }),
        Object.assign(document.createElement('div'), { textContent: `mode ${S.mode + 1}; ${md.energy.known} members with a known section; torsion not included` }));
    }
    function pick(i) { S.mode = i; mark(); update(); HV.refresh(); }
    ctx.layers.parts = false;
    update();
    return ctl;
  },
};

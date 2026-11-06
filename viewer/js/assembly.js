// Assembly (H1/H3/H6/H7): part boxes coloured by step order, status or interference depth; the
// assembly animation (each part along its path_mm in step order) on the time slider; entry_opening
// and spray_reach results as numbers.
window.HV = window.HV || {};
HV.fields = HV.fields || {};
HV.fields.assembly = {
  label: 'Assembly',
  async init(D, ctx) {
    const beams = await ctx.addBeams();
    const boxes = ctx.addParts(D.parts, { opacity: 0.6 });
    const steps = D.steps, nS = steps.length, FR = 8;          // FR animation frames per step
    const stepOf = D.parts.map(() => -1);
    steps.forEach((s, k) => { if (s.pi >= 0) stepOf[s.pi] = k; });
    const STATUS = { ok: [[0.35, 0.65, 0.4], 'ok'], blocked: [[0.85, 0.1, 0.1], 'blocked (interference on the way)'],
                     no_tool: [[0.95, 0.6, 0.1], 'no tool clearance'], no_reach: [[0.55, 0.2, 0.75], 'out of hand reach'] };
    const GREY = [0.75, 0.75, 0.78];
    // interference depth per part and member (largest pair)
    const depthPart = D.parts.map(() => 0), depthMember = D.frame.member_ids.map(() => 0);
    let dmax = 0;
    for (const it of D.interferences) {
      dmax = Math.max(dmax, it.depth);
      for (const e of it.ends) {
        if (e.kind === 'part' && e.index >= 0) depthPart[e.index] = Math.max(depthPart[e.index], it.depth);
        if (e.kind === 'member' && e.index >= 0) depthMember[e.index] = Math.max(depthMember[e.index], it.depth);
      }
    }
    const S = { i: nS * FR, mode: 'order', others: false, log: false };
    const spec = () => {
      if (S.mode === 'order') return { min: 1, max: Math.max(2, nS), log: false, unit: '', title: 'assembly step (order)', note: 'grey: not in the sequence' };
      if (S.mode === 'depth') return { min: S.log ? 0.1 : 0, max: Math.max(dmax, 1), log: S.log, unit: 'mm', title: 'interference depth (largest pair)',
                                       note: D.interferences.length ? `${D.interferences.length} pairs; grey: none` : 'no interference pairs in this record' };
      return null;
    };
    function colours() {
      const c = spec();
      D.parts.forEach((p, i) => {
        const k = stepOf[i];
        let col = GREY;
        if (S.mode === 'order' && k >= 0) col = HV.colour(c, k + 1);
        else if (S.mode === 'status' && k >= 0) col = STATUS[steps[k].status][0];
        else if (S.mode === 'depth' && depthPart[i] > 0) col = HV.colour(c, depthPart[i]);
        boxes.colour(i, col);
      });
      beams.colourMembers(m => S.mode === 'depth' && depthMember[m] > 0 ? HV.colour(c, depthMember[m]) : [0.55, 0.55, 0.58]);
    }
    function along(path, f) {   // point at fraction f of the polyline
      if (path.length < 2) return path[0] || [0, 0, 0];
      const L = []; let tot = 0;
      for (let j = 1; j < path.length; j++) { const d = Math.hypot(...[0, 1, 2].map(a => path[j][a] - path[j - 1][a])); L.push(d); tot += d; }
      let s = f * tot;
      for (let j = 1; j < path.length; j++) {
        if (s <= L[j - 1] || j === path.length - 1) { const u = L[j - 1] > 0 ? Math.min(1, s / L[j - 1]) : 1; return [0, 1, 2].map(a => path[j - 1][a] + u * (path[j][a] - path[j - 1][a])); }
        s -= L[j - 1];
      }
      return path[path.length - 1];
    }
    function pose() {
      const k = Math.min(nS, Math.floor(S.i / FR)), f = (S.i % FR) / FR;   // k = steps done; step k moves at fraction f
      D.parts.forEach((p, i) => {
        const s = stepOf[i];
        if (s < 0) { boxes.show[i] = S.others; boxes.off[i] = [0, 0, 0]; return; }
        if (s < k) { boxes.show[i] = true; boxes.off[i] = [0, 0, 0]; return; }
        if (s > k || S.i >= nS * FR) { boxes.show[i] = S.i >= nS * FR; boxes.off[i] = [0, 0, 0]; return; }
        const st = steps[s], q = along(st.path, f);
        boxes.show[i] = true;
        boxes.off[i] = st.path.length ? [0, 1, 2].map(a => q[a] + st.off[a] - p.c[a]) : [0, 0, 0];
      });
      boxes.update();
    }
    function update() { colours(); pose(); }
    const curStep = () => Math.min(nS - 1, Math.floor(S.i / FR));
    const ctl = {
      nT: nS * FR + 1, t0: nS * FR, playMs: 45,
      timeNote: `Assembly animation: ${FR} frames per step, ${nS} steps in order; the last position shows the whole vehicle. Not a time: the record has no durations.`,
      scale: { min: 0, max: 1, step: 0.05, value: 0.6, label: 'Part box opacity', note: 'Boxes are the design\'s part boxes (centre, size, rpy).' },
      setScale(v) { boxes.mat.opacity = v; boxes.mat.needsUpdate = true; },
      setTime(i) { S.i = i; pose(); },
      setLog(on) { S.log = on; colours(); },
      timeText(i) {
        if (i >= nS * FR) return `all ${nS} steps done`;
        const s = steps[curStep()];
        return `step ${curStep() + 1} of ${nS} (order ${s.order}): ${s.part}, moving ${s.dir}` + (s.status !== 'ok' ? ` [${STATUS[s.status][1]}]` : '');
      },
      hud() {
        if (S.i >= nS * FR) {
          const n = k => steps.filter(s => s.status === k).length;
          return [`${nS} steps: ${n('ok')} ok, ${n('no_tool')} without tool clearance, ${n('blocked')} blocked, ${n('no_reach')} out of reach`,
                  `${D.interferences.length} interference pairs in the record` + (dmax > 0 ? `, deepest ${dmax.toFixed(1)} mm` : '')];
        }
        const s = steps[curStep()], cut = (t, n) => (t || '').length > n ? t.slice(0, n) + ' …' : (t || '');
        return [cut(s.text, 160), cut(s.note, 160), `fastenings (${s.fastenings.length}): ` + (s.fastenings.map(f => `${f.kind} to ${f.to}` + (f.gap !== null ? ` (tool gap ${HV.fmt(f.gap)} mm)` : '')).join('; ') || 'none')];
      },
      contour: spec,
      plot() {
        const x = steps.map((s, k) => k + 1), cur = S.i >= nS * FR ? null : curStep() + 1;
        const marks = steps.map((s, k) => s.status === 'ok' ? null : { x: k + 1, col: s.status === 'no_tool' ? '#e90' : '#c00', dash: [1, 2] }).filter(Boolean);
        return [{ title: 'smallest tool gap at the step\'s fastenings (click: go to the step; dotted: steps not ok)', unit: 'mm', xlabel: 'step',
                  x, ys: [{ v: steps.map(s => s.gap_min === null ? 0 : s.gap_min), col: '#1f4e9c' }], marks, cursor: cur }];
      },
      plotClick(_, xv) { return Math.max(0, Math.min(nS - 1, Math.round(xv) - 1)) * FR + FR - 1; },
      pickables: () => [boxes.mesh, beams.mesh],
      info(hit) {
        if (hit.object === beams.mesh) {
          const m = beams.memberOf(hit), pairs = D.interferences.filter(it => it.ends.some(e => e.kind === 'member' && e.index === m));
          return `<b>${HV.esc(D.frame.member_ids[m])}</b> ${HV.esc(D.frame.member_names[m])}` +
                 pairs.map(it => `<br>interference with ${HV.esc(it.a)} / ${HV.esc(it.b)}: ${it.depth.toFixed(1)} mm (${HV.esc(it.state)} ${HV.esc(it.condition)})`).join('');
        }
        const i = hit.instanceId, p = D.parts[i], k = stepOf[i];
        const pairs = D.interferences.filter(it => it.ends.some(e => e.kind === 'part' && e.index === i));
        return `<b>${HV.esc(p.id)}</b> ${HV.esc(p.name)} (${HV.esc(p.kind)}, module ${HV.esc(p.module)})` +
               (k >= 0 ? `<br>step ${k + 1} (order ${steps[k].order}), ${HV.esc(STATUS[steps[k].status][1])}, moves ${HV.esc(steps[k].dir)}<br>${HV.esc(steps[k].text)}` : '<br>not in the assembly sequence') +
               pairs.map(it => `<br>interference ${HV.esc(it.a)} / ${HV.esc(it.b)}: ${it.depth.toFixed(1)} mm (${HV.esc(it.state)} ${HV.esc(it.condition)})`).join('');
      },
      extraInputs() {
        let h = '';
        for (const c of D.checks || []) {
          h += `<div class="h" style="margin-top:6px">${HV.esc(c.tool)} ${HV.esc(c.load_case_id)} (${HV.esc(c.analysis_id)})</div><table>` +
               c.results.map(r => `<tr><td>${HV.esc(r.q)}</td><td>${HV.fmt(r.v)} ${HV.esc(r.u)}</td><td class="muted">${HV.esc(r.where)}</td></tr>`).join('') + '</table>';
        }
        if (D.interferences.length) {
          h += `<div class="h" style="margin-top:6px">Interference pairs</div><table>` +
               D.interferences.map(it => `<tr><td>${HV.esc(it.a)} / ${HV.esc(it.b)}</td><td>${it.depth.toFixed(1)} mm</td><td class="muted">${HV.esc(it.state)} ${HV.esc(it.condition)}</td></tr>`).join('') + '</table>';
        }
        return h;
      },
      panel(div) {
        const h = document.createElement('h3'); h.textContent = 'Assembly'; div.appendChild(h);
        const row = document.createElement('div'); row.className = 'row';
        row.append('colour by ');
        const sel = document.createElement('select'); sel.id = 'asmMode';
        for (const [v, t] of [['order', 'step order'], ['status', 'status'], ['depth', 'interference depth']]) { const o = document.createElement('option'); o.value = v; o.textContent = t; sel.appendChild(o); }
        sel.onchange = () => { S.mode = sel.value; colours(); boxes.update(); HV.refresh(); };
        row.appendChild(sel); div.appendChild(row);
        const r2 = document.createElement('div'); r2.className = 'row';
        const cb = document.createElement('input'); cb.type = 'checkbox'; cb.id = 'asmOthers';
        cb.onchange = () => { S.others = cb.checked; pose(); HV.refresh(); };
        const lb = document.createElement('label'); lb.append(cb, ' parts not in the sequence (occupants, cargo, ...)'); r2.appendChild(lb); div.appendChild(r2);
        const leg = document.createElement('div'); leg.className = 'muted';
        leg.textContent = 'status colours: green ok, orange no tool clearance, red blocked, purple out of reach, grey not in the sequence';
        div.appendChild(leg);
      },
      selectStep: k => ctl.plotClick(0, k + 1),
    };
    update();
    return ctl;
  },
};

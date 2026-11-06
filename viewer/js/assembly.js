// Assembly (H1/H3/H6/H7): part boxes coloured by step order, status or interference depth; the
// assembly animation (each part along its path_mm in step order) on the time slider; entry_opening
// and spray_reach results as numbers.
window.HV = window.HV || {};
HV.fields = HV.fields || {};
HV.fields.assembly = {
  label: '組み立て',
  async init(D, ctx) {
    const beams = await ctx.addBeams();
    const boxes = ctx.addParts(D.parts, { opacity: 0.6 });
    const steps = D.steps, nS = steps.length, FR = 8;          // FR animation frames per step
    const stepOf = D.parts.map(() => -1);
    steps.forEach((s, k) => { if (s.pi >= 0) stepOf[s.pi] = k; });
    const STATUS = { ok: [[0.35, 0.65, 0.4], 'OK'], blocked: [[0.85, 0.1, 0.1], 'NG: 途中で干渉'],
                     no_tool: [[0.95, 0.6, 0.1], 'NG: 工具が入らない'], no_reach: [[0.55, 0.2, 0.75], 'NG: 手が届かない'] };
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
      if (S.mode === 'order') return { min: 1, max: Math.max(2, nS), log: false, unit: '', title: '組み立ての順番', note: '灰色 = 手順にない' };
      if (S.mode === 'depth') return { min: S.log ? 0.1 : 0, max: Math.max(dmax, 1), log: S.log, unit: 'mm', title: '干渉の深さ (最大の組)',
                                       note: D.interferences.length ? `${D.interferences.length} 組、灰色 = なし` : '干渉の組はない' };
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
      timeNote: `手順の順に ${nS} 手 (時間ではない)。右端 = 全部を組んだ姿`,
      partMode: 'field',
      scale: { min: 0, max: 1, step: 0.05, value: 0.6, label: '部品の箱の不透明度', note: '' },
      setScale(v) { boxes.mat.opacity = v; boxes.mat.needsUpdate = true; },
      setTime(i) { S.i = i; update(); },
      setLog(on) { S.log = on; colours(); },
      timeText(i) {
        if (i >= nS * FR) return `全 ${nS} 手を組んだ姿`;
        const s = steps[curStep()];
        return `${curStep() + 1} / ${nS} 手目: ${s.part} を ${s.dir} へ   判定 ${STATUS[s.status][1]}`;
      },
      hud() {
        const n = k => steps.filter(s => s.status === k).length;
        const s = S.i >= nS * FR ? null : steps[curStep()];
        return [`判定: OK ${n('ok')} 手、NG ${nS - n('ok')} 手 (工具 ${n('no_tool')}、干渉 ${n('blocked')}、手 ${n('no_reach')})   干渉の組 ${D.interferences.length}` +
                  (dmax > 0 ? ` (最深 ${dmax.toFixed(1)} mm)` : ''),
                s ? `締結 ${s.fastenings.length} か所` + (s.gap_min !== null ? `、工具のすき間 最小 ${HV.fmt(s.gap_min)} mm` : '') + '   動きは実寸' : '動きは実寸 (倍率なし)'];
      },
      contour: spec,
      plot() {
        const x = steps.map((s, k) => k + 1), cur = S.i >= nS * FR ? null : curStep() + 1;
        const marks = steps.map((s, k) => s.status === 'ok' ? null : { x: k + 1, col: s.status === 'no_tool' ? '#e90' : '#c00', dash: [1, 2] }).filter(Boolean);
        return [{ title: '締結の工具のすき間 最小 (クリックでその手へ、点線 = NG の手)', unit: 'mm', xlabel: '手',
                  x, ys: [{ v: steps.map(s => s.gap_min === null ? 0 : s.gap_min), col: '#1f4e9c' }], marks, cursor: cur }];
      },
      plotClick(_, xv) { return Math.max(0, Math.min(nS - 1, Math.round(xv) - 1)) * FR + FR - 1; },
      pickables: () => [boxes.mesh, beams.mesh],
      info(hit) {
        if (hit.object === beams.mesh) {
          const m = beams.memberOf(hit), pairs = D.interferences.filter(it => it.ends.some(e => e.kind === 'member' && e.index === m));
          return `<b>${HV.esc(D.frame.member_ids[m])}</b> ${HV.esc(D.frame.member_names[m])}` +
                 pairs.map(it => `<br>干渉 ${HV.esc(it.a)} / ${HV.esc(it.b)}: ${it.depth.toFixed(1)} mm`).join('');
        }
        const i = hit.instanceId, p = D.parts[i], k = stepOf[i];
        const pairs = D.interferences.filter(it => it.ends.some(e => e.kind === 'part' && e.index === i));
        return `<b>${HV.esc(p.id)}</b> ${HV.esc(p.name)}` +
               (k >= 0 ? `<br>${k + 1} 手目、${HV.esc(steps[k].dir)} へ、判定 ${HV.esc(STATUS[steps[k].status][1])}` +
                         (steps[k].text ? `<details><summary>手順の説明</summary>${HV.esc(steps[k].text)}</details>` : '') : '<br>手順にない') +
               pairs.map(it => `<br>干渉 ${HV.esc(it.a)} / ${HV.esc(it.b)}: ${it.depth.toFixed(1)} mm`).join('');
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
        const h = document.createElement('h3'); h.textContent = '組み立て'; div.appendChild(h);
        const row = document.createElement('div'); row.className = 'row';
        row.append('色 (この分野の色のとき): ');
        const sel = document.createElement('select'); sel.id = 'asmMode';
        for (const [v, t] of [['order', '順番'], ['status', '判定'], ['depth', '干渉の深さ']]) { const o = document.createElement('option'); o.value = v; o.textContent = t; sel.appendChild(o); }
        sel.onchange = () => { S.mode = sel.value; colours(); boxes.update(); HV.refresh(); };
        row.appendChild(sel); div.appendChild(row);
        const r2 = document.createElement('div'); r2.className = 'row';
        const cb = document.createElement('input'); cb.type = 'checkbox'; cb.id = 'asmOthers';
        cb.onchange = () => { S.others = cb.checked; pose(); HV.refresh(); };
        const lb = document.createElement('label'); lb.append(cb, ' 手順にない部品も出す'); r2.appendChild(lb); div.appendChild(r2);
        const leg = document.createElement('div'); leg.className = 'muted';
        leg.textContent = '判定の色: 緑 OK、橙 工具 NG、赤 干渉 NG、紫 手 NG、灰 手順にない';
        div.appendChild(leg);
      },
      selectStep: k => ctl.plotClick(0, k + 1),
    };
    update();
    return ctl;
  },
};

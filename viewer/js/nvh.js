// NVH (modal): frame beams moved by a mode shape, x = x0 + scale * shape / |shape|max * sin(phase),
// coloured by member strain / the mode's largest (src/nvh.py member_strain) or, switched, |shape| / |shape|max;
// mode list with the top strain-energy members; frequency response plot.
window.HV = window.HV || {};
HV.fields = HV.fields || {};
HV.fields.nvh = {
  label: 'NVH (固有振動)',
  async init(D, ctx) {
    const beams = await ctx.addBeams();
    const nodes0 = beams.nodes0, n = nodes0.length / 3;
    const shapes = await Promise.all(D.modes.map(m => HV.unpack(m.shape)));
    // each part's rigid motion per mode (src/nvh.py part_motion): fit centre, translation, rotation per unit shape
    const pmot = await Promise.all(D.modes.map(async m => m.parts ? { c: await HV.unpack(m.parts.c), t: await HV.unpack(m.parts.t), w: await HV.unpack(m.parts.w), rel: m.parts.rel } : null));
    const boxes = ctx.addParts(D.parts, { opacity: 0.22 });
    const wheels = (D.wheels || []).length ? new HV.Wheels(ctx.view, D.wheels) : null;
    if (wheels) ctx.root.add(wheels.group);
    const frf = D.frf ? { x: await HV.unpack(D.frf.x), y: await HV.unpack(D.frf.y) } : null;
    const NT = 48;
    const S = { mode: 0, scale: 150, t: 0, log: false, by: 'strain' };
    const v3 = (a, j) => [a[3 * j], a[3 * j + 1], a[3 * j + 2]];
    function moveParts(k) {   // k: mm per unit of the shape now
      const P = pmot[S.mode];
      for (let j = 0; j < D.parts.length; j++) {
        if (!P) { boxes.off[j] = [0, 0, 0]; boxes.rot[j] = null; continue; }
        const pose = HV.rigidPose(D.parts[j].c, v3(P.c, j), v3(P.t, j), v3(P.w, j), k);
        boxes.off[j] = pose.off; boxes.rot[j] = pose.q;
        if (ctx.partMode === 'field') boxes.colour(j, HV.turbo(P.rel[j]));
      }
      boxes.update();
      if (wheels) { const u = D.modes[S.mode].wheels || []; D.wheels.forEach((_, i) => wheels.set(i, (u[i] || [0, 0, 0]).map(c => c * k))); }
    }
    const cur = new Float32Array(nodes0.length);
    const amp = i => { const s = shapes[S.mode], a = D.modes[S.mode].amp_max || 1; return Math.hypot(s[3 * i], s[3 * i + 1], s[3 * i + 2]) / a; };
    const rel = m => { const st = D.modes[S.mode].strain; return st.max > 0 ? st.total[m] / st.max : 0; };
    const spec = () => S.by === 'strain'
      ? { min: S.log ? 1e-3 : 0, max: 1, log: S.log, unit: '1', title: '部材のひずみ (軸 + 曲げ)、モード内の最大 = 1', note: '相対値' }
      : { min: S.log ? 1e-3 : 0, max: 1, log: S.log, unit: '1', title: '節点の変位 |u| / |u|max', note: '相対値' };
    function update() {
      const s = shapes[S.mode], a = D.modes[S.mode].amp_max || 1, f = S.scale / a * Math.sin(2 * Math.PI * S.t / NT);
      for (let i = 0; i < nodes0.length; i++) cur[i] = nodes0[i] + f * s[i];
      beams.setNodes(cur);
      moveParts(f);
      const c = spec();
      if (S.by === 'strain') beams.colourMembers(m => HV.colour(c, rel(m)));
      else beams.colourNodes(i => HV.colour(c, amp(i)));
    }
    const ctl = {
      nT: NT, t0: Math.round(NT / 4), playMs: 35, partMode: 'category',
      timeNote: 'モードの位相 (1 周期、時間ではない)',
      scale: { min: 0, max: 600, step: 5, value: S.scale, label: '変形の表示 (最大点の mm)', note: '実寸ではない' },
      setScale(v) { S.scale = v; update(); },
      setTime(i) { S.t = i; update(); },
      setLog(on) { S.log = on; update(); },
      timeText(i) { return `モード ${S.mode + 1}: ${D.modes[S.mode].f_Hz.toFixed(2)} Hz   位相 ${Math.round(360 * i / NT)}°`; },
      hud() {
        const m = D.modes[S.mode];
        return [`変形: 最大点を ${S.scale} mm で表示 (実寸ではない、相対値)   部品とタイヤは載っている節点と一緒に動く`,
                `色: ${S.by === 'strain' ? '部材のひずみ (モード内の最大 = 1)' : '節点の変位 |u|/|u|max'}` +
                (ctx.partMode === 'field' ? '   部品: 動きの大きさ (部品の最大 = 1)' : '') +
                (m.share_bending !== null && m.share_bending !== undefined ? `   フレームが曲がる割合 ${(100 * m.share_bending).toFixed(0)} %` : '')];
      },
      contour: spec,
      plot() {
        if (!frf) return [];
        const marks = D.modes.map((m, i) => ({ x: m.f_Hz, label: `mode ${i + 1}`, col: i === S.mode ? '#d00' : '#999', w: i === S.mode ? 2 : 1, dash: i === S.mode ? [] : [3, 3] }))
          .concat((D.lines || []).map(l => ({ x: l.f_Hz, label: l.label, col: '#2a6', dash: [6, 3] })));
        return [{ title: `周波数応答 ${D.frf.channel} (クリックで近いモード)`, unit: D.frf.y_unit, xlabel: `周波数 [${D.frf.x_unit}]`,
                  x: Array.from(frf.x), ys: [{ name: '', v: Array.from(frf.y), col: '#1f4e9c' }], marks, cursor: null, logy: true }];
      },
      plotClick(_, x) { let b = 0, bd = Infinity; D.modes.forEach((m, i) => { const d = Math.abs(m.f_Hz - x); if (d < bd) { bd = d; b = i; } }); pick(b); },
      pickables: () => [beams.mesh, boxes.mesh],
      info(hit) {
        if (hit.object === boxes.mesh) {
          const j = hit.instanceId, p = D.parts[j], P = pmot[S.mode], ns = (D.part_nodes || [])[j] || [];
          return `<b>${HV.esc(p.id)}</b> ${HV.esc(p.name)}<br>` + (ns.length
            ? `動き: 節点 ${ns.slice(0, 6).map(HV.esc).join(', ')}${ns.length > 6 ? ' ほか' : ''} と一緒 (${ns.length > 1 ? '平均の並進 + 剛体回転' : '並進'})` +
              (P ? `、大きさ ${P.rel[j].toFixed(2)} (部品の最大 = 1)` : '')
            : '動かない (計算に質量がない)');
        }
        const m = beams.memberOf(hit), [a, b] = D.frame.members[m], ids = D.frame.node_ids, md = D.modes[S.mode], st = md.strain, sh = md.energy.share[m];
        return `<b>${HV.esc(D.frame.member_ids[m])}</b> ${HV.esc(D.frame.member_names[m])}<br>節点 ${HV.esc(ids[a])} (|u|/|u|max ${amp(a).toFixed(3)}), ` +
               `${HV.esc(ids[b])} (${amp(b).toFixed(3)})<br>ひずみ (相対) ${rel(m).toFixed(3)} ` +
               `(軸 ${st.max > 0 ? (st.axial[m] / st.max).toFixed(3) : '-'}、曲げ ${st.max > 0 ? (st.bending[m] / st.max).toFixed(3) : '-'})<br>` +
               `ひずみエネルギーの割合 ${sh === null ? '断面不明' : (100 * sh).toFixed(2) + ' %'}`;
      },
      extraInputs() { return `<details><summary>ひずみの求め方・応答の説明</summary><div class="muted">${HV.esc(D.strain_method)}</div>` +
                             (D.frf && D.frf.note ? `<div class="muted">${HV.esc(D.frf.note)}</div>` : '') + '</details>'; },
      panel(div) {
        const h = document.createElement('h3'); h.textContent = 'モード'; div.appendChild(h);
        const list = document.createElement('div'); list.className = 'list'; list.id = 'modelist';
        D.modes.forEach((m, i) => { const d = document.createElement('div'); d.textContent = `${i + 1}. ${m.f_Hz.toFixed(2)} Hz  ${m.label}`; d.onclick = () => pick(i); list.appendChild(d); });
        div.appendChild(list);
        const h2 = document.createElement('h3'); h2.textContent = '色の量'; div.appendChild(h2);
        for (const [k, lab] of [['strain', '部材のひずみ (相対)'], ['disp', '節点の変位 |u|/|u|max']]) {
          const l = document.createElement('label'); l.style.display = 'block';
          const r = document.createElement('input'); r.type = 'radio'; r.name = 'nvhcol'; r.id = 'nvhcol_' + k; r.checked = S.by === k;
          r.onchange = () => { S.by = k; update(); HV.refresh(); };
          l.append(r, ' ' + lab); div.appendChild(l);
        }
        const h3 = document.createElement('h3'); h3.textContent = 'ひずみエネルギーの多い部材'; div.appendChild(h3);
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
        d.textContent = `${j + 1}. ${x.member}: ${(100 * x.share).toFixed(1)} % (うち軸 ${(100 * x.axial_part).toFixed(0)} %)`; return d; }));
    }
    function pick(i) { S.mode = i; mark(); update(); HV.refresh(); }
    update();
    return ctl;
  },
};

// Thermal (T1-T3, K3's RC network; viewer/src/thermal.py): the part boxes of each network node's kind coloured by
// that node's temperature at the time of the slider (temp.<node> from the record); the frame grey (not in the
// network). Plots: the temperatures against their limits, the selected node's loss and heat to the air, the power
// through the chain (and the battery's power and charge from drive_energy when the run has it). The side panel draws
// the network as the tool builds it (each node: C, R to the air).
window.HV = window.HV || {};
HV.fields = HV.fields || {};
HV.LEDGER_COL.heat_node = [0.85, 0.2, 0.15];
HV.LEDGER_COL.power_stage = [0.95, 0.65, 0.1];
HV.interpAt = function (xs, ys, x) {   // linear, clamped; xs ascending
  if (x <= xs[0]) return ys[0];
  const n = xs.length; if (x >= xs[n - 1]) return ys[n - 1];
  let lo = 0, hi = n - 1;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (xs[m] <= x) lo = m; else hi = m; }
  return ys[lo] + (ys[hi] - ys[lo]) * (x - xs[lo]) / (xs[hi] - xs[lo]);
};
HV.fields.thermal = {
  label: '熱',
  async init(D, ctx) {
    const beams = await ctx.addBeams();
    beams.colourMembers(() => [0.72, 0.72, 0.74]);
    const boxes = ctx.addParts(D.parts, { opacity: 0.55 });
    const t = await HV.unpack(D.t), nT = t.length, tx = Array.from(t);
    const N = D.nodes, temp = {};
    for (const n of N) temp[n.name] = n.temp ? await HV.unpack(n.temp) : null;
    const chain = [];
    for (const s of D.chain) chain.push({ ...s, tv: await HV.unpack(s.t), vv: await HV.unpack(s.values) });
    const E = D.energy;
    const en = E ? { t: Array.from(await HV.unpack(E.t)), speed: await HV.unpack(E.speed), soc: E.soc ? await HV.unpack(E.soc) : null,
                     pb: E.power_battery ? await HV.unpack(E.power_battery) : null } : null;
    const JA = { battery: '電池', motor: 'モーター', controller: '制御器', reducer: '減速機', wheel: '駆動輪' };
    const ja = n => JA[n] || n;
    // the colour runs from the air to the highest temperature of the run (the limits are marked where they fall in it)
    const tmax = Math.max(...N.map(n => n.max ?? D.ambient_C));
    const S = { k: nT - 1, sel: N[0].name };
    const spec = () => ({ min: D.ambient_C, max: Math.max(tmax, D.ambient_C + 1), log: false, unit: '℃', title: '部品の温度 (熱回路の節点)',
                          marks: N.filter(n => n.limit).map(n => ({ v: n.limit.value, label: `${ja(n.name)} 上限` })), note: `下端 = 気温 ${HV.fmt(D.ambient_C)} ℃、上端 = 走行中の最高` });
    const nowT = n => temp[n] ? temp[n][S.k] : null;
    function update() {
      const c = spec();
      D.parts.forEach((p, i) => { const n = D.part_node[i]; boxes.colour(i, n && temp[n] ? HV.colour(c, nowT(n)) : [0.95, 0.95, 0.96]); });
      boxes.update();
      if (S.draw) S.draw();
    }
    const okng = v => v === null || v === undefined ? '' : (v ? 'OK' : 'NG');
    const at = (s, x) => HV.interpAt(s.tv, s.vv, x);
    const PCOL = { battery: '#c0392b', motor: '#1f4e9c', controller: '#0a6b2a', reducer: '#8e44ad', wheel: '#a05a00' };
    const ctl = {
      nT, t0: nT - 1, playMs: 30, partMode: 'field', viewDir: [0.9, -1.05, 0.75],
      timeNote: `記録の ${nT} 時刻 (走行 ${HV.fmt(tx[nT - 1] / 60)} 分)。再生は 1 こま 1 時刻`,
      scale: { min: 1, max: 1, step: 1, value: 1, label: '変形なし', note: '熱回路は形を持たない (部品の箱は動かない)' },
      setScale() {},
      setTime(i) { S.k = i; update(); },
      timeText(k) { return `t = ${HV.fmt(t[k] / 60)} 分   ` + N.map(n => `${ja(n.name)} ${HV.fmt(nowT(n.name))} ℃`).join('   '); },
      hud() {
        const c = D.criterion;
        return [(c ? `判定 ${c.quantity} ${HV.fmt(c.value)} ${c.unit} ${c.op} ${HV.fmt(c.limit)} ${c.unit}: ${okng(c.ok)}   ` : '') +
                N.map(n => `${ja(n.name)} 最高 ${HV.fmt(n.max)} ℃` + (n.limit ? ` / 上限 ${HV.fmt(n.limit.value)} ${okng(n.ok)}` : '')).join('   '),
                `色: 部品の温度 (その種類の熱回路の節点)。気温 ${HV.fmt(D.ambient_C)} ℃`];
      },
      contour: spec,
      plot() {
        const c = t[S.k], P = [];
        const ys = N.filter(n => temp[n.name]).map(n => ({ name: ja(n.name), v: Array.from(temp[n.name]), col: PCOL[n.name] }));
        for (const n of N) if (n.limit && n.limit.value <= tmax * 1.3) ys.push({ name: `${ja(n.name)} 上限`, v: tx.map(() => n.limit.value), col: PCOL[n.name], w: 0.8 });
        P.push({ title: '温度 (細線 = 上限)', unit: '℃', xlabel: 't [s]', x: tx, ys, marks: [], cursor: c });
        const loss = chain.filter(s => s.stage === S.sel && (s.kind === 'loss' || s.kind === 'heat_to_air'));
        if (loss.length) {
          const sum = kind => tx.map(x => loss.filter(s => s.kind === kind).reduce((a, s) => a + at(s, x), 0));
          P.push({ title: `${ja(S.sel)}: 損失と空気への放熱`, unit: 'W', xlabel: 't [s]', x: tx,
                   ys: [{ name: '損失', v: sum('loss'), col: '#c0392b' }, { name: '放熱', v: sum('heat_to_air'), col: '#1f4e9c' }], marks: [], cursor: c });
        }
        const pw = ['controller', 'motor', 'reducer', 'wheel'].map(st => ({ st, ss: chain.filter(s => s.kind === 'power' && s.stage === st) })).filter(o => o.ss.length);
        if (pw.length) P.push({ title: '電力の流れ (各段の入力)', unit: 'kW', xlabel: 't [s]', x: tx,
          ys: pw.map(o => ({ name: ja(o.st), v: tx.map(x => o.ss.reduce((a, s) => a + at(s, x), 0) / 1000), col: PCOL[o.st] })), marks: [], cursor: c });
        if (en && en.soc) P.push({ title: '電池の残量 (drive_energy)', unit: '%', xlabel: 't [s]', x: en.t, ys: [{ v: Array.from(en.soc), col: '#0a6b2a' }], marks: [], cursor: c });
        return P;
      },
      plotClick(_, tv) { let b = 0, bd = Infinity; t.forEach((v, i) => { const d = Math.abs(v - tv); if (d < bd) { bd = d; b = i; } }); return b; },
      pickables: () => [beams.mesh, boxes.mesh],
      info(hit) {
        if (hit.object !== boxes.mesh) return `<b>${HV.esc(D.frame.member_ids[beams.memberOf(hit)])}</b><br>フレームは熱回路に入らない (車両の質量だけ)`;
        const i = hit.instanceId, p = D.parts[i], nn = D.part_node[i], n = N.find(x => x.name === nn);
        const ser = chain.filter(s => s.part_id === p.id);
        if (n) { S.sel = n.name; setTimeout(HV.refresh, 0); }
        return `<b>${HV.esc(p.id)}</b> ${HV.esc(p.name)}<br>` +
          (n ? `節点 ${ja(n.name)}: いま ${HV.fmt(nowT(n.name))} ℃、最高 ${HV.fmt(n.max)} ℃` + (n.limit ? `、上限 ${HV.fmt(n.limit.value)} ℃ ${okng(n.ok)}` : '') + '<br>' : '熱回路の節点ではない<br>') +
          ser.map(s => `${HV.esc(s.channel)} いま ${HV.fmt(at(s, t[S.k]))} ${HV.esc(s.unit)}`).join('<br>');
      },
      extraInputs() {
        const r = (E && E.results) || {};
        return `<div class="h" style="margin-top:4px">結果</div><table>` +
          N.map(n => `<tr><td>${ja(n.name)}</td><td>最高 ${HV.fmt(n.max)} ℃、終わり ${HV.fmt(n.end)} ℃、平均の損失 ${HV.fmt(n.loss_mean_W)} W</td><td>${n.limit ? `上限 ${HV.fmt(n.limit.value)} <span class="${n.ok ? 'ok' : 'ng'}">${okng(n.ok)}</span>` : ''}</td></tr>`).join('') +
          (E ? `<tr><td>電池 (P4)</td><td>使った電力量 ${HV.fmt((r.energy_used || [])[0])} Wh、残量 ${HV.fmt((r.soc_end || [])[0])} %、最大 ${HV.fmt((r.max_battery_power || [])[0])} kW</td><td class="muted">${HV.esc(E.source)}</td></tr>` : '') +
          '</table>';
      },
      panel(div) {
        const h = document.createElement('h3'); h.textContent = '熱回路 (ツールが組む形)'; div.appendChild(h);
        const cv = document.createElement('canvas'); cv.id = 'rcnet'; cv.width = 300; cv.height = 40 + 62 * N.length; div.appendChild(cv);
        const note = document.createElement('div'); note.className = 'muted'; note.textContent = '各節点: 熱容量 C、空気への熱抵抗 R (τ = R C)。節点をクリックで損失のグラフを切替';
        div.appendChild(note);
        cv.onclick = e => { const r = cv.getBoundingClientRect(), j = Math.floor((e.clientY - r.top - 10) / 62); if (N[j]) { S.sel = N[j].name; HV.refresh(); } };
        S.draw = () => {
          const g = cv.getContext('2d'), c = spec(); g.clearRect(0, 0, cv.width, cv.height);
          g.font = '11px system-ui, sans-serif';
          const xa = 250;   // the air: a line on the right
          g.strokeStyle = '#1f4e9c'; g.lineWidth = 3; g.beginPath(); g.moveTo(xa, 8); g.lineTo(xa, cv.height - 26); g.stroke(); g.lineWidth = 1;
          g.fillStyle = '#1f4e9c'; g.fillText(`空気 ${HV.fmt(D.ambient_C)} ℃`, xa - 40, cv.height - 10);
          N.forEach((n, j) => {
            const y = 14 + 62 * j, col = temp[n.name] ? HV.colour(c, nowT(n.name)) : [0.8, 0.8, 0.8];
            g.fillStyle = `rgb(${col.map(v => Math.round(255 * v)).join(',')})`; g.fillRect(4, y, 112, 44);
            g.strokeStyle = n.name === S.sel ? '#000' : '#666'; g.lineWidth = n.name === S.sel ? 2 : 1; g.strokeRect(4, y, 112, 44); g.lineWidth = 1;
            g.fillStyle = '#000'; g.font = 'bold 11px system-ui, sans-serif'; g.fillText(`${ja(n.name)} ${HV.fmt(nowT(n.name))} ℃`, 8, y + 14);
            g.font = '10px system-ui, sans-serif'; g.fillText(`C ${HV.fmt(n.C_J_per_K)} J/K`, 8, y + 27); g.fillText(`部品 ${n.parts.length} 個`, 8, y + 39);
            // the resistor to the air (zigzag)
            const ym = y + 22, x0 = 116, x1 = xa; g.strokeStyle = '#333'; g.beginPath(); g.moveTo(x0, ym); g.lineTo(150, ym);
            for (let k = 0; k <= 8; k++) g.lineTo(150 + k * 7, ym + (k % 2 ? -6 : 6) * (k > 0 && k < 8 ? 1 : 0));
            g.lineTo(x1, ym); g.stroke();
            g.fillStyle = '#333'; g.fillText(`R ${HV.fmt(n.R_K_per_W)} K/W  τ ${HV.fmt(n.tau_s)} s`, 122, ym - 9);
            for (const [o, gg] of n.G_to) g.fillText(`↔ ${ja(o)} ${HV.fmt(1 / gg)} K/W`, 122, ym + 18);
          });
        };
        S.draw();
      },
    };
    update();
    return ctl;
  },
};

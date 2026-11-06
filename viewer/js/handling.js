// Handling and stability (viewer/src/handling.py): the K2 replay of tilt (S1), braking (S4), lane change (S3)
// or the rough side slope (S5), one case at a time (D.cases), with tyres coloured by wheel load or friction use.
window.HV = window.HV || {};
HV.fields = HV.fields || {};
HV.fields.handling = {
  label: HV.FIELD_LABEL.handling,   // js/names.js: the contract's DOMAIN_NAMES_JA
  async init(D, ctx) {
    HV.K2.useCase('handling', D);
    const R = await HV.K2.Replay.make(D, ctx);
    if (D.rollover) return HV.K2.rollover(D, ctx, R);
    return HV.K2.controller('handling', D, R, {
      plots(R, c) {
        const ang = D.tool === 'tilt' ? ['table_angle', '台の角度'] : D.tool === 'offroad_roll' ? ['slope_angle', '斜面の角度'] : null;
        const P = [R.panelOf([['roll', 'ロール'], ...(ang ? [ang] : [])], 'ロール角' + (ang ? 'と' + ang[1] : ''), 'deg', c),
                   R.wheelPanel('wheel_load', '輪の荷重', 'N', c)];
        if (D.tool === 'braking') P.push(R.panelOf([['accel_x', '前後']], '前後の加速度', 'm/s²', c), R.panelOf([['speed', '速さ']], '速さ', 'km/h', c));
        else if (D.tool === 'tilt') P.push(R.wheelPanel('friction_use', '摩擦の使用', '', c));
        else P.push(R.panelOf([['accel_y', '横']], '横の加速度', 'm/s²', c), R.pathPanel());
        return P;
      },
    });
  },
};

// K2's rollover record (viewer/src/handling.py rollover_case): the K2 replay in the table's frame turned by the
// table angle, the record's floor, each contact point's force (arrow, kN label, the point glowing while it
// touches), the record's peak vectors at the sample nearest the peak, and the occupant boxes' least distance to
// the floor. A time window (17-23 s by default, or the whole run) and playback at 0.5x (default) or real time.
HV.K2.rollover = async function (D, ctx, R) {
  const T = window.THREE, X = D.rollover, f = HV.fields.handling, view = ctx.view;
  const whole = f.rollWin === 'all';
  let k0 = 0, k1 = R.nT - 1;
  if (!whole) {
    while (k0 < R.nT - 1 && R.t[k0] < X.window[0]) k0++;
    while (k1 > k0 && R.t[k1] > X.window[1]) k1--;
  }
  const nT = k1 - k0 + 1, kN = v => HV.fmt(v / 1000);
  const gap = await HV.unpack(X.occupant_gap), rollW = await HV.unpack(X.roll_world);
  const C = [];
  for (const c of X.contacts) C.push({ c, F: await HV.unpack(c.force), P: await HV.unpack(c.point), on: await HV.unpack(c.touch) });
  // a text label (a sprite: it turns with the view), redrawn when its text changes
  const label = (col) => {
    const cv = document.createElement('canvas'); cv.width = 1024; cv.height = 64;
    const tex = new T.CanvasTexture(cv), s = new T.Sprite(new T.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
    s.scale.set(1600, 100, 1); s.renderOrder = 10; s.center.set(0, 0.5); s.userData.text = '';
    s.setText = txt => {
      if (txt === s.userData.text) return; s.userData.text = txt;
      const g = cv.getContext('2d'); g.clearRect(0, 0, 1024, 64); g.font = 'bold 30px "BIZ UDPGothic", "Yu Gothic", sans-serif';
      const w = Math.min(1020, g.measureText(txt).width + 20);
      g.fillStyle = 'rgba(255,253,248,0.92)'; g.strokeStyle = col; g.lineWidth = 4; g.fillRect(2, 8, w, 48); g.strokeRect(2, 8, w, 48);
      g.fillStyle = '#1d1d1f'; g.textBaseline = 'middle'; g.fillText(txt, 12, 33); tex.needsUpdate = true;
    };
    ctx.root.add(s); return s;
  };
  const ORANGE = '#e9893a', PURPLE = '#8e6bbf';
  const arrows = R.arrows;
  const glowMat = new T.MeshBasicMaterial({ color: 0xffb020, transparent: true, opacity: 0.9, depthTest: false });
  const haloMat = new T.MeshBasicMaterial({ color: 0xffd060, transparent: true, opacity: 0.35, depthTest: false });
  for (const e of C) {
    e.arrow = arrows.add(ORANGE); e.label = label(ORANGE);
    e.glow = new T.Mesh(new T.SphereGeometry(35, 16, 12), glowMat); e.halo = new T.Mesh(new T.SphereGeometry(90, 16, 12), haloMat);
    e.glow.renderOrder = e.halo.renderOrder = 9; ctx.root.add(e.glow, e.halo);
  }
  // the record's peak: its vectors at the sample nearest the peak instant
  // the two samples around the peak instant (the series are every 40 ms)
  let kPeak = -1; if (X.peak.t !== null) { kPeak = 0; while (kPeak + 1 < R.nT && R.t[kPeak + 1] <= X.peak.t) kPeak++; }
  const isPeak = k => k === kPeak || k === kPeak + 1;
  const P = X.peak.vectors.map(v => ({ v, arrow: arrows.add(PURPLE), label: label(PURPLE) }));
  const at3 = (A, k) => [A[3 * k], A[3 * k + 1], A[3 * k + 2]];
  const draw = Rr => {
    const k = Rr.k, s = Rr.mmPerN / 10;
    for (const e of C) {
      const F = at3(e.F, k), p = at3(e.P, k), m = Math.hypot(...F), on = e.on[k] > 0.5 && m > 0;
      arrows.set(e.arrow, p, on ? F : [0, 0, 0], s);
      e.glow.visible = e.halo.visible = on; e.glow.position.set(...p); e.halo.position.set(...p);
      e.halo.scale.setScalar(0.6 + Math.min(1.6, m / 20000));
      e.label.visible = on && m * s > 40;
      if (e.label.visible) { const u = 1 / m; e.label.position.set(p[0] + F[0] * u * m * s, p[1] + F[1] * u * m * s, p[2] + F[2] * u * m * s + 60); e.label.setText(`${e.c.short} ${kN(m)} kN`); }
    }
    for (const e of P) {
      const show = isPeak(k), m = Math.hypot(...e.v.f);
      arrows.set(e.arrow, e.v.o, show ? e.v.f : [0, 0, 0], s);
      e.label.visible = show && m * s > 40;
      if (e.label.visible) { e.label.position.set(e.v.o[0] + e.v.f[0] * s, e.v.o[1] + e.v.f[1] * s, e.v.o[2] + e.v.f[2] * s + 60); e.label.setText(`${X.peak.t} s (最大の瞬間) ${kN(m)} kN`); }
    }
  };
  const clip = p => {
    if (!p || whole) return p;
    const t0 = R.t[k0], t1 = R.t[k1], idx = [];
    p.x.forEach((x, i) => { if (x >= t0 - 1e-9 && x <= t1 + 1e-9) idx.push(i); });
    return Object.assign({}, p, { x: idx.map(i => p.x[i]), ys: p.ys.map(y => Object.assign({}, y, { v: idx.map(i => y.v[i]) })) });
  };
  const tm = X.times, gmin = X.occupant_gap_min;
  const base = HV.K2.controller('handling', D, R, {
    viewDir: [1.5, -1.2, 0.55],
    update: draw,
    plots(Rr, c) {
      const ang = [R.panelOf([['roll', 'ロール (台に対して)'], ['roll_world', 'ロール (床に対して)'], ['table_angle', '台の角度']], 'ロール角と台の角度', 'deg', c),
                   R.wheelPanel('wheel_load', '輪の荷重', 'N', c)];
      const cols = ['#e9893a', '#8e6bbf', '#2b59c3', '#2f7d4f'];
      const s0 = R.plots['contact_force.' + X.contacts[0].id];
      ang.push({ title: '接触点の力 (|F|、40 ms おき)', unit: 'kN', xlabel: 't [s]', x: s0.t, cursor: c,
                 ys: X.contacts.map((ct, i) => ({ name: ct.name, v: R.plots['contact_force.' + ct.id].v, col: cols[i % cols.length] })),
                 marks: [{ x: X.peak.t, label: `記録の最大 ${kN(X.peak.N)} kN`, col: PURPLE }] });
      return ang;
    },
  });
  const ctl = Object.assign({}, base, {
    nT, t0: 0, times: Array.from(R.t.slice(k0, k1 + 1)), speed: 0.5,
    timeNote: `記録の ${R.nT} 時刻のうち ${whole ? '全体' : `${HV.fmt(R.t[k0])}–${HV.fmt(R.t[k1])} s`} (${nT} 時刻)。再生は実時間の倍率 (既定 0.5 倍、1 倍 = 実時間)`,
    setTime(k) { base.setTime(k0 + Math.max(0, Math.min(nT - 1, k))); },
    timeText(k) {
      const j = k0 + k, on = C.filter(e => e.on[j] > 0.5).map(e => `${e.c.name} ${kN(Math.hypot(...at3(e.F, j)))} kN`);
      const loads = D.motion.corners.map((_, i) => R.w(j, i, 'wheel_load'));
      return `t = ${R.t[j].toFixed(3)} s   台 ${HV.fmt(R.seriesAt('table_angle', j))}°   ロール 台に対して ${HV.fmt(R.pose[6 * j + 3])}° / 床に対して ${HV.fmt(rollW[j])}°   ` +
             `乗員の箱と床のいちばん近い距離 ${HV.fmt(gap[j])} mm   浮いた輪 ${loads.filter(v => v <= 0).length} 本` + (on.length ? `   接触: ${on.join('、')}` : '');
    },
    hud() {
      return [`横転: ${HV.fmt(tm.wheels_off_time)} s 上側の輪が浮く → ${HV.fmt(tm.tip_time)} s 倒れる → ${HV.fmt(tm.rollbar_first_contact_time)} s ロールバーが床に当たる (接触力の最大 ${kN(X.peak.N)} kN、${X.peak.t} s) → ${HV.fmt(X.end.t)} s 横倒しで止まる (床に対して ${HV.fmt(X.end.roll_world)}°)`,
              gmin > 0 ? `乗員の箱は一度も床に触れない: 床までのいちばん近い距離は ${HV.fmt(gmin)} mm (${HV.fmt(X.occupant_gap_min_t)} s、${X.occupant_gap_min_box})`
                       : `乗員の箱が床に入る: いちばん深くて ${HV.fmt(-gmin)} mm (${HV.fmt(X.occupant_gap_min_t)} s、${X.occupant_gap_min_box})`];
    },
    plot() { return base.plot().map(clip); },
    plotClick(i, x) { return Math.max(0, Math.min(nT - 1, base.plotClick(i, x) - k0)); },
    panel(div) {
      base.panel(div);
      const h = document.createElement('h3'); h.textContent = '時間の範囲'; div.appendChild(h);
      for (const [key, lab] of [['fall', `${X.window[0]}–${X.window[1]} s (浮いてから止まるまで)`], ['all', `全体 (0–${HV.fmt(R.t[R.nT - 1])} s)`]]) {
        const l = document.createElement('label'); l.style.display = 'block';
        const r = document.createElement('input'); r.type = 'radio'; r.name = 'rollwin'; r.id = 'rollwin_' + key; r.checked = (whole ? 'all' : 'fall') === key;
        r.onchange = () => { f.rollWin = key; HV.open('handling'); }; l.append(r, ' ' + lab); div.appendChild(l);
      }
      const h2 = document.createElement('h3'); h2.textContent = '再生の速さ'; div.appendChild(h2);
      for (const v of ['0.5', '1']) {
        const b = document.createElement('button'); b.id = 'rollspeed_' + v; b.textContent = v === '1' ? '実時間 (1 倍)' : '0.5 倍'; b.style.margin = '1px';
        b.onclick = () => { const s = document.getElementById('speed'); s.value = v; s.dispatchEvent(new Event('change')); };
        div.appendChild(b);
      }
    },
  });
  // the camera box: the drawn vehicle (world) at the window's first and last instants, the sun's shadow box fixed to it
  const box = new T.Box3();
  for (const k of [k0, k1]) { base.setTime(k); ctx.root.updateMatrixWorld(true); box.expandByObject(R.beams.mesh, true); for (const m of R.tyres) box.expandByObject(m, true); }
  ctx.lo = box.min.toArray(); ctx.hi = box.max.toArray(); R.fixedSun = true;
  HV.Look.aimSun(view, ctx.lo, ctx.hi);
  R.last = null; base.setTime(k0);
  return ctl;
};

// the controller of a K2 replay field (handling, aero and the powertrain replays)
HV.K2.controller = function (name, D, R, opt) {
  const S = { col: opt.col || 'load' };
  const COLS = Object.assign({ load: ['輪の荷重', HV.K2.loadSpec(D), (k, i) => R.w(k, i, 'wheel_load')],
                               use: ['摩擦の使用 |F| / μN', HV.K2.useSpec(), (k, i) => R.w(k, i, 'friction_use')] }, opt.cols || {});
  const tyre = i => { const c = COLS[S.col]; return HV.colour(c[1], c[2](R.k, i)); };
  let panels = [];
  const update = () => { R.setFrame(R.k, tyre); if (opt.update) opt.update(R); };
  const ctl = {
    nT: R.nT, t0: 0, viewDir: opt.viewDir || [0.9, -1.4, 0.7], times: Array.from(R.t), speed: 1,
    timeNote: `記録の ${R.nT} 時刻 (${HV.fmt(R.t[R.nT - 1])} s)。再生は実時間の倍率`,
    scale: { min: 0, max: 1, step: 0.01, value: R.mmPerN, label: '矢印の長さ (mm / N)', note: '動きは実寸 (×1)。矢印は 1 kN あたり 1000 × この値 mm' },
    setScale(v) { R.mmPerN = v; update(); },
    setTime(k) { R.k = k; update(); },
    timeText(k) {
      const ids = D.motion.corners.map(c => c.id), loads = ids.map((_, i) => R.w(k, i, 'wheel_load'));
      const ang = R.turn ? `   ${R.turn.kind === 'tilt_table' ? '台' : '斜面'} ${HV.fmt(R.seriesAt(R.turn.angle_channel, k))}°` : '';
      return `t = ${R.t[k].toFixed(2)} s   速さ ${HV.fmt(R.seriesAt('speed', k))} km/h   ロール ${HV.fmt(R.pose[6 * k + 3])}°${ang}   最小の輪の荷重 ${HV.fmt(Math.min(...loads))} N` + (opt.timeExtra ? opt.timeExtra(R, k) : '');
    },
    hud() {
      return [HV.K2.judgeText(D.judge) || D.title,
              (opt.hud ? opt.hud(R) + '   ' : '') + `色: タイヤ = ${COLS[S.col][0]}、部品 = 計算での扱い   矢印: 緑 垂直力、青 摩擦力   動き ×1 (実寸)`];
    },
    contour: () => COLS[S.col][1],
    plot() { panels = opt.plots(R, R.t[R.k]).filter(Boolean); if (opt.morePlots) panels.push(...opt.morePlots(R, R.t[R.k])); return panels; },
    plotClick(i, x) { return R.plotClick(panels[i], x); },
    pickables: () => R.pickables(),
    info: hit => R.info(hit),
    extraInputs: () => HV.K2.judgeRow(D) + (opt.extraInputs ? opt.extraInputs() : ''),
    panel(div) {
      HV.K2.casePanel(name, D, div);
      R.panel(div, Object.entries(COLS).map(([k, c]) => [k, c[0]]), S.col, k => { S.col = k; update(); HV.refresh(); });
    },
  };
  update();
  return ctl;
};

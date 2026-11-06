// Handling and stability (viewer/src/handling.py): the K2 replay of tilt (S1), braking (S4), lane change (S3)
// or the rough side slope (S5), one case at a time (D.cases), with tyres coloured by wheel load or friction use.
window.HV = window.HV || {};
HV.fields = HV.fields || {};
HV.fields.handling = {
  label: HV.FIELD_LABEL.handling,   // js/names.js: the contract's DOMAIN_NAMES_JA
  async init(D, ctx) {
    HV.K2.useCase('handling', D);
    const R = await HV.K2.Replay.make(D, ctx);
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

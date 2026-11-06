// Aerodynamics (viewer/src/aero.py): the K2 crosswind replay, with the side force drawn at the side centre of
// pressure (moving with the body) as the record's full-gust side force x (wind_speed(t) / gust)^2.
window.HV = window.HV || {};
HV.fields = HV.fields || {};
HV.fields.aero = {
  label: '空力',
  async init(D, ctx) {
    HV.K2.useCase('aero', D);
    const T = window.THREE, R = await HV.K2.Replay.make(D, ctx), W = D.wind;
    const wa = R.arrows.add('#1565c0'), WIND_MM_PER_N = 1.0;
    const force = k => { const v = R.seriesAt('wind_speed', k) || 0, f = W.side_force_N * (v / (W.gust || 1)) ** 2; return W.dir.map(c => c * f); };
    return HV.K2.controller('aero', D, R, {
      viewDir: [0.2, -1.6, 0.6],
      update(R) {
        const q = R.quat(R.k), o = R.origin(R.k), p = new T.Vector3(...W.cp_mm).applyQuaternion(q), f = force(R.k);
        // the arrow ends at the centre of pressure, coming from the windward side
        const len = Math.hypot(...f) * WIND_MM_PER_N, at = [o[0] + p.x, o[1] + p.y, o[2] + p.z];
        const n = W.dir, start = len > 1 ? at.map((v, a) => v - n[a] * len) : at;
        R.arrows.set(wa, start, f, WIND_MM_PER_N);
      },
      hud: () => '空気の力: 抗力と横の力だけ (部品の箱の圧力の中心)   青 = 横風の力 (1 kN = 1 m)',
      timeExtra: (R, k) => `   風速 ${HV.fmt(R.seriesAt('wind_speed', k))} m/s   横の力 ${HV.fmt(Math.hypot(...force(k)))} N   横の流れ ${HV.fmt((R.pose[6 * k + 1] - R.pose[1]) / 1000)} m`,
      plots(R, c) {
        return [R.panelOf([['wind_speed', '風速']], '風速', 'm/s', c), R.wheelPanel('wheel_load', '輪の荷重 (風上の輪が軽くなる)', 'N', c),
                R.panelOf([['body_y', '横の位置']], '横の流れ', 'm', c), R.panelOf([['roll', 'ロール']], 'ロール角', 'deg', c)];
      },
    });
  },
};

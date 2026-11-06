// The Hybrid III 50th percentile male dummy: the dimensions and masses of its product sheet, kept
// as the sheet's table gives them (inches, pounds).
//
// Source: Humanetics, "Hybrid III 50th Percentile Male Technical Product Sheet" (2012),
// https://www.humaneticsgroup.com/sites/default/files/2020-06/hybiii50-tps.pdf
// The values were copied from the sheet's tables by HIRAKU's coordinating team (統括部); the
// sheet itself is not read in this repository. Millimetres are worked out here (MM_PER_IN).
//
// What they are used for: only the shape of the real look's dummy (shapes.js occupantDummyPieces)
// takes ratios from them, for what no calculation uses (the chest's depth and girth, the hips'
// breadth against the shoulders', the thighs', knees' and ankles' thickness, the foot's breadth
// against its length). The lengths, the joints' centres and the head's outer size stay those of
// the design's occupant boxes (the AIST sizes the checks use). The masses are kept as the table
// has them and are not used in the picture.

export const MM_PER_IN = 25.4;
export const KG_PER_LB = 0.45359237;

// dimensions, in (the sheet's table)
export const H3_IN = Object.freeze({
  sitting_height: 34.8, // 座高
  shoulder_pivot_height: 20.2, // 肩の回転中心の高さ
  h_point_height: 3.4, // H 点の高さ
  h_point_from_seat_back: 5.4, // H 点と背もたれ
  shoulder_pivot_from_back_line: 3.5, // 肩の回転中心と背の線
  thigh_clearance: 5.8, // ももの厚さ
  elbow_wrist_pivot: 11.0, // 肘から手首の回転中心
  head_back_from_back_line: 1.7, // 頭頂と背の線
  shoulder_elbow: 13.3, // 肩から肘
  elbow_rest_height: 7.9, // 肘を置く高さ
  buttock_knee: 23.3, // 尻から膝
  popliteal_height: 17.4, // 膝裏の高さ
  knee_pivot_height: 19.4, // 膝の回転中心から床
  buttock_popliteal: 18.3, // 尻から膝裏
  chest_depth: 8.7, // 胸の厚さ
  foot_length: 10.2, // 足の長さ
  foot_breadth: 3.9, // 足の幅
  shoulder_breadth: 16.9, // 肩幅
  hip_breadth: 14.3, // H 点の腰幅
  chest_circumference: 38.8, // 胸囲
  waist_circumference: 33.5, // 腰囲
});

// masses, lb (the sheet's table; kept for reference, not used in the picture)
export const H3_LB = Object.freeze({
  head: 10.0,
  neck: 3.4,
  upper_torso: 37.9,
  lower_torso: 50.8,
  upper_arm: 4.4,
  lower_arm_hand: 5.0,
  upper_leg: 13.2,
  lower_leg_foot: 12.0,
  total: 171.3,
});

// A dimension of the table in millimetres.
export function h3mm(key) {
  const v = H3_IN[key];
  if (v === undefined) throw new Error(`no Hybrid III dimension ${key}`);
  return v * MM_PER_IN;
}

// The perimeter of an ellipse of semi-axes a, b (Ramanujan's first approximation).
export function ellipsePerimeter(a, b) {
  return Math.PI * (3 * (a + b) - Math.sqrt((3 * a + b) * (a + 3 * b)));
}

// The breadth of an ellipse of the given perimeter and depth (bisection on ellipsePerimeter). The
// chest taken as an ellipse is an assumption of the picture.
export function ellipseBreadth(perimeter, depth) {
  let lo = 0;
  let hi = perimeter / 2;
  for (let i = 0; i < 80; i += 1) {
    const mid = (lo + hi) / 2;
    if (ellipsePerimeter(mid / 2, depth / 2) < perimeter) lo = mid;
    else hi = mid;
  }
  return (lo + hi) / 2;
}

// The ratios the dummy's shape takes (all from the table, worked out here):
// - chestBreadth, chestDepth: the chest's breadth (an ellipse of the chest girth and the chest depth)
//   and depth, over the shoulder breadth;
// - waist: the waist girth over the chest girth (the waist's ellipse the chest's, scaled);
// - hipBreadth: the hip breadth at the H point over the shoulder breadth;
// - footBreadth: the foot's breadth over its length;
// and, in mm of the dummy (scaled to a person by the knee pivot's height, shapes.js):
// - kneeHalf: half the knee's depth (buttock-knee less buttock-popliteal): the knee pivot to the
//   knee's front;
// - thighHalf: half the thigh's thickness;
// - ankleHalf: half the foot's breadth;
// - kneeHeight: the knee pivot's height over the floor.
const CHEST_BREADTH_IN = ellipseBreadth(H3_IN.chest_circumference, H3_IN.chest_depth);
export const H3_SHAPE = Object.freeze({
  chestBreadth: CHEST_BREADTH_IN / H3_IN.shoulder_breadth,
  chestDepth: H3_IN.chest_depth / H3_IN.shoulder_breadth,
  waist: H3_IN.waist_circumference / H3_IN.chest_circumference,
  hipBreadth: H3_IN.hip_breadth / H3_IN.shoulder_breadth,
  footBreadth: H3_IN.foot_breadth / H3_IN.foot_length,
  kneeHalf: ((H3_IN.buttock_knee - H3_IN.buttock_popliteal) / 2) * MM_PER_IN,
  thighHalf: (H3_IN.thigh_clearance / 2) * MM_PER_IN,
  ankleHalf: (H3_IN.foot_breadth / 2) * MM_PER_IN,
  kneeHeight: H3_IN.knee_pivot_height * MM_PER_IN,
});

// The line the workbench's くわしく says under a dummy (app.js)
export const DUMMY_NOTE_JA = "絵のダミーの形は Hybrid III 50 パーセンタイル男性の比率。長さは計算に使う乗員の寸法";

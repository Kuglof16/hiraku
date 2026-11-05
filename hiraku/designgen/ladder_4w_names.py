"""ladder_4w: a person's name (Japanese) for each frame member (member.name: what it is and from where to where) and
for each design variable a module's designer may change (design.variable_names). A member or a node the tables do not
know keeps its id in the name, so a new one shows up as such."""

from __future__ import annotations

import re

SIDE = {"L": "左", "R": "右"}
STATION = {"F": "前の端", "A": "前の車軸", "S1": "座席の前", "S2": "座席の後ろ", "H": "ロールバーの足", "B": "後ろの車軸",
           "E": "後ろの端"}
# a node of a rail (its id without the side letter) -> where it is along the rail
ON_RAIL = {"F": "前の端", "Ap1": "前の塔の前の柱", "Au1": "上の腕の前の付け根の下", "A": "前の車軸",
           "Au2": "上の腕の後ろの付け根の下", "Ap2": "前の塔の後ろの柱", "S1": "座席の前", "S2": "座席の後ろ",
           "H": "ロールバーの足", "B": "後ろの車軸", "E": "後ろの端", "SO": "開口の前の足", "N": "網の前の足",
           "SG": "横の守りの前の足"}
ORDINAL = {"1": "下", "2": "上", "3": "いちばん上"}


def _where(node: str) -> str:
    return ON_RAIL.get(node[1:], node)


def member_name(m: dict) -> str:
    mid, a, b = m["id"], m["node_a"], m["node_b"]
    if (g := re.fullmatch(r"rail_([LR])_.+", mid)):
        return f"{SIDE[g[1]]}のレール ({_where(a)}から{_where(b)}まで)"
    if (g := re.fullmatch(r"cross_(\w+)", mid)) and g[1] in STATION:
        return f"{STATION[g[1]]}の横の部材"
    if mid == "dash_cross":
        return "ダッシュの横の部材 (コラムの金具を持つ)"
    if (g := re.fullmatch(r"dash_post_([LR])", mid)):
        return f"ダッシュの{SIDE[g[1]]}の柱 (前の塔の後ろの柱の上から)"
    if (g := re.fullmatch(r"lower_cross_A([12])", mid)):
        return f"前の下の横の部材 (下の腕の{'前' if g[1] == '1' else '後ろ'}の付け根どうし)"
    if (g := re.fullmatch(r"lower_leg_([LR])A([12])", mid)):
        fr = "前" if g[2] == "1" else "後ろ"
        return f"前{SIDE[g[1]]}の下の脚 (塔の{fr}の柱の足から、下の腕の{fr}の付け根へ)"
    if (g := re.fullmatch(r"tower_post_([LR])([AB])([12])_up", mid)):
        axle = "前" if g[2] == "A" else "後ろ"
        return f"{axle}{SIDE[g[1]]}のサスペンションの塔の柱 ({'前' if g[3] == '1' else '後ろ'}、ペダルの梁より上)"
    if (g := re.fullmatch(r"tower_post_([LR])([AB])([12]?)", mid)):
        axle = "前" if g[2] == "A" else "後ろ"
        return f"{axle}{SIDE[g[1]]}のサスペンションの塔の柱" + (f" ({'前' if g[3] == '1' else '後ろ'})" if g[3] else "")
    if (g := re.fullmatch(r"tower_top_([LR])([AB])([12])", mid)):
        axle = "前" if g[2] == "A" else "後ろ"
        part = "前の柱から真ん中まで" if g[3] == "1" else "真ん中から後ろの柱まで"
        return f"{axle}{SIDE[g[1]]}の塔の上の梁 ({part})"
    if (g := re.fullmatch(r"tower_arm_([LR])([AB])", mid)):
        return f"{'前' if g[2] == 'A' else '後ろ'}{SIDE[g[1]]}の塔の腕 (ダンパーの上へ)"
    if (g := re.fullmatch(r"rollbar_foot_([LR])", mid)):
        return f"ロールバーの{SIDE[g[1]]}の足 (レールから外へ)"
    if (g := re.fullmatch(r"rollbar_post_([LR])", mid)):
        return f"ロールバーの{SIDE[g[1]]}の柱"
    if mid == "rollbar_top":
        return "ロールバーの上の棒"
    if (g := re.fullmatch(r"rops_front_foot_([LR])", mid)):
        return f"前の柱の{SIDE[g[1]]}の足 (座席の前でレールから外へ)"
    if (g := re.fullmatch(r"rops_front_post_([LR])_(\d)", mid)):
        return f"{SIDE[g[1]]}の前の柱 ({ORDINAL.get(g[2], g[2])}の段)"
    if mid == "rops_front_top":
        return "前の柱の上の棒"
    if (g := re.fullmatch(r"rops_roof_([LR])", mid)):
        return f"{SIDE[g[1]]}の屋根の棒 (前の柱の上からロールバーの上へ)"
    if (g := re.fullmatch(r"rops_stay_([LR])", mid)):
        return f"ロールバーの{SIDE[g[1]]}の後ろの支え (上から後ろの端へ)"
    if (g := re.fullmatch(r"side_bar_([LR])(\d)", mid)):
        return f"{SIDE[g[1]]}の横の守りの棒 (下から {g[2]} 本目)"
    if (g := re.fullmatch(r"side_foot_([LR])(S2|SG)", mid)):
        return f"{SIDE[g[1]]}の横の守りの{'後ろ' if g[2] == 'S2' else '前'}の足 (レールから外へ)"
    if (g := re.fullmatch(r"side_post_([LR])(S2|SG)_(\d)", mid)):
        return f"{SIDE[g[1]]}の横の守りの{'後ろ' if g[2] == 'S2' else '前'}の柱 ({ORDINAL.get(g[3], g[3])}の段)"
    if (g := re.fullmatch(r"side_net_foot_([LR])", mid)):
        return f"{SIDE[g[1]]}の網の前の足 (レールから外へ)"
    if (g := re.fullmatch(r"side_open_foot_([LR])", mid)):
        return f"{SIDE[g[1]]}の開口の前の足 (網か扉の留め)"
    if (g := re.fullmatch(r"grab_([LR])_(\d)", mid)):
        return f"{SIDE[g[1]]}の握り棒" + {"1": "の下の腕", "2": " (握る所)", "3": "の上の腕"}.get(g[2], f" {g[2]}")
    if mid == "pedal_beam":
        return "ペダルの梁 (前右の塔の後ろの柱から)"
    return mid


VARIABLE_NAMES_JA = {
    "wheelbase": "軸距 (前と後ろの車軸の間)", "track": "トレッド (左右の車輪の間)", "rail_spacing": "レールの左右の間",
    "rail_height": "レールの高さ", "front_overhang": "前の張り出し (前の車軸より前の枠)",
    "rear_overhang": "後ろの張り出し (後ろの車軸より後ろの枠)", "seat_x": "座席の前後の位置",
    "seat_y": "座席の左右の位置", "steering_x": "ハンドルの前後の位置", "battery_x": "電池の前後の位置",
    "pedal_x": "ペダルの前後の位置", "bulkhead_dx": "前の隔壁の位置 (前の車軸からの後ろへの距離)",
    "rail_b": "レールの管の幅", "rail_h": "レールの管の高さ", "rail_t": "レールの管の厚さ",
    "cross_b": "横の部材の管の幅", "cross_h": "横の部材の管の高さ", "cross_t": "横の部材の管の厚さ",
    "tower_b": "塔とダッシュの管の幅", "tower_t": "塔とダッシュの管の厚さ", "lower_b": "下の脚の管の幅",
    "lower_t": "下の脚の管の厚さ", "lower_cross": "下の横の部材 (あり・なし)", "gusset_leg": "ガセットの辺の長さ",
    "gusset_thickness": "ガセットの板の厚さ", "cross_members_through": "横の部材がレールを通り抜けるか",
    "mount_floor": "床・荷台・前の囲いの取付の金具", "mount_electrics": "電池・制御器・配線箱の取付の金具",
    "mount_seats": "座席とベルトの取付の金具", "mount_drive": "モーターと減速機の取付の金具",
    "wheel_radius": "タイヤの半径", "wheel_width": "タイヤの幅", "front_spring_rate": "前のばねの硬さ",
    "rear_spring_rate": "後ろのばねの硬さ", "suspension_travel": "車輪の上下の動く量",
    "mount_plate_t": "サスペンションの取付の金具の板厚", "mount_bolt_d": "サスペンションの取付の金具のピンの径",
    "mount_weld_leg": "サスペンションの取付の金具の溶接の脚長",
    "steering_column_angle": "コラムの傾き", "steering_wheel_height": "ハンドルの高さ (座面から)",
    "max_steer_angle": "ハンドルを切れる角度", "pedal_mount": "ペダルの台",
    "seat_height": "座面の高さ (床から)", "seat_slide": "座席の前後の滑りの量", "seat_depth": "座面の奥行き",
    "rollbar": "ロールバー (あり・なし)", "rollbar_height": "ロールバーの高さ (座面から)",
    "rollbar_width": "ロールバーの左右の柱の間", "rollbar_offset": "ロールバーの位置 (座席の後ろからの距離)",
    "rollbar_square": "ロールバーを角管にするか", "rollbar_d": "ロールバーの丸管の太さ",
    "rollbar_b": "ロールバーの角管の幅", "rollbar_t": "ロールバーの管の厚さ", "side_bars": "横の守りの枠 (あり・なし)",
    "side_bar_count": "横の守りの棒の本数", "side_bar_low": "横の守りのいちばん下の棒の高さ",
    "side_bar_high": "横の守りのいちばん上の棒の高さ", "side_bar_d": "横の守りの管の太さ",
    "side_bar_t": "横の守りの管の厚さ", "side_net": "横の網 (あり・なし)", "side_net_wire_d": "網の線の太さ",
    "side_net_pitch": "網の目の間隔", "rollbar_foot_b": "外へ出す足の管の幅", "rollbar_foot_h": "外へ出す足の管の高さ",
    "rollbar_foot_t": "外へ出す足の管の厚さ", "rollbar_stays": "ロールバーの後ろの支え (あり・なし)",
    "rollbar_stay_d": "後ろの支えの管の太さ", "rollbar_stay_t": "後ろの支えの管の厚さ",
    "rollbar_front_posts": "前の柱 (4 本柱にするか)", "side_opening": "乗り降りの開口 (なし・網・扉)",
    "side_bar_reach": "横の守りの前の柱の位置 (肩と腰の点より前へ)", "grab_bar": "握り棒 (あり・なし)",
    "grab_bar_d": "握り棒の太さ", "grab_bar_t": "握り棒の管の厚さ", "grab_bar_gap": "握り棒と柱の間 (手の入る隙間)",
    "motor_peak_power": "モーターの最大出力", "motor_peak_torque": "モーターの最大トルク",
    "motor_max_speed": "モーターの最高回転数", "gear_ratio": "減速比", "battery_capacity": "電池の容量",
    "driveshafts": "駆動軸 (あり・なし)", "battery_split": "電池を 2 つに分けるか",
    "bumper_stroke": "バンパーのつぶれる量", "crush_stiffness": "前のつぶれる所の硬さ", "lamps": "灯火 (あり・なし)",
    "fenders": "フェンダー (あり・なし)", "fender_front_deg": "フェンダーの前への長さ (角度)",
    "fender_rear_deg": "フェンダーの後ろへの長さ (角度)", "fender_width": "フェンダーの幅",
    "fender_gap": "フェンダーとタイヤの間", "floor_t": "床の板の厚さ", "deck_t": "荷台の板の厚さ",
    "bulkhead_t": "隔壁の板の厚さ",
}


def write(design: dict) -> dict:
    """member.name on every member; design.variable_names for every variable a module's designer may change."""
    for m in design.get("members", []):
        m["name"] = member_name(m)
    names = {}
    for mod in design.get("modules") or []:
        for v in mod.get("variables", []):
            names[v] = VARIABLE_NAMES_JA.get(v, v)
    if names:
        design["variable_names"] = names
    return design

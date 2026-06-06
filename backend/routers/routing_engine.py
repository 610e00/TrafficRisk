"""
安全路線規劃引擎 v2
參考：Berhanu et al. (2024) - 累計風險最低路徑

核心策略：
  不依賴 OSRM alternatives（常常只給 1 條）
  改為主動產生「繞開高風險路口」的安全路線：
    1. 先取得最快路線
    2. 找出最快路線上風險最高的路口
    3. 在該路口旁邊 300m 製造一個「繞開點」
    4. 用 origin → 繞開點 → dest 打第二次 OSRM
    5. 比較兩條，選風險懲罰較低的作為安全路線
"""

import math, httpx
from typing import Optional

OSRM_BASE = "https://router.project-osrm.org/route/v1/driving"

# ── 距離計算 ─────────────────────────────────────────────────────
def haversine(lat1, lng1, lat2, lng2) -> float:
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def offset_point(lat, lng, bearing_deg, dist_m):
    """從一個點往某方向偏移 dist_m 公尺，回傳新座標"""
    R = 6371000
    bearing = math.radians(bearing_deg)
    lat1 = math.radians(lat)
    lng1 = math.radians(lng)
    lat2 = math.asin(
        math.sin(lat1)*math.cos(dist_m/R) +
        math.cos(lat1)*math.sin(dist_m/R)*math.cos(bearing)
    )
    lng2 = lng1 + math.atan2(
        math.sin(bearing)*math.sin(dist_m/R)*math.cos(lat1),
        math.cos(dist_m/R) - math.sin(lat1)*math.sin(lat2)
    )
    return math.degrees(lat2), math.degrees(lng2)

# ── 動態風險分數 ──────────────────────────────────────────────────
WEATHER_BOOST = {"晴天": 0, "陰天": 3, "小雨": 12, "大雨": 22}

def dynamic_score(node: dict, period: str, weather: str) -> float:
    score_obj = node.get("score", {})
    base = score_obj.get(period, score_obj.get("all", 50))
    boost = WEATHER_BOOST.get(weather, 0) * node.get("rain_sensitivity", 1.0)
    return min(100.0, base + boost)

# ── 路線風險計算 ──────────────────────────────────────────────────
def calc_risk(coords, nodes, period, weather, radius_m=150):
    penalty = 0.0
    hit = []
    for node in nodes:
        s = dynamic_score(node, period, weather)
        if s < 50:
            continue
        min_dist = min(haversine(node["lat"], node["lng"], c[0], c[1]) for c in coords)
        if min_dist > radius_m:
            continue
        w = 10 if s >= 70 else 3
        penalty += s * w
        hit.append({
            "id":    node.get("id", ""),
            "name":  node["name"],
            "score": round(s, 1),
            "level": "高風險" if s >= 70 else "中風險",
            "dist_m": round(min_dist),
            "hints": node.get("hints", {}),
            "note":  node.get("note", ""),
        })
    hit.sort(key=lambda x: -x["score"])
    return penalty, hit

# ── OSRM 單次呼叫 ─────────────────────────────────────────────────
async def osrm_route(waypoints: list[dict]) -> Optional[dict]:
    """
    waypoints: [{"lat":..,"lng":..}, ...]
    回傳 {"coords":[[lat,lng],...], "duration":秒, "distance":公尺}
    """
    coord_str = ";".join(f"{p['lng']},{p['lat']}" for p in waypoints)
    url = f"{OSRM_BASE}/{coord_str}"
    params = {"overview": "full", "geometries": "geojson", "steps": "false"}
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        raise RuntimeError(f"OSRM 請求失敗：{e}")

    if data.get("code") != "Ok" or not data.get("routes"):
        raise RuntimeError(f"OSRM 無結果：{data.get('code')}")

    rt = data["routes"][0]
    coords_raw = rt["geometry"]["coordinates"]
    return {
        "coords":   [[c[1], c[0]] for c in coords_raw],
        "duration": rt["duration"],
        "distance": rt["distance"],
    }

# ── 找繞開點：在最高風險路口旁邊找一個垂直偏移點 ─────────────────
def find_detour_waypoints(fast_route: dict, risky_nodes: list, n_detours=2):
    """
    從最危險的幾個路口，往路線垂直方向偏移 300m 當中繼點
    回傳 [(lat, lng), ...] 中繼點列表
    """
    if not risky_nodes:
        return []

    detours = []
    coords = fast_route["coords"]
    used = set()

    for node in risky_nodes[:n_detours]:
        nlat, nlng = node["lat"], node["lng"]

        # 找路線上最近的點
        best_i = min(range(len(coords)),
                     key=lambda i: haversine(nlat, nlng, coords[i][0], coords[i][1]))

        # 算路線在該點的方向角（用前後點）
        i = best_i
        if i > 0 and i < len(coords)-1:
            dlat = coords[i+1][0] - coords[i-1][0]
            dlng = coords[i+1][1] - coords[i-1][1]
        elif i > 0:
            dlat = coords[i][0] - coords[i-1][0]
            dlng = coords[i][1] - coords[i-1][1]
        else:
            dlat = coords[i+1][0] - coords[i][0]
            dlng = coords[i+1][1] - coords[i][1]

        # 垂直方向 = 路線方向 + 90 度
        bearing = math.degrees(math.atan2(dlng, dlat)) + 90

        key = round(nlat, 3), round(nlng, 3)
        if key in used:
            continue
        used.add(key)

        # 往兩側各嘗試 300m
        for side in [300, -300]:
            wlat, wlng = offset_point(nlat, nlng, bearing + (0 if side > 0 else 180), abs(side))
            detours.append({"lat": wlat, "lng": wlng})
            break  # 先只取一側

    return detours

# ── 主函數 ────────────────────────────────────────────────────────
async def compute_routes(
    origin: dict, dest: dict, nodes: list[dict],
    period: str = "day", weather: str = "晴天",
    vehicle_mode: str = "car"
) -> dict:

    # ① 最快路線
    fast_rt = await osrm_route([origin, dest])

    # ② 計算最快路線的風險
    fast_penalty, fast_hits = calc_risk(fast_rt["coords"], nodes, period, weather)

    # ③ 找繞開點（從高風險路口中找，把節點補上 lat/lng）
    risky_with_coords = []
    for h in fast_hits:
        node = next((n for n in nodes if n.get("id") == h["id"]), None)
        if node:
            risky_with_coords.append({**h, "lat": node["lat"], "lng": node["lng"]})

    print(f"[route] 最快路線風險懲罰={fast_penalty:.0f}秒，高風險路口={len([h for h in fast_hits if h['score']>=70])}個")

    # ④ 嘗試產生安全路線
    safe_rt = None
    safe_penalty = fast_penalty
    safe_hits = fast_hits

    if risky_with_coords:
        detour_pts = find_detour_waypoints(fast_rt, risky_with_coords, n_detours=2)
        print(f"[route] 嘗試 {len(detour_pts)} 個繞開點")

        best_safe = None
        best_safe_penalty = fast_penalty

        for wp in detour_pts:
            try:
                candidate = await osrm_route([origin, wp, dest])
                c_penalty, c_hits = calc_risk(candidate["coords"], nodes, period, weather)
                print(f"[route]   繞開候選：懲罰={c_penalty:.0f}秒，時間={candidate['duration']:.0f}秒")
                # 接受條件：風險確實下降，且不超過原本時間 + 15 分鐘
                if c_penalty < best_safe_penalty and candidate["duration"] <= fast_rt["duration"] + 900:
                    best_safe = candidate
                    best_safe_penalty = c_penalty
                    best_safe_hits = c_hits
            except Exception as e:
                print(f"[route]   繞開候選失敗：{e}")
                continue

        if best_safe:
            safe_rt = best_safe
            safe_penalty = best_safe_penalty
            safe_hits = best_safe_hits
            print(f"[route] 安全路線找到，風險降低 {fast_penalty-safe_penalty:.0f} 秒")
        else:
            print("[route] 沒有找到更好的安全路線，使用最快路線")

    # 找不到更好的就用最快路線
    if safe_rt is None:
        safe_rt = fast_rt

    def summarize(rt, hits, label):
        high = [n for n in hits if n["score"] >= 70]
        mid  = [n for n in hits if 50 <= n["score"] < 70]
        return {
            "label":        label,
            "coords":       rt["coords"],
            "duration":     rt["duration"],
            "duration_min": round(rt["duration"] / 60, 1),
            "distance_km":  round(rt["distance"] / 1000, 2),
            "risk_penalty": round(fast_penalty if label=="最快路線" else safe_penalty),
            "risk_nodes":   hits[:8],
            "high_count":   len(high),
            "mid_count":    len(mid),
        }

    fast_s = summarize(fast_rt, fast_hits, "最快路線")
    safe_s = summarize(safe_rt, safe_hits, "安全路線")

    extra_sec = safe_rt["duration"] - fast_rt["duration"]
    same = fast_rt["coords"] == safe_rt["coords"]

    comparison = {
        "same_route":        same,
        "extra_seconds":     round(extra_sec),
        "extra_minutes":     round(extra_sec / 60, 1),
        "saved_high":        fast_s["high_count"] - safe_s["high_count"],
        "saved_mid":         fast_s["mid_count"]  - safe_s["mid_count"],
        "saved_penalty_sec": round(fast_penalty - safe_penalty),
        "routes_available":  2 if not same else 1,
    }

    return {"fast": fast_s, "safe": safe_s, "comparison": comparison}

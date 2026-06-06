"""
安全路線規劃引擎 v4
修正：
- ORS 回傳 encoded polyline 格式，加入解碼函數
- calc_risk 座標點太少時自動插補（每 50m 一個點）
- 繞開邏輯觸發條件修正
"""

import math, httpx, os
from typing import Optional

ORS_BASE = "https://api.openrouteservice.org/v2/directions/driving-car"
ORS_KEY  = os.environ.get("ORS_KEY", "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6IjZkODM3OThlYTdiNDQ3NTZhZjRhYjNmZmZhNmQxZWJkIiwiaCI6Im11cm11cjY0In0=")

RADIUS_M        = 60
DETOUR_OFFSET_M = 600
MAX_EXTRA_SEC   = 900
N_DETOURS       = 3

WEATHER_BOOST = {"晴天": 0, "陰天": 3, "小雨": 12, "大雨": 22}

def decode_polyline(encoded: str, precision: int = 5) -> list:
    coords = []
    i = lat = lng = 0
    factor = 10 ** precision
    while i < len(encoded):
        b = shift = result = 0
        while True:
            b = ord(encoded[i]) - 63; i += 1
            result |= (b & 0x1f) << shift; shift += 5
            if b < 0x20: break
        lat += ~(result >> 1) if result & 1 else result >> 1
        shift = result = 0
        while True:
            b = ord(encoded[i]) - 63; i += 1
            result |= (b & 0x1f) << shift; shift += 5
            if b < 0x20: break
        lng += ~(result >> 1) if result & 1 else result >> 1
        coords.append([lat / factor, lng / factor])
    return coords

def interpolate_coords(coords: list, step_m: float = 50) -> list:
    if len(coords) < 2:
        return coords
    result = [coords[0]]
    for i in range(len(coords) - 1):
        a, b = coords[i], coords[i+1]
        d = haversine(a[0], a[1], b[0], b[1])
        if d < 1:
            continue
        n = max(1, int(d / step_m))
        for j in range(1, n + 1):
            t = j / n
            result.append([a[0] + t*(b[0]-a[0]), a[1] + t*(b[1]-a[1])])
    return result

def haversine(lat1, lng1, lat2, lng2) -> float:
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def offset_point(lat, lng, bearing_deg, dist_m):
    R = 6371000
    b  = math.radians(bearing_deg)
    la = math.radians(lat)
    lo = math.radians(lng)
    la2 = math.asin(math.sin(la)*math.cos(dist_m/R) +
                    math.cos(la)*math.sin(dist_m/R)*math.cos(b))
    lo2 = lo + math.atan2(math.sin(b)*math.sin(dist_m/R)*math.cos(la),
                          math.cos(dist_m/R) - math.sin(la)*math.sin(la2))
    return math.degrees(la2), math.degrees(lo2)

def dynamic_score(node, period, weather) -> float:
    s    = node.get("score", {})
    base = s.get(period, s.get("all", 50))
    boost = WEATHER_BOOST.get(weather, 0) * node.get("rain_sensitivity", 1.0)
    return min(100.0, base + boost)

def calc_risk(coords, nodes, period, weather):
    dense = interpolate_coords(coords, step_m=50)
    penalty, hits = 0.0, []
    for node in nodes:
        s = dynamic_score(node, period, weather)
        if s < 50:
            continue
        min_dist = min(haversine(node["lat"], node["lng"], c[0], c[1]) for c in dense)
        if min_dist > RADIUS_M:
            continue
        w = 10 if s >= 70 else 3
        penalty += s * w
        hits.append({
            "id":     node.get("id"),
            "name":   node["name"],
            "lat":    node["lat"],
            "lng":    node["lng"],
            "score":  round(s, 1),
            "level":  "高風險" if s >= 70 else "中風險",
            "dist_m": round(min_dist),
            "hints":  node.get("hints", {}),
            "note":   node.get("note", ""),
        })
    hits.sort(key=lambda x: -x["score"])
    return penalty, hits

async def ors_route(waypoints: list) -> dict:
    coords_ors = [[p["lng"], p["lat"]] for p in waypoints]
    body = {"coordinates": coords_ors}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            ORS_BASE,
            json=body,
            headers={
                "Authorization": ORS_KEY,
                "Content-Type":  "application/json",
                "Accept":        "application/json",
            }
        )
        if r.status_code != 200:
            raise RuntimeError(f"ORS HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()

    routes = data.get("routes")
    if not routes:
        raise RuntimeError(f"ORS 無結果：{list(data.keys())}")

    rt      = routes[0]
    summary = rt["summary"]
    geom    = rt.get("geometry")

    if isinstance(geom, str):
        coords = decode_polyline(geom)
        print(f"[ORS] encoded polyline 解碼，{len(coords)} 個座標點")
    elif isinstance(geom, dict):
        raw    = geom["coordinates"]
        coords = [[c[1], c[0]] for c in raw]
    else:
        coords = [[waypoints[0]["lat"], waypoints[0]["lng"]],
                  [waypoints[-1]["lat"], waypoints[-1]["lng"]]]
        print("[ORS] 無 geometry，使用起終點 fallback")

    return {
        "coords":   coords,
        "duration": summary["duration"],
        "distance": summary["distance"],
    }

def make_detour_waypoints(node, fast_coords):
    nlat, nlng = node["lat"], node["lng"]
    dense  = interpolate_coords(fast_coords, step_m=30)
    best_i = min(range(len(dense)),
                 key=lambda i: haversine(nlat, nlng, dense[i][0], dense[i][1]))
    i = best_i
    n = len(dense)
    if i > 0 and i < n-1:
        dlat = dense[i+1][0] - dense[i-1][0]
        dlng = dense[i+1][1] - dense[i-1][1]
    elif i > 0:
        dlat = dense[i][0] - dense[i-1][0]
        dlng = dense[i][1] - dense[i-1][1]
    else:
        dlat = dense[min(i+1,n-1)][0] - dense[i][0]
        dlng = dense[min(i+1,n-1)][1] - dense[i][1]
    bearing = math.degrees(math.atan2(dlng, dlat)) + 90
    return [
        {"lat": offset_point(nlat, nlng, bearing,       DETOUR_OFFSET_M)[0],
         "lng": offset_point(nlat, nlng, bearing,       DETOUR_OFFSET_M)[1]},
        {"lat": offset_point(nlat, nlng, bearing + 180, DETOUR_OFFSET_M)[0],
         "lng": offset_point(nlat, nlng, bearing + 180, DETOUR_OFFSET_M)[1]},
    ]

async def compute_routes(origin, dest, nodes,
                         period="day", weather="晴天", vehicle_mode="car"):

    try:
        fast_rt = await ors_route([origin, dest])
    except Exception as e:
        raise RuntimeError(f"無法取得最快路線：{e}")

    fast_penalty, fast_hits = calc_risk(fast_rt["coords"], nodes, period, weather)
    high_nodes = [h for h in fast_hits if h["score"] >= 70]

    print(f"[route] 最快路線 {fast_rt['duration']/60:.1f}分 "
          f"座標點={len(fast_rt['coords'])} "
          f"懲罰={fast_penalty:.0f}秒 高={len(high_nodes)} 中={len(fast_hits)-len(high_nodes)}")

    candidates       = high_nodes[:N_DETOURS] or fast_hits[:N_DETOURS]
    best_safe_rt      = None
    best_safe_penalty = fast_penalty
    best_safe_hits    = fast_hits

    for node in candidates:
        for wp in make_detour_waypoints(node, fast_rt["coords"]):
            try:
                cand = await ors_route([origin, wp, dest])
            except Exception as e:
                print(f"[route]   繞開失敗({node['name']}): {e}")
                continue
            c_pen, c_hits = calc_risk(cand["coords"], nodes, period, weather)
            extra = cand["duration"] - fast_rt["duration"]
            print(f"[route]   繞開 {node['name']}: 懲罰={c_pen:.0f}({fast_penalty-c_pen:+.0f}) 多={extra/60:.1f}分")
            if c_pen < best_safe_penalty and extra <= MAX_EXTRA_SEC:
                best_safe_rt, best_safe_penalty, best_safe_hits = cand, c_pen, c_hits

    if best_safe_rt:
        safe_rt, safe_penalty, safe_hits = best_safe_rt, best_safe_penalty, best_safe_hits
        print(f"[route] 安全路線確立，降低 {fast_penalty-safe_penalty:.0f}秒風險")
    else:
        safe_rt, safe_penalty, safe_hits = fast_rt, fast_penalty, fast_hits
        print("[route] 無更好替代路線")

    def summarize(rt, hits, pen, label):
        clean = [{k:v for k,v in h.items() if k not in ("lat","lng")} for h in hits]
        return {
            "label":        label,
            "coords":       rt["coords"],
            "duration":     rt["duration"],
            "duration_min": round(rt["duration"] / 60, 1),
            "distance_km":  round(rt["distance"] / 1000, 2),
            "risk_penalty": round(pen),
            "risk_nodes":   clean[:8],
            "high_count":   len([h for h in hits if h["score"] >= 70]),
            "mid_count":    len([h for h in hits if 50 <= h["score"] < 70]),
        }

    fast_s = summarize(fast_rt, fast_hits, fast_penalty, "最快路線")
    safe_s = summarize(safe_rt, safe_hits, safe_penalty, "安全路線")
    extra  = safe_rt["duration"] - fast_rt["duration"]
    same   = fast_penalty == safe_penalty and abs(extra) < 5

    return {
        "fast": fast_s,
        "safe": safe_s,
        "comparison": {
            "same_route":        same,
            "extra_seconds":     round(extra),
            "extra_minutes":     round(extra / 60, 1),
            "saved_high":        fast_s["high_count"] - safe_s["high_count"],
            "saved_mid":         fast_s["mid_count"]  - safe_s["mid_count"],
            "saved_penalty_sec": round(fast_penalty - safe_penalty),
            "routes_available":  2 if not same else 1,
        }
    }

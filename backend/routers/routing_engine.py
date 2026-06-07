"""
安全路線規劃引擎 v6
- 使用 Google Directions API（後端 Server Key，無 CORS 問題）
- 要求最多 3 條備選路線
- 對每條計算風險懲罰，選最低的為安全路線
- 參考 Berhanu et al. (2024)：選歷史事故累計最低的路線
"""

import math, httpx, os

GOOGLE_KEY = os.environ.get("GOOGLE_MAPS_KEY", "")
DIRECTIONS_URL = "https://maps.googleapis.com/maps/api/directions/json"

RADIUS_M      = 60
MAX_EXTRA_SEC = 900
WEATHER_BOOST = {"晴天": 0, "陰天": 3, "小雨": 12, "大雨": 22}

def haversine(lat1, lng1, lat2, lng2) -> float:
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2-lat1); dl = math.radians(lng2-lng1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def decode_polyline(encoded: str) -> list:
    coords = []
    i = lat = lng = 0
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
        coords.append([lat / 1e5, lng / 1e5])
    return coords

def interpolate_coords(coords: list, step_m: float = 50) -> list:
    if len(coords) < 2: return coords
    result = [coords[0]]
    for i in range(len(coords) - 1):
        a, b = coords[i], coords[i+1]
        d = haversine(a[0], a[1], b[0], b[1])
        if d < 1: continue
        n = max(1, int(d / step_m))
        for j in range(1, n + 1):
            t = j / n
            result.append([a[0]+t*(b[0]-a[0]), a[1]+t*(b[1]-a[1])])
    return result

def dynamic_score(node, period, weather) -> float:
    s = node.get("score", {})
    base = s.get(period, s.get("all", 50))
    boost = WEATHER_BOOST.get(weather, 0) * node.get("rain_sensitivity", 1.0)
    return min(100.0, base + boost)

def calc_risk(coords, nodes, period, weather):
    dense = interpolate_coords(coords, step_m=50)
    penalty, hits = 0.0, []
    for node in nodes:
        s = dynamic_score(node, period, weather)
        if s < 50: continue
        min_dist = min(haversine(node["lat"], node["lng"], c[0], c[1]) for c in dense)
        if min_dist > RADIUS_M: continue
        w = 10 if s >= 70 else 3
        penalty += s * w
        hits.append({
            "id":     node.get("id"),
            "name":   node["name"],
            "score":  round(s, 1),
            "level":  "高風險" if s >= 70 else "中風險",
            "dist_m": round(min_dist),
            "hints":  node.get("hints", {}),
            "note":   node.get("note", ""),
        })
    hits.sort(key=lambda x: -x["score"])
    return penalty, hits

async def google_routes(origin: dict, dest: dict) -> list:
    if not GOOGLE_KEY:
        raise RuntimeError("GOOGLE_MAPS_KEY 環境變數未設定")

    params = {
        "origin":       f"{origin['lat']},{origin['lng']}",
        "destination":  f"{dest['lat']},{dest['lng']}",
        "alternatives": "true",
        "mode":         "driving",
        "avoid":        "highways",
        "language":     "zh-TW",
        "region":       "TW",
        "key":          GOOGLE_KEY,
    }

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(DIRECTIONS_URL, params=params)
        if r.status_code != 200:
            raise RuntimeError(f"Google Directions HTTP {r.status_code}")
        data = r.json()

    if data.get("status") != "OK":
        raise RuntimeError(f"Google Directions 失敗：{data.get('status')} - {data.get('error_message','')}")

    routes = []
    for rt in data.get("routes", []):
        leg = rt["legs"][0]
        coords = decode_polyline(rt["overview_polyline"]["points"])
        routes.append({
            "coords":   coords,
            "duration": leg["duration"]["value"],
            "distance": leg["distance"]["value"],
            "summary":  rt.get("summary", ""),
        })

    print(f"[Google] 取得 {len(routes)} 條備選路線")
    for i, rt in enumerate(routes):
        print(f"  路線{i+1}: {rt['summary']} {rt['duration']/60:.1f}分 {rt['distance']/1000:.1f}km")

    return routes

async def compute_routes(origin, dest, nodes,
                         period="day", weather="晴天", vehicle_mode="car"):

    try:
        routes = await google_routes(origin, dest)
    except Exception as e:
        raise RuntimeError(f"無法取得路線：{e}")

    if not routes:
        raise RuntimeError("Google Directions 無路線結果")

    scored = []
    for rt in routes:
        penalty, hits = calc_risk(rt["coords"], nodes, period, weather)
        scored.append({**rt, "risk_penalty": penalty, "risk_hits": hits})
        high = len([h for h in hits if h["score"] >= 70])
        mid  = len([h for h in hits if 50 <= h["score"] < 70])
        print(f"[route] {rt['summary']}: {rt['duration']/60:.1f}分 懲罰={penalty:.0f} 高={high} 中={mid}")

    # 排除國道/高速公路路線
    HIGHWAY_KEYWORDS = ['國道', '高速', '快速道路', 'freeway', 'highway']
    scored_filtered = [
        r for r in scored
        if not any(kw in r.get('summary', '') for kw in HIGHWAY_KEYWORDS)
    ]
    # 如果過濾後沒有路線，就用全部（至少要有路線）
    if not scored_filtered:
        scored_filtered = scored
        print("[route] 警告：所有路線都是高速公路，無法排除")

    fast = min(scored_filtered, key=lambda x: x["duration"])
    candidates = [r for r in scored_filtered if r["duration"] <= fast["duration"] + MAX_EXTRA_SEC]
    safe = min(candidates, key=lambda x: x["risk_penalty"])

    same = (fast is safe) or (
        abs(fast["duration"] - safe["duration"]) < 5 and
        fast["risk_penalty"] == safe["risk_penalty"]
    )

    def summarize(rt, label):
        hits = rt["risk_hits"]
        high = [h for h in hits if h["score"] >= 70]
        mid  = [h for h in hits if 50 <= h["score"] < 70]
        return {
            "label":        label,
            "coords":       rt["coords"],
            "duration":     rt["duration"],
            "duration_min": round(rt["duration"] / 60, 1),
            "distance_km":  round(rt["distance"] / 1000, 2),
            "risk_penalty": round(rt["risk_penalty"]),
            "risk_nodes":   hits[:8],
            "high_count":   len(high),
            "mid_count":    len(mid),
            "route_name":   rt.get("summary", ""),
        }

    fast_s = summarize(fast, "最快路線")
    safe_s = summarize(safe, "安全路線")
    extra  = safe["duration"] - fast["duration"]

    print(f"[route] 最快={fast_s['route_name']} 安全={safe_s['route_name']} 相同={same}")

    return {
        "fast": fast_s,
        "safe": safe_s,
        "comparison": {
            "same_route":        same,
            "extra_seconds":     round(extra),
            "extra_minutes":     round(extra / 60, 1),
            "saved_high":        fast_s["high_count"] - safe_s["high_count"],
            "saved_mid":         fast_s["mid_count"]  - safe_s["mid_count"],
            "saved_penalty_sec": round(fast["risk_penalty"] - safe["risk_penalty"]),
            "routes_available":  len(routes),
            "vehicle_mode":      vehicle_mode,
        }
    }

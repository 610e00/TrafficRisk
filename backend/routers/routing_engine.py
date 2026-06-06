"""
安全路線規劃引擎 v3
- 使用 OpenRouteService API（支援 alternative_routes）
- radius_m = 60 公尺（只算真正在路線上的路口）
- 對最快路線上前3個高風險路口各產生繞開點
- 打3次ORS，選風險最低且不超時15分鐘的那條
"""

import math, httpx, os
from typing import Optional

ORS_BASE = "https://api.openrouteservice.org/v2/directions/driving-car"
ORS_KEY  = os.environ.get("ORS_KEY", "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6IjZkODM3OThlYTdiNDQ3NTZhZjRhYjNmZmZhNmQxZWJkIiwiaCI6Im11cm11cjY0In0=")

RADIUS_M        = 60    # 只算真正在路線上的路口
DETOUR_OFFSET_M = 600   # 繞開點偏移距離
MAX_EXTRA_SEC   = 900   # 安全路線最多多15分鐘
N_DETOURS       = 3     # 嘗試繞開前幾個高風險路口

WEATHER_BOOST = {"晴天": 0, "陰天": 3, "小雨": 12, "大雨": 22}

# ── 工具函數 ──────────────────────────────────────────────────────
def haversine(lat1, lng1, lat2, lng2) -> float:
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def offset_point(lat, lng, bearing_deg, dist_m):
    R = 6371000
    b = math.radians(bearing_deg)
    la = math.radians(lat)
    lo = math.radians(lng)
    la2 = math.asin(math.sin(la)*math.cos(dist_m/R) +
                    math.cos(la)*math.sin(dist_m/R)*math.cos(b))
    lo2 = lo + math.atan2(math.sin(b)*math.sin(dist_m/R)*math.cos(la),
                          math.cos(dist_m/R) - math.sin(la)*math.sin(la2))
    return math.degrees(la2), math.degrees(lo2)

def dynamic_score(node, period, weather) -> float:
    s = node.get("score", {})
    base = s.get(period, s.get("all", 50))
    boost = WEATHER_BOOST.get(weather, 0) * node.get("rain_sensitivity", 1.0)
    return min(100.0, base + boost)

def calc_risk(coords, nodes, period, weather):
    penalty, hits = 0.0, []
    for node in nodes:
        s = dynamic_score(node, period, weather)
        if s < 50:
            continue
        min_dist = min(haversine(node["lat"], node["lng"], c[0], c[1]) for c in coords)
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

# ── ORS 路線請求 ──────────────────────────────────────────────────
async def ors_route(waypoints: list[dict]) -> Optional[dict]:
    """
    waypoints: [{"lat":..,"lng":..}, ...]
    回傳 {"coords":[[lat,lng],...], "duration":秒, "distance":公尺}
    """
    coords = [[p["lng"], p["lat"]] for p in waypoints]
    body = {"coordinates": coords}

    try:
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
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"ORS 請求失敗：{e}")

    routes = data.get("routes")
    if not routes:
        raise RuntimeError("ORS 無結果")

    rt = routes[0]
    # ORS geojson geometry
    geom = rt.get("geometry")
    if isinstance(geom, dict):
        raw = geom["coordinates"]
    else:
        # encoded polyline fallback
        raise RuntimeError("ORS 回傳格式不支援，請確認 geometries 參數")

    return {
        "coords":   [[c[1], c[0]] for c in raw],
        "duration": rt["summary"]["duration"],
        "distance": rt["summary"]["distance"],
    }

async def ors_route_geojson(waypoints):
    """明確要求 geojson 格式"""
    coords = [[p["lng"], p["lat"]] for p in waypoints]
    body = {
        "coordinates": coords,
        "geometry_simplify": False,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            ORS_BASE + "?geometries=geojson",
            json=body,
            headers={
                "Authorization": ORS_KEY,
                "Content-Type":  "application/json",
                "Accept":        "application/json, application/geo+json",
            }
        )
        if r.status_code != 200:
            raise RuntimeError(f"ORS HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()

    # DEBUG：印出完整結構幫助診斷
    import json as _json
    print(f"[ORS DEBUG] 頂層keys: {list(data.keys())}")
    print(f"[ORS DEBUG] 原始回傳前800字: {_json.dumps(data, ensure_ascii=False)[:800]}")

    # GeoJSON Feature response
    if "features" in data:
        feat = data["features"][0]
        raw  = feat["geometry"]["coordinates"]
        props = feat["properties"]["segments"][0]
        return {
            "coords":   [[c[1], c[0]] for c in raw],
            "duration": props["duration"],
            "distance": props["distance"],
        }
    # routes response
    if "routes" in data:
        rt = data["routes"][0]
        geom = rt.get("geometry", {})
        if isinstance(geom, dict):
            raw = geom["coordinates"]
        else:
            raise RuntimeError(f"ORS geometry 格式錯誤: {type(geom)}")
        return {
            "coords":   [[c[1], c[0]] for c in raw],
            "duration": rt["summary"]["duration"],
            "distance": rt["summary"]["distance"],
        }
    raise RuntimeError(f"ORS 回傳未知格式：{list(data.keys())}")

# ── 產生繞開點 ────────────────────────────────────────────────────
def make_detour_waypoint(node, fast_coords):
    """在高風險路口旁邊，垂直路線方向偏移 DETOUR_OFFSET_M 公尺"""
    nlat, nlng = node["lat"], node["lng"]

    # 找路線上最近的點
    best_i = min(range(len(fast_coords)),
                 key=lambda i: haversine(nlat, nlng, fast_coords[i][0], fast_coords[i][1]))

    i = best_i
    n = len(fast_coords)
    if i > 0 and i < n-1:
        dlat = fast_coords[i+1][0] - fast_coords[i-1][0]
        dlng = fast_coords[i+1][1] - fast_coords[i-1][1]
    elif i > 0:
        dlat = fast_coords[i][0] - fast_coords[i-1][0]
        dlng = fast_coords[i][1] - fast_coords[i-1][1]
    else:
        dlat = fast_coords[min(i+1,n-1)][0] - fast_coords[i][0]
        dlng = fast_coords[min(i+1,n-1)][1] - fast_coords[i][1]

    # 路線方向角，垂直方向 +90
    bearing = math.degrees(math.atan2(dlng, dlat)) + 90

    # 往兩側各試一個，回傳兩個候選
    pts = []
    for side in [0, 180]:
        wlat, wlng = offset_point(nlat, nlng, bearing + side, DETOUR_OFFSET_M)
        pts.append({"lat": wlat, "lng": wlng})
    return pts

# ── 主函數 ────────────────────────────────────────────────────────
async def compute_routes(origin, dest, nodes,
                         period="day", weather="晴天", vehicle_mode="car"):

    # ① 最快路線
    try:
        fast_rt = await ors_route_geojson([origin, dest])
    except Exception as e:
        raise RuntimeError(f"無法取得最快路線：{e}")

    fast_penalty, fast_hits = calc_risk(fast_rt["coords"], nodes, period, weather)
    high_nodes = [h for h in fast_hits if h["score"] >= 70]

    print(f"[route] 最快路線 {fast_rt['duration']/60:.1f}分 "
          f"懲罰={fast_penalty:.0f}秒 高風險={len(high_nodes)}個")

    # ② 對前 N_DETOURS 個高風險路口各嘗試繞開
    best_safe_rt      = None
    best_safe_penalty = fast_penalty
    best_safe_hits    = fast_hits

    candidates = high_nodes[:N_DETOURS] or fast_hits[:N_DETOURS]

    for node in candidates:
        detour_pts = make_detour_waypoint(node, fast_rt["coords"])
        for wp in detour_pts:
            try:
                candidate = await ors_route_geojson([origin, wp, dest])
            except Exception as e:
                print(f"[route]   繞開失敗 ({node['name']}): {e}")
                continue

            c_penalty, c_hits = calc_risk(candidate["coords"], nodes, period, weather)
            extra = candidate["duration"] - fast_rt["duration"]

            print(f"[route]   繞開 {node['name']}: "
                  f"懲罰={c_penalty:.0f}秒 多花={extra/60:.1f}分")

            # 接受條件：風險下降 且 不超時 MAX_EXTRA_SEC
            if c_penalty < best_safe_penalty and extra <= MAX_EXTRA_SEC:
                best_safe_rt      = candidate
                best_safe_penalty = c_penalty
                best_safe_hits    = c_hits

    if best_safe_rt:
        safe_rt    = best_safe_rt
        safe_penalty = best_safe_penalty
        safe_hits  = best_safe_hits
        print(f"[route] 安全路線確立，風險降低 {fast_penalty-safe_penalty:.0f}秒")
    else:
        safe_rt    = fast_rt
        safe_penalty = fast_penalty
        safe_hits  = fast_hits
        print("[route] 無更好替代路線，安全路線=最快路線")

    def summarize(rt, hits, label):
        high = [n for n in hits if n["score"] >= 70]
        mid  = [n for n in hits if 50 <= n["score"] < 70]
        # 移除 lat/lng 欄位再回傳（前端不需要）
        clean_hits = [{k:v for k,v in h.items() if k not in ("lat","lng")} for h in hits]
        return {
            "label":        label,
            "coords":       rt["coords"],
            "duration":     rt["duration"],
            "duration_min": round(rt["duration"] / 60, 1),
            "distance_km":  round(rt["distance"] / 1000, 2),
            "risk_penalty": round(fast_penalty if label=="最快路線" else safe_penalty),
            "risk_nodes":   clean_hits[:8],
            "high_count":   len(high),
            "mid_count":    len(mid),
        }

    fast_s = summarize(fast_rt, fast_hits, "最快路線")
    safe_s = summarize(safe_rt, safe_hits, "安全路線")

    extra_sec = safe_rt["duration"] - fast_rt["duration"]
    same = (fast_rt["coords"] == safe_rt["coords"])

    return {
        "fast": fast_s,
        "safe": safe_s,
        "comparison": {
            "same_route":        same,
            "extra_seconds":     round(extra_sec),
            "extra_minutes":     round(extra_sec / 60, 1),
            "saved_high":        fast_s["high_count"] - safe_s["high_count"],
            "saved_mid":         fast_s["mid_count"]  - safe_s["mid_count"],
            "saved_penalty_sec": round(fast_penalty - safe_penalty),
            "routes_available":  2 if not same else 1,
        }
    }

"""
安全路線規劃引擎
參考：Berhanu et al. (2024) - 累計風險最低路徑

邏輯：
1. 前端送來 origin / dest 座標
2. 後端用 OSRM 公開 API 取得多條備選路線的完整路網點
3. 對每條路線計算「加權成本」= 時間成本 + 風險懲罰
4. 回傳最快路線 + 最安全路線，附上詳細比較

風險懲罰公式：
  每個高風險路口（距路線 150m 內）加 score × 10 秒懲罰
  中風險路口加 score × 3 秒懲罰
  → 讓路線演算法「感受到」繞開高風險路口是值得的
"""

import math
import httpx
from typing import Optional

OSRM_BASE = "https://router.project-osrm.org/route/v1/driving"

# ── 距離計算（Haversine，公尺）────────────────────────────────────
def haversine(lat1, lng1, lat2, lng2) -> float:
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


# ── 動態風險分數（考慮時段 + 天候）──────────────────────────────────
WEATHER_BOOST = {"晴天": 0, "陰天": 3, "小雨": 12, "大雨": 22}
PERIOD_MAP = {"peak": "peak", "day": "day", "eve": "eve", "night": "night"}

def dynamic_score(node: dict, period: str, weather: str) -> float:
    score_obj = node.get("score", {})
    base = score_obj.get(period, score_obj.get("all", 50))
    boost_base = WEATHER_BOOST.get(weather, 0)
    sensitivity = node.get("rain_sensitivity", 1.0)
    return min(100.0, base + boost_base * sensitivity)


# ── 路線上的風險懲罰（秒）────────────────────────────────────────
def calc_risk_penalty(coords: list[list[float]], nodes: list[dict],
                      period: str, weather: str,
                      radius_m: float = 150) -> tuple[float, list[dict]]:
    """
    回傳 (總懲罰秒數, 途經風險路口清單)
    """
    penalty = 0.0
    hit_nodes = []
    for node in nodes:
        s = dynamic_score(node, period, weather)
        if s < 50:
            continue
        # 找最近的路線點
        min_dist = min(
            haversine(node["lat"], node["lng"], c[0], c[1])
            for c in coords
        )
        if min_dist > radius_m:
            continue
        # 懲罰：高風險 score×10秒，中風險 score×3秒
        w = 10 if s >= 70 else 3
        penalty += s * w
        hit_nodes.append({
            "id": node["id"],
            "name": node["name"],
            "dist": node.get("dist", ""),
            "score": round(s, 1),
            "level": "高風險" if s >= 70 else "中風險",
            "dist_m": round(min_dist),
            "hints": node.get("hints", {}),
            "note": node.get("note", ""),
        })
    hit_nodes.sort(key=lambda x: -x["score"])
    return penalty, hit_nodes


# ── OSRM 取路線（含備選）────────────────────────────────────────
async def fetch_osrm_routes(origin: dict, dest: dict,
                             alternatives: bool = True) -> Optional[list]:
    """
    回傳 list of { coords, duration, distance }
    coords = [[lat, lng], ...]
    """
    coord_str = (
        f"{origin['lng']},{origin['lat']}"
        f";{dest['lng']},{dest['lat']}"
    )
    params = {
        "alternatives": "true" if alternatives else "false",
        "overview":     "full",
        "geometries":   "geojson",
        "steps":        "false",
    }
    url = f"{OSRM_BASE}/{coord_str}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        raise RuntimeError(f"OSRM 請求失敗：{e}")

    if data.get("code") != "Ok" or not data.get("routes"):
        raise RuntimeError(f"OSRM 無結果：{data.get('code')}")

    routes = []
    for rt in data["routes"]:
        coords_geojson = rt["geometry"]["coordinates"]  # [lng, lat]
        coords = [[c[1], c[0]] for c in coords_geojson]  # 轉成 [lat, lng]
        routes.append({
            "coords":   coords,
            "duration": rt["duration"],   # 秒
            "distance": rt["distance"],   # 公尺
        })
    return routes


# ── 主函數：計算最快 + 最安全路線 ────────────────────────────────
async def compute_routes(
    origin: dict, dest: dict, nodes: list[dict],
    period: str = "day", weather: str = "晴天",
    vehicle_mode: str = "car"
) -> dict:
    """
    回傳：
    {
      fast:   { coords, duration, distance, risk_penalty, risk_nodes, summary },
      safe:   { coords, duration, distance, risk_penalty, risk_nodes, summary },
      comparison: { saved_penalty_sec, saved_high, saved_mid, extra_seconds }
    }
    """
    routes = await fetch_osrm_routes(origin, dest, alternatives=True)
    if not routes:
        raise RuntimeError("無法取得路線")

    # 計算每條路線的加權成本
    scored = []
    for rt in routes:
        penalty, hit = calc_risk_penalty(rt["coords"], nodes, period, weather)
        scored.append({
            **rt,
            "risk_penalty": penalty,
            "risk_nodes":   hit,
            "weighted_cost": rt["duration"] + penalty,  # 時間 + 風險懲罰
        })

    # 最快：時間最短
    fast = min(scored, key=lambda x: x["duration"])
    # 最安全：加權成本最低（時間 + 風險懲罰）
    safe = min(scored, key=lambda x: x["weighted_cost"])

    def summarize(rt, label):
        high = [n for n in rt["risk_nodes"] if n["score"] >= 70]
        mid  = [n for n in rt["risk_nodes"] if 50 <= n["score"] < 70]
        return {
            "label":       label,
            "coords":      rt["coords"],
            "duration":    rt["duration"],
            "duration_min": round(rt["duration"] / 60, 1),
            "distance_km": round(rt["distance"] / 1000, 2),
            "risk_penalty": round(rt["risk_penalty"]),
            "risk_nodes":  rt["risk_nodes"][:8],  # 最多回傳8個
            "high_count":  len(high),
            "mid_count":   len(mid),
        }

    fast_s = summarize(fast, "最快路線")
    safe_s = summarize(safe, "安全路線")

    # 比較資訊
    extra_sec = safe["duration"] - fast["duration"]
    comparison = {
        "same_route":        fast["coords"] == safe["coords"],
        "extra_seconds":     round(extra_sec),
        "extra_minutes":     round(extra_sec / 60, 1),
        "saved_high":        fast_s["high_count"] - safe_s["high_count"],
        "saved_mid":         fast_s["mid_count"]  - safe_s["mid_count"],
        "saved_penalty_sec": round(fast["risk_penalty"] - safe["risk_penalty"]),
        "routes_available":  len(routes),
    }

    return {"fast": fast_s, "safe": safe_s, "comparison": comparison}

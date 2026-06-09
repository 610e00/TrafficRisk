"""
施工排程 API
從 TDX 即時抓取桃園市中壢區道路施工事件
端點：GET /api/construction
"""
import re
import os
import requests
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()

# ── 填你自己的 TDX 金鑰 ──────────────────────────
CLIENT_ID     = os.environ.get("TDX_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("TDX_CLIENT_SECRET", "")
# ─────────────────────────────────────────────────

TDX_TOKEN_URL = "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token"
TDX_EVENT_URL = "https://tdx.transportdata.tw/api/basic/v1/Traffic/RoadEvent/LiveEvent/City/Taoyuan"


def get_token() -> str:
    res = requests.post(
        TDX_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type":    "client_credentials",
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        timeout=10,
    )
    res.raise_for_status()
    return res.json()["access_token"]


def parse_point(positions: str):
    """從 'POINT(lng lat)' 取出座標"""
    m = re.search(r"POINT\(([0-9.]+)\s+([0-9.]+)\)", positions or "")
    if m:
        return float(m.group(2)), float(m.group(1))  # lat, lng
    return None, None


def parse_event(e: dict):
    loc_other = e.get("Location", {}).get("Other", "")
    imp       = e.get("Impact", {})

    # 只留施工（EventType == 2）
    if e.get("EventType") != 2:
        return None

    # 只留中壢區
    if "中壢" not in loc_other:
        return None

    lat, lng = parse_point(e.get("Positions", ""))

    return {
        "id":                 e.get("EventID", ""),
        "type":               e.get("EventTitle", "道路施工"),
        "description":        e.get("Description", ""),
        "location":           loc_other,
        "district":           "中壢區",
        "severity":           imp.get("Severity", 0),
        "description_impact": imp.get("Description", ""),
        "start":              e.get("EffectiveTime", "")[:10],
        "end":                "",
        "lat":                lat,
        "lng":                lng,
        "status":             "進行中",
    }


@router.get("/")
def get_construction():
    try:
        token = get_token()
        res = requests.get(
            TDX_EVENT_URL,
            headers={"Authorization": f"Bearer {token}"},
            params={"$format": "JSON", "$top": 200},
            timeout=10,
        )
        res.raise_for_status()
        raw = res.json()

        items = []
        for e in raw.get("LiveEvents", []):
            parsed = parse_event(e)
            if parsed:
                items.append(parsed)

        return JSONResponse(content={"items": items, "count": len(items)})

    except Exception as e:
        return JSONResponse(
            status_code=200,
            content={"items": [], "count": 0, "error": str(e)}
        )

"""
消防局出警即時資料
端點：GET /api/fire
"""
import re
import time
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from bs4 import BeautifulSoup
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()

TARGET_DISTRICT = "中壢區"
TARGET_EVENT    = "車禍"

def geocode(location: str) -> tuple:
    """用 Nominatim 把路名轉經緯度"""
    try:
        res = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": f"桃園市{location}",
                "format": "json",
                "limit": 1,
            },
            headers={"User-Agent": "TrafficRiskPlatform/1.0"},
            timeout=5,
        )
        data = res.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception:
        pass
    return None, None


def scrape_fire() -> list:
    res = requests.get(
    "https://www.tyfd.gov.tw/cht/index.php?act=caselist",
    headers={"User-Agent": "Mozilla/5.0"},
    timeout=15,
    verify=False,
    )
    res.encoding = "utf-8"
    soup = BeautifulSoup(res.text, "html.parser")
    full_text = " ".join(
        line.strip() for line in soup.get_text().splitlines() if line.strip()
    )

    items = []
    seen = set()

    for block in full_text.split("案別"):
        if not block.strip().startswith(TARGET_EVENT):
            continue
        if TARGET_DISTRICT not in block:
            continue

        clean = " ".join(block.split())

        # 取路名作為唯一 key
        m = re.search(rf"{TARGET_DISTRICT}\S+", clean)
        if not m:
            continue
        loc_key = m.group(0)
        if loc_key in seen:
            continue
        seen.add(loc_key)

        # 取時間
        time_m = re.search(r"\d{2}:\d{2}", clean)
        event_time = time_m.group(0) if time_m else "—"

        # 地理編碼（每筆間隔 1 秒，遵守 Nominatim 限制）
        lat, lng = geocode(loc_key)
        time.sleep(1)

        items.append({
            "id":       f"FIRE-{loc_key}",
            "type":     "車禍出警",
            "location": loc_key,
            "district": TARGET_DISTRICT,
            "time":     event_time,
            "detail":   f"案別 {clean[:80]}…",
            "lat":      lat,
            "lng":      lng,
            "status":   "出警中",
        })

    return items


@router.get("/")
def get_fire():
    try:
        items = scrape_fire()
        return JSONResponse(content={"items": items, "count": len(items)})
    except Exception as e:
        return JSONResponse(
            status_code=200,
            content={"items": [], "count": 0, "error": str(e)}
        )
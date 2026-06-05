"""
施工排程 API
政府後台組員完成後直接呼叫 POST /api/construction 寫入資料
格式參考 Section 8.1 of VibeCoding spec
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import json, os
from datetime import datetime, date

router = APIRouter()
DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "construction.json")

class Construction(BaseModel):
    title:       str
    description: Optional[str] = ""
    lat:         float
    lng:         float
    road_name:   Optional[str] = ""
    start_date:  str   # "2026-06-01"
    end_date:    str   # "2026-06-05"
    agency:      Optional[str] = "工務局"

def load_all():
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            return json.load(f).get("items", [])
    except FileNotFoundError:
        return []

def save_all(items):
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump({"items": items, "updated_at": datetime.now().isoformat()},
                  f, ensure_ascii=False, indent=2)

def is_active(item: dict) -> bool:
    """判斷施工是否在有效期間內"""
    today = date.today().isoformat()
    return item["start_date"] <= today <= item["end_date"]

@router.get("/")
def get_construction(active_only: bool = True):
    """取得施工排程，active_only=True 只回傳今天有效的施工"""
    items = load_all()
    if active_only:
        items = [i for i in items if is_active(i)]
    for item in items:
        item["is_active"] = is_active(item)
    return {"items": items, "count": len(items)}

@router.post("/")
def add_construction(item: Construction):
    """新增施工排程（政府後台呼叫）"""
    items = load_all()
    new_item = item.dict()
    new_item["id"] = len(items) + 1
    new_item["created_at"] = datetime.now().isoformat()
    items.append(new_item)
    save_all(items)
    return {"status": "ok", "id": new_item["id"]}

@router.delete("/{item_id}")
def delete_construction(item_id: int):
    """刪除施工排程"""
    items = [i for i in load_all() if i.get("id") != item_id]
    save_all(items)
    return {"status": "ok"}

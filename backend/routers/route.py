from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field
from typing import Literal
from .routing_engine import compute_routes

router = APIRouter()

class RouteRequest(BaseModel):
    origin_lat:   float = Field(..., example=24.969)
    origin_lng:   float = Field(..., example=121.224)
    dest_lat:     float = Field(..., example=24.990)
    dest_lng:     float = Field(..., example=121.308)
    period:       Literal["peak","day","eve","night"] = "day"
    weather:      Literal["晴天","陰天","小雨","大雨"] = "晴天"
    vehicle_mode: Literal["car","moto"] = "car"

@router.post("/")
async def get_route(req: RouteRequest, request: Request):
    nodes = request.app.state.risk_data.get("nodes", [])
    if not nodes:
        raise HTTPException(503, "風險資料尚未載入")
    try:
        result = await compute_routes(
            origin  = {"lat": req.origin_lat, "lng": req.origin_lng},
            dest    = {"lat": req.dest_lat,   "lng": req.dest_lng},
            nodes   = nodes,
            period  = req.period,
            weather = req.weather,
            vehicle_mode = req.vehicle_mode,
        )
        return result
    except RuntimeError as e:
        raise HTTPException(502, str(e))

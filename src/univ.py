"""대학교 캠퍼스 (초중고 학교 위치 자료에는 대학이 없어서 따로 둠).

config/universities.yaml 에 캠퍼스 경계(다각형)를 넣어 두고, 거리는 캠퍼스 '경계'까지 잽니다
(캠퍼스 안이면 0m). 대학은 거의 바뀌지 않아 자동 수집하지 않습니다.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

YAML = Path(__file__).resolve().parents[1] / "config" / "universities.yaml"


@lru_cache(maxsize=1)
def _load() -> dict:
    try:
        with open(YAML, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def campuses(region_key: str) -> list[dict]:
    return _load().get("regions", {}).get(region_key, []) or []


def _xy(lat0: float, lon0: float, lat: float, lon: float) -> tuple[float, float]:
    return ((lon - lon0) * 111_320 * math.cos(math.radians(lat0)), (lat - lat0) * 110_540)


def boundary_distance(lat: float, lon: float, polygon: list) -> float:
    """점에서 다각형 경계까지 거리(m). 안쪽이면 0."""
    pts = [_xy(lat, lon, y, x) for x, y in polygon]
    inside = False
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        if (y1 > 0) != (y2 > 0) and 0 < (x2 - x1) * (0 - y1) / (y2 - y1) + x1:
            inside = not inside
    if inside:
        return 0.0
    best = float("inf")
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        dx, dy = x2 - x1, y2 - y1
        t = max(0.0, min(1.0, -(x1 * dx + y1 * dy) / (dx * dx + dy * dy or 1)))
        best = min(best, math.hypot(x1 + t * dx, y1 + t * dy))
    return best


def center(polygon: list) -> tuple[float, float]:
    xs, ys = [p[0] for p in polygon], [p[1] for p in polygon]
    return sum(ys) / len(ys), sum(xs) / len(xs)


def near(region_key: str, lat: float, lon: float, radius: int) -> pd.DataFrame:
    rows = []
    for c in campuses(region_key):
        d = boundary_distance(lat, lon, c["polygon"])
        if d <= radius:
            clat, clon = center(c["polygon"])
            rows.append({"name": c["name"], "level": c.get("kind", "대학교"), "addr": c.get("addr", ""), "dist": d,
                         "lat": clat, "lon": clon, "campus": len(c["polygon"]) > 2})
    return pd.DataFrame(rows, columns=["name", "level", "addr", "dist", "lat", "lon", "campus"])


def table(region_key: str) -> pd.DataFrame:
    rows = []
    for c in campuses(region_key):
        clat, clon = center(c["polygon"])
        rows.append({"name": c["name"], "level": c.get("kind", "대학교"), "addr": c.get("addr", ""), "lat": clat, "lon": clon,
                     "weight": c.get("weight", 1.0)})
    return pd.DataFrame(rows, columns=["name", "level", "addr", "lat", "lon", "weight"])


def with_dong(region_key: str, stores: pd.DataFrame) -> pd.DataFrame:
    """캠퍼스 가운데에서 가장 가까운 점포의 행정동을 붙입니다."""
    t = table(region_key)
    if t.empty or stores is None or stores.empty:
        return t.assign(adong_cd=None, adong_nm=None, sigungu_cd=None)
    s = stores.dropna(subset=["lat", "lon"])
    cds, nms, sgg = [], [], []
    for _, r in t.iterrows():
        d = (s["lat"] - r["lat"]) ** 2 + ((s["lon"] - r["lon"]) * math.cos(math.radians(r["lat"]))) ** 2
        i = d.idxmin()
        cds.append(s.at[i, "adong_cd"]), nms.append(s.at[i, "adong_nm"]), sgg.append(s.at[i, "sigungu_cd"])
    return t.assign(adong_cd=cds, adong_nm=nms, sigungu_cd=sgg)


REACH_M = 1500  # 상권 중심에서 이 거리 안의 캠퍼스만 점수에 넣음 (가까울수록 크게)


def dong_scores(region_key: str, centers: pd.DataFrame, campus_dong: dict | None = None) -> pd.DataFrame:
    """동네별 대학 근접 점수.

    centers: index=동 코드, 열 lat/lon (상권 중심). campus_dong: {캠퍼스 이름: 동 코드}.
    raw = Σ 가중치 × max(0, 1 − 경계까지 거리/1.5km)  (캠퍼스가 그 동네 안이면 최소 0.5)
    """
    cs = campuses(region_key)
    raw, near_txt = [], []
    for cd, r in centers.iterrows():
        tot, items = 0.0, []
        for c in cs:
            if pd.isna(r["lat"]):
                continue
            d = boundary_distance(r["lat"], r["lon"], c["polygon"])
            prox = max(0.0, 1 - d / REACH_M)
            inside = bool(campus_dong and campus_dong.get(c["name"]) == cd)
            if inside:
                prox = max(prox, 0.5)
            if prox > 0:
                tot += c.get("weight", 1.0) * prox
                where = "상권 안" if d < 1 else f"{d / 1000:.1f}km"
                items.append((d, f"{c['name']} {where}" + (" · 동네 안" if inside and d >= 1 else "")))
        raw.append(tot)
        near_txt.append(", ".join(t for _, t in sorted(items)[:2]))
    out = pd.DataFrame({"univ_raw": raw, "univ_near": near_txt}, index=centers.index)
    mx = out["univ_raw"].max()
    out["univ_score"] = (out["univ_raw"] / mx * 100) if mx and mx > 0 else 0.0
    return out

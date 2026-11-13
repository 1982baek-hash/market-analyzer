"""내 매장 화면 계산: 한 지점(위도·경도) 반경 안의 경쟁점 현황과 변화.

- 지금 있는 점포: 소상공인 상가정보 최신 스냅샷 (업종 소분류 기준)
- 개업·폐업 추이: 휴게음식점 인허가 이력 (개업일·폐업일이 있어 과거로 거슬러 셀 수 있음)
- 상가정보 스냅샷이 2개 이상 쌓이면 두 시점을 비교해 새로 생긴·없어진 점포도 보여 줍니다.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from src import scoring

MY_STORES_YAML = scoring.ROOT / "config" / "my_stores.yaml"
RADII = (300, 500, 1000)

# 인허가(휴게음식점) 업태 → 카페 / 간식점 묶음
LICENSE_GROUPS = {
    "cafe": ["커피숍", "다방", "전통찻집", "떡카페"],
    "snack": ["일반조리판매", "과자점", "아이스크림", "패스트푸드", "푸드트럭", "기타 휴게음식점"],
}
GROUP_LABEL = {"cafe": "카페", "snack": "간식점"}


# ---------------------------------------------------------------- 거리
def distance_m(lat: float, lon: float, lats, lons) -> np.ndarray:
    """두 지점 사이 거리(m, 하버사인)."""
    lat1, lon1 = np.radians(lat), np.radians(lon)
    lat2, lon2 = np.radians(np.asarray(lats, dtype=float)), np.radians(np.asarray(lons, dtype=float))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000 * np.arcsin(np.sqrt(a))


def _near(df: pd.DataFrame | None, lat: float, lon: float, radius: int) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    d = df.dropna(subset=["lat", "lon"]).copy()
    d["dist"] = distance_m(lat, lon, d["lat"], d["lon"])
    return d[d["dist"] <= radius].sort_values("dist")


# ---------------------------------------------------------------- 내 매장 목록
def load_my_stores(region_key: str | None = None) -> list[dict]:
    """config/my_stores.yaml 의 매장·후보지 목록."""
    if not MY_STORES_YAML.exists():
        return []
    with open(MY_STORES_YAML, encoding="utf-8") as f:
        items = (yaml.safe_load(f) or {}).get("stores") or []
    out = []
    for it in items:
        try:
            it = {**it, "lat": float(it["lat"]), "lon": float(it["lon"])}
        except (KeyError, TypeError, ValueError):
            continue
        if region_key is None or it.get("region") == region_key:
            out.append(it)
    return out


def search_places(data: dict, query: str, limit: int = 20) -> pd.DataFrame:
    """주소·가게 이름으로 위치 찾기 (상가정보·인허가 자료의 좌표를 빌려 씀)."""
    q = (query or "").strip()
    if len(q) < 2:
        return pd.DataFrame(columns=["label", "lat", "lon"])
    rows = []
    s = data.get("stores")
    if s is not None:
        hit = s[s["rdnm_adr"].fillna("").str.contains(q, regex=False)
                | s["lno_adr"].fillna("").str.contains(q, regex=False)
                | s["store_name"].fillna("").str.contains(q, regex=False)]
        for _, r in hit.head(limit).iterrows():
            addr = r["rdnm_adr"] if isinstance(r["rdnm_adr"], str) and r["rdnm_adr"] else r["lno_adr"]
            rows.append({"label": f"{r['store_name']} · {addr}", "lat": r["lat"], "lon": r["lon"]})
    for lic in data.get("licenses", {}).values():
        if len(rows) >= limit:
            break
        hit = lic[lic["addr"].fillna("").str.contains(q, regex=False)
                  | lic["biz_name"].fillna("").str.contains(q, regex=False)].dropna(subset=["lat", "lon"])
        for _, r in hit.head(limit - len(rows)).iterrows():
            rows.append({"label": f"{r['biz_name']} ({r['status_nm']}) · {r['addr']}", "lat": r["lat"], "lon": r["lon"]})
    out = pd.DataFrame(rows, columns=["label", "lat", "lon"]).dropna()
    return out.drop_duplicates("label").head(limit).reset_index(drop=True)


# ---------------------------------------------------------------- 지금 있는 점포
def group_scls(cfg: dict) -> dict:
    groups = cfg["presets"]["coffee_snack"]["groups"]
    return {g: [str(c) for c in groups[g]["scls"]] for g in ("cafe", "snack")}


def stores_near(data: dict, cfg: dict, lat: float, lon: float, radius: int, groups=("cafe", "snack")) -> pd.DataFrame:
    """반경 안 카페·간식점 (상가정보 최신 스냅샷)."""
    scls = group_scls(cfg)
    near = _near(data.get("stores"), lat, lon, radius)
    if near.empty:
        return near
    near["group"] = None
    for g in groups:
        near.loc[near["inds_scls_cd"].astype(str).isin(scls[g]), "group"] = g
    near = near[near["group"].notna()].copy()
    brands = cfg.get("lowcost_coffee_brands", [])
    name = near["store_name"].fillna("")
    near["lowcost"] = (near["group"] == "cafe") & name.apply(lambda n: any(b in n for b in brands))
    return near


def context_near(data: dict, lat: float, lon: float, radius: int) -> dict:
    """반경 안 버스정류장·학교 수, 전체 점포 수."""
    bus = _near(data.get("busstops"), lat, lon, radius)
    sch = _near(data.get("schools"), lat, lon, radius)
    allst = _near(data.get("stores"), lat, lon, radius)
    levels = sch["level"].value_counts().to_dict() if not sch.empty and "level" in sch else {}
    return {"bus": len(bus), "schools": len(sch), "school_levels": levels, "stores": len(allst), "school_list": sch}


# ---------------------------------------------------------------- 인허가로 본 변화
def licenses_near(data: dict, lat: float, lon: float, radius: int, groups=("cafe", "snack")) -> pd.DataFrame:
    lic = data.get("licenses", {}).get("rest_cafes")
    near = _near(lic, lat, lon, radius)
    if near.empty:
        return near
    near["group"] = None
    for g in groups:
        near.loc[near["bzstat_nm"].isin(LICENSE_GROUPS[g]), "group"] = g
    near = near[near["group"].notna()].copy()
    near["open_dt"] = scoring._to_date(near["open_date"])
    near["close_dt"] = scoring._to_date(near["close_date"])
    near["closed"] = near["status_cd"].eq("03")
    # 폐업했는데 폐업일이 없는 건 언제 문을 닫았는지 알 수 없어 추이 계산에서 뺀다
    return near[~(near["closed"] & near["close_dt"].isna()) & near["open_dt"].notna()]


def monthly_counts(lic_near: pd.DataFrame, months: int = 60, today: date | None = None) -> pd.DataFrame:
    """월말 기준 영업 중인 가게 수 (묶음별). 열: month, group, count"""
    today = pd.Timestamp(today or date.today())
    ends = pd.date_range(end=today.to_period("M").to_timestamp("M"), periods=months, freq="ME")
    rows = []
    for g, sub in lic_near.groupby("group"):
        op, cl, closed = sub["open_dt"].values, sub["close_dt"].values, sub["closed"].values
        for m in ends:
            mv = np.datetime64(m)
            alive = (op <= mv) & (~closed | (cl > mv))
            rows.append({"month": m, "group": g, "count": int(alive.sum())})
    return pd.DataFrame(rows, columns=["month", "group", "count"])


def recent_changes(lic_near: pd.DataFrame, days: int = 365, today: date | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """최근 N일 안에 문을 연 / 닫은 가게."""
    today = pd.Timestamp(today or date.today())
    since = today - pd.Timedelta(days=days)
    cols = ["biz_name", "bzstat_nm", "open_dt", "close_dt", "dist", "addr", "group"]
    if lic_near.empty:
        empty = pd.DataFrame(columns=cols)
        return empty, empty
    opened = lic_near[lic_near["open_dt"] > since].sort_values("open_dt", ascending=False)[cols]
    closed = lic_near[lic_near["closed"] & (lic_near["close_dt"] > since)].sort_values("close_dt", ascending=False)[cols]
    return opened, closed


# ---------------------------------------------------------------- 상가정보 두 시점 비교
def store_snapshots(region_key: str) -> list[Path]:
    return sorted((scoring.MART / "stores").glob(f"{region_key}_*.parquet"))


def snapshot_diff(region_key: str, cfg: dict, lat: float, lon: float, radius: int, groups=("cafe", "snack")):
    """가장 최근 두 스냅샷의 반경 안 점포를 비교합니다. 스냅샷이 하나뿐이면 None."""
    snaps = store_snapshots(region_key)
    if len(snaps) < 2:
        return None
    prev_p, cur_p = snaps[-2], snaps[-1]
    prev = stores_near({"stores": pd.read_parquet(prev_p)}, cfg, lat, lon, radius, groups)
    cur = stores_near({"stores": pd.read_parquet(cur_p)}, cfg, lat, lon, radius, groups)
    cols = ["store_name", "inds_scls_nm", "dist", "rdnm_adr", "group"]
    added = cur[~cur["store_id"].isin(prev["store_id"])][cols] if not cur.empty else pd.DataFrame(columns=cols)
    removed = prev[~prev["store_id"].isin(cur["store_id"])][cols] if not prev.empty else pd.DataFrame(columns=cols)
    return {"prev": prev_p.stem.split("_")[-1], "cur": cur_p.stem.split("_")[-1], "added": added, "removed": removed,
            "prev_cnt": len(prev), "cur_cnt": len(cur)}

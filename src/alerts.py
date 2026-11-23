"""새 경쟁점 알림 계산: 최근 N일 안에 새로 영업 신고(인허가)된 카페·간식점.

- 자료: 휴게음식점 인허가 (data/mart/licenses/rest_cafes_{지역}.parquet · 매주 월요일 새벽 자동 갱신)
- '새 경쟁점' = 인허가일이 최근 N일 안이고 지금 영업 중인 카페·간식점 (편의점·백화점 입점 매장 등은 제외)
- 축제·야시장 임시 매장은 목록에는 보여 주되 팝업 알림에서는 뺍니다.
- 팝업은 저장된 내 매장(Streamlit Secrets [[my_stores]] 또는 config/my_stores.yaml)에서
  가까운 곳만 알립니다. 멀리 떨어진 동네의 가게는 경쟁점이 아니므로, 내 매장이 없으면 알리지 않습니다.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from src import mystore, scoring

DEFAULTS = {
    "enabled": True,
    "days": 30,          # 최근 며칠 안에 영업 신고된 가게를 '새 경쟁점'으로 볼지
    "radius_m": 1000,    # 내 매장에서 이 거리 안에 생긴 곳만 '경쟁점'으로 보고 팝업으로 알림
    "temp_keywords": ["페스티벌", "축제", "야시장", "행사", "박람회", "어울림마당", "플리마켓"],
    # 편의점이 '기타 휴게음식점'으로 신고되는 일이 있어 상호로 한 번 더 거른다
    "exclude_keywords": ["씨유", "CU ", "지에스25", "지에스(GS)25", "GS25", "세븐일레븐", "이마트24", "미니스톱"],
}

COLS = ["mgmt_no", "biz_name", "bzstat_nm", "group", "open_dt", "adong_nm", "addr", "lat", "lon",
        "lowcost", "temporary", "region", "near_name", "near_dist"]


def settings(cfg: dict | None = None) -> dict:
    """config/business_types.yaml의 alerts 설정 (없는 값은 기본값)."""
    cfg = cfg if cfg is not None else scoring.load_config()
    return {**DEFAULTS, **(cfg.get("alerts") or {})}


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=COLS)


def new_competitors(data: dict, cfg: dict, days: int, today: date | None = None) -> pd.DataFrame:
    """한 지역에서 최근 days일 안에 새로 영업 신고된(지금 영업 중인) 카페·간식점. 최근 개업 순."""
    lic = (data.get("licenses") or {}).get("rest_cafes")
    if lic is None or lic.empty:
        return _empty()
    s = settings(cfg)
    today = pd.Timestamp(today or date.today()).normalize()
    since = today - pd.Timedelta(days=int(days))
    df = lic.copy()
    df["group"] = None
    for g, names in mystore.LICENSE_GROUPS.items():
        df.loc[df["bzstat_nm"].isin(names), "group"] = g
    df["open_dt"] = scoring._to_date(df["open_date"])
    df = df[df["group"].notna() & df["status_cd"].eq("01") & (df["open_dt"] > since) & (df["open_dt"] <= today)].copy()
    if df.empty:
        return _empty()
    name = df["biz_name"].fillna("")
    df = df[~name.apply(lambda n: any(k in n for k in s["exclude_keywords"]))].copy()
    if df.empty:
        return _empty()
    name = df["biz_name"].fillna("")
    brands = cfg.get("lowcost_coffee_brands", [])
    df["lowcost"] = (df["group"] == "cafe") & name.apply(lambda n: any(b in n for b in brands))
    df["temporary"] = name.apply(lambda n: any(k in n for k in s["temp_keywords"]))
    df["region"] = data.get("region_key")
    df["near_name"], df["near_dist"] = None, np.nan
    return df.sort_values(["open_dt", "mgmt_no"], ascending=False)[COLS].reset_index(drop=True)


def attach_nearest(df: pd.DataFrame, stores: list[dict]) -> pd.DataFrame:
    """각 가게에서 가장 가까운 '내 매장' 이름과 거리(m)를 붙입니다. 좌표가 없으면 거리는 비워 둡니다."""
    df = df.copy()
    df["near_name"], df["near_dist"] = None, np.nan
    if df.empty or not stores:
        return df
    has = df["lat"].notna() & df["lon"].notna()
    if not has.any():
        return df
    dist = np.vstack([mystore.distance_m(s["lat"], s["lon"], df.loc[has, "lat"], df.loc[has, "lon"]) for s in stores])
    idx = dist.argmin(axis=0)
    df.loc[has, "near_dist"] = dist[idx, np.arange(dist.shape[1])]
    df.loc[has, "near_name"] = [str(stores[i].get("name") or "내 매장") for i in idx]
    return df


def region_items(region_key: str, days: int, data: dict | None = None, cfg: dict | None = None,
                 stores: list[dict] | None = None, today: date | None = None) -> pd.DataFrame:
    """'새 경쟁점' 화면용: 한 지역의 새 가게 전체 + 가장 가까운 내 매장까지 거리."""
    cfg = cfg if cfg is not None else scoring.load_config()
    data = data if data is not None else scoring.load_region_data(region_key)
    stores = mystore.load_my_stores(region_key) if stores is None else [s for s in stores if s.get("region") == region_key]
    return attach_nearest(new_competitors(data, cfg, days, today), stores)


def popup_items(cfg: dict | None = None, stores: list[dict] | None = None, today: date | None = None,
                loader=None) -> pd.DataFrame:
    """앱을 열 때 팝업으로 알릴 가게: 저장된 내 매장에서 radius_m 안에 새로 생긴 곳.

    - 좌표가 없어 거리를 모르는 곳도 놓치지 않게 포함합니다.
    - 축제·야시장 임시 매장은 뺍니다.
    - 저장된 내 매장이 없으면 알릴 곳도 없습니다 (시 전체를 알리지 않음).
    """
    cfg = cfg if cfg is not None else scoring.load_config()
    s = settings(cfg)
    stores = mystore.load_my_stores() if stores is None else stores
    loader = loader or scoring.load_region_data
    known = scoring.load_regions()
    regions = list(dict.fromkeys(st["region"] for st in stores if st.get("region") in known))
    parts = []
    for rk in regions:
        df = region_items(rk, s["days"], loader(rk), cfg, stores, today)
        df = df[df["near_dist"].isna() | (df["near_dist"] <= float(s["radius_m"]))]
        parts.append(df[~df["temporary"]])
    parts = [p for p in parts if not p.empty]
    if not parts:
        return _empty()
    out = pd.concat(parts, ignore_index=True)
    # 내 매장에서 가까운 순(거리를 모르는 곳은 뒤), 같은 거리면 최근 개업 순
    return out.sort_values(["near_dist", "open_dt"], ascending=[True, False], na_position="last").reset_index(drop=True)


def store_names(stores: list[dict]) -> str:
    """'도그도그' 또는 '도그도그 · 2호점' — 화면에 어느 매장 기준인지 보여 줄 때 씁니다."""
    names = list(dict.fromkeys(str(st.get("name") or "내 매장") for st in stores))
    return " · ".join(names) if names else "내 매장"


def group_label(row) -> str:
    """'카페 · 저가 커피' 같은 짧은 설명."""
    label = mystore.GROUP_LABEL.get(row["group"], "")
    if row.get("lowcost"):
        label += " · 저가 커피 브랜드"
    if row.get("temporary"):
        label += " · 행사 임시 매장"
    return label


def dist_text(row) -> str:
    d = row.get("near_dist")
    if row.get("near_name") is None or pd.isna(d):
        return ""
    return f"{row['near_name']}에서 {int(round(d, -1)):,}m"

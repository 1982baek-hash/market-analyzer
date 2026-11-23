"""새 경쟁점 알림 계산: 최근 N일 안에 내 매장 주변에 새로 영업 신고(인허가)된, 점심을 대신할 수 있는 가게.

- 경쟁점 = 점심 한 끼를 대신할 수 있는 곳 모두
  · 식당: 일반음식점 인허가 전체 (술집 업태는 제외 — config alerts.lunch_exclude_bzstat)
  · 카페·간식점·편의점: 휴게음식점 인허가 (백화점·역 구내·고속도로 휴게소 등은 제외)
- 자료: data/mart/licenses/ (매주 월요일 새벽 자동 갱신)
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
    "temp_keywords": ["페스티벌", "축제", "야시장", "행사", "박람회", "어울림마당", "플리마켓", "부스", "한시적"],
    # 일반음식점 중 점심을 대신하기 어려운 업태 (술집·공연·출장 등)
    "lunch_exclude_bzstat": ["호프/통닭", "정종/대포집/소주방", "라이브카페", "감성주점", "키즈카페", "출장조리"],
}

GROUP_LABEL = {"meal": "식당", "cafe": "카페", "snack": "간식점", "conv": "편의점"}
GROUP_ORDER = ["meal", "cafe", "snack", "conv"]
# 편의점이 '기타 휴게음식점'으로 신고되는 일이 있어 상호로도 알아본다
CONV_NAMES = ["씨유", "CU ", "지에스25", "지에스(GS)25", "GS25", "세븐일레븐", "이마트24", "미니스톱"]
# 일반음식점 중 카페로 볼 업태
GENERAL_CAFE = ["까페", "전통찻집"]
# 상가정보(지금 있는 가게 수) 기준: 음식 대분류 중 주점(I211) 제외 + 편의점(G20405)
STORE_BAR_MCLS = "I211"
STORE_CONV_SCLS = "G20405"
STORE_CAFE_SCLS = "I21201"


COLS = ["mgmt_no", "biz_name", "bzstat_nm", "group", "open_dt", "adong_nm", "addr", "lat", "lon",
        "lowcost", "temporary", "region", "near_name", "near_dist"]


def settings(cfg: dict | None = None) -> dict:
    """config/business_types.yaml의 alerts 설정 (없는 값은 기본값)."""
    cfg = cfg if cfg is not None else scoring.load_config()
    return {**DEFAULTS, **(cfg.get("alerts") or {})}


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=COLS)


def _classify(data: dict, cfg: dict) -> pd.DataFrame:
    """인허가 자료에 경쟁점 묶음(group)을 붙입니다. 경쟁점이 아닌 행은 group이 비어 있습니다."""
    s = settings(cfg)
    lics = data.get("licenses") or {}
    parts = []
    rc = lics.get("rest_cafes")
    if rc is not None and not rc.empty:
        d = rc.copy()
        d["group"] = None
        for g, names in mystore.LICENSE_GROUPS.items():
            d.loc[d["bzstat_nm"].isin(names), "group"] = g
        d.loc[d["bzstat_nm"].eq("편의점"), "group"] = "conv"
        is_conv = d["biz_name"].fillna("").apply(lambda n: any(k in n for k in CONV_NAMES))
        d.loc[is_conv & d["group"].notna(), "group"] = "conv"
        parts.append(d)
    gr = lics.get("general_restaurants")
    if gr is not None and not gr.empty:
        d = gr.copy()
        d["group"] = "meal"
        d.loc[d["bzstat_nm"].isin(s["lunch_exclude_bzstat"]), "group"] = None
        d.loc[d["bzstat_nm"].isin(GENERAL_CAFE), "group"] = "cafe"
        parts.append(d)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def new_competitors(data: dict, cfg: dict, days: int, today: date | None = None) -> pd.DataFrame:
    """한 지역에서 최근 days일 안에 새로 영업 신고된(지금 영업 중인) 점심 경쟁점. 최근 개업 순."""
    lic = _classify(data, cfg)
    if lic.empty:
        return _empty()
    s = settings(cfg)
    today = pd.Timestamp(today or date.today()).normalize()
    since = today - pd.Timedelta(days=int(days))
    df = lic
    df["open_dt"] = scoring._to_date(df["open_date"])
    df = df[df["group"].notna() & df["status_cd"].eq("01") & (df["open_dt"] > since) & (df["open_dt"] <= today)].copy()
    if df.empty:
        return _empty()
    name = df["biz_name"].fillna("")
    brands = cfg.get("lowcost_coffee_brands", [])
    df["lowcost"] = (df["group"] == "cafe") & name.apply(lambda n: any(b in n for b in brands))
    df["temporary"] = name.apply(lambda n: any(k in n for k in s["temp_keywords"]))
    df["region"] = data.get("region_key")
    df["near_name"], df["near_dist"] = None, np.nan
    return df.sort_values(["open_dt", "mgmt_no"], ascending=False)[COLS].reset_index(drop=True)


def _store_ldong(data: dict | None, st: dict) -> str:
    """내 매장 자리의 법정동 이름 (예: '송천동') — 가장 가까운 상가정보 점포에서 빌려 옴."""
    stores = (data or {}).get("stores")
    if stores is None or stores.empty or "ldong_nm" not in stores:
        return ""
    d = stores.dropna(subset=["lat", "lon"])
    if d.empty:
        return ""
    nm = d.iloc[int(np.argmin(mystore.distance_m(st["lat"], st["lon"], d["lat"], d["lon"])))]["ldong_nm"]
    return nm if isinstance(nm, str) else ""


def attach_nearest(df: pd.DataFrame, stores: list[dict], data: dict | None = None) -> pd.DataFrame:
    """각 가게에서 가장 가까운 '내 매장' 이름과 거리(m)를 붙입니다.

    좌표가 없는 가게는 거리를 알 수 없으므로, 주소에 내 매장과 같은 법정동(예: '송천동')이
    들어 있을 때만 그 매장 이름을 붙이고 거리는 비워 둡니다. 다른 동네면 매장 이름도 비워 둡니다.
    """
    df = df.copy()
    df["near_name"], df["near_dist"] = None, np.nan
    if df.empty or not stores:
        return df
    has = df["lat"].notna() & df["lon"].notna()
    if has.any():
        dist = np.vstack([mystore.distance_m(s["lat"], s["lon"], df.loc[has, "lat"], df.loc[has, "lon"]) for s in stores])
        idx = dist.argmin(axis=0)
        df.loc[has, "near_dist"] = dist[idx, np.arange(dist.shape[1])]
        df.loc[has, "near_name"] = [str(stores[i].get("name") or "내 매장") for i in idx]
    if (~has).any():
        for st in stores:
            ld = _store_ldong(data, st)
            if not ld:
                continue
            hit = ~has & df["near_name"].isna() & df["addr"].fillna("").str.contains(ld, regex=False)
            df.loc[hit, "near_name"] = str(st.get("name") or "내 매장")
    return df


def in_range(df: pd.DataFrame, radius: float) -> pd.DataFrame:
    """내 매장 radius(m) 안 + 좌표가 없지만 내 매장과 같은 동네 주소인 곳."""
    return df[(df["near_dist"] <= float(radius)) | (df["near_dist"].isna() & df["near_name"].notna())]


def region_items(region_key: str, days: int, data: dict | None = None, cfg: dict | None = None,
                 stores: list[dict] | None = None, today: date | None = None) -> pd.DataFrame:
    """'새 경쟁점' 화면용: 한 지역의 새 가게 전체 + 가장 가까운 내 매장까지 거리."""
    cfg = cfg if cfg is not None else scoring.load_config()
    data = data if data is not None else scoring.load_region_data(region_key)
    stores = mystore.load_my_stores(region_key) if stores is None else [s for s in stores if s.get("region") == region_key]
    return attach_nearest(new_competitors(data, cfg, days, today), stores, data)


def popup_items(cfg: dict | None = None, stores: list[dict] | None = None, today: date | None = None,
                loader=None) -> pd.DataFrame:
    """앱을 열 때 팝업으로 알릴 가게: 저장된 내 매장에서 radius_m 안에 새로 생긴 곳.

    - 좌표가 없어 거리를 모르는 곳은 주소가 내 매장과 같은 동네일 때만 포함합니다.
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
        df = in_range(df, s["radius_m"])
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
    """'식당 · 중국식', '카페 · 저가 커피 브랜드' 같은 짧은 설명."""
    label = GROUP_LABEL.get(row["group"], "")
    if row["group"] == "meal" and isinstance(row.get("bzstat_nm"), str) and row["bzstat_nm"] not in ("", "기타"):
        label += f" · {row['bzstat_nm']}"
    if row.get("lowcost"):
        label += " · 저가 커피 브랜드"
    if row.get("temporary"):
        label += " · 행사 임시 매장"
    return label


def existing_near(data: dict, lat: float, lon: float, radius: int) -> dict:
    """반경 안에 지금 있는 점심 경쟁점 수 (상가정보 최신 스냅샷). {묶음: 곳 수}"""
    st = data.get("stores")
    out = {g: 0 for g in GROUP_ORDER}
    if st is None or st.empty:
        return out
    d = st.dropna(subset=["lat", "lon"])
    d = d[mystore.distance_m(lat, lon, d["lat"], d["lon"]) <= radius]
    food = d["inds_lcls_nm"].fillna("").str.contains("음식") & d["inds_mcls_cd"].ne(STORE_BAR_MCLS)
    cafe = d["inds_scls_cd"].eq(STORE_CAFE_SCLS)
    snack = d["inds_mcls_cd"].eq("I210")
    out["cafe"] = int(cafe.sum())
    out["snack"] = int((food & snack).sum())
    out["meal"] = int((food & ~cafe & ~snack).sum())
    out["conv"] = int(d["inds_scls_cd"].eq(STORE_CONV_SCLS).sum())
    return out


def dist_text(row) -> str:
    d = row.get("near_dist")
    if row.get("near_name") is None or pd.isna(d):
        return ""
    return f"{row['near_name']}에서 {int(round(d, -1)):,}m"

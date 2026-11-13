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
def _secret_stores() -> list[dict]:
    """Streamlit Secrets의 [[my_stores]] (저장소에 올라가지 않아 매출·보증금을 넣어도 안전)."""
    try:
        import streamlit as st
        items = st.secrets.get("my_stores", [])
        return [{**dict(it), "private": True} for it in items]
    except Exception:  # noqa: BLE001  (Secrets 파일이 없을 때 등)
        return []


def load_my_stores(region_key: str | None = None) -> list[dict]:
    """내 매장·후보지 목록: Streamlit Secrets [[my_stores]] + config/my_stores.yaml"""
    items = _secret_stores()
    if MY_STORES_YAML.exists():
        with open(MY_STORES_YAML, encoding="utf-8") as f:
            items += (yaml.safe_load(f) or {}).get("stores") or []
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


# ---------------------------------------------------------------- 우리 가게 진단 (유지할까, 옮길까?)
PYEONG_M2 = 3.3058
# 월세 ÷ 월매출 — 외식업에서 흔히 쓰는 기준 (10% 안팎이 적정, 20%를 넘으면 위험)
RENT_BANDS = [(0.10, "적정", 1), (0.15, "보통", 0), (0.20, "높은 편", -1), (float("inf"), "위험", -2)]


def dong_of_point(data: dict, lat: float, lon: float) -> tuple[str, str] | None:
    """그 자리에서 가장 가까운 점포의 행정동 = 우리 가게 동네."""
    s = data.get("stores")
    if s is None or s.empty:
        return None
    s = s.dropna(subset=["lat", "lon"])
    near = s.iloc[[int(np.argmin(distance_m(lat, lon, s["lat"], s["lon"])))]]
    master = scoring.dong_master(data)
    cd = scoring.attach_dong_key(near, master).iloc[0]
    if not isinstance(cd, str):
        return None
    nm = master.set_index("adong_cd")["adong_nm"].get(cd, near["adong_nm"].iloc[0])
    return cd, nm


def sbiz_sales(region_key: str, dong_nm: str, scls_nm: str) -> float | None:
    """config/sbiz_floating.yaml 의 sales: 동네·업종별 점포당 월평균 매출(만원, 소상공인365)."""
    if not scoring.SBIZ_FLOATING_YAML.exists():
        return None
    with open(scoring.SBIZ_FLOATING_YAML, encoding="utf-8") as f:
        y = yaml.safe_load(f) or {}
    v = ((y.get("sales") or {}).get(region_key) or {}).get(dong_nm, {}) or {}
    return v.get(scls_nm)


def diagnose(table: pd.DataFrame, dong_cd: str, sales: float, rent: float, deposit: float, area_pyeong: float,
             area_avg_sales: float | None = None, comp_change: tuple[int, int] | None = None) -> dict:
    """입지 점수·임대료 부담·매출 수준·경쟁 변화를 모아 '유지 / 개선 / 이전 검토'를 판단합니다.

    table: 입지 점수표 (score, rank, adong_cd, adong_nm, rent_small …) — 같은 업종으로 계산한 것
    금액 단위는 모두 만원.
    """
    factors, pts = [], 0
    row = table[table["adong_cd"] == dong_cd]
    r = row.iloc[0] if len(row) else None
    scored = table[table["score"].notna()]
    n = len(scored)
    cur_score = r["score"] if r is not None else np.nan

    # 1) 동네 입지 점수
    if r is None:
        factors.append(("⚪", "동네 입지 점수: 이 동네를 점수표에서 찾지 못했습니다."))
    elif pd.isna(cur_score):
        factors.append(("⚪", f"동네 입지 점수: 판단 보류 ({r.get('hold_reason') or '자료 부족'})"))
    else:
        rank = int(r["rank"])
        top = rank <= max(1, round(n * 0.3))
        p = 2 if cur_score >= 65 else 1 if cur_score >= 50 else 0 if cur_score >= 40 else -1
        p += 1 if top else 0
        pts += p
        icon = "✅" if p >= 2 else "🟡" if p >= 0 else "⚠️"
        factors.append((icon, f"동네 입지 점수 {cur_score:.0f}점 · 시 안에서 {n}곳 중 {rank}위"
                              + (" (상위 30%)" if top else "")))

    # 2) 임대료 부담 (월세 ÷ 월매출)
    if sales and sales > 0 and rent is not None:
        ratio = rent / sales
        label, p = next((lb, pp) for lim, lb, pp in RENT_BANDS if ratio <= lim)
        pts += p
        icon = "✅" if p > 0 else "🟡" if p == 0 else "⚠️"
        factors.append((icon, f"임대료 부담 {ratio * 100:.0f}% ({label}) — 월세 {rent:,.0f}만원 ÷ 월매출 {sales:,.0f}만원. "
                              "보통 10% 안팎이 적정, 20%를 넘으면 위험으로 봅니다."))
        # 상권 평균 임대료와 비교 (조사 상권일 때만)
        if r is not None and area_pyeong and area_pyeong > 0 and pd.notna(r.get("rent_small")):
            mine = rent * 10 / (area_pyeong * PYEONG_M2)  # 천원/㎡
            avg = float(r["rent_small"])
            # 부동산원 조사값은 층 구분 없는 상권 평균이라 1층 가게는 보통 더 비싸다 → 점수에는 넣지 않고 참고로만
            factors.append(("✅" if mine <= avg * 1.5 else "🟡" if mine <= avg * 2.5 else "⚠️",
                            f"㎡당 월세 {mine:.1f}천원 · {r['rent_district']} 상권 평균 {avg:.1f}천원의 {mine / avg:.1f}배 "
                            f"({r.get('rent_period', '')} · 상권 평균은 층 구분 없는 값이라 1층은 보통 더 비쌈)"))

    # 3) 매출 수준 (동네 같은 업종 평균과 비교)
    if sales and area_avg_sales:
        rel = sales / area_avg_sales
        p = 1 if rel >= 1.1 else 0 if rel >= 0.8 else -1
        pts += p
        icon = "✅" if p > 0 else "🟡" if p == 0 else "⚠️"
        factors.append((icon, f"월매출 {sales:,.0f}만원 · 동네 같은 업종 평균 {area_avg_sales:,.0f}만원의 {rel:.1f}배"))
        weak_sales = rel < 0.8
    else:
        weak_sales = False
        factors.append(("⚪", "동네 같은 업종 평균 매출을 넣으면 매출 수준도 비교합니다 (소상공인365 › 상권분석에서 확인)."))

    # 4) 반경 안 경쟁점 변화 (5년)
    if comp_change:
        a, b = comp_change
        if a > 0:
            ch = (b - a) / a
            p = -1 if ch >= 0.2 else 0
            pts += p
            factors.append(("⚠️" if p < 0 else "🟡" if ch > 0 else "✅",
                            f"반경 안 같은 업종 {a}곳 → {b}곳 (5년, {ch * 100:+.0f}%)"))

    # 5) 더 나은 동네
    alts = scored[scored["adong_cd"] != dong_cd]
    if pd.notna(cur_score):
        alts = alts[alts["score"] > cur_score]  # 지금 동네보다 점수가 높은 곳만
    alts = alts.head(3).copy()
    better = alts[alts["score"] >= (cur_score + 15 if pd.notna(cur_score) else 65)]

    if pts >= 3:
        verdict, tone = "지금 자리 유지 추천", "good"
        why = "입지와 비용이 모두 괜찮은 편입니다. 옮기기보다 지금 자리에서 매출을 키우는 쪽이 낫습니다."
    elif pts >= 1:
        verdict, tone = "유지하면서 개선", "mid"
        why = "자리는 나쁘지 않습니다. ⚠️ 표시된 항목(임대료·매출)을 먼저 손보는 것을 권합니다."
    elif len(better) and (weak_sales or pts <= -1):
        verdict, tone = "이전 검토 추천", "bad"
        why = (f"지금 동네보다 점수가 15점 이상 높은 동네가 있습니다 ({', '.join(better['adong_nm'])}). "
               "계약 만료 시점에 맞춰 옮기는 것을 검토해 보세요.")
    else:
        verdict, tone = "자리보다 운영 점검 먼저", "mid"
        why = "시 안에 확실히 더 나은 동네가 보이지 않습니다. 옮기기보다 메뉴·가격·임대료 협상을 먼저 검토하세요."
    return {"verdict": verdict, "tone": tone, "why": why, "factors": factors, "points": pts,
            "alternatives": alts, "deposit": deposit}

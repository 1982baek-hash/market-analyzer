"""입지·업종 점수 계산 (v2 설계서 '입지·업종 점수 모델').

GitHub Actions가 저장소에 올린 data/mart/ 파일만 읽어서 계산합니다 (DuckDB 불필요).
모든 점수는 같은 시·군 안 동네끼리의 백분위(0–100)로 매깁니다.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
MART = ROOT / "data" / "mart"
TYPES_YAML = ROOT / "config" / "business_types.yaml"
REGIONS_YAML = ROOT / "config" / "regions.yaml"
CATEGORY_CSV = ROOT / "config" / "category_mapping.csv"
RENT_YAML = ROOT / "config" / "rent_districts.yaml"
SBIZ_FLOATING_YAML = ROOT / "config" / "sbiz_floating.yaml"

NOT_PROVIDED = "이 지역은 제공되지 않음"


# ---------------------------------------------------------------- 설정·원자료
def load_config() -> dict:
    with open(TYPES_YAML, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_regions() -> dict:
    with open(REGIONS_YAML, encoding="utf-8") as f:
        return yaml.safe_load(f)["regions"]


def _latest(folder: str, prefix: str) -> Path | None:
    files = sorted((MART / folder).glob(f"{prefix}_*.parquet"))
    return files[-1] if files else None


def mart_signature() -> str:
    """캐시 키: 수집 파일이 바뀌면 달라집니다."""
    parts = [f"{p.relative_to(MART)}:{p.stat().st_mtime_ns}:{p.stat().st_size}" for p in sorted(MART.rglob("*.parquet"))]
    parts += [f"{p.name}:{p.stat().st_mtime_ns}" for p in (TYPES_YAML,)]
    return "|".join(parts)


def load_region_data(region_key: str) -> dict:
    """한 지역의 최신 점포·인구·종사자·인허가 자료를 읽습니다. 없는 자료는 None."""
    out = {"stores": None, "population": None, "sgis": None, "schools": None, "busstops": None, "floating": None,
           "licenses": {}, "periods": {}}
    for key, folder in (("stores", "stores"), ("population", "population"), ("sgis", "sgis")):
        p = _latest(folder, region_key)
        if p is not None:
            out[key] = pd.read_parquet(p)
            out["periods"][key] = p.stem.split("_")[-1]
    for key in ("schools", "busstops", "floating"):
        p = MART / key / f"{region_key}.parquet"
        if p.exists():
            out[key] = pd.read_parquet(p)
    for svc in ("rest_cafes", "general_restaurants"):
        p = MART / "licenses" / f"{svc}_{region_key}.parquet"
        if p.exists():
            out["licenses"][svc] = pd.read_parquet(p)
    return out


def sbiz_floating(region_key: str) -> tuple[dict, str]:
    """소상공인365에서 옮겨 적은 동네별 일평균 유동인구 {동 이름: 명}, 기준 시점."""
    if not SBIZ_FLOATING_YAML.exists():
        return {}, ""
    with open(SBIZ_FLOATING_YAML, encoding="utf-8") as f:
        y = yaml.safe_load(f) or {}
    return (y.get("regions") or {}).get(region_key) or {}, str(y.get("period", ""))


def rent_table(region_key: str) -> pd.DataFrame:
    """부동산원 상권 임대료를 동네별로 펼칩니다 (조사 상권이 걸친 동네만)."""
    try:
        with open(RENT_YAML, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return pd.DataFrame(columns=["adong_nm", "rent_district", "rent_small", "rent_large", "rent_period"])
    rows = []
    for d in cfg.get("districts", []):
        if d.get("region") != region_key:
            continue
        for nm in d.get("dongs", []):
            rows.append({"adong_nm": nm, "rent_district": d["name"], "rent_small": d.get("small"),
                         "rent_large": d.get("large"), "rent_period": cfg.get("period", "")})
    t = pd.DataFrame(rows, columns=["adong_nm", "rent_district", "rent_small", "rent_large", "rent_period"])
    return t.drop_duplicates("adong_nm")


def attach_rent(m: pd.DataFrame, region_key: str) -> pd.DataFrame:
    rt = rent_table(region_key)
    return m.merge(rt, on="adong_nm", how="left")


# ---------------------------------------------------------------- 공통 계산
def pct_rank(s: pd.Series, higher_is_better: bool = True) -> pd.Series:
    """같은 시·군 안 백분위 (0–100). 값이 없는 동네는 NaN."""
    r = s.rank(pct=True, method="average") * 100
    if not higher_is_better:
        r = 100 - r + (100 / s.notna().sum() if s.notna().sum() else 0)
    return r.clip(0, 100)


def weighted(parts: dict, weights: dict) -> tuple[pd.Series, list]:
    """있는 항목만 가중 평균합니다 (없는 항목은 비례로 나눠 가짐). 빠진 항목 이름도 돌려줌."""
    avail = {k: v for k, v in parts.items() if v is not None and weights.get(k, 0) > 0}
    missing = [k for k in weights if weights[k] > 0 and k not in avail]
    if not avail:
        return None, missing
    total = sum(weights[k] for k in avail)
    acc = sum(avail[k].fillna(avail[k].mean()) * weights[k] for k in avail)
    return acc / total, missing


def dong_master(data: dict) -> pd.DataFrame:
    """행정동 목록: 주민등록 인구 기준, 없으면 점포 자료 기준."""
    pop = data["population"]
    if pop is not None:
        m = pop[["sigungu_cd", "sigungu_nm", "adong_cd", "adong_nm"]].copy()
    else:
        m = data["stores"][["sigungu_cd", "sigungu_nm", "adong_cd", "adong_nm"]].drop_duplicates("adong_cd")
    m["adong_nm"] = m["adong_nm"].astype(str).str.strip()
    return m.reset_index(drop=True)


def attach_dong_key(df: pd.DataFrame, master: pd.DataFrame) -> pd.Series:
    """점포·인허가 자료의 행정동을 동네 목록의 코드로 맞춥니다 (코드가 바뀐 곳은 이름으로)."""
    codes = set(master["adong_cd"])
    by_name = dict(zip(master["sigungu_cd"].str[:4] + "|" + master["adong_nm"], master["adong_cd"]))
    cd = df["adong_cd"].fillna("").astype(str)
    nm = df.get("adong_nm", pd.Series("", index=df.index)).fillna("").astype(str).str.strip()
    sgg = df.get("sigungu_cd", pd.Series("", index=df.index)).fillna("").astype(str).str[:4]
    alt = (sgg + "|" + nm).map(by_name)
    return cd.where(cd.isin(codes), alt)


# ---------------------------------------------------------------- 동네 기본 지표
def base_metrics(data: dict, cfg: dict, target_ages=(20, 30, 40)) -> pd.DataFrame:
    m = dong_master(data).set_index("adong_cd")
    stores = data["stores"].copy()
    stores["dong"] = attach_dong_key(stores, m.reset_index())

    pop = data["population"]
    if pop is not None:
        p = pop.set_index("adong_cd")
        m["resident"] = p["population"]
        age_cols = [f"age_{a}" for a in target_ages if f"age_{a}" in p]
        m["target_cnt"] = p[age_cols].sum(axis=1) if age_cols else np.nan
        m["target_share"] = m["target_cnt"] / m["resident"].replace(0, np.nan)
        for a in range(0, 110, 10):
            if f"age_{a}" in p:
                m[f"age_{a}"] = p[f"age_{a}"]
    else:
        m["resident"] = m["target_cnt"] = m["target_share"] = np.nan

    sg = data["sgis"]
    if sg is not None:
        names = dict(zip(sg["adm_nm"].astype(str).str.strip(), sg["tot_worker"]))
        m["worker"] = m["adong_nm"].map(names)
    else:
        m["worker"] = np.nan

    m["stores_total"] = stores.groupby("dong").size().reindex(m.index).fillna(0)
    acad = set(cfg.get("student_academy_scls_names", []))
    m["academies"] = stores[stores["inds_scls_nm"].isin(acad)].groupby("dong").size().reindex(m.index).fillna(0)
    sch = data.get("schools")
    if sch is not None and len(sch):
        sch = sch.copy()
        sch["dong"] = attach_dong_key(sch, m.reset_index())
        lvl = sch["level"].astype(str)
        m["schools_mid_high"] = sch[lvl.str.contains("중학교|고등학교")].groupby("dong").size().reindex(m.index).fillna(0)
        m["schools_elem"] = sch[lvl.str.contains("초등")].groupby("dong").size().reindex(m.index).fillna(0)
    else:
        m["schools_mid_high"] = m["schools_elem"] = np.nan
    bus = data.get("busstops")
    if bus is not None and len(bus):
        # 면적이 넓은 읍·면은 정류장이 길 따라 흩어져 있어 개수가 부풀려진다.
        # 그래서 동네 상권 중심(점포 위치의 중앙값) 반경 500m 안의 정류장만 센다.
        cent = stores.dropna(subset=["lon", "lat"]).groupby("dong")[["lon", "lat"]].median().reindex(m.index)
        blon, blat = bus["lon"].to_numpy(float), bus["lat"].to_numpy(float)
        cnt = []
        for lon, lat in cent[["lon", "lat"]].to_numpy(float):
            if np.isnan(lon):
                cnt.append(0)
                continue
            dx = (blon - lon) * 111.0 * np.cos(np.radians(lat))
            dy = (blat - lat) * 111.0
            cnt.append(int(((dx * dx + dy * dy) <= 0.25).sum()))
        m["bus_stops"] = cnt
    else:
        m["bus_stops"] = np.nan
    fl = data.get("floating")
    if fl is not None and len(fl):
        latest = fl["base_ym"].max()
        tot = fl[(fl["base_ym"] == latest) & (fl["tz"] == "TOT")].copy()
        tot["dong"] = attach_dong_key(tot, m.reset_index())
        m["floating"] = tot.groupby("dong")["floating"].sum().reindex(m.index)
        m["floating_ym"] = latest
    else:
        m["floating"] = np.nan
    m["people"] = m["resident"].fillna(0) + m["worker"].fillna(0)
    return m


def school_score(m: pd.DataFrame):
    """학교·학원 근접: 중·고교 수 60% + 학생 학원 수 40% (학교 자료가 없으면 학원 수만)."""
    if m["schools_mid_high"].notna().any():
        return pct_rank(m["schools_mid_high"]) * 0.6 + pct_rank(m["academies"]) * 0.4
    return pct_rank(m["academies"])


def floating_score(m: pd.DataFrame):
    """유동: 경기도 유동인구가 있으면 그것을, 없으면 버스정류장 수로 대신합니다 (둘 다 없으면 None)."""
    if m["floating"].notna().any():
        return pct_rank(m["floating"])
    if m["bus_stops"].notna().any() and m["bus_stops"].sum() > 0:
        return pct_rank(m["bus_stops"])
    return None


# ---------------------------------------------------------------- 인허가 안정성
def _to_date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s.astype(str).str[:8], format="%Y%m%d", errors="coerce")


def license_stability(lic: pd.DataFrame, master: pd.DataFrame, bzstat: list | None, asof: date) -> pd.DataFrame:
    """동네별 3년 폐업률, 최근 1년 개업·폐업, 현재 영업 수."""
    df = lic.copy()
    if bzstat:
        df = df[df["bzstat_nm"].isin(bzstat)]
    df["dong"] = attach_dong_key(df, master)
    df = df[df["dong"].notna()]
    op, cl = _to_date(df["open_date"]), _to_date(df["close_date"])
    closed = df["status_cd"].eq("03") & cl.notna()
    asof = pd.Timestamp(asof)
    # 3년이 지난 코호트: 7년 전 ~ 3년 전에 문을 연 가게 중 3년 안에 폐업한 비율
    cohort = op.between(asof - pd.DateOffset(years=7), asof - pd.DateOffset(years=3))
    died3 = cohort & closed & (cl <= op + pd.DateOffset(years=3))
    last = asof - pd.DateOffset(years=1)
    g = pd.DataFrame({
        "dong": df["dong"], "cohort": cohort, "died3": died3,
        "open_1y": op > last, "close_1y": closed & (cl > last), "open_now": df["status_cd"].eq("01"),
    }).groupby("dong").sum()
    g["closure3"] = g["died3"] / g["cohort"].replace(0, np.nan)
    g["net_1y"] = g["open_1y"] - g["close_1y"]
    return g


# ---------------------------------------------------------------- 커피+간식 프리셋
def score_preset(region_key: str, preset_key: str = "coffee_snack", data: dict | None = None,
                 asof: date | None = None) -> tuple[pd.DataFrame, dict]:
    cfg = load_config()
    pc = cfg["presets"][preset_key]
    data = data or load_region_data(region_key)
    asof = asof or date.today()
    m = base_metrics(data, cfg, pc.get("target_ages", (10, 20)))
    master = m.reset_index()

    stores = data["stores"].copy()
    stores["dong"] = attach_dong_key(stores, master)
    city_total = len(stores)

    # --- 경쟁: 그룹별 점포 수와 인구 1만 명당 밀도
    comp_parts, comp_w = {}, {}
    for gk, g in pc["groups"].items():
        sub = stores[stores["inds_scls_cd"].isin(g["scls"])]
        cnt = sub.groupby("dong").size().reindex(m.index).fillna(0)
        m[f"{gk}_cnt"] = cnt
        m[f"{gk}_per10k"] = cnt / m["people"].replace(0, np.nan) * 10_000
        city_share = len(sub) / city_total if city_total else np.nan
        m[f"{gk}_lq"] = (cnt / m["stores_total"].replace(0, np.nan)) / city_share if city_share else np.nan
        m[f"{gk}_city_per10k"] = len(sub) / m["people"].sum() * 10_000
        comp_parts[gk] = pct_rank(m[f"{gk}_per10k"], higher_is_better=False)
        comp_w[gk] = g["weight"]
    C, _ = weighted(comp_parts, comp_w)

    brands = cfg.get("lowcost_coffee_brands", [])
    cafes = stores[stores["inds_scls_cd"].isin(pc["groups"]["cafe"]["scls"])]
    is_low = cafes["store_name"].fillna("").str.contains("|".join(map(re.escape, brands))) if brands else False
    m["lowcost_cnt"] = cafes[is_low].groupby("dong").size().reindex(m.index).fillna(0)
    m["lowcost_ratio"] = m["lowcost_cnt"] / m["cafe_cnt"].replace(0, np.nan)
    city_low = (is_low.sum() / len(cafes)) if len(cafes) else np.nan
    m["city_lowcost_ratio"] = city_low

    bonus = np.where(m["snack_lq"] < pc["snack_gap_lq"], pc["snack_gap_bonus"], 0)
    penalty = np.where(m["lowcost_ratio"] > city_low, pc["franchise_penalty"], 0)
    m["C"] = (C + bonus - penalty).clip(0, 100)

    # --- 수요
    dparts = {
        "target_age": pct_rank(m["target_share"]) if m["target_share"].notna().any() else None,
        "school": school_score(m),
        "worker": pct_rank(m["worker"]) if m["worker"].notna().any() else None,
        "floating": floating_score(m),  # 동네 유동인구 자료가 없어 버스정류장 수로 대신
        "resident": pct_rank(m["resident"]) if m["resident"].notna().any() else None,
    }
    m["D"], d_missing = weighted(dparts, pc["demand"])

    # --- 안정성
    lic = data["licenses"].get(pc["license_service"])
    s_missing = lic is None
    if lic is not None:
        st = license_stability(lic, master, pc.get("license_bzstat"), asof).reindex(m.index)
        for c in ("cohort", "closure3", "open_1y", "close_1y", "net_1y", "open_now"):
            m[f"lic_{c}"] = st[c]
        valid = m["lic_cohort"].fillna(0) >= cfg["min_license_cohort"]
        clo = m["lic_closure3"].where(valid)
        net_rate = m["lic_net_1y"] / m["lic_open_now"].replace(0, np.nan)
        s_parts = {"closure": pct_rank(clo, higher_is_better=False), "net": pct_rank(net_rate)}
        m["S"], _ = weighted(s_parts, {"closure": 70, "net": 30})
        m.loc[~valid, "S"] = np.nan
        # 시 평균은 동네별 폐업률의 중앙값 (백화점 팝업처럼 짧게 열고 닫는 곳이 몰린 동네에 끌려가지 않게)
        m["city_closure3"] = clo.median()
    else:
        m["S"] = np.nan

    # --- 종합
    aw = cfg["axis_weights"]
    axes = {"demand": m["D"], "competition": m["C"], "stability": None if s_missing else m["S"], "cost": None}
    used = {k: v for k, v in axes.items() if v is not None and aw.get(k, 0) > 0}
    total_w = sum(aw[k] for k in used)
    m["score"] = sum(used[k] * aw[k] for k in used) / total_w

    comp_cnt = sum(m[f"{gk}_cnt"] for gk in pc["groups"])
    m["competitors"] = comp_cnt
    hold = []
    for idx in m.index:
        why = []
        if comp_cnt[idx] < cfg["min_competitors"]:
            why.append(f"커피·간식 점포 {int(comp_cnt[idx])}개")
        if not s_missing and pd.isna(m.at[idx, "S"]):
            n = m.at[idx, "lic_cohort"]
            why.append(f"인허가 이력 {0 if pd.isna(n) else int(n)}건")
        hold.append(" · ".join(why))
    m["hold_reason"] = hold
    m.loc[m["hold_reason"] != "", "score"] = np.nan

    m = attach_rent(m.reset_index(), region_key)
    m["reasons"] = [_reasons_coffee(r, m) for _, r in m.iterrows()]
    m = m.sort_values(["score", "D"], ascending=[False, False], na_position="last").reset_index(drop=True)
    m["rank"] = np.where(m["score"].notna(), np.arange(1, len(m) + 1), np.nan)

    missing_labels = {"floating": "유동인구", "school": "학교 위치", "cost": "임대료"}
    proxies = []
    if dparts["floating"] is not None and not m["floating"].notna().any():
        proxies.append("유동인구(버스정류장 수로 대신)")
    if not m["schools_mid_high"].notna().any():
        proxies.append("학교 위치(학원 수로 대신)")
    info = {
        "preset": pc["name"],
        "periods": data["periods"],
        "used_axes": {k: round(aw[k] / total_w * 100) for k in used},
        "missing": [missing_labels.get(k, k) for k in d_missing] + proxies + ["임대료(조사 상권만 표시·점수 미반영)"],
        "asof": asof.isoformat(),
    }
    return m, info


def _fmt_pct(x):
    return "–" if pd.isna(x) else f"{x * 100:.0f}%"


def _reasons_coffee(r: pd.Series, m: pd.DataFrame) -> list:
    """점수 옆에 붙일 근거: 시 평균과 가장 크게 다른 지표 3개."""
    cand = []

    def add(good: bool, strength: float, text: str):
        if pd.notna(strength):
            cand.append((abs(strength), ("✅ " if good else "⚠️ ") + text))

    avg_share = m["target_cnt"].sum() / m["resident"].sum() if m["resident"].sum() else np.nan
    if pd.notna(r.get("target_share")):
        add(r["target_share"] >= avg_share, (r["target_share"] - avg_share) / avg_share,
            f"10–29세 비중 {_fmt_pct(r['target_share'])} (시 평균 {_fmt_pct(avg_share)})")
    city_snack = r.get("snack_city_per10k")
    if pd.notna(r.get("snack_per10k")):
        add(r["snack_per10k"] <= city_snack, (r["snack_per10k"] - city_snack) / city_snack,
            f"인구 1만 명당 간식점 {r['snack_per10k']:.1f}개 (시 평균 {city_snack:.1f}개)")
    city_cafe = r.get("cafe_city_per10k")
    if pd.notna(r.get("cafe_per10k")):
        add(r["cafe_per10k"] <= city_cafe, (r["cafe_per10k"] - city_cafe) / city_cafe,
            f"인구 1만 명당 카페 {r['cafe_per10k']:.1f}개 (시 평균 {city_cafe:.1f}개)")
    avg_sch = m["schools_mid_high"].mean()
    if pd.notna(r.get("schools_mid_high")) and avg_sch:
        add(r["schools_mid_high"] >= avg_sch, (r["schools_mid_high"] - avg_sch) / avg_sch,
            f"중·고등학교 {int(r['schools_mid_high'])}곳 (동네 평균 {avg_sch:.1f}곳)")
    avg_fl = m["floating"].mean()
    if pd.notna(r.get("floating")) and avg_fl:
        add(r["floating"] >= avg_fl, (r["floating"] - avg_fl) / avg_fl,
            f"유동인구가 동네 평균의 {r['floating'] / avg_fl:.1f}배 (경기도 집계 {r.get('floating_ym', '')})")
    avg_bus = m["bus_stops"].mean()
    if pd.notna(r.get("bus_stops")) and avg_bus:
        add(r["bus_stops"] >= avg_bus, (r["bus_stops"] - avg_bus) / avg_bus * 0.8,
            f"상권 중심 500m 안 버스정류장 {int(r['bus_stops'])}곳 (동네 평균 {avg_bus:.0f}곳)")
    avg_acad = m["academies"].mean()
    if avg_acad:
        add(r["academies"] >= avg_acad, (r["academies"] - avg_acad) / avg_acad,
            f"학생 학원 {int(r['academies'])}곳 (동네 평균 {avg_acad:.0f}곳)")
    avg_w = m["worker"].mean()
    if pd.notna(r.get("worker")) and avg_w:
        add(r["worker"] >= avg_w, (r["worker"] - avg_w) / avg_w,
            f"직장인 {int(r['worker']):,}명 (동네 평균 {avg_w:,.0f}명)")
    if pd.notna(r.get("lic_closure3")) and pd.notna(r.get("city_closure3")) and r.get("lic_cohort", 0) >= 10:
        c0 = r["city_closure3"]
        add(r["lic_closure3"] <= c0, (r["lic_closure3"] - c0) / c0 if c0 else np.nan,
            f"카페·간식점 3년 내 폐업 {_fmt_pct(r['lic_closure3'])} (동네 중앙값 {_fmt_pct(c0)})")
    if pd.notna(r.get("lowcost_ratio")) and r.get("cafe_cnt", 0) >= 3:
        c0 = r["city_lowcost_ratio"]
        add(r["lowcost_ratio"] <= c0, (r["lowcost_ratio"] - c0) / c0 if c0 else np.nan,
            f"카페 중 저가 브랜드 {int(r['lowcost_cnt'])}곳 ({_fmt_pct(r['lowcost_ratio'])}, 시 평균 {_fmt_pct(c0)})")
    cand.sort(key=lambda x: -x[0])
    return [t for _, t in cand[:3]]


# ---------------------------------------------------------------- 업종 고르기 (기회 점수)
CONSUMER_LCLS = ["I2", "G2", "S2", "P1", "R1", "Q1"]
# 가족이 새로 열 업종 후보로 보기 어려운 소분류 (유흥주점, 연료 소매 등)
EXCLUDE_SCLS = {"I21101", "I21102", "G21401", "G21402", "G21403"}


def opportunity_table(data: dict, adong_cd: str, min_city_count: int = 10) -> pd.DataFrame:
    """한 동네 안에서 소분류별 점포 수·LQ·인구 1만 명당 밀도. LQ < 0.7 부족, > 1.5 포화."""
    cfg = load_config()
    m = base_metrics(data, cfg)
    master = m.reset_index()
    stores = data["stores"].copy()
    stores["dong"] = attach_dong_key(stores, master)
    stores = stores[stores["inds_lcls_cd"].isin(CONSUMER_LCLS) & ~stores["inds_scls_cd"].isin(EXCLUDE_SCLS)]
    city = stores.groupby(["inds_lcls_nm", "inds_scls_cd", "inds_scls_nm"]).size().rename("city_cnt")
    here = stores[stores["dong"] == adong_cd].groupby(["inds_lcls_nm", "inds_scls_cd", "inds_scls_nm"]).size().rename("dong_cnt")
    t = pd.concat([city, here], axis=1).fillna(0).reset_index()
    t = t[t["city_cnt"] >= min_city_count]
    dong_total = (stores["dong"] == adong_cd).sum()
    t["lq"] = (t["dong_cnt"] / max(dong_total, 1)) / (t["city_cnt"] / len(stores))
    people_city = m["people"].sum()
    people_dong = m.loc[adong_cd, "people"] if adong_cd in m.index else np.nan
    t["dong_per10k"] = t["dong_cnt"] / people_dong * 10_000 if people_dong else np.nan
    t["city_per10k"] = t["city_cnt"] / people_city * 10_000
    t["status"] = np.select([t["lq"] < 0.7, t["lq"] > 1.5], ["부족", "포화"], "보통")
    # 기회: 시 전체에서는 흔한데(수요가 확인된 업종) 이 동네에만 적은 업종
    t["opportunity"] = (pct_rank(t["city_per10k"]) * 0.4 + pct_rank(t["lq"], higher_is_better=False) * 0.6).round(0)
    if dong_total < 30:
        t["opportunity"] = np.nan
    return t.sort_values(["opportunity", "city_cnt"], ascending=[False, False]).reset_index(drop=True)


# ---------------------------------------------------------------- 동네 리포트
def license_trend(data: dict, adong_cd: str, years: int = 10, bzstat: list | None = None) -> pd.DataFrame:
    """연도별 개업·폐업 (휴게음식점·일반음식점)."""
    master = dong_master(data)
    rows = []
    for svc, label in (("rest_cafes", "휴게음식점"), ("general_restaurants", "일반음식점")):
        lic = data["licenses"].get(svc)
        if lic is None:
            continue
        df = lic.copy()
        if bzstat and svc == "rest_cafes":
            df = df[df["bzstat_nm"].isin(bzstat)]
        df = df[attach_dong_key(df, master) == adong_cd]
        op, cl = _to_date(df["open_date"]), _to_date(df["close_date"])
        this_year = date.today().year
        for y in range(this_year - years + 1, this_year + 1):
            rows.append({"연도": y, "구분": label, "개업": int((op.dt.year == y).sum()),
                         "폐업": int(((cl.dt.year == y) & df["status_cd"].eq("03")).sum())})
    return pd.DataFrame(rows)


def dong_centroids(data: dict) -> pd.DataFrame:
    master = dong_master(data)
    s = data["stores"].copy()
    s["dong"] = attach_dong_key(s, master)
    c = s.dropna(subset=["lon", "lat"]).groupby("dong")[["lon", "lat"]].median()
    return c


def category_target_ages(scls_nm: str) -> tuple:
    """category_mapping.csv의 키워드로 업종 타깃 연령을 찾습니다 (없으면 20–49세)."""
    try:
        cm = pd.read_csv(CATEGORY_CSV)
    except Exception:
        return (20, 30, 40)
    for _, r in cm.iterrows():
        if any(k and k in str(scls_nm) for k in str(r["keyword"]).split("|")):
            ages = []
            for a in str(r["target_ages"]).split(";"):
                mt = re.match(r"(\d+)", a)
                if mt:
                    ages.append(int(mt.group(1)))
                elif "이하" in a:
                    ages.append(10)
            return tuple(sorted(set(ages))) or (20, 30, 40)
    return (20, 30, 40)


def score_category(region_key: str, scls_cd: str, demand_type: str, data: dict | None = None,
                   asof: date | None = None) -> tuple[pd.DataFrame, dict]:
    """업종 하나(소분류)에 대한 동네 점수 — 일반 업종용."""
    cfg = load_config()
    data = data or load_region_data(region_key)
    asof = asof or date.today()
    stores = data["stores"]
    nm_row = stores.loc[stores["inds_scls_cd"] == scls_cd, ["inds_lcls_cd", "inds_scls_nm"]].head(1)
    scls_nm = nm_row["inds_scls_nm"].iloc[0] if len(nm_row) else scls_cd
    lcls = nm_row["inds_lcls_cd"].iloc[0] if len(nm_row) else ""
    ages = category_target_ages(scls_nm)
    m = base_metrics(data, cfg, ages)
    master = m.reset_index()
    s = stores.copy()
    s["dong"] = attach_dong_key(s, master)
    sub = s[s["inds_scls_cd"] == scls_cd]
    m["comp_cnt"] = sub.groupby("dong").size().reindex(m.index).fillna(0)
    m["comp_per10k"] = m["comp_cnt"] / m["people"].replace(0, np.nan) * 10_000
    city_per10k = len(sub) / m["people"].sum() * 10_000
    m["lq"] = (m["comp_cnt"] / m["stores_total"].replace(0, np.nan)) / (len(sub) / len(s))
    fr = sub.groupby("dong")["is_franchise"].mean().reindex(m.index)
    city_fr = sub["is_franchise"].mean() if len(sub) else np.nan
    m["C"] = (pct_rank(m["comp_per10k"], higher_is_better=False)
              - np.where(fr > city_fr, 10, 0)).clip(0, 100)

    w = cfg["demand_types"][demand_type]
    dparts = {
        "target_age": pct_rank(m["target_share"]) if m["target_share"].notna().any() else None,
        "worker": pct_rank(m["worker"]) if m["worker"].notna().any() else None,
        "floating": floating_score(m),
        "resident": pct_rank(m["resident"]) if m["resident"].notna().any() else None,
    }
    m["D"], d_missing = weighted(dparts, w)

    svc = None
    if lcls == "I2":
        svc = "rest_cafes" if scls_cd.startswith(("I210", "I212")) else "general_restaurants"
    lic = data["licenses"].get(svc) if svc else None
    if lic is not None:
        bz = ["커피숍", "다방", "전통찻집", "떡카페"] if scls_cd == "I21201" else None
        st = license_stability(lic, master, bz, asof).reindex(m.index)
        m["lic_cohort"], m["lic_closure3"] = st["cohort"], st["closure3"]
        valid = m["lic_cohort"].fillna(0) >= cfg["min_license_cohort"]
        net_rate = st["net_1y"] / st["open_now"].replace(0, np.nan)
        m["S"], _ = weighted({"closure": pct_rank(m["lic_closure3"].where(valid), False), "net": pct_rank(net_rate)},
                             {"closure": 70, "net": 30})
        m.loc[~valid, "S"] = np.nan
    else:
        m["S"] = np.nan

    aw = cfg["axis_weights"]
    used = {"demand": m["D"], "competition": m["C"]}
    if lic is not None:
        used["stability"] = m["S"]
    total_w = sum(aw[k] for k in used)
    m["score"] = sum(used[k] * aw[k] for k in used) / total_w
    hold = np.where(m["comp_cnt"] < cfg["min_competitors"], "동종 점포 " + m["comp_cnt"].astype(int).astype(str) + "개", "")
    if lic is not None:
        hold = np.where((hold == "") & m["S"].isna(), "인허가 이력 부족", hold)
    m["hold_reason"] = hold
    m.loc[m["hold_reason"] != "", "score"] = np.nan
    m = attach_rent(m.reset_index(), region_key)

    def reasons(r):
        out = [f"인구 1만 명당 {scls_nm} {r['comp_per10k']:.1f}개 (시 평균 {city_per10k:.1f}개)"]
        if pd.notna(r.get("target_share")):
            out.append(f"타깃 연령 비중 {_fmt_pct(r['target_share'])}")
        if pd.notna(r.get("lic_closure3")):
            out.append(f"3년 내 폐업 {_fmt_pct(r['lic_closure3'])} (음식점 인허가 기준)")
        elif pd.notna(r.get("worker")):
            out.append(f"직장인 {int(r['worker']):,}명")
        return out

    m["reasons"] = [reasons(r) for _, r in m.iterrows()]
    m = m.sort_values(["score", "D"], ascending=[False, False], na_position="last").reset_index(drop=True)
    m["rank"] = np.where(m["score"].notna(), np.arange(1, len(m) + 1), np.nan)
    info = {"preset": scls_nm, "periods": data["periods"],
            "used_axes": {k: round(aw[k] / total_w * 100) for k in used},
            "missing": ([] if m["floating"].notna().any() else
                        ["유동인구(버스정류장 수로 대신)"] if dparts["floating"] is not None else ["유동인구"])
                       + ["임대료(조사 상권만 표시·점수 미반영)"] + ([] if lic is not None else ["개·폐업 이력(음식점만 제공)"]),
            "target_ages": ages, "asof": asof.isoformat()}
    return m, info

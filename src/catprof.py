"""업종 요약 카드 — 검색한 업종이 이 시·군에서 어떤 장사인지 한눈에."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from src import sbiz, scoring

# 소분류 → 인허가(업태) 매칭. (코드 접두, [(서비스, [업태…])])
LIC_MAP = [
    ("I21201", [("rest_cafes", ["커피숍", "다방", "전통찻집", "떡카페"])]),
    ("I21001", [("rest_cafes", ["과자점"])]),
    ("I21008", [("rest_cafes", ["아이스크림"])]),
    ("I21002", [("rest_cafes", ["떡카페", "일반조리판매"])]),
    ("I21005", [("rest_cafes", ["일반조리판매", "기타 휴게음식점"])]),
    ("I21099", [("rest_cafes", ["일반조리판매", "기타 휴게음식점"])]),
    ("I21003", [("rest_cafes", ["패스트푸드"]), ("general_restaurants", ["패스트푸드"])]),
    ("I21004", [("rest_cafes", ["패스트푸드"]), ("general_restaurants", ["패스트푸드"])]),
    ("I21006", [("general_restaurants", ["통닭(치킨)", "호프/통닭"])]),
    ("I21007", [("general_restaurants", ["분식", "김밥(도시락)"])]),
    ("I2110", [("general_restaurants", ["호프/통닭", "정종/대포집/소주방", "감성주점"])]),
    ("I20111", [("general_restaurants", ["횟집"])]),
    ("I20107", [("general_restaurants", ["식육(숯불구이)"])]),
    ("I20108", [("general_restaurants", ["식육(숯불구이)"])]),
    ("I201", [("general_restaurants", ["한식"])]),
    ("I202", [("general_restaurants", ["중국식"])]),
    ("I203", [("general_restaurants", ["일식"])]),
    ("I204", [("general_restaurants", ["경양식", "패밀리레스트랑"])]),
    ("I205", [("general_restaurants", ["외국음식전문점(인도,태국등)"])]),
    ("I2070", [("general_restaurants", ["뷔페식"])]),
]


def lic_targets(codes: list[str]) -> list[tuple[str, list[str]]]:
    out = []
    for c in codes:
        for pre, tg in LIC_MAP:
            if c.startswith(pre):
                out += [t for t in tg if t not in out]
                break
    return out


def _residents(data: dict) -> float:
    pop = data.get("population")
    if pop is None or pop.empty:
        return np.nan
    if "base_ym" in pop:
        pop = pop[pop["base_ym"] == pop["base_ym"].max()]
    return float(pop["population"].sum())


def density(data: dict, codes: list[str]) -> tuple[int, float]:
    n = int(data["stores"]["inds_scls_cd"].isin(codes).sum())
    r = _residents(data)
    return n, (n / r * 10_000 if r else np.nan)


def lic_stats(data: dict, targets, asof: date | None = None) -> dict | None:
    if not targets:
        return None
    asof = pd.Timestamp(asof or date.today())
    frames = []
    for svc, bz in targets:
        lic = data.get("licenses", {}).get(svc)
        if lic is not None:
            frames.append(lic[lic["bzstat_nm"].isin(bz)])
    if not frames:
        return None
    df = pd.concat(frames)
    if df.empty:
        return None
    op, cl = scoring._to_date(df["open_date"]), scoring._to_date(df["close_date"])
    closed = df["status_cd"].eq("03") & cl.notna()
    alive = df["status_cd"].eq("01")
    cohort = op.between(asof - pd.DateOffset(years=7), asof - pd.DateOffset(years=3))
    died3 = cohort & closed & (cl <= op + pd.DateOffset(years=3))
    last = asof - pd.DateOffset(years=1)
    yrs = (asof - op[alive]).dt.days / 365.25
    return {"open_1y": int((op > last).sum()), "close_1y": int((closed & (cl > last)).sum()),
            "closure3": died3.sum() / cohort.sum() if cohort.sum() >= 10 else None,
            "young2": float((yrs < 2).mean()) if len(yrs) else None,
            "old5": float((yrs >= 5).mean()) if len(yrs) else None,
            "bz": sorted({b for _, bz in targets for b in bz})}


def profile(region_key: str, codes: list[str], label: str, table: pd.DataFrame,
            others: dict[str, dict] | None = None, comp_col: str = "comp_cnt", show_top: bool = True) -> dict:
    """codes: 소분류 코드들, table: 동네 점수표(score_preset/score_category 결과), others: 다른 시·군 data."""
    data = scoring.load_region_data(region_key)
    regions = scoring.load_regions()
    city = regions[region_key]["name"].split()[-1]
    st = data["stores"]
    sub = st[st["inds_scls_cd"].isin(codes)]
    n, per10k = density(data, codes)
    head, tags, facts, tips, warns = [], [], [], [], []

    # 밀도 (다른 시·군과 비교)
    comp = []
    for rk, d in (others or {}).items():
        if rk != region_key:
            _, p = density(d, codes)
            if pd.notna(p) and p > 0:
                comp.append((regions[rk]["name"].split()[-1], p))
    note = " · ".join(f"{nm} {p:.1f}곳" for nm, p in comp)
    facts.append((f"{label} 가게 수", f"{n:,}곳", f"{city} 전체"))
    if pd.notna(per10k):
        facts.append(("주민 1만 명당", f"{per10k:.1f}곳", note))
        if comp:
            avg = np.mean([p for _, p in comp])
            r = per10k / avg
            head.append("가게가 많은 편" if r >= 1.25 else "가게가 적은 편" if r <= 0.8 else "가게 수 보통")
            if r >= 1.25:
                warns.append(f"주민 수에 비해 가게가 다른 시·군 평균의 {r:.1f}배 — 손님을 나눠 먹는 경쟁이 셈")
    # 프랜차이즈
    if len(sub) and "is_franchise" in sub:
        fr = float(sub["is_franchise"].mean())
        tags.append(f"프랜차이즈 {fr * 100:.0f}%")
    if "I21201" in codes:
        brands = scoring.load_config().get("lowcost_coffee_brands", [])
        cafes = st[st["inds_scls_cd"] == "I21201"]
        lc = cafes["store_name"].astype(str).str.contains("|".join(brands)).mean() if brands and len(cafes) else None
        if lc is not None:
            tags.append(f"저가 커피 브랜드 {lc * 100:.0f}%")
            if lc >= 0.2:
                tips.append(f"카페 {lc * 100:.0f}%가 저가 커피 — 커피 값으로 경쟁하기보다 간식·세트로 차별화")
    # 어디에 몰려 있나
    if len(sub):
        cnt = sub.groupby("adong_nm").size().sort_values(ascending=False)
        top = cnt.head(3)
        tags.append("많은 곳: " + "·".join(f"{k} {v}" for k, v in top.items()))
    # 개·폐업 (인허가)
    ls = lic_stats(data, lic_targets(codes))
    if ls:
        net = ls["open_1y"] - ls["close_1y"]
        facts.append(("최근 1년 개업 / 폐업", f"{ls['open_1y']} / {ls['close_1y']}곳",
                      f"순증 {net:+d}곳 (인허가)"))
        if ls["closure3"] is not None:
            facts.append(("3년 안에 문 닫은 비율", f"{ls['closure3'] * 100:.0f}%", "3–7년 전 개업한 가게 기준"))
            if ls["closure3"] >= 0.35:
                warns.append(f"새로 연 가게 {ls['closure3'] * 100:.0f}%가 3년을 못 버팀 — 초기 자금·임대 기간을 넉넉히")
        if ls["close_1y"] and ls["open_1y"] >= ls["close_1y"] * 1.5:
            head.append("새 가게 느는 중")
        elif ls["open_1y"] < ls["close_1y"]:
            head.append("문 닫는 곳이 더 많음")
        if ls["young2"] is not None:
            tags.append(f"2년 미만 가게 {ls['young2'] * 100:.0f}%")
            if ls["young2"] >= 0.3:
                tips.append(f"영업 중인 가게 {ls['young2'] * 100:.0f}%가 2년 미만 — 새로 생기고 없어지는 게 빠른 업종, 주변 신규 개업 감시")
            elif ls["old5"] and ls["old5"] >= 0.5:
                tips.append(f"절반 이상({ls['old5'] * 100:.0f}%)이 5년 넘게 버틴 가게 — 단골이 붙은 터줏대감이 많아 차별점이 필요")
    # 점수 기준 좋은 동네 / 아직 없는 동네
    if table is not None and len(table):
        ranked = table[table["score"].notna()].sort_values("score", ascending=False)
        if len(ranked) and show_top:
            tips.insert(0, "점수 상위 동네: " + ", ".join(f"{r.adong_nm}({r.score:.0f}점)" for r in ranked.head(3).itertuples()))
        if comp_col in table:
            empty = table[(table[comp_col] == 0) & table["D"].notna()].sort_values("D", ascending=False)
            if len(empty):
                tips.append("아직 하나도 없는 동네 중 수요가 큰 곳: " + ", ".join(empty["adong_nm"].head(3)))
    # 소상공인365 시 전체 카페 매출 (카페일 때)
    if "I21201" in codes and region_key in sbiz.CITY:
        cn = sbiz.CITY[region_key]
        s = sbiz.latest_sales(region_key, cn, "카페")
        if s:
            g = sbiz.yoy(region_key, cn, "카페")
            facts.append(("카페 한 곳당 월매출", f"{s[0]:,.0f}만원",
                          f"{s[1]} · 1년 {g * 100:+.0f}%" if g is not None else s[1]))
            if g is not None and g <= -0.1:
                warns.append(f"{cn} 카페 한 곳당 매출이 1년 새 {g * 100:.0f}% — 시 전체가 식고 있음")
        mx = sbiz.sales_mix(region_key, cn)
        if mx is not None:
            wk = mx["weekday"][5] + mx["weekday"][6]
            h = int(np.argmax(mx["hour"]))
            a = int(np.argmax(mx["age"]))
            tags += [f"{cn} 주말 매출 {wk:.0f}%", f"피크 {sbiz.HOUR[h]}", f"주 고객 {sbiz.AGE[a]}"]
        dv = sbiz.delivery(region_key, cn)
        if len(dv):
            facts.append(("카페 배달 매출", f"월 {dv['합계'].iloc[-1]:,.0f}만원", f"{cn} 전체 · 소상공인365"))
    if not head:
        head.append(f"{city} {label}")
    return {"headline": " · ".join(head), "tags": tags, "facts": facts, "tips": tips, "warns": warns,
            "lic_note": ("인허가 업태: " + ", ".join(ls["bz"])) if ls else ""}

"""소상공인365에서 옮겨 적은 동네별 자료 (data/sbiz/{지역}/{동}.yaml) 읽기·요약.

동네마다 파일 하나: 그 동네의 유동인구 구성, 카페 매출(동 전체·반경 500m), 매출 구성,
배달 매출, 반경 500m 방문 고객 특성. 다른 동네 자료와 섞지 않고 그 동네 값 그대로 씁니다.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from src import scoring

DATA_DIR = scoring.ROOT / "data" / "sbiz"
AGE = ["10대", "20대", "30대", "40대", "50대", "60대+"]
HOUR = ["05–09시", "09–12시", "12–14시", "14–18시", "18–23시", "23–05시"]
WEEK = ["월", "화", "수", "목", "금", "토", "일"]
CITY = {"andong": "안동시", "seongnam": "성남시", "yecheon": "예천군"}
_cache: dict = {}


def _files(region_key: str) -> list[Path]:
    d = DATA_DIR / region_key
    return sorted(d.glob("*.yaml")) if d.exists() else []


def _load_region(region_key: str) -> dict:
    files = _files(region_key)
    key = tuple((p.name, p.stat().st_mtime_ns) for p in files)
    if _cache.get(region_key, {}).get("key") != key:
        data = {}
        for p in files:
            with open(p, encoding="utf-8") as f:
                y = yaml.safe_load(f) or {}
            data[y.get("name", p.stem)] = y
        _cache[region_key] = {"key": key, "data": data}
    return _cache[region_key]["data"]


def get(region_key: str, dong_nm: str) -> dict:
    return _load_region(region_key).get(dong_nm) or {}


def period(region_key: str = "andong") -> str:
    city = get(region_key, CITY.get(region_key, ""))
    return str(city.get("period", "")) if city else ""


def dongs(region_key: str) -> list[str]:
    city = CITY.get(region_key)
    return [d for d in _load_region(region_key) if d != city]


def floating_totals(region_key: str) -> dict:
    """{동: 일평균 유동인구}"""
    return {d: v["floating_daily"] for d, v in _load_region(region_key).items()
            if v.get("floating_daily") and d != CITY.get(region_key)}


def _shares(v) -> np.ndarray:
    a = np.array(v, dtype=float)
    return a / a.sum() * 100 if a.sum() else a


def detail(region_key: str, dong_nm: str) -> dict | None:
    """유동인구 구성 비율(%) {'sex','age','weekday','hour', 'total'}"""
    y = get(region_key, dong_nm)
    fl = y.get("floating")
    if not fl:
        return None
    out = {k: _shares(fl[k]) for k in ("sex", "age", "weekday", "hour") if k in fl}
    out["total"] = y.get("floating_daily") or int(sum(fl.get("sex", [0])))
    return out


def city_detail(region_key: str) -> dict | None:
    return detail(region_key, CITY.get(region_key, ""))


def _month_index(months) -> list[str]:
    return [f"{str(m)[:4]}.{str(m)[4:]}" for m in months]


def sales_series(region_key: str, dong_nm: str, item: str = "카페", which: str = "dong") -> pd.Series:
    """카페 점포당 월평균 매출(만원). which='dong'(동 전체) 또는 'r500'(동 중심 반경 500m)"""
    cs = get(region_key, dong_nm).get("cafe_sales") or {}
    vals = cs.get(which)
    if item != "카페" or not vals:
        return pd.Series(dtype=float)
    return pd.Series([np.nan if v is None else float(v) for v in vals], index=_month_index(cs["months"][: len(vals)]))


def latest_sales(region_key: str, dong_nm: str, item: str = "카페", which: str = "dong") -> tuple[float, str] | None:
    s = sales_series(region_key, dong_nm, item, which).dropna()
    return (float(s.iloc[-1]), s.index[-1]) if len(s) else None


def yoy(region_key: str, dong_nm: str, item: str = "카페", which: str = "dong") -> float | None:
    """마지막 달 vs 1년 전 같은 달"""
    s = sales_series(region_key, dong_nm, item, which)
    if len(s) == 13 and pd.notna(s.iloc[0]) and pd.notna(s.iloc[-1]) and s.iloc[0]:
        return s.iloc[-1] / s.iloc[0] - 1
    return None


def sales_note(region_key: str, dong_nm: str) -> str:
    return (get(region_key, dong_nm).get("cafe_sales") or {}).get("note", "")


def sales_mix(region_key: str, dong_nm: str, which: str = "dong") -> dict | None:
    """카페 매출 구성(%) {'weekday','hour','sex','age'}"""
    m = (get(region_key, dong_nm).get("cafe_sales_mix") or {}).get(which)
    return {k: np.array(v, dtype=float) for k, v in m.items()} if m else None


def delivery(region_key: str, dong_nm: str) -> pd.DataFrame:
    """카페 배달 매출(만원/월): index 월, columns 남·여·합계"""
    d = get(region_key, dong_nm).get("cafe_delivery")
    if not d:
        return pd.DataFrame()
    t = pd.DataFrame({"남": d["male"], "여": d["female"]}, index=_month_index(d["months"]))
    t["합계"] = t["남"] + t["여"]
    return t


def customers(region_key: str, dong_nm: str) -> dict | None:
    return get(region_key, dong_nm).get("customers_r500")


# ---------------------------------------------------------------- 분석결과 해설 (그 동네 값만)
def commentary(region_key: str, dong_nm: str, item: str = "카페") -> list[str]:
    d, c = detail(region_key, dong_nm), city_detail(region_key)
    city = CITY.get(region_key, "시")
    out = []
    if d is not None:
        h, w = int(np.argmax(d["hour"])), int(np.argmax(d["weekday"]))
        out.append(f"하루 평균 **{d['total']:,}명**이 지나가고, **{HOUR[h]}**({d['hour'][h]:.0f}%)와 **{WEEK[w]}요일**에 가장 많습니다.")
        if c is not None:
            young, cy = d["age"][0] + d["age"][1], c["age"][0] + c["age"][1]
            diff = young - cy
            if abs(diff) <= 0.5:
                out.append(f"10–20대 비중은 **{young:.0f}**%로 {city} 평균({cy:.0f}%)과 비슷합니다.")
            else:
                out.append(f"10–20대 비중은 **{young:.0f}**%로 {city} 평균({cy:.0f}%)보다 {'높습니다' if diff > 0 else '낮습니다'} ({diff:+.1f}%p).")
    ls, cs = latest_sales(region_key, dong_nm, item), latest_sales(region_key, city, item)
    if ls:
        txt = f"{item} 한 곳당 월매출은 **{ls[0]:,.0f}만원**({ls[1]})"
        if cs:
            txt += f"으로 {city} 평균({cs[0]:,.0f}만원)의 **{ls[0] / cs[0]:.1f}배**"
        g = yoy(region_key, dong_nm, item)
        if g is not None:
            txt += f", 1년 전보다 **{g * 100:+.0f}**%"
        out.append(txt + "입니다.")
    mx = sales_mix(region_key, dong_nm)
    if mx is not None:
        wk = mx["weekday"][5] + mx["weekday"][6]
        h = int(np.argmax(mx["hour"]))
        a = int(np.argmax(mx["age"]))
        kind = "**주말 장사**" if wk >= 40 else "**평일 장사**" if wk <= 30 else "평일·주말 고른"
        out.append(f"카페 매출은 주말(토·일)이 {wk:.0f}%인 {kind} 동네이고, **{HOUR[h]}**에 {mx['hour'][h]:.0f}%가 팔리며 "
                   f"주 고객은 **{AGE[a]}**({mx['age'][a]:.0f}%)입니다.")
    dv = delivery(region_key, dong_nm)
    if len(dv) == 13:
        g = dv["합계"].iloc[-1] / dv["합계"].iloc[0] - 1 if dv["합계"].iloc[0] else None
        out.append(f"카페 배달 매출은 월 **{dv['합계'].iloc[-1]:,.0f}만원**({dv.index[-1]})"
                   + (f", 1년 전보다 {g * 100:+.0f}%" if g is not None else "")
                   + f" · 주문자 여성 {dv['여'].sum() / dv['합계'].sum() * 100:.0f}%.")
    cu = customers(region_key, dong_nm)
    if cu:
        out.append(f"반경 500m 카페 손님은 신규 {cu['new_regular'][0]:.0f}% · 단골 {cu['new_regular'][1]:.0f}%"
                   f"(월 3회 이상 방문)입니다.")
    return out


# ---------------------------------------------------------------- 상권 비교 (참고용)
def compare_table(region_key: str, scores: pd.DataFrame | None = None, item: str = "카페") -> pd.DataFrame:
    rows = []
    for nm in dongs(region_key):
        d = detail(region_key, nm)
        if d is None:
            continue
        ls = latest_sales(region_key, nm, item)
        mx = sales_mix(region_key, nm)
        dv = delivery(region_key, nm)
        g = yoy(region_key, nm, item)
        rows.append({
            "동네": nm, "일평균 유동인구(명)": d["total"],
            "10–20대 %": round(d["age"][0] + d["age"][1], 1),
            f"{item} 월매출(만원)": ls[0] if ls else np.nan,
            "매출 1년 증감 %": float(round(g * 100)) if g is not None else np.nan,
            "주말 매출 %": round(mx["weekday"][5] + mx["weekday"][6], 1) if mx is not None else np.nan,
            "배달 월매출(만원)": float(dv["합계"].iloc[-1]) if len(dv) else np.nan,
        })
    t = pd.DataFrame(rows)
    if scores is not None and len(t):
        sc = scores.set_index(scores["adong_nm"].astype(str).str.strip())["score"]
        t["입지 점수"] = t["동네"].map(sc).round(0)
    return t.sort_values("일평균 유동인구(명)", ascending=False).reset_index(drop=True) if len(t) else t


# ---------------------------------------------------------------- 업력 현황 (우리 인허가 자료)
AGE_BINS = [(0, 1, "1년 미만"), (1, 2, "1–2년"), (2, 3, "2–3년"), (3, 5, "3–5년"), (5, 200, "5년 이상")]


def business_age(data: dict, adong_cd: str | None, bzstat: list[str], today: date | None = None) -> pd.Series:
    lic = data.get("licenses", {}).get("rest_cafes")
    if lic is None:
        return pd.Series(dtype=int)
    df = lic[lic["bzstat_nm"].isin(bzstat) & lic["status_cd"].ne("03")].copy()
    if adong_cd is not None:
        df = df[scoring.attach_dong_key(df, scoring.dong_master(data)) == adong_cd]
    op = scoring._to_date(df["open_date"])
    yrs = (pd.Timestamp(today or date.today()) - op).dt.days / 365.25
    return pd.Series({lb: int(((yrs >= a) & (yrs < b)).sum()) for a, b, lb in AGE_BINS})


# ---------------------------------------------------------------- 동네 요약 카드 (그 동네 값만)
def profile(region_key: str, dong_nm: str, item: str = "카페") -> dict | None:
    """'이 동네 장사 성격' 요약: headline, tags, facts, tips, warns. 소상공인365 자료가 없으면 None."""
    d = detail(region_key, dong_nm)
    ls = latest_sales(region_key, dong_nm, item)
    mx = sales_mix(region_key, dong_nm)
    if d is None and ls is None and mx is None:
        return None
    city = CITY.get(region_key, "시")
    cs = latest_sales(region_key, city, item)
    g = yoy(region_key, dong_nm, item)
    dv = delivery(region_key, dong_nm)
    cu = customers(region_key, dong_nm)
    tags, facts, tips, warns, head = [], [], [], [], []

    # 장사 요일
    if mx is not None:
        wk = mx["weekday"][5] + mx["weekday"][6]
        if wk <= 30:
            head.append("평일 장사")
            tips.append(f"매출의 {100 - wk:.0f}%가 평일 — 출근·점심 직장인 테이크아웃 중심, 주말은 짧게 열거나 쉬는 것도 방법")
        elif wk >= 40:
            head.append("주말 장사")
            tips.append(f"주말(토·일) 매출이 {wk:.0f}% — 주말 인력·재고를 늘리고 나들이·가족 손님용 간식 세트")
        else:
            head.append("평일·주말 고른 동네")
        h = int(np.argmax(mx["hour"]))
        tags.append(f"매출 피크 {HOUR[h]} ({mx['hour'][h]:.0f}%)")
        if mx["hour"][0] + mx["hour"][1] >= 25:
            tips.append(f"오전(05–12시) 매출이 {mx['hour'][0] + mx['hour'][1]:.0f}% — 일찍 여는 모닝커피·아침 간식")
        if mx["hour"][4] >= 24:
            tips.append(f"저녁(18–23시) 매출이 {mx['hour'][4]:.0f}% — 늦게까지 영업할 가치가 있음")
        a = int(np.argmax(mx["age"]))
        head.append(f"{AGE[a]} 손님")
        young = mx["age"][0] + mx["age"][1]
        tags.append(f"주 고객 {AGE[a]} ({mx['age'][a]:.0f}%)")
        tags.append(f"여성 매출 {mx['sex'][1]:.0f}%")
        if young >= 17:
            tips.append(f"10–20대 매출 {young:.0f}% — SNS에 올릴 만한 디저트·음료, 학생 할인")
        elif young <= 9:
            tips.append(f"10–20대 매출이 {young:.0f}%뿐 — 핫도그·간식은 학생보다 어른 손님 입맛(덜 단 메뉴) 위주로")

    # 매출 수준·추세
    if ls:
        if cs:
            r = ls[0] / cs[0]
            head.append("카페 매출 높음" if r >= 1.2 else "카페 매출 낮음" if r <= 0.8 else "카페 매출 보통")
            facts.append((f"{item} 한 곳당 월매출", f"{ls[0]:,.0f}만원", f"{city} 평균의 {r:.1f}배 ({ls[1]})"))
        else:
            facts.append((f"{item} 한 곳당 월매출", f"{ls[0]:,.0f}만원", ls[1]))
        if g is not None:
            facts.append(("1년 전보다", f"{g * 100:+.0f}%", "같은 달 비교"))
            if g >= 0.1:
                head.append("매출 성장 중")
            elif g <= -0.15:
                warns.append(f"카페 한 곳당 매출이 1년 새 {g * 100:.0f}% — 새 가게가 늘었거나 손님이 줄고 있는지 현장 확인 필요")
    # 유동인구
    if d is not None:
        tot = floating_totals(region_key)
        rank = sorted(tot.values(), reverse=True).index(d["total"]) + 1 if d["total"] in tot.values() else None
        facts.append(("일평균 유동인구", f"{d['total']:,}명", f"조사한 {len(tot)}개 동 중 {rank}위" if rank else ""))
        if ls and cs and rank and rank <= 3 and ls[0] / cs[0] <= 0.85:
            warns.append("지나가는 사람은 많은데 카페 한 곳당 매출은 낮음 — 카페가 이미 많아 나눠 먹는 상권일 수 있음")
    # 배달
    if len(dv):
        last = dv["합계"].iloc[-1]
        dg = last / dv["합계"].iloc[0] - 1 if len(dv) == 13 and dv["합계"].iloc[0] else None
        facts.append(("카페 배달 매출", f"월 {last:,.0f}만원", (f"1년 {dg * 100:+.0f}% · " if dg is not None else "")
                      + f"여성 주문 {dv['여'].sum() / dv['합계'].sum() * 100:.0f}%"))
        if last >= 500 or (dg is not None and dg >= 0.3):
            tips.append("배달이 크거나 빠르게 늘고 있음 — 배달앱 입점과 포장 간식 메뉴를 처음부터 준비")
    if cu:
        reg = cu["new_regular"][1]
        tags.append(f"단골 {reg:.0f}%")
        if reg < 20:
            tips.append(f"반경 500m 손님 중 단골(월 3회 이상)이 {reg:.0f}%뿐 — 스탬프·멤버십으로 재방문 만들기")
    return {"headline": " · ".join(head), "tags": tags, "facts": facts, "tips": tips, "warns": warns,
            "period": period(region_key)}

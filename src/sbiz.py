"""소상공인365에서 옮겨 적은 동네 자료 (config/sbiz_floating.yaml) 읽기·요약.

소상공인365의 좋은 기능 중 우리 앱에 없던 것을 가져왔습니다.
- 유동인구 구성: 성별·연령·요일·시간대 (동네 vs 안동시 평균)
- 업종 매출 추이: 점포당 월평균 매출 13개월 (동네 vs 안동시)
- 분석결과 해설: 숫자를 한 문장으로 풀어 주는 해설
- 상권 비교: 동네끼리 유동인구·매출·연령 구성을 한 표로
- 업력 현황: 지금 영업 중인 가게가 문을 연 지 얼마나 됐는지 (우리 인허가 자료로 계산)
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import yaml

from src import scoring

AGE = ["10대", "20대", "30대", "40대", "50대", "60대+"]
HOUR = ["05–09시", "09–12시", "12–14시", "14–18시", "18–23시", "23–05시"]
WEEK = ["월", "화", "수", "목", "금", "토", "일"]
CITY = {"andong": "안동시", "seongnam": "성남시", "yecheon": "예천군"}
_cache: dict = {}


def load() -> dict:
    p = scoring.SBIZ_FLOATING_YAML
    if not p.exists():
        return {}
    key = p.stat().st_mtime_ns
    if _cache.get("key") != key:
        with open(p, encoding="utf-8") as f:
            _cache.update(key=key, data=yaml.safe_load(f) or {})
    return _cache["data"]


def period() -> str:
    return str(load().get("period", ""))


def _shares(v) -> np.ndarray:
    a = np.array(v, dtype=float)
    return a / a.sum() * 100 if a.sum() else a


def detail(region_key: str, dong_nm: str) -> dict | None:
    """{'sex','age','weekday','hour': 비율(%) 배열, 'total': 일평균} — 자료가 없으면 None."""
    d = ((load().get("detail") or {}).get(region_key) or {}).get(dong_nm)
    if not d:
        return None
    out = {k: _shares(d[k]) for k in ("sex", "age", "weekday", "hour") if k in d}
    out["total"] = (load().get("regions") or {}).get(region_key, {}).get(dong_nm) or int(sum(d.get("sex", [0])))
    return out


def city_detail(region_key: str) -> dict | None:
    return detail(region_key, CITY.get(region_key, ""))


def sales_series(region_key: str, dong_nm: str, item: str = "카페") -> pd.Series:
    """점포당 월평균 매출(만원) — index: 'YYYY.MM'"""
    s = load().get("sales") or {}
    months = s.get("months") or []
    vals = ((s.get(region_key) or {}).get(dong_nm) or {}).get(item)
    if vals is None:
        return pd.Series(dtype=float)
    if not isinstance(vals, list):  # 예전 형식: 숫자 하나
        return pd.Series([float(vals)], index=["최근"])
    idx = [f"{str(m)[:4]}.{str(m)[4:]}" for m in months[: len(vals)]]
    return pd.Series([np.nan if v is None else float(v) for v in vals], index=idx)


def latest_sales(region_key: str, dong_nm: str, item: str = "카페") -> tuple[float, str] | None:
    s = sales_series(region_key, dong_nm, item).dropna()
    return (float(s.iloc[-1]), s.index[-1]) if len(s) else None


def yoy(region_key: str, dong_nm: str, item: str = "카페") -> float | None:
    """같은 달 1년 전 대비 증감률 (마지막 달과 13개월 전 첫 달이 모두 있을 때)"""
    s = sales_series(region_key, dong_nm, item)
    if len(s) == 13 and pd.notna(s.iloc[0]) and pd.notna(s.iloc[-1]) and s.iloc[0]:
        return s.iloc[-1] / s.iloc[0] - 1
    return None


def dongs(region_key: str) -> list[str]:
    city = CITY.get(region_key)
    return [d for d in ((load().get("detail") or {}).get(region_key) or {}) if d != city]


# ---------------------------------------------------------------- 분석결과 해설
def commentary(region_key: str, dong_nm: str, item: str = "카페") -> list[str]:
    """소상공인365의 '분석결과 해설'처럼 숫자를 문장으로."""
    d, c = detail(region_key, dong_nm), city_detail(region_key)
    city = CITY.get(region_key, "시")
    out = []
    if d is not None:
        h = int(np.argmax(d["hour"]))
        w = int(np.argmax(d["weekday"]))
        young = d["age"][0] + d["age"][1]
        line = (f"하루 평균 **{d['total']:,}명**이 지나가고, **{HOUR[h]}**({d['hour'][h]:.0f}%)와 "
                f"**{WEEK[w]}요일**에 가장 많습니다.")
        out.append(line)
        if c is not None:
            cy = c["age"][0] + c["age"][1]
            diff = young - cy
            if abs(diff) <= 0.5:
                out.append(f"10–20대 비중은 **{young:.0f}**%로 {city} 평균({cy:.0f}%)과 비슷합니다.")
            else:
                word = "높습니다" if diff > 0 else "낮습니다"
                out.append(f"10–20대 비중은 **{young:.0f}**%로 {city} 평균({cy:.0f}%)보다 {word} ({diff:+.1f}%p).")
            wk = d["weekday"][5] + d["weekday"][6]
            cwk = c["weekday"][5] + c["weekday"][6]
            if abs(wk - cwk) >= 2:
                out.append(f"주말(토·일) 유동 비중이 {wk:.0f}%로 {city}({cwk:.0f}%)보다 "
                           + ("높아 **주말 장사**가 유리한 동네입니다." if wk > cwk else "낮아 **평일 장사** 중심 동네입니다."))
    ls = latest_sales(region_key, dong_nm, item)
    cs = latest_sales(region_key, city, item)
    if ls:
        txt = f"{item} 점포당 월매출은 **{ls[0]:,.0f}만원**({ls[1]})"
        if cs:
            txt += f"으로 {city} 평균({cs[0]:,.0f}만원)의 **{ls[0] / cs[0]:.1f}배**"
        g = yoy(region_key, dong_nm, item)
        if g is not None:
            txt += f", 1년 전보다 {g * 100:+.0f}%"
        out.append(txt + "입니다.")
    return out


# ---------------------------------------------------------------- 상권 비교
def compare_table(region_key: str, scores: pd.DataFrame | None = None, item: str = "카페") -> pd.DataFrame:
    rows = []
    for nm in dongs(region_key):
        d = detail(region_key, nm)
        ls = latest_sales(region_key, nm, item)
        row = {"동네": nm, "일평균 유동인구(명)": d["total"],
               "10–20대 %": round(d["age"][0] + d["age"][1], 1),
               "14–18시 %": round(d["hour"][3], 1),
               "주말 %": round(d["weekday"][5] + d["weekday"][6], 1),
               f"{item} 월매출(만원)": ls[0] if ls else np.nan}
        g = yoy(region_key, nm, item)
        row["매출 1년 증감 %"] = float(round(g * 100)) if g is not None else np.nan
        rows.append(row)
    t = pd.DataFrame(rows)
    if scores is not None and len(t):
        sc = scores.set_index(scores["adong_nm"].astype(str).str.strip())["score"]
        t["입지 점수"] = t["동네"].map(sc).round(0)
    return t.sort_values("일평균 유동인구(명)", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------- 업력 현황 (우리 인허가 자료)
AGE_BINS = [(0, 1, "1년 미만"), (1, 2, "1–2년"), (2, 3, "2–3년"), (3, 5, "3–5년"), (5, 200, "5년 이상")]


def business_age(data: dict, adong_cd: str | None, bzstat: list[str], today: date | None = None) -> pd.Series:
    """지금 영업 중인 가게가 문을 연 지 몇 년 됐는지 구간별 가게 수 (adong_cd=None이면 시 전체)."""
    lic = data.get("licenses", {}).get("rest_cafes")
    if lic is None:
        return pd.Series(dtype=int)
    df = lic[lic["bzstat_nm"].isin(bzstat) & lic["status_cd"].ne("03")].copy()
    if adong_cd is not None:
        df = df[scoring.attach_dong_key(df, scoring.dong_master(data)) == adong_cd]
    op = scoring._to_date(df["open_date"])
    yrs = (pd.Timestamp(today or date.today()) - op).dt.days / 365.25
    return pd.Series({lb: int(((yrs >= a) & (yrs < b)).sum()) for a, b, lb in AGE_BINS})

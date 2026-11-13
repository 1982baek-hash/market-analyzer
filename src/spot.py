"""지도에서 고른 자리 주변 상권 — 반경 안 경쟁점·전체 가게·버스·학교·최근 개업/폐업을 한 번에."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from src import catprof, mystore, sbiz, scoring


def analyze(region_key: str, data: dict, lat: float, lon: float, radius: int,
            codes: dict[str, list[str]], table: pd.DataFrame | None = None) -> dict:
    """codes: {묶음 이름: [소분류 코드…]} 예) {'카페': ['I21201'], '간식점': [...]} 또는 {'치킨': ['I21006']}"""
    cfg = scoring.load_config()
    near_all = mystore._near(data.get("stores"), lat, lon, radius)
    comp = near_all[near_all["inds_scls_cd"].astype(str).isin([c for cs in codes.values() for c in cs])].copy() \
        if not near_all.empty else near_all
    if not comp.empty:
        br = comp["branch_name"].fillna("").astype(str) if "branch_name" in comp else ""
        comp["store_name"] = comp["store_name"].fillna("").astype(str) + br  # '커피베이안' + '동대점'
        comp["group"] = None
        for g, cs in codes.items():
            comp.loc[comp["inds_scls_cd"].astype(str).isin(cs), "group"] = g
        brands = cfg.get("lowcost_coffee_brands", [])
        comp["lowcost"] = comp["inds_scls_cd"].eq("I21201") & comp["store_name"].fillna("").apply(
            lambda n: any(b in n for b in brands))
    ctx = mystore.context_near(data, lat, lon, radius, region_key)
    dong = mystore.dong_of_point(data, lat, lon)

    # 최근 1년 개업·폐업 (음식점 인허가로 확인되는 업종만)
    targets = catprof.lic_targets([c for cs in codes.values() for c in cs])
    opened = closed = None
    if targets:
        frames = []
        for svc, bz in targets:
            lic = data.get("licenses", {}).get(svc)
            if lic is not None:
                frames.append(lic[lic["bzstat_nm"].isin(bz)])
        if frames:
            ln = mystore._near(pd.concat(frames), lat, lon, radius)
            if not ln.empty:
                op, cl = scoring._to_date(ln["open_date"]), scoring._to_date(ln["close_date"])
                since = pd.Timestamp(date.today()) - pd.Timedelta(days=365)
                opened = ln[op > since].assign(day=op[op > since]).sort_values("day", ascending=False)
                isc = ln["status_cd"].eq("03") & (cl > since)
                closed = ln[isc].assign(day=cl[isc]).sort_values("day", ascending=False)
            else:
                opened = closed = ln

    row = None
    if dong and table is not None and len(table):
        t = table[table["adong_cd"] == dong[0]]
        row = t.iloc[0] if len(t) else None
    prof = sbiz.profile(region_key, dong[1]) if dong else None
    return {"comp": comp, "near_all": near_all, "ctx": ctx, "dong": dong, "row": row, "opened": opened,
            "closed": closed, "prof": prof, "n_ranked": int(table["score"].notna().sum()) if table is not None else 0}


def lines(a: dict, codes: dict[str, list[str]], radius: int) -> list[str]:
    """한 줄 해설."""
    out = []
    comp = a["comp"]
    for g in codes:
        sub = comp[comp["group"] == g] if not comp.empty else comp
        if len(sub):
            n = sub.sort_values("dist").iloc[0]
            out.append(f"가장 가까운 {g}: **{n['store_name']}** {n['dist']:.0f}m")
        else:
            out.append(f"반경 {radius:,}m 안에 {g}이(가) **하나도 없어요** — 경쟁은 없지만 수요가 있는 자리인지 현장 확인 필요")
    sl = a["ctx"]["school_list"]
    if sl is not None and len(sl):
        top = sl.sort_values("dist").head(3)
        out.append("가까운 학교·대학: " + ", ".join(
            f"{r['name']} {'캠퍼스 안' if r.get('campus') is True and r['dist'] < 1 else f'{r.dist:.0f}m'}"
            for _, r in top.iterrows()))
    if a["opened"] is not None and a["closed"] is not None:
        no, nc = len(a["opened"]), len(a["closed"])
        if no or nc:
            out.append(f"최근 1년 같은 업종 **새로 연 곳 {no} · 문 닫은 곳 {nc}** (인허가)"
                       + (" — 문 닫는 곳이 더 많아요" if nc > no else ""))
    if a["prof"] and a["prof"].get("headline"):
        out.append(f"{a['dong'][1]} 카페 장사 성격: {a['prof']['headline']} (소상공인365)")
    return out

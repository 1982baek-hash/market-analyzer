import re

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src import scoring, ui

st.set_page_config(page_title="동네 리포트", page_icon="🏘️", layout="wide")
ui.require_login()

region = ui.pick_region()
data = ui.region_data(region)
dong_cd, dong_nm = ui.pick_dong(data)

st.title(f"🏘️ {dong_nm} 동네 리포트")
ui.period_badges(data["periods"])

table, _ = ui.preset_scores(region)
row = table.set_index("adong_cd").loc[dong_cd] if dong_cd in set(table["adong_cd"]) else None
cfg = scoring.load_config()
pc = cfg["presets"]["coffee_snack"]

# ---------------- 한눈에
if row is not None:
    c = st.columns(6)
    c[0].metric("커피+간식 입지 점수", "판단 보류" if pd.isna(row["score"]) else f"{row['score']:.0f}점",
                help=row["hold_reason"] or None)
    c[1].metric("주민", f"{int(row['resident']):,}명" if pd.notna(row["resident"]) else scoring.NOT_PROVIDED)
    c[2].metric("10–29세 비중", f"{row['target_share'] * 100:.0f}%" if pd.notna(row["target_share"]) else "–")
    c[3].metric("직장인", f"{int(row['worker']):,}명" if pd.notna(row["worker"]) else scoring.NOT_PROVIDED)
    c[4].metric("카페", f"{int(row['cafe_cnt'])}곳")
    c[5].metric("간식점", f"{int(row['snack_cnt'])}곳")
    if row["reasons"]:
        st.markdown("  \n".join(row["reasons"]))

st.divider()
left, right = st.columns(2)

# ---------------- 인구 피라미드
with left:
    st.subheader("인구 구성")
    pop = data["population"]
    if pop is None or dong_cd not in set(pop["adong_cd"]):
        st.info(scoring.NOT_PROVIDED)
    else:
        p = pop.set_index("adong_cd").loc[dong_cd]
        ages = [a for a in range(0, 110, 10) if f"m_{a}" in p]
        labels = [f"{a}대" if a else "0–9세" for a in ages]
        labels[-1] = f"{ages[-1]}세 이상"
        fig = go.Figure()
        fig.add_bar(y=labels, x=[-p[f"m_{a}"] for a in ages], orientation="h", name="남",
                    marker_color="#4C78A8", customdata=[p[f"m_{a}"] for a in ages],
                    hovertemplate="%{y} 남 %{customdata:,}명<extra></extra>")
        fig.add_bar(y=labels, x=[p[f"f_{a}"] for a in ages], orientation="h", name="여",
                    marker_color="#E45756", hovertemplate="%{y} 여 %{x:,}명<extra></extra>")
        mx = max(max(p[f"m_{a}"], p[f"f_{a}"]) for a in ages) or 1
        step = int(max(1, round(mx / 3, -len(str(int(mx / 3))) + 1)))
        ticks = [i * step for i in range(-3, 4)]
        fig.update_layout(barmode="relative", height=360, margin=dict(l=10, r=10, t=10, b=10),
                          xaxis=dict(tickvals=ticks, ticktext=[f"{abs(v):,}" for v in ticks], title=None),
                          legend=dict(orientation="h", y=1.08), bargap=0.1)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"주민등록 인구 {data['periods'].get('population', '')} 기준")

# ---------------- 업종 구성
with right:
    st.subheader("업종 구성 (상위 10개 중분류)")
    s = data["stores"].copy()
    s["dong"] = scoring.attach_dong_key(s, scoring.dong_master(data))
    here = s[s["dong"] == dong_cd]
    top = here.groupby("inds_mcls_nm").size().sort_values(ascending=True).tail(10)
    fig = go.Figure(go.Bar(x=top.values, y=top.index, orientation="h", marker_color="#72B7B2",
                           hovertemplate="%{y} %{x}곳<extra></extra>"))
    fig.update_layout(height=360, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, use_container_width=True)
    st.caption(f"점포 {len(here):,}곳 · 상가정보 {data['periods'].get('stores', '')} 기준")

# ---------------- 개·폐업 추이
st.subheader("최근 10년 개업·폐업 (음식점 인허가)")
trend = scoring.license_trend(data, dong_cd)
if trend.empty:
    st.info(scoring.NOT_PROVIDED)
else:
    which = st.radio("업태", ["휴게음식점", "일반음식점"], horizontal=True,
                     help="휴게음식점 = 카페·분식·제과·패스트푸드 등 / 일반음식점 = 식사·주류")
    tr = trend[trend["구분"] == which]
    fig = go.Figure()
    fig.add_bar(x=tr["연도"], y=tr["개업"], name="개업", marker_color="#54A24B")
    fig.add_bar(x=tr["연도"], y=-tr["폐업"], name="폐업", marker_color="#E45756", customdata=tr["폐업"],
                hovertemplate="%{x} 폐업 %{customdata}곳<extra></extra>")
    fig.update_layout(barmode="relative", height=300, margin=dict(l=10, r=10, t=10, b=10),
                      legend=dict(orientation="h", y=1.1), xaxis=dict(dtick=1))
    st.plotly_chart(fig, use_container_width=True)
    st.caption("올해는 지금까지의 건수입니다. 인허가 자료는 2일 전 기준으로 갱신됩니다.")

# ---------------- 유동인구
st.subheader("유동인구")
st.info(f"{scoring.NOT_PROVIDED} — 유동·방문인구 자료는 다음 단계에서 붙입니다.")

# ---------------- 커피·간식 경쟁점
st.subheader("커피·간식 경쟁점")
groups = {g["name"]: g["scls"] for g in pc["groups"].values()}
comp = here[here["inds_scls_cd"].isin(sum(groups.values(), []))].copy()
if comp.empty:
    st.info("이 동네에는 커피·간식 점포가 없습니다.")
else:
    brands = cfg.get("lowcost_coffee_brands", [])
    comp["구분"] = comp["inds_scls_cd"].map({c: k for k, v in groups.items() for c in v})
    comp["저가 커피"] = comp["store_name"].fillna("").str.contains("|".join(map(re.escape, brands))) if brands else False
    import folium
    from streamlit_folium import st_folium

    pts = comp.dropna(subset=["lat", "lon"])
    if len(pts):
        fmap = folium.Map(location=[pts["lat"].median(), pts["lon"].median()], zoom_start=15, tiles="CartoDB positron")
        for _, r in pts.iterrows():
            color = "#8c564b" if r["구분"] == "카페" else "#ff7f0e"
            folium.CircleMarker([r["lat"], r["lon"]], radius=5, color=color, fill=True, fill_opacity=0.9,
                                tooltip=f"{r['store_name']} · {r['inds_scls_nm']}" + (" · 저가 커피" if r["저가 커피"] else "")).add_to(fmap)
        st_folium(fmap, height=380, use_container_width=True, returned_objects=[])
        st.caption("갈색 = 카페, 주황 = 간식점")
    st.dataframe(
        comp[["구분", "store_name", "inds_scls_nm", "저가 커피", "rdnm_adr"]].rename(
            columns={"store_name": "상호", "inds_scls_nm": "업종", "rdnm_adr": "주소"}).sort_values(["구분", "업종"]),
        hide_index=True, use_container_width=True,
    )
    st.caption("상가정보에는 폐업한 가게가 한동안 남아 있을 수 있습니다. 현장에서 한 번 더 확인해 주세요.")

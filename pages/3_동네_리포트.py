import re

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src import sbiz, scoring, ui

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
    ui.stat_tiles([
        ("커피+간식 입지 점수", "판단 보류" if pd.isna(row["score"]) else f"{row['score']:.0f}점",
         row["hold_reason"] or ""),
        ("주민", f"{int(row['resident']):,}명" if pd.notna(row["resident"]) else "–",
         "" if pd.notna(row["resident"]) else scoring.NOT_PROVIDED),
        ("10–29세 비중", f"{row['target_share'] * 100:.0f}%" if pd.notna(row["target_share"]) else "–", ""),
        ("직장인", f"{int(row['worker']):,}명" if pd.notna(row["worker"]) else "–",
         "" if pd.notna(row["worker"]) else scoring.NOT_PROVIDED),
        ("카페", f"{int(row['cafe_cnt'])}곳", ""),
        ("간식점", f"{int(row['snack_cnt'])}곳", ""),
    ])
    if row["reasons"]:
        st.markdown("  \n".join(row["reasons"]))
    rl = ui.rent_line(row)
    st.caption(rl if rl else "💰 임대료: 한국부동산원 조사 상권이 아니라 제공되지 않음 · 주변 매물 시세로 확인해 주세요")

# ---------------- 소상공인365로 본 이 동네 (유동인구 구성·매출 추이·해설)
sd, sc = sbiz.detail(region, dong_nm), sbiz.city_detail(region)
cafe_sales = sbiz.sales_series(region, dong_nm, "카페")
if sd is not None or len(cafe_sales):
    st.divider()
    st.subheader("📊 유동인구 구성 · 카페 매출 (소상공인365)")
    lines = sbiz.commentary(region, dong_nm, "카페")
    if lines:
        st.info("  \n".join(f"· {x}" for x in lines))
    city_nm = sbiz.CITY.get(region, "시")
    colors = {"here": "#E45756", "city": "#BAB0AC"}

    def _pair_bar(labels, here, city, title, height=230):
        fig = go.Figure()
        fig.add_bar(x=labels, y=here, name=dong_nm, marker_color=colors["here"],
                    hovertemplate="%{x} %{y:.1f}%<extra>" + dong_nm + "</extra>")
        if city is not None:
            fig.add_bar(x=labels, y=city, name=f"{city_nm} 평균", marker_color=colors["city"],
                        hovertemplate="%{x} %{y:.1f}%<extra>" + city_nm + "</extra>")
        fig.update_layout(barmode="group", height=height, margin=dict(l=10, r=10, t=30, b=10),
                          title=dict(text=title, font=dict(size=14), x=0), yaxis=dict(ticksuffix="%"),
                          legend=dict(orientation="h", y=1.18, x=1, xanchor="right"), bargap=0.25)
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    if sd is not None:
        g1, g2 = st.columns(2)
        with g1:
            _pair_bar(sbiz.HOUR, sd["hour"], sc["hour"] if sc else None, "시간대별 유동인구 비중")
        with g2:
            _pair_bar(sbiz.AGE, sd["age"], sc["age"] if sc else None, "연령대별 유동인구 비중")
        g3, g4 = st.columns(2)
        with g3:
            _pair_bar(sbiz.WEEK, sd["weekday"], sc["weekday"] if sc else None, "요일별 유동인구 비중", 210)
        with g4:
            ui.stat_tiles([("일평균 유동인구", f"{sd['total']:,}명", ""),
                           ("남 : 여", f"{sd['sex'][0]:.0f} : {sd['sex'][1]:.0f}", ""),
                           ("10–20대", f"{sd['age'][0] + sd['age'][1]:.0f}%",
                            f"{city_nm} {sc['age'][0] + sc['age'][1]:.0f}%" if sc else ""),
                           ("14–18시 비중", f"{sd['hour'][3]:.0f}%", f"{city_nm} {sc['hour'][3]:.0f}%" if sc else "")])
    if len(cafe_sales):
        city_sales = sbiz.sales_series(region, city_nm, "카페")
        fig = go.Figure()
        fig.add_scatter(x=cafe_sales.index, y=cafe_sales.values, name=dong_nm, mode="lines+markers",
                        line=dict(color=colors["here"], width=2.5), connectgaps=True,
                        hovertemplate="%{x} %{y:,.0f}만원<extra>" + dong_nm + "</extra>")
        if len(city_sales):
            fig.add_scatter(x=city_sales.index, y=city_sales.values, name=f"{city_nm} 평균", mode="lines",
                            line=dict(color="#888", width=2, dash="dot"),
                            hovertemplate="%{x} %{y:,.0f}만원<extra>" + city_nm + "</extra>")
        fig.update_layout(height=260, margin=dict(l=10, r=10, t=30, b=10), hovermode="x unified",
                          xaxis=dict(type="category", tickangle=0, nticks=7), yaxis=dict(tickformat=","),
                          title=dict(text="카페 점포당 월평균 매출 (만원)", font=dict(size=14), x=0),
                          legend=dict(orientation="h", y=1.18, x=1, xanchor="right"))
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.caption(f"출처: 소상공인365 빅데이터 상권분석 ({sbiz.period()} 기준, 통신·카드 자료 기반 추정) · "
               "값은 config/sbiz_floating.yaml에 옮겨 적은 것이라 분기마다 손으로 갱신합니다.")

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
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
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
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
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
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.caption("올해는 지금까지의 건수입니다. 인허가 자료는 2일 전 기준으로 갱신됩니다.")

# ---------------- 유동인구
st.subheader("유동인구 · 학교")
fc1, fc2 = st.columns(2)
with fc1:
    fl = data.get("floating")
    if fl is not None and len(fl):
        latest = fl["base_ym"].max()
        f_here = fl[(fl["base_ym"] == latest) & (scoring.attach_dong_key(fl, scoring.dong_master(data)) == dong_cd)]
        f_tz = f_here[f_here["tz"] != "TOT"].sort_values("tz")
        if len(f_tz):
            fig = go.Figure(go.Bar(x=f_tz["tz"], y=f_tz["share"], marker_color="#B279A2",
                                   hovertemplate="%{x} %{y:.1f}%<extra></extra>"))
            fig.update_layout(height=220, margin=dict(l=10, r=10, t=10, b=10), yaxis=dict(title="비율(%)"))
            st.markdown("**시간대별 유동인구 비율** (경기도 집계)")
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
            st.caption(f"기준 {latest[:4]}.{latest[4:]} · 시간대코드는 경기데이터드림 정의(TZ01부터 이른 시간대 순)")
    sb, sb_period = scoring.sbiz_floating(region)
    if dong_nm in sb and sd is None:
        ranked = sorted(sb.values(), reverse=True)
        st.metric("일평균 유동인구 (소상공인365)", f"{sb[dong_nm]:,}명")
        st.caption(f"조사한 동네 {len(sb)}곳 중 {ranked.index(sb[dong_nm]) + 1}위 · {sb_period} 기준")
    bus = data.get("busstops")
    if bus is not None and len(bus):
        n_bus = int((scoring.attach_dong_key(bus, scoring.dong_master(data)) == dong_cd).sum())
        st.metric("동네 안 버스정류장", f"{n_bus}곳", help="동네 단위 유동인구는 무료로 공개되지 않아, 지나다니는 사람의 대리 지표로 씁니다.")
    else:
        st.metric("버스정류장", scoring.NOT_PROVIDED)
    if sd is None:
        st.caption("시간대·연령별 유동인구는 [소상공인365](https://bigdata.sbiz.or.kr) › 빅데이터 상권분석에서 확인할 수 있습니다.")
with fc2:
    sch = data.get("schools")
    if sch is not None and len(sch):
        mine_sch = sch[scoring.attach_dong_key(sch, scoring.dong_master(data)) == dong_cd]
        st.metric("중·고등학교", f"{int(mine_sch['level'].str.contains('중학교|고등학교').sum())}곳")
        st.caption(f"초등학교 {int(mine_sch['level'].str.contains('초등').sum())}곳 · 학교 위치표준데이터 기준")
        if len(mine_sch):
            st.dataframe(mine_sch[["name", "level", "addr"]].rename(columns={"name": "학교", "level": "구분", "addr": "주소"}),
                         hide_index=True, use_container_width=True, height=180)
    else:
        st.metric("중·고등학교", scoring.NOT_PROVIDED)

# ---------------- 커피·간식 경쟁점
st.subheader("커피·간식 경쟁점")
bz_cafe = ["커피숍", "다방", "전통찻집", "떡카페"]
ages_here = sbiz.business_age(data, dong_cd, bz_cafe)
ages_city = sbiz.business_age(data, None, bz_cafe)
if ages_here.sum():
    share_here = ages_here / ages_here.sum() * 100
    share_city = ages_city / ages_city.sum() * 100 if ages_city.sum() else None
    fig = go.Figure()
    fig.add_bar(x=share_here.index, y=share_here.values, name=dong_nm, marker_color="#8c564b",
                customdata=ages_here.values, hovertemplate="%{x} %{customdata}곳 (%{y:.0f}%)<extra></extra>")
    if share_city is not None:
        fig.add_bar(x=share_city.index, y=share_city.values, name="시 전체", marker_color="#BAB0AC",
                    hovertemplate="%{x} %{y:.0f}%<extra>시 전체</extra>")
    fig.update_layout(barmode="group", height=230, margin=dict(l=10, r=10, t=30, b=10), yaxis=dict(ticksuffix="%"),
                      title=dict(text=f"영업 중인 카페 {int(ages_here.sum())}곳의 업력 (문 연 지 몇 년?)", font=dict(size=14), x=0),
                      legend=dict(orientation="h", y=1.18, x=1, xanchor="right"))
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    old = share_here.get("5년 이상", 0)
    st.caption(f"5년 넘게 버틴 카페가 {old:.0f}%" + (f" (시 전체 {share_city.get('5년 이상', 0):.0f}%)" if share_city is not None else "")
               + " — 높을수록 오래 살아남는 동네, '1년 미만'이 높으면 새로 많이 들어오는 동네입니다. (휴게음식점 인허가 기준)")
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

    from src import navermap

    pts = comp.dropna(subset=["lat", "lon"])
    naver_pts = [{"lat": r["lat"], "lon": r["lon"], "size": 12,
                  "color": "#8c564b" if r["구분"] == "카페" else "#ff7f0e",
                  "tooltip": f"{r['store_name']} · {r['inds_scls_nm']}" + (" · 저가 커피" if r["저가 커피"] else "")}
                 for _, r in pts.iterrows()]
    if len(pts) and navermap.render(naver_pts, height=400):
        st.caption("갈색 = 카페, 주황 = 간식점 · 점에 마우스를 올리면 상호가 보입니다")
    elif len(pts):
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

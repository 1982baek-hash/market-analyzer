"""내 매장 — 우리 가게(또는 후보지) 주변 경쟁점이 어떻게 변했나?"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src import mystore, navermap, sbiz, scoring, ui

st.set_page_config(page_title="내 매장", page_icon="📍", layout="wide")
ui.require_login()

region = ui.pick_region()
regions = scoring.load_regions()
data = ui.region_data(region)
cfg = scoring.load_config()

st.title("📍 내 매장 주변")
st.caption("우리 가게(또는 후보지) 반경 안의 카페·간식점이 얼마나 있고, 최근에 어떻게 변했는지 봅니다.")
ui.period_badges(data["periods"])

# ---------------------------------------------------------------- 1. 위치 정하기
saved = mystore.load_my_stores(region)
if "my_session_places" not in st.session_state:
    st.session_state["my_session_places"] = []
session_places = [p for p in st.session_state["my_session_places"] if p["region"] == region]

modes = ["주소·가게 이름으로 찾기", "저장된 위치"]
mode = st.radio("위치 정하기", modes, index=1 if (saved or session_places) else 0, horizontal=True)

place = None
if mode == "저장된 위치":
    options = [{**p, "src": "목록"} for p in saved] + [{**p, "src": "이번 접속"} for p in session_places]
    if not options:
        st.info("저장된 위치가 없습니다. **주소·가게 이름으로 찾기**로 위치를 정해 주세요. "
                "(자주 볼 곳은 `config/my_stores.yaml`에 넣어 두면 여기에 나옵니다)")
    else:
        if st.session_state.pop("my_place_pick_last", False):
            st.session_state["my_place_i"] = len(options) - 1
        if st.session_state.get("my_place_i", 0) >= len(options):
            st.session_state["my_place_i"] = 0
        i = st.selectbox("위치", range(len(options)), key="my_place_i",
                         format_func=lambda k: f"{options[k]['name']}" + (" (이번 접속에서만)" if options[k]["src"] == "이번 접속" else ""))
        place = options[i]
else:
    q = st.text_input("주소나 가게 이름", placeholder="예: 경동로 123 · 옥야동 · 안동역 · 메가커피 안동",
                      help="수집된 점포·인허가 자료의 주소와 상호에서 찾습니다. 도로명이나 가게 이름 일부만 써도 됩니다.")
    if q:
        found = mystore.search_places(data, q)
        if found.empty:
            st.warning("찾은 곳이 없습니다. 도로명(예: '경동로')이나 근처 가게 이름으로 다시 찾아 보세요.")
        else:
            j = st.selectbox(f"찾은 곳 {len(found)}개 — 가게 자리와 가장 가까운 곳을 고르세요", range(len(found)),
                             format_func=lambda k: found.loc[k, "label"])
            r = found.loc[j]
            place = {"name": str(r["label"]).split(" · ")[0], "lat": float(r["lat"]), "lon": float(r["lon"])}

if place is None:
    st.stop()

c1, c2 = st.columns(2)
with c1:
    group_opts = {"both": "카페 + 간식점", "cafe": "카페만", "snack": "간식점만"}
    default_g = place.get("group", "both") if place.get("group") in group_opts else "both"
    gsel = st.radio("볼 업종", list(group_opts), index=list(group_opts).index(default_g),
                    format_func=group_opts.get, horizontal=True)
with c2:
    default_r = place.get("radius", 500) if place.get("radius") in mystore.RADII else 500
    radius = st.radio("반경", mystore.RADII, index=mystore.RADII.index(default_r),
                      format_func=lambda v: f"{v:,}m", horizontal=True)
groups = ("cafe", "snack") if gsel == "both" else (gsel,)
lat, lon = place["lat"], place["lon"]

# ---------------------------------------------------------------- 2. 계산
now = mystore.stores_near(data, cfg, lat, lon, radius, groups)
ctx = mystore.context_near(data, lat, lon, radius, region)
lic = mystore.licenses_near(data, lat, lon, radius, groups)
opened, closed = mystore.recent_changes(lic)
trend = mystore.monthly_counts(lic, months=60) if not lic.empty else pd.DataFrame()

def cnt(g):
    return int((now["group"] == g).sum()) if not now.empty else 0

st.subheader(f"{place['name']} · 반경 {radius:,}m")
tiles = []
if "cafe" in groups:
    tiles.append(("카페", f"{cnt('cafe')}곳", ""))
    tiles.append(("저가 커피 브랜드", f"{int(now['lowcost'].sum()) if not now.empty else 0}곳", ""))
if "snack" in groups:
    tiles.append(("간식점", f"{cnt('snack')}곳", "빵·떡·토스트·분식·아이스크림 등"))
tiles += [
    ("최근 1년 새로 연 곳", f"{len(opened)}곳", "인허가 기준"),
    ("최근 1년 문 닫은 곳", f"{len(closed)}곳", "인허가 기준"),
    ("버스정류장 · 학교", f"{ctx['bus']} · {ctx['schools']}곳", ""),
]
ui.stat_tiles(tiles)

# ---------------------------------------------------------------- 2-1. 우리 가게 진단
st.divider()
st.subheader("🩺 지금 자리, 유지할까 옮길까?")
st.caption("운영 중인 가게 숫자를 넣으면 이 자리(동네)가 지금 업종에 맞는지 판단합니다. "
           "넣은 숫자는 어디에도 저장되지 않습니다 (계속 쓰려면 맨 아래 '비공개 저장' 참고).")
dong = mystore.dong_of_point(data, lat, lon)
stores_df = data["stores"]
scls_names = stores_df.groupby("inds_scls_cd")["inds_scls_nm"].first()
codes = [c for c in stores_df["inds_scls_cd"].value_counts().index if c in scls_names.index]
default_scls = str(place.get("scls", "I21201"))
avg_default = mystore.sbiz_sales(region, dong[1], scls_names.get(default_scls, "")) if dong else None

with st.form("diag"):
    f1, f2 = st.columns(2)
    scls = f1.selectbox("지금 업종", codes, index=codes.index(default_scls) if default_scls in codes else 0,
                        format_func=lambda c: scls_names[c])
    sales = f2.number_input("월 매출 (만원)", min_value=0, step=50, value=int(place.get("monthly_sales", 0)))
    f3, f4, f5 = st.columns(3)
    rent = f3.number_input("월세+관리비 (만원)", min_value=0, step=10, value=int(place.get("monthly_rent", 0)))
    deposit = f4.number_input("보증금 (만원)", min_value=0, step=500, value=int(place.get("deposit", 0)))
    area = f5.number_input("면적 (평)", min_value=0.0, step=1.0, value=float(place.get("area_pyeong", 0)))
    avg_sales = st.number_input("동네 같은 업종 평균 월매출 (만원 · 모르면 0)", min_value=0, step=50,
                                value=int(place.get("area_avg_sales") or avg_default or 0),
                                help="소상공인365(bigdata.sbiz.or.kr) › 빅데이터 상권분석에서 이 동네·업종을 고르면 나옵니다.")
    if st.form_submit_button("진단하기", type="primary", use_container_width=True):
        st.session_state["diag_on"] = True

if (st.session_state.get("diag_on") or place.get("monthly_sales")) and dong:
    if not sales:
        st.warning("월 매출을 넣어 주세요.")
    else:
        dong_cd, dong_nm = dong
        groups_cfg = mystore.group_scls(cfg)
        grp = next((g for g, lst in groups_cfg.items() if scls in lst), None)
        if grp:  # 카페·간식은 형님 매장용 점수(커피+간식)로
            table, _ = ui.preset_scores(region)
        else:
            table, _ = ui.category_scores(region, scls, "생활밀착형")
        comp = None
        if grp:
            lg = mystore.licenses_near(data, lat, lon, radius, (grp,))
            tg = mystore.monthly_counts(lg, months=60) if not lg.empty else pd.DataFrame()
            if not tg.empty:
                comp = (int(tg["count"].iloc[0]), int(tg["count"].iloc[-1]))
        res = mystore.diagnose(table, dong_cd, sales, rent, deposit, area, avg_sales or None, comp,
                              sbiz.yoy(region, dong_nm, "카페") if grp == "cafe" else None, dong_nm)
        box = {"good": st.success, "mid": st.info, "bad": st.warning}[res["tone"]]
        box(f"### {res['verdict']}\n{dong_nm} · {scls_names[scls]} 기준 — {res['why']}")
        st.markdown("\n".join(f"- {icon} {text}" for icon, text in res["factors"]))
        alts = res["alternatives"]
        if not len(alts):
            st.caption("시 안에서 이 동네보다 같은 업종 점수가 높은 동네는 없습니다.")
        else:
            cur = table.loc[table["adong_cd"] == dong_cd, "score"]
            cur = cur.iloc[0] if len(cur) else float("nan")
            st.markdown("**옮긴다면 — 지금 동네보다 같은 업종 점수가 높은 동네**")
            ui.stat_tiles([
                (f"{int(r['rank'])}위 {r['adong_nm']}", f"{r['score']:.0f}점",
                 (f"지금보다 {r['score'] - cur:+.0f}점" if pd.notna(cur) else "") +
                 (" · " + ui.rent_line(r).split("→")[0].replace("💰 ", "") if ui.rent_line(r) else ""))
                for _, r in alts.iterrows()
            ])
        st.caption(f"보증금 {deposit:,}만원은 옮길 때 돌려받는 돈입니다. 권리금·인테리어·이사 비용, 단골이 다시 모이는 기간, "
                   "남은 계약 기간은 반영하지 않았으니 참고용으로만 봐 주세요.")
elif not dong:
    st.info("이 위치의 동네를 찾지 못해 진단할 수 없습니다.")

# ---------------------------------------------------------------- 3. 지도
colors = {"cafe": "#8B5A2B", "snack": "#E4572E"}
pts = [{"lat": lat, "lon": lon, "color": "#1f6feb", "size": 20, "tooltip": f"📍 {place['name']}", "label": "여기"}]
for _, r in now.iterrows():
    tag = "저가 커피 · " if r["lowcost"] else ""
    pts.append({"lat": r["lat"], "lon": r["lon"], "color": colors[r["group"]], "size": 11,
                "tooltip": f"{r['store_name']} · {tag}{r['inds_scls_nm']} · {r['dist']:.0f}m"})

if not navermap.render(pts, height=440, circle={"lat": lat, "lon": lon, "radius": radius}):
    import folium
    from streamlit_folium import st_folium
    fmap = folium.Map(location=[lat, lon], zoom_start={300: 16, 500: 15, 1000: 14}[radius], tiles="CartoDB positron")
    folium.Circle([lat, lon], radius=radius, color="#1f6feb", weight=2, fill=True, fill_opacity=0.06).add_to(fmap)
    for p in pts:
        folium.CircleMarker([p["lat"], p["lon"]], radius=p["size"] / 2, color=p["color"], fill=True,
                            fill_color=p["color"], fill_opacity=0.9, weight=1, tooltip=p["tooltip"]).add_to(fmap)
    st_folium(fmap, height=440, use_container_width=True, returned_objects=[])
legend = " · ".join(f"<span style='color:{colors[g]}'>●</span> {mystore.GROUP_LABEL[g]}" for g in groups)
st.markdown(f"<span style='color:#1f6feb'>●</span> 우리 위치 · {legend}", unsafe_allow_html=True)

# ---------------------------------------------------------------- 3-1. 주변 뉴스
if dong:
    _s = data["stores"].dropna(subset=["lat", "lon"])
    _near_ld = _s.iloc[int(np.argmin(mystore.distance_m(lat, lon, _s["lat"], _s["lon"])))].get("ldong_nm")
    extra = [_near_ld] if isinstance(_near_ld, str) and _near_ld and _near_ld != dong[1] else []
    sl = ctx.get("school_list")
    if sl is not None and len(sl) and "campus" in sl:
        for nm in sl.loc[sl["campus"].notna(), "name"]:
            extra.append(nm.split()[0].replace("국립", "").replace("대학교", "대"))
    st.divider()
    ui.news_section(region, dong[1], extra[:2], title=f"📰 {dong[1]} 주변 주요 뉴스")

# ---------------------------------------------------------------- 4. 경쟁점 수 추이
st.divider()
st.subheader("경쟁점 수 추이 (최근 5년)")
if trend.empty:
    st.info("반경 안에 인허가 이력이 있는 카페·간식점이 없습니다.")
else:
    fig = go.Figure()
    for g in groups:
        t = trend[trend["group"] == g]
        if t.empty:
            continue
        fig.add_scatter(x=t["month"], y=t["count"], mode="lines", name=mystore.GROUP_LABEL[g],
                        line=dict(color=colors[g], width=2.5),
                        hovertemplate="%{x|%Y.%m} " + mystore.GROUP_LABEL[g] + " %{y}곳<extra></extra>")
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10), hovermode="x unified",
                      legend=dict(orientation="h", y=1.1), yaxis=dict(title="영업 중(곳)", rangemode="tozero",
                                 dtick=1 if trend["count"].max() <= 8 else None, tickformat="d"),
                      xaxis=dict(title=None, tickformat="%Y.%m"))
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    first = trend.groupby("group")["count"].first()
    last = trend.groupby("group")["count"].last()
    parts = [f"{mystore.GROUP_LABEL[g]} {first[g]}곳 → {last[g]}곳 ({last[g] - first[g]:+d})" for g in groups if g in last]
    st.caption("5년 전 대비 · " + " · ".join(parts) +
               " — 휴게음식점 인허가의 개업일·폐업일로 셈 (상가정보 업종 구분과 조금 다를 수 있음)")

# ---------------------------------------------------------------- 5. 새로 생긴·없어진 가게
st.subheader("최근 1년 새로 생긴 · 문 닫은 가게")


def _show(df: pd.DataFrame, date_col: str, date_label: str):
    if df.empty:
        st.caption("없음")
        return
    out = pd.DataFrame({
        date_label: df[date_col].dt.strftime("%Y.%m.%d"),
        "가게": df["biz_name"],
        "업태": df["bzstat_nm"],
        "거리": df["dist"].round(-1).astype(int).astype(str) + "m",
        "주소": df["addr"],
    })
    st.dataframe(out, hide_index=True, use_container_width=True)


t1, t2 = st.tabs([f"🟢 새로 연 곳 {len(opened)}", f"🔴 문 닫은 곳 {len(closed)}"])
with t1:
    _show(opened, "open_dt", "개업일")
with t2:
    _show(closed, "close_dt", "폐업일")

diff = mystore.snapshot_diff(region, cfg, lat, lon, radius, groups)
if diff is None:
    st.caption("상가정보는 분기마다 새로 받습니다. 다음 스냅샷이 쌓이면 두 시점의 점포 목록 비교도 여기에 나옵니다.")
else:
    with st.expander(f"상가정보 비교 {diff['prev']} → {diff['cur']} : {diff['prev_cnt']}곳 → {diff['cur_cnt']}곳"):
        a, b = st.columns(2)
        for col, df, title in ((a, diff["added"], "새로 보이는 점포"), (b, diff["removed"], "사라진 점포")):
            with col:
                st.markdown(f"**{title} {len(df)}곳**")
                if not df.empty:
                    st.dataframe(pd.DataFrame({"가게": df["store_name"], "업종": df["inds_scls_nm"],
                                               "거리": df["dist"].round(-1).astype(int).astype(str) + "m"}),
                                 hide_index=True, use_container_width=True)

# ---------------------------------------------------------------- 6. 지금 있는 경쟁점 목록
with st.expander(f"지금 있는 경쟁점 {len(now)}곳 (가까운 순)"):
    if not now.empty:
        st.dataframe(pd.DataFrame({
            "가게": now["store_name"] + now["branch_name"].fillna("").map(lambda b: f" {b}" if b else ""),
            "업종": now["inds_scls_nm"] + now["lowcost"].map({True: " · 저가", False: ""}),
            "거리": now["dist"].round(-1).astype(int).astype(str) + "m",
            "주소": now["rdnm_adr"],
        }), hide_index=True, use_container_width=True)

if ctx["schools"]:
    with st.expander(f"반경 안 학교 {ctx['schools']}곳"):
        s = ctx["school_list"]
        st.dataframe(pd.DataFrame({"학교": s["name"], "구분": s["level"],
                                   "거리": [("캠퍼스 안" if cp is True and d < 1 else f"{int(round(d, -1))}m")
                                          for d, cp in zip(s["dist"], s.get("campus", pd.Series([None] * len(s))))]}),
                     hide_index=True, use_container_width=True)
        st.caption("초·중·고는 학교 위치표준데이터, 대학·대학원·방통대 학습관은 캠퍼스 경계(OpenStreetMap)까지 거리 기준")

# ---------------------------------------------------------------- 7. 위치 저장
st.divider()
with st.expander("📌 이 위치·가게 숫자를 계속 쓰는 법 (비공개 저장)"):
    if st.button("이번 접속 동안 '저장된 위치'에 추가"):
        st.session_state["my_session_places"].append(
            {"name": place["name"], "region": region, "lat": lat, "lon": lon, "radius": radius, "group": gsel})
        st.success("추가했습니다. 앱을 다시 열면 사라지니, 계속 쓸 곳은 아래 방법으로 저장해 주세요.")
    st.markdown(
        "저장소는 공개 상태라 실제 가게 정보는 **Streamlit Secrets**(나만 보는 설정)에 넣습니다.\n\n"
        "1. 앱 오른쪽 아래 **Manage app** → 오른쪽 위 **⋮** → **Settings** → **Secrets**\n"
        "2. 이미 있는 줄은 그대로 두고, 맨 아래에 아래 내용을 붙여 넣은 뒤 **Save changes**\n"
        "3. 1분쯤 뒤 이 화면의 **저장된 위치**에 나오고, 가게 숫자가 미리 채워져 진단이 바로 보입니다.")
    ss = st.session_state
    toml = (f'\n[[my_stores]]\nname = "{place["name"]}"\nregion = "{region}"\nlat = {lat:.6f}\nlon = {lon:.6f}\n'
            f'radius = {radius}\ngroup = "{gsel}"\nscls = "{scls}"            # 업종 ({scls_names[scls]})\n'
            f"monthly_sales = {int(sales)}      # 월 매출(만원)\nmonthly_rent = {int(rent)}       # 월세+관리비(만원)\n"
            f"deposit = {int(deposit)}           # 보증금(만원)\narea_pyeong = {area:g}      # 면적(평)\n"
            f"area_avg_sales = {int(avg_sales)}   # 동네 같은 업종 평균 월매출(만원)\n")
    st.code(toml, language="toml")
    st.caption("가게가 여러 곳이면 [[my_stores]] 묶음을 여러 번 붙이면 됩니다. Secrets는 GitHub에 올라가지 않습니다.")

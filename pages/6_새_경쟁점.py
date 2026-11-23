"""새 경쟁점 — 내 매장 주변에 최근 새로 영업 신고한, 점심을 대신할 수 있는 가게 (앱을 열 때 뜨는 알림 팝업의 전체 목록)."""

import pandas as pd
import streamlit as st

from src import alerts, mystore, navermap, scoring, ui

st.set_page_config(page_title="새 경쟁점", page_icon="🆕", layout="wide")
ui.require_login()
ui.competitor_alert()

region = ui.pick_region()
regions = scoring.load_regions()
data = ui.region_data(region)
cfg = scoring.load_config()
conf = alerts.settings(cfg)
city = regions[region]["name"].split()[-1]

st.title("🆕 새 경쟁점")

# ---------------------------------------------------------------- 1. 기준 매장
stores = mystore.load_my_stores(region)
base = None
if len(stores) == 1:
    base = stores[0]
elif len(stores) > 1:
    i = st.selectbox("기준 매장", range(len(stores)), format_func=lambda k: str(stores[k].get("name") or "내 매장"))
    base = stores[i]
base_name = str(base.get("name") or "내 매장") if base else ""

if base:
    st.caption(f"**{base_name}** 주변에 최근 새로 영업 신고(인허가)한, 점심을 대신할 수 있는 가게(식당·카페·간식점·편의점)입니다. 술집은 뺐어요. "
               "신고한 날 기준이라 실제로 문을 여는 날과 며칠 차이 날 수 있어요.")
else:
    others = [regions[s["region"]]["name"] for s in mystore.load_my_stores() if s.get("region") in regions]
    if others:
        st.info(f"저장된 내 매장은 **{' · '.join(dict.fromkeys(others))}**에 있습니다. "
                "왼쪽 **지역**을 그곳으로 바꾸면 매장 주변만 볼 수 있어요. 지금은 이 지역 전체를 보여 줍니다.")
    else:
        st.warning("저장된 내 매장이 없어 매장 주변만 골라 보여 줄 수 없습니다. 지금은 이 지역 전체를 보여 주고, 알림 팝업은 뜨지 않습니다.\n\n"
                   "**내 매장** 화면에서 가게를 찾은 뒤, 맨 아래 **'이 위치·가게 숫자를 계속 쓰는 법 (비공개 저장)'** 을 따라 저장하면 "
                   "그 가게 주변에 생긴 곳만 보여 주고 알려 드립니다.")
        st.page_link("pages/4_내_매장.py", label="내 매장 화면으로 가기", icon="📍")

# ---------------------------------------------------------------- 2. 조건
ALL = 0  # '지역 전체'를 뜻하는 값
cols = st.columns(3 if base else 2)
with cols[0]:
    day_opts = sorted({30, 60, 90, 365, int(conf["days"])})
    days = st.radio("기간", day_opts, index=day_opts.index(int(conf["days"])),
                    format_func=lambda d: "최근 1년" if d == 365 else f"최근 {d}일", horizontal=True)
with cols[1]:
    group_opts = {"both": "전체", **alerts.GROUP_LABEL}
    gsel = st.radio("볼 업종", list(group_opts), format_func=group_opts.get, horizontal=True)
radius = ALL
if base:
    with cols[2]:
        r_opts = sorted({500, 1000, 2000, int(conf["radius_m"])}) + [ALL]
        radius = st.radio("범위 (매장에서)", r_opts, index=r_opts.index(int(conf["radius_m"])),
                          format_func=lambda v: f"{city} 전체" if v == ALL else f"{v:,}m", horizontal=True)

# ---------------------------------------------------------------- 3. 계산
items = alerts.region_items(region, days, data, cfg, [base] if base else [])
if gsel != "both":
    items = items[items["group"] == gsel]
if base and radius != ALL:
    # 좌표가 없어 거리를 모르는 가게는 주소가 매장과 같은 동네일 때만 남겨 목록 맨 뒤에 둔다
    items = alerts.in_range(items, radius)
if base:
    items = items.sort_values(["near_dist", "open_dt"], ascending=[True, False], na_position="last")
real = items[~items["temporary"]]
period = "최근 1년" if days == 365 else f"최근 {days}일"
where = f"{city} 전체" if (not base or radius == ALL) else f"{base_name} 반경 {radius:,}m"

tiles = [
    ("새로 연 곳", f"{len(real)}곳", f"{where} · {period}"),
    ("식당", f"{int((real['group'] == 'meal').sum())}곳", "한식·분식·중식·일식·양식 등"),
    ("카페", f"{int((real['group'] == 'cafe').sum())}곳", ""),
    ("간식점", f"{int((real['group'] == 'snack').sum())}곳", "빵·떡·토스트·아이스크림 등"),
    ("편의점", f"{int((real['group'] == 'conv').sum())}곳", ""),
    ("저가 커피 브랜드", f"{int(real['lowcost'].sum())}곳", ""),
]
if base and radius != ALL:
    ex = alerts.existing_near(data, base["lat"], base["lon"], radius)
    tiles.append(("지금 있는 경쟁점", f"{sum(ex.values())}곳",
                  " · ".join(f"{alerts.GROUP_LABEL[g]} {ex[g]}" for g in alerts.GROUP_ORDER) + " (같은 범위, 상가정보)"))
ui.stat_tiles(tiles)

if items.empty:
    st.success(f"**{where}** 안에는 {period} 동안 새로 영업 신고한 점심 경쟁점(식당·카페·간식점·편의점)이 없습니다.")
else:
    # ------------------------------------------------------------ 지도
    colors = {"meal": "#2E7D32", "cafe": "#8B5A2B", "snack": "#E4572E", "conv": "#7B3FA0"}
    pts = []
    if base:
        pts.append({"lat": base["lat"], "lon": base["lon"], "color": "#1f6feb", "size": 20,
                    "tooltip": f"📍 {base_name}", "label": base_name})
    for _, r in items.dropna(subset=["lat", "lon"]).iterrows():
        tip = f"{r['biz_name']} · {alerts.group_label(r)} · {r['open_dt']:%m.%d} 신고"
        pts.append({"lat": r["lat"], "lon": r["lon"], "color": colors[r["group"]], "size": 13, "tooltip": tip})
    circle = {"lat": base["lat"], "lon": base["lon"], "radius": radius} if (base and radius != ALL) else None
    if pts and not navermap.render(pts, height=420, circle=circle):
        import folium
        from streamlit_folium import st_folium
        fmap = folium.Map(location=[sum(p["lat"] for p in pts) / len(pts), sum(p["lon"] for p in pts) / len(pts)],
                          zoom_start=14 if circle else 12, tiles="CartoDB positron")
        if circle:
            folium.Circle([circle["lat"], circle["lon"]], radius=circle["radius"], color="#1f6feb", weight=2,
                          fill=True, fill_opacity=0.06).add_to(fmap)
        for p in pts:
            folium.CircleMarker([p["lat"], p["lon"]], radius=p["size"] / 2, color=p["color"], fill=True,
                                fill_color=p["color"], fill_opacity=0.9, weight=1, tooltip=p["tooltip"]).add_to(fmap)
        if len(pts) > 1 and not circle:
            fmap.fit_bounds([[min(p["lat"] for p in pts), min(p["lon"] for p in pts)],
                             [max(p["lat"] for p in pts), max(p["lon"] for p in pts)]], padding=(30, 30))
        st_folium(fmap, height=420, use_container_width=True, returned_objects=[])
    legend = " · ".join(f"<span style='color:{colors[g]}'>●</span> {alerts.GROUP_LABEL[g]}" for g in colors)
    st.markdown((f"<span style='color:#1f6feb'>●</span> {base_name} · " if base else "") + legend, unsafe_allow_html=True)

    # ------------------------------------------------------------ 목록
    table = pd.DataFrame({
        "영업 신고일": items["open_dt"].dt.strftime("%Y.%m.%d"),
        "가게": items["biz_name"],
        "구분": [alerts.group_label(r) for _, r in items.iterrows()],
        "업태": items["bzstat_nm"],
        "동네": items["adong_nm"],
        "주소": items["addr"],
    })
    if base:
        table.insert(0, "매장에서", [f"{int(round(d, -1)):,}m" if pd.notna(d) else "거리 모름" for d in items["near_dist"]])
    st.dataframe(table, hide_index=True, use_container_width=True)
    no_xy = int(items["lat"].isna().sum())
    if no_xy:
        st.caption(f"위치 좌표가 없는 {no_xy}곳은 거리를 알 수 없어 목록 맨 뒤에 두었고, 지도에는 표시되지 않습니다. "
                   "(매장 범위를 고른 경우, 주소가 매장과 같은 동네인 곳만 남겼어요)")

if base:
    st.page_link("pages/4_내_매장.py", icon="📍",
                 label=f"**내 매장** 화면에서 {base_name} 주변 경쟁점 전체 지도·5년 추이·문 닫은 가게 보기")

# ---------------------------------------------------------------- 4. 알림 설명·다시 보기
st.divider()
with st.expander("🔔 알림 팝업은 이렇게 뜹니다"):
    all_stores = mystore.load_my_stores()
    if all_stores:
        target = (f"**{alerts.store_names(all_stores)}**에서 **{int(conf['radius_m']):,}m 안**에 생긴 곳만 알립니다. "
                  "멀리 떨어진 동네의 가게는 알리지 않습니다.")
    else:
        target = "저장된 내 매장이 없어 **지금은 알림이 뜨지 않습니다.** 내 매장 위치를 저장하면 그 주변에 생긴 곳만 알립니다."
    st.markdown(
        f"- 앱을 열었을 때, 최근 **{int(conf['days'])}일** 안에 새로 영업 신고한 점심 경쟁점(식당·카페·간식점·편의점)이 매장 가까이에 있으면 화면 가운데에 팝업이 뜹니다.\n"
        f"- 알리는 범위: {target}\n"
        "- 팝업에서 **확인했어요**를 누른 가게는 그 휴대폰·PC에서 다시 알리지 않습니다. 기기마다 따로 기억합니다.\n"
        "- 인허가 자료는 **매주 월요일 새벽**에 새로 받으므로, 새 가게는 보통 그 주 월요일 이후에 알림으로 나옵니다.\n"
        "- 경쟁점은 점심 한 끼를 대신할 수 있는 곳 모두입니다: 식당(일반음식점), 카페, 간식점, 편의점.\n"
        f"- 술집 업태({', '.join(conf['lunch_exclude_bzstat'])}), 백화점 입점 매장, 축제·야시장 임시 매장은 알림에서 뺍니다.\n"
        "- 기간·거리는 `config/business_types.yaml`의 `alerts` 항목에서 바꿀 수 있습니다.")
    if st.button("이 기기에서 알림 팝업 다시 보기"):
        ui.alert_reset()
        st.rerun()
    st.caption("누르면 이 기기에서 확인했던 기록을 지우고, 지금 알릴 가게가 있으면 팝업을 다시 띄웁니다.")

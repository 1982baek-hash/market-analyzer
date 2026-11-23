"""새 경쟁점 — 최근에 새로 영업 신고한 카페·간식점 (앱을 열 때 뜨는 알림 팝업의 전체 목록)."""

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
st.caption("최근에 시청·군청에 새로 영업 신고(인허가)한 카페·간식점입니다. "
           "신고한 날 기준이라 실제로 문을 여는 날과 며칠 차이 날 수 있어요.")

c1, c2 = st.columns(2)
with c1:
    day_opts = sorted({30, 60, 90, int(conf["days"])})
    days = st.radio("기간", day_opts, index=day_opts.index(int(conf["days"])),
                    format_func=lambda d: f"최근 {d}일", horizontal=True)
with c2:
    group_opts = {"both": "카페 + 간식점", "cafe": "카페만", "snack": "간식점만"}
    gsel = st.radio("볼 업종", list(group_opts), format_func=group_opts.get, horizontal=True)

stores = mystore.load_my_stores(region)
items = alerts.region_items(region, days, data, cfg, stores)
if gsel != "both":
    items = items[items["group"] == gsel]
real = items[~items["temporary"]]

tiles = [
    ("새로 연 곳", f"{len(real)}곳", f"{city} · 최근 {days}일"),
    ("카페", f"{int((real['group'] == 'cafe').sum())}곳", ""),
    ("간식점", f"{int((real['group'] == 'snack').sum())}곳", "빵·떡·토스트·분식·아이스크림 등"),
    ("저가 커피 브랜드", f"{int(real['lowcost'].sum())}곳", ""),
]
if stores:
    near = real[real["near_dist"] <= float(conf["radius_m"])]
    tiles.insert(1, (f"내 매장 {int(conf['radius_m']):,}m 안", f"{len(near)}곳", "앱을 열 때 알림으로 뜨는 곳"))
ui.stat_tiles(tiles)

if items.empty:
    st.info(f"{city}에는 최근 {days}일 동안 새로 영업 신고한 카페·간식점이 없습니다.")
else:
    # ------------------------------------------------------------ 지도
    colors = {"cafe": "#8B5A2B", "snack": "#E4572E"}
    pts = [{"lat": s["lat"], "lon": s["lon"], "color": "#1f6feb", "size": 20,
            "tooltip": f"📍 {s.get('name', '내 매장')}", "label": str(s.get("name", "내 매장"))} for s in stores]
    for _, r in items.dropna(subset=["lat", "lon"]).iterrows():
        pts.append({"lat": r["lat"], "lon": r["lon"], "color": colors[r["group"]], "size": 13,
                    "tooltip": f"{r['biz_name']} · {alerts.group_label(r)} · {r['open_dt']:%m.%d} 신고"})
    if pts and not navermap.render(pts, height=420):
        import folium
        from streamlit_folium import st_folium
        fmap = folium.Map(location=[sum(p["lat"] for p in pts) / len(pts), sum(p["lon"] for p in pts) / len(pts)],
                          zoom_start=12, tiles="CartoDB positron")
        for p in pts:
            folium.CircleMarker([p["lat"], p["lon"]], radius=p["size"] / 2, color=p["color"], fill=True,
                                fill_color=p["color"], fill_opacity=0.9, weight=1, tooltip=p["tooltip"]).add_to(fmap)
        if len(pts) > 1:
            fmap.fit_bounds([[min(p["lat"] for p in pts), min(p["lon"] for p in pts)],
                             [max(p["lat"] for p in pts), max(p["lon"] for p in pts)]], padding=(30, 30))
        st_folium(fmap, height=420, use_container_width=True, returned_objects=[])
    legend = " · ".join(f"<span style='color:{colors[g]}'>●</span> {mystore.GROUP_LABEL[g]}" for g in colors)
    st.markdown(("<span style='color:#1f6feb'>●</span> 내 매장 · " if stores else "") + legend, unsafe_allow_html=True)

    # ------------------------------------------------------------ 목록
    table = pd.DataFrame({
        "영업 신고일": items["open_dt"].dt.strftime("%Y.%m.%d"),
        "가게": items["biz_name"],
        "구분": [alerts.group_label(r) for _, r in items.iterrows()],
        "업태": items["bzstat_nm"],
        "동네": items["adong_nm"],
        "주소": items["addr"],
    })
    if stores:
        table.insert(3, "내 매장에서", [alerts.dist_text(r) or "거리 모름" for _, r in items.iterrows()])
    st.dataframe(table, hide_index=True, use_container_width=True)
    no_xy = int(items["lat"].isna().sum())
    if no_xy:
        st.caption(f"위치 좌표가 없는 {no_xy}곳은 지도에 표시되지 않습니다 (목록에는 있음).")

# ---------------------------------------------------------------- 알림 설명·다시 보기
st.divider()
with st.expander("🔔 알림 팝업은 이렇게 뜹니다"):
    target = (f"저장된 내 매장에서 **{int(conf['radius_m']):,}m 안**에 생긴 곳" if mystore.load_my_stores()
              else "**" + " · ".join(regions[r]["name"].split()[-1] for r in conf["regions"] if r in regions) + " 전체**"
                   "에 생긴 곳 (내 매장 위치를 저장하면 가게 주변만 알립니다)")
    st.markdown(
        f"- 앱을 열었을 때, 최근 **{int(conf['days'])}일** 안에 새로 영업 신고한 카페·간식점이 있으면 화면 가운데에 팝업이 뜹니다.\n"
        f"- 알리는 범위: {target}\n"
        "- 팝업에서 **확인했어요**를 누른 가게는 그 휴대폰·PC에서 다시 알리지 않습니다. 기기마다 따로 기억합니다.\n"
        "- 인허가 자료는 **매주 월요일 새벽**에 새로 받으므로, 새 가게는 보통 그 주 월요일 이후에 알림으로 나옵니다.\n"
        "- 편의점, 백화점 입점 매장, 축제·야시장 임시 매장은 알림에서 뺍니다.\n"
        "- 기간·거리·지역은 `config/business_types.yaml`의 `alerts` 항목에서 바꿀 수 있습니다.")
    if st.button("이 기기에서 알림 팝업 다시 보기"):
        ui.alert_reset()
        st.rerun()
    st.caption("누르면 이 기기에서 확인했던 기록을 지우고, 지금 알릴 가게가 있으면 팝업을 다시 띄웁니다.")

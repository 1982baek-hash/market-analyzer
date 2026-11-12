import json
import time
from pathlib import Path
import pandas as pd
import plotly.express as px
import streamlit as st
import streamlit.components.v1 as components

from src.config_loader import BASE_DIR, get_api_key
from src.analyzer import MarketAnalyzer
from src.collectors.store_collector import StoreCollector
from src.collectors.pop_collector import PopulationAndBoundaryCollector

st.set_page_config(page_title="상권 현황 및 지도", page_icon="🗺️", layout="wide")

analyzer = MarketAnalyzer()
regions = analyzer.get_available_regions()
naver_client_id = get_api_key("NAVER_MAP_CLIENT_ID")


def get_choropleth_color(val: float, max_val: float) -> str:
    """단계구분도 색상(노란색 -> 주황색 -> 진한 빨간색 5단계)을 반환합니다."""
    if max_val <= 0 or val <= 0:
        return "#FFFFCC"
    ratio = val / max_val
    if ratio >= 0.8:
        return "#BD0026"
    elif ratio >= 0.6:
        return "#F03B20"
    elif ratio >= 0.4:
        return "#FD8D3C"
    elif ratio >= 0.2:
        return "#FECC5C"
    return "#FFFFB2"


def build_map_payload(summary_df: pd.DataFrame, stores_df: pd.DataFrame, selected_metric: str, selected_adong_cd: str):
    """네이버 지도에 전달할 행정동 폴리곤·점포 마커 JSON 데이터를 생성합니다."""
    max_val = float(summary_df[selected_metric].max()) if not summary_df.empty and pd.notna(summary_df[selected_metric].max()) else 1.0

    polygons_data = []
    for _, row in summary_df.iterrows():
        if pd.notna(row["geojson_str"]) and row["geojson_str"]:
            geom = json.loads(row["geojson_str"])
            val = float(row[selected_metric]) if pd.notna(row[selected_metric]) else 0.0
            fill_color = get_choropleth_color(val, max_val)
            is_selected = str(row["adong_cd"]) == str(selected_adong_cd)

            rings = []
            if geom["type"] == "Polygon":
                rings.append([[pt[1], pt[0]] for pt in geom["coordinates"][0]])
            elif geom["type"] == "MultiPolygon":
                for poly in geom["coordinates"]:
                    rings.append([[pt[1], pt[0]] for pt in poly[0]])

            polygons_data.append({
                "adong_cd": str(row["adong_cd"]),
                "sigungu_nm": str(row["sigungu_nm"]),
                "adong_nm": str(row["adong_nm"]),
                "store_count": int(row["store_count"]),
                "franchise_ratio": float(row["franchise_ratio"]),
                "pop_txt": f"{int(row['population']):,}명" if pd.notna(row["population"]) else "미등록",
                "p1k_txt": f"{row['stores_per_1k_pop']}개" if pd.notna(row["stores_per_1k_pop"]) else "미제공",
                "km2_txt": f"{row['stores_per_km2']}개/km²" if pd.notna(row["stores_per_km2"]) else "미제공",
                "fill_color": fill_color,
                "stroke_color": "#0044FF" if is_selected else "#333333",
                "stroke_weight": 3.5 if is_selected else 1.5,
                "rings": rings,
            })

    markers_data = []
    for _, s_row in stores_df.iterrows():
        full_name = str(s_row["store_name"])
        if s_row["branch_name"]:
            full_name += f" ({s_row['branch_name']})"
        markers_data.append({
            "name": full_name,
            "cat": f"{s_row['inds_lcls_nm']} > {s_row['inds_mcls_nm']} > {s_row['inds_scls_nm']}",
            "dong": f"{s_row['sigungu_nm']} {s_row['adong_nm']}",
            "addr": str(s_row["rdnm_adr"] or s_row["lno_adr"]),
            "lat": float(s_row["lat"]),
            "lon": float(s_row["lon"]),
            "is_fr": bool(s_row["is_franchise"]),
        })

    return json.dumps(polygons_data, ensure_ascii=False), json.dumps(markers_data, ensure_ascii=False)


def render_naver_map(
    client_id: str,
    center_lat: float,
    center_lon: float,
    zoom_lv: int,
    summary_df: pd.DataFrame,
    stores_df: pd.DataFrame,
    selected_metric: str,
    selected_adong_cd: str,
):
    """네이버 지도 API v3를 static/naver_map.html로 저장한 뒤 iframe으로 렌더링합니다."""
    polygons_json, markers_json = build_map_payload(summary_df, stores_df, selected_metric, selected_adong_cd)

    html_code = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body, html {{ margin: 0; padding: 0; width: 100%; height: 100%; font-family: 'Malgun Gothic', sans-serif; }}
        #map {{ width: 100%; height: 590px; border-radius: 8px; border: 1px solid #ddd; }}
        .legend-box {{
            position: absolute; top: 12px; right: 12px; z-index: 100;
            background: rgba(255,255,255,0.94); padding: 8px 12px;
            border-radius: 6px; box-shadow: 0 2px 6px rgba(0,0,0,0.2);
            font-size: 12px; line-height: 1.5;
        }}
        .legend-color {{ display: inline-block; width: 12px; height: 12px; margin-right: 4px; vertical-align: middle; border: 1px solid #888; }}
        #hover-box {{
            position: absolute; bottom: 16px; left: 16px; z-index: 100;
            background: rgba(255,255,255,0.96); padding: 10px 14px;
            border-radius: 6px; border: 1px solid #2b2b2b; box-shadow: 0 2px 6px rgba(0,0,0,0.2);
            font-size: 12.5px; line-height: 1.55; display: none; min-width: 230px;
        }}
    </style>
    <script type="text/javascript" src="https://oapi.map.naver.com/openapi/v3/maps.js?ncpKeyId={client_id}&callback=initMap"></script>
</head>
<body>
    <div id="map"></div>
    <div id="hover-box"></div>
    <div class="legend-box">
        <b>단계구분도 범례</b><br>
        <span class="legend-color" style="background:#FFFFB2"></span>낮음 (0~20%)<br>
        <span class="legend-color" style="background:#FECC5C"></span>중하 (20~40%)<br>
        <span class="legend-color" style="background:#FD8D3C"></span>중간 (40~60%)<br>
        <span class="legend-color" style="background:#F03B20"></span>중상 (60~80%)<br>
        <span class="legend-color" style="background:#BD0026"></span>높음 (80~100%)<br>
        <hr style="margin:4px 0;">
        🔴 프랜차이즈/지점 &nbsp; 🔵 일반점포
    </div>

    <script>
        window.navermap_authFailure = function () {{
            document.getElementById('map').innerHTML =
                "<div style='padding:30px; color:#b00020; background:#fff3f3; height:100%;'>" +
                "<h3>⚠️ 네이버 지도 인증에 실패했습니다.</h3>" +
                "<p>현재 접속 주소: <code>" + window.location.origin + "</code><br>" +
                "1. 네이버 클라우드 콘솔 → [Maps] → [Application] → [Web 서비스 URL]에 위 접속 주소가 등록되어 있는지 확인해 주세요.<br>" +
                "2. [Dynamic Map] 체크 및 Secrets/env의 <code>NAVER_MAP_CLIENT_ID</code>를 확인해 주세요.</p></div>";
        }};

        function initMap() {{
            var map = new naver.maps.Map('map', {{
                center: new naver.maps.LatLng({center_lat}, {center_lon}),
                zoom: {zoom_lv},
                zoomControl: true,
                zoomControlOptions: {{ position: naver.maps.Position.TOP_LEFT }},
                mapTypeControl: true
            }});

            var polygonsData = {polygons_json};
            var markersData = {markers_json};
            var hoverBox = document.getElementById('hover-box');
            var infowindow = new naver.maps.InfoWindow({{ borderWidth: 1, backgroundColor: "#fff" }});

            polygonsData.forEach(function(item) {{
                item.rings.forEach(function(ring) {{
                    var path = ring.map(function(pt) {{ return new naver.maps.LatLng(pt[0], pt[1]); }});
                    var polygon = new naver.maps.Polygon({{
                        map: map,
                        paths: [path],
                        fillColor: item.fill_color,
                        fillOpacity: 0.45,
                        strokeColor: item.stroke_color,
                        strokeOpacity: 0.85,
                        strokeWeight: item.stroke_weight,
                        clickable: true
                    }});

                    naver.maps.Event.addListener(polygon, 'mouseover', function() {{
                        polygon.setOptions({{ fillOpacity: 0.72 }});
                        hoverBox.style.display = 'block';
                        hoverBox.innerHTML =
                            "<b>" + item.sigungu_nm + " " + item.adong_nm + "</b> (" + item.adong_cd + ")<br>" +
                            "• 점포수: <b>" + item.store_count + "개</b> (프랜차이즈 " + item.franchise_ratio + "%)<br>" +
                            "• 주민등록인구: " + item.pop_txt + "<br>" +
                            "• 인구1천명당: " + item.p1k_txt + " / 면적1km²당: " + item.km2_txt;
                    }});

                    naver.maps.Event.addListener(polygon, 'mouseout', function() {{
                        polygon.setOptions({{ fillOpacity: 0.45 }});
                        hoverBox.style.display = 'none';
                    }});
                }});
            }});

            var redSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14"><circle cx="7" cy="7" r="5.5" fill="#e60000" stroke="#ffffff" stroke-width="1.5"/></svg>';
            var blueSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14"><circle cx="7" cy="7" r="5.5" fill="#0055ff" stroke="#ffffff" stroke-width="1.5"/></svg>';

            markersData.forEach(function(st) {{
                var marker = new naver.maps.Marker({{
                    map: map,
                    position: new naver.maps.LatLng(st.lat, st.lon),
                    title: st.name,
                    icon: {{
                        content: st.is_fr ? redSvg : blueSvg,
                        size: new naver.maps.Size(14, 14),
                        anchor: new naver.maps.Point(7, 7)
                    }}
                }});

                naver.maps.Event.addListener(marker, 'click', function() {{
                    var iwContent =
                        "<div style='padding:10px 14px; font-size:12px; min-width:220px; line-height:1.55;'>" +
                        "<b>" + st.name + "</b><br>" +
                        "• 업종: " + st.cat + "<br>" +
                        "• 행정동: " + st.dong + "<br>" +
                        "• 주소: " + st.addr + "</div>";
                    infowindow.setContent(iwContent);
                    infowindow.open(map, marker);
                }});
            }});
        }}
    </script>
</body>
</html>"""

    static_dir = Path(BASE_DIR) / "static"
    static_dir.mkdir(exist_ok=True)
    map_file = static_dir / "naver_map.html"
    map_file.write_text(html_code, encoding="utf-8")

    components.iframe(f"/app/static/naver_map.html?v={int(time.time() * 1000)}", height=600)


st.title("🗺️ 지역·업종별 상권 현황 및 지도 시각화")

st.sidebar.header("🔍 분석 조건 선택")

region_options = {k: v["name"] for k, v in regions.items()}
selected_region_key = st.sidebar.selectbox(
    "1️⃣ 시군구 선택",
    options=list(region_options.keys()),
    format_func=lambda k: region_options[k],
)

region_info = regions[selected_region_key]
sgg_codes = region_info["sigungu_codes"]
latest_ym = analyzer.get_latest_base_ym(sgg_codes)

if not latest_ym:
    st.warning(f"**{region_info['name']}**의 점포 데이터가 아직 데이터베이스에 없습니다.")
    if st.button(f"🚀 지금 바로 {region_info['name']} 점포 데이터 자동 수집하기", type="primary"):
        prog_bar = st.progress(0.0, text=f"{region_info['name']} API 연결 중...")
        try:
            sc = StoreCollector()
            cnt = sc.collect_from_api(
                region_key=selected_region_key,
                progress_callback=lambda ratio, msg: prog_bar.progress(min(ratio, 1.0), text=msg),
            )
            prog_bar.progress(1.0, text="행정동 지도 권역 경계 생성 중...")
            bc = PopulationAndBoundaryCollector()
            bc.build_fallback_boundaries_from_stores()
            st.success(f"🎉 {region_info['name']} 점포 {cnt:,}건 수집 및 지도 경계 생성이 완료되었습니다!")
            st.rerun()
        except Exception as e:
            st.error(f"수집 중 오류가 발생했습니다: {e}")
    st.stop()

st.sidebar.caption(f"📅 데이터 기준연월: **{latest_ym}**")

adong_df = analyzer.get_adong_list(sgg_codes, latest_ym)
adong_options = {"ALL": f"전체 행정동 ({len(adong_df)}개 읍면동)"}
for _, row in adong_df.iterrows():
    adong_options[row["adong_cd"]] = f"{row['sigungu_nm']} {row['adong_nm']} ({row['adong_cd']})"

selected_adong_cd = st.sidebar.selectbox(
    "2️⃣ 행정동/읍면 선택",
    options=list(adong_options.keys()),
    format_func=lambda k: adong_options[k],
)

st.sidebar.divider()
st.sidebar.subheader("🏷️ 업종 3단계 선택 (전체 지원)")

cat_tree = analyzer.get_category_tree(sgg_codes, latest_ym)

lcls_df = cat_tree[["inds_lcls_cd", "inds_lcls_nm"]].drop_duplicates().sort_values("inds_lcls_nm")
lcls_options = {"ALL": "전체 대분류", **dict(zip(lcls_df["inds_lcls_cd"], lcls_df["inds_lcls_nm"]))}
selected_lcls = st.sidebar.selectbox(
    "대분류",
    options=list(lcls_options.keys()),
    format_func=lambda k: lcls_options[k],
)

if selected_lcls != "ALL":
    mcls_sub = cat_tree[cat_tree["inds_lcls_cd"] == selected_lcls][["inds_mcls_cd", "inds_mcls_nm"]].drop_duplicates().sort_values("inds_mcls_nm")
else:
    mcls_sub = pd.DataFrame(columns=["inds_mcls_cd", "inds_mcls_nm"])

mcls_options = {"ALL": "전체 중분류", **dict(zip(mcls_sub["inds_mcls_cd"], mcls_sub["inds_mcls_nm"]))}
selected_mcls = st.sidebar.selectbox(
    "중분류",
    options=list(mcls_options.keys()),
    format_func=lambda k: mcls_options[k],
    disabled=(selected_lcls == "ALL"),
)

if selected_mcls != "ALL":
    scls_sub = cat_tree[cat_tree["inds_mcls_cd"] == selected_mcls][["inds_scls_cd", "inds_scls_nm"]].drop_duplicates().sort_values("inds_scls_nm")
else:
    scls_sub = pd.DataFrame(columns=["inds_scls_cd", "inds_scls_nm"])

scls_options = {"ALL": "전체 소분류", **dict(zip(scls_sub["inds_scls_cd"], scls_sub["inds_scls_nm"]))}
selected_scls = st.sidebar.selectbox(
    "소분류",
    options=list(scls_options.keys()),
    format_func=lambda k: scls_options[k],
    disabled=(selected_mcls == "ALL"),
)

st.sidebar.divider()
max_markers = st.sidebar.slider("지도 점포 마커 최대 표시 수", min_value=100, max_value=2000, value=600, step=100)

summary_df = analyzer.get_adong_summary_and_density(
    sigungu_codes=sgg_codes,
    base_ym=latest_ym,
    lcls_cd=selected_lcls,
    mcls_cd=selected_mcls,
    scls_cd=selected_scls,
)

stores_df = analyzer.get_filtered_stores(
    sigungu_codes=sgg_codes,
    base_ym=latest_ym,
    adong_cd=selected_adong_cd,
    lcls_cd=selected_lcls,
    mcls_cd=selected_mcls,
    scls_cd=selected_scls,
)

if summary_df["geojson_str"].isna().any():
    PopulationAndBoundaryCollector().build_fallback_boundaries_from_stores()
    summary_df = analyzer.get_adong_summary_and_density(
        sigungu_codes=sgg_codes,
        base_ym=latest_ym,
        lcls_cd=selected_lcls,
        mcls_cd=selected_mcls,
        scls_cd=selected_scls,
    )

cat_label_parts = []
if selected_lcls != "ALL":
    cat_label_parts.append(lcls_options[selected_lcls])
if selected_mcls != "ALL":
    cat_label_parts.append(mcls_options[selected_mcls])
if selected_scls != "ALL":
    cat_label_parts.append(scls_options[selected_scls])
selected_cat_label = " > ".join(cat_label_parts) if cat_label_parts else "전체 업종"

target_summary = summary_df[summary_df["adong_cd"] == selected_adong_cd] if selected_adong_cd != "ALL" else summary_df

total_stores = int(target_summary["store_count"].sum())
total_franchise = int(target_summary["franchise_count"].sum())
franchise_pct = round(total_franchise * 100.0 / total_stores, 1) if total_stores > 0 else 0.0

has_pop = target_summary["population"].notna().any() and target_summary["population"].sum() > 0
has_area = target_summary["area_km2"].notna().any() and target_summary["area_km2"].sum() > 0

if has_pop:
    total_pop = int(target_summary["population"].sum())
    per_1k_pop_str = f"{round(total_stores * 1000.0 / total_pop, 2)}개"
    pop_caption = f"주민등록 인구: {total_pop:,}명"
else:
    per_1k_pop_str = "데이터 미등록"
    pop_caption = "주민등록 인구 CSV 등록 시 자동 산출"

if has_area:
    total_area = float(target_summary["area_km2"].sum())
    per_km2_str = f"{round(total_stores / total_area, 2)}개/km²"
    area_caption = f"행정동 면적 합계: {total_area:.2f} km²"
else:
    per_km2_str = "데이터 미등록"
    area_caption = "공식 행정동 경계(.shp) 등록 시 자동 산출"

st.markdown(f"### 📊 `{adong_options[selected_adong_cd]}` × `{selected_cat_label}` 핵심 요약")
k1, k2, k3, k4 = st.columns(4)
with k1:
    st.metric("선택 업종 점포 수", f"{total_stores:,}개", f"지점명 보유(프랜차이즈 추정) {franchise_pct}%")
with k2:
    st.metric("인구 1천명당 점포 수", per_1k_pop_str, pop_caption)
with k3:
    st.metric("면적(1km²)당 점포 수", per_km2_str, area_caption)
with k4:
    top_dong_row = summary_df.iloc[0] if not summary_df.empty else None
    if top_dong_row is not None:
        st.metric("시군구 내 최다 밀집 행정동", f"{top_dong_row['adong_nm']}", f"{int(top_dong_row['store_count']):,}개 점포")

st.divider()
st.subheader("🗺️ 상권 지도: 행정동별 밀집도 단계구분도 & 점포 위치 마커 (네이버 지도)")

metric_choices = {"store_count": "점포 수 (개)"}
if summary_df["stores_per_1k_pop"].notna().any():
    metric_choices["stores_per_1k_pop"] = "인구 1천명당 점포 수 (개/천명)"
if summary_df["stores_per_km2"].notna().any():
    metric_choices["stores_per_km2"] = "면적 1km²당 점포 수 (개/km²)"

selected_map_metric = st.radio(
    "단계구분도 색상 기준 지표",
    options=list(metric_choices.keys()),
    format_func=lambda k: metric_choices[k],
    horizontal=True,
)

if selected_adong_cd != "ALL" and not target_summary.empty:
    c_lat = float(target_summary.iloc[0]["center_lat"])
    c_lon = float(target_summary.iloc[0]["center_lon"])
    zoom_lv = 15
else:
    c_lat = region_info["center_lat"]
    c_lon = region_info["center_lon"]
    zoom_lv = region_info["default_zoom"]

display_stores = stores_df.head(max_markers)
if len(stores_df) > max_markers:
    st.caption(
        f"ℹ️ 현재 조건의 점포 총 **{len(stores_df):,}개** 중 속도 유지를 위해 상위 **{max_markers:,}개** 마커를 표시 중입니다. "
        f"(🔴 빨간점: 프랜차이즈/지점 / 🔵 파란점: 일반 점포)"
    )
else:
    st.caption(f"ℹ️ 현재 조건의 점포 **{len(stores_df):,}개** 전체가 지도에 표시되었습니다. (🔴 빨간점: 프랜차이즈/지점 / 🔵 파란점: 일반 점포)")

if not naver_client_id or "여기에_" in naver_client_id:
    st.error("🔑 `Secrets`(또는 `.env`)에 `NAVER_MAP_CLIENT_ID`가 입력되지 않았습니다.")
else:
    render_naver_map(
        client_id=naver_client_id,
        center_lat=c_lat,
        center_lon=c_lon,
        zoom_lv=zoom_lv,
        summary_df=summary_df,
        stores_df=display_stores,
        selected_metric=selected_map_metric,
        selected_adong_cd=selected_adong_cd,
    )

st.divider()
col_chart, col_table = st.columns([1, 1])

with col_chart:
    st.subheader("📊 행정동별 점포 수 상위 20곳 비교")
    top20_df = summary_df.head(20).sort_values("store_count", ascending=True)
    fig = px.bar(
        top20_df,
        x="store_count",
        y="adong_nm",
        color="sigungu_nm",
        orientation="h",
        text="store_count",
        labels={"store_count": "점포 수 (개)", "adong_nm": "행정동", "sigungu_nm": "시군구"},
        height=500,
    )
    fig.update_layout(margin=dict(l=10, r=10, t=20, b=10))
    st.plotly_chart(fig, width="stretch")

with col_table:
    st.subheader("📋 행정동별 밀집도 및 상세 지표 표")
    display_summary = summary_df[[
        "sigungu_nm", "adong_cd", "adong_nm", "store_count",
        "franchise_ratio", "population", "stores_per_1k_pop", "area_km2", "stores_per_km2"
    ]].copy()

    display_summary["population"] = display_summary["population"].apply(
        lambda x: f"{int(x):,}" if pd.notna(x) else "이 지역 미등록"
    )
    display_summary["stores_per_1k_pop"] = display_summary["stores_per_1k_pop"].apply(
        lambda x: f"{x:.2f}" if pd.notna(x) else "미제공(인구 필요)"
    )
    display_summary["area_km2"] = display_summary["area_km2"].apply(
        lambda x: f"{x:.2f}" if pd.notna(x) else "미등록(경계 필요)"
    )
    display_summary["stores_per_km2"] = display_summary["stores_per_km2"].apply(
        lambda x: f"{x:.2f}" if pd.notna(x) else "미제공(면적 필요)"
    )

    display_summary.columns = [
        "시군구", "행정동코드", "행정동", "점포수(개)",
        "프랜차이즈비율(%)", "주민등록인구(명)", "인구1천명당점포수", "면적(km²)", "면적1km²당점포수"
    ]
    st.dataframe(display_summary, width="stretch", height=500, hide_index=True)

with st.expander("🔎 선택된 지역의 개별 점포 목록 보기 / 엑셀(CSV) 내려받기"):
    show_stores = stores_df[[
        "store_name", "branch_name", "inds_lcls_nm", "inds_mcls_nm", "inds_scls_nm",
        "sigungu_nm", "adong_nm", "rdnm_adr", "lno_adr"
    ]].rename(columns={
        "store_name": "상호명",
        "branch_name": "지점명",
        "inds_lcls_nm": "대분류",
        "inds_mcls_nm": "중분류",
        "inds_scls_nm": "소분류",
        "sigungu_nm": "시군구",
        "adong_nm": "행정동",
        "rdnm_adr": "도로명주소",
        "lno_adr": "지번주소",
    })
    st.dataframe(show_stores, width="stretch", height=350, hide_index=True)
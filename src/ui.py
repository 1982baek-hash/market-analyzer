"""화면 공통: 가족 비밀번호, 지역·동네 선택, 데이터 캐시, 지도."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src import scoring
from src.config_loader import get_api_key

DEFAULT_REGION = "andong"


def require_login() -> None:
    """FAMILY_PASSWORD가 설정돼 있으면 비밀번호를 확인합니다. 모든 화면 맨 위에서 부릅니다."""
    expected = get_api_key("FAMILY_PASSWORD")
    if not expected or st.session_state.get("auth_ok", False):
        return
    st.title("🔒 우리 가족 상권 분석기")
    st.info("가족 공유용 페이지입니다. 비밀번호를 입력해 주세요.")
    pw = st.text_input("가족 비밀번호", type="password", placeholder="비밀번호 입력 후 엔터")
    if pw:
        if pw == expected:
            st.session_state["auth_ok"] = True
            st.rerun()
        st.error("비밀번호가 일치하지 않습니다.")
    st.stop()


@st.cache_data(show_spinner="수집 자료를 읽는 중입니다...")
def _region_data(region_key: str, signature: str) -> dict:
    return scoring.load_region_data(region_key)


def region_data(region_key: str) -> dict:
    return _region_data(region_key, scoring.mart_signature())


@st.cache_data(show_spinner="점수를 계산하는 중입니다...")
def _preset_scores(region_key: str, signature: str):
    return scoring.score_preset(region_key, data=scoring.load_region_data(region_key))


def preset_scores(region_key: str):
    return _preset_scores(region_key, scoring.mart_signature())


@st.cache_data(show_spinner="점수를 계산하는 중입니다...")
def _category_scores(region_key: str, scls_cd: str, demand_type: str, signature: str):
    return scoring.score_category(region_key, scls_cd, demand_type, data=scoring.load_region_data(region_key))


def category_scores(region_key: str, scls_cd: str, demand_type: str):
    return _category_scores(region_key, scls_cd, demand_type, scoring.mart_signature())


def pick_region(key: str = "region") -> str:
    regions = scoring.load_regions()
    keys = list(regions)
    # 화면을 옮겨 다녀도 같은 지역을 유지
    current = st.session_state.get("sel_region", DEFAULT_REGION if DEFAULT_REGION in keys else keys[0])
    sel = st.sidebar.selectbox("지역", keys, index=keys.index(current) if current in keys else 0,
                               format_func=lambda k: regions[k]["name"], key=key)
    st.session_state["sel_region"] = sel
    return sel


def pick_dong(data: dict, key: str = "dong") -> tuple[str, str]:
    master = scoring.dong_master(data).sort_values(["sigungu_nm", "adong_nm"])
    labels = dict(zip(master["adong_cd"], master["adong_nm"]))
    if master["sigungu_nm"].nunique() > 1:  # 성남시는 구 이름도 함께
        labels = dict(zip(master["adong_cd"], master["sigungu_nm"].str.split().str[-1] + " " + master["adong_nm"]))
    codes = list(labels)
    current = st.session_state.get("sel_dong")
    sel = st.sidebar.selectbox("동네", codes, index=codes.index(current) if current in codes else 0,
                               format_func=lambda c: labels[c], key=key)
    st.session_state["sel_dong"] = sel
    return sel, labels[sel]


def period_badges(info_periods: dict) -> None:
    names = {"stores": "점포", "population": "인구", "sgis": "직장인"}
    parts = []
    for k, label in names.items():
        v = info_periods.get(k)
        if v:
            v = f"{v[:4]}.{v[4:6]}" if len(v) == 6 else f"{v}년"
        parts.append(f"{label} {v or '없음'}")
    st.caption("기준 시점 · " + " · ".join(parts))


def score_color(score) -> str:
    if pd.isna(score):
        return "#9e9e9e"
    if score >= 65:
        return "#1a9850"
    if score >= 50:
        return "#91cf60"
    if score >= 40:
        return "#fdae61"
    return "#d73027"


def dong_score_map(data: dict, table: pd.DataFrame, height: int = 420):
    """동네 중심점(점포 위치의 중앙값)에 점수를 색으로 표시합니다."""
    import folium
    from streamlit_folium import st_folium

    cent = scoring.dong_centroids(data)
    t = table.set_index("adong_cd").join(cent, how="inner")
    if t.empty:
        st.info("지도에 표시할 위치 정보가 없습니다.")
        return
    fmap = folium.Map(location=[t["lat"].mean(), t["lon"].mean()], zoom_start=12 if len(t) > 30 else 11,
                      tiles="CartoDB positron")
    for _, r in t.iterrows():
        s = r.get("score")
        label = f"{r['adong_nm']} · " + ("판단 보류" if pd.isna(s) else f"{s:.0f}점")
        folium.CircleMarker(
            location=[r["lat"], r["lon"]], radius=9 if pd.notna(s) else 6,
            color=score_color(s), fill=True, fill_color=score_color(s), fill_opacity=0.85, weight=1,
            tooltip=label,
        ).add_to(fmap)
        if pd.notna(r.get("rank")) and r["rank"] <= 3:
            folium.Marker(
                [r["lat"], r["lon"]],
                icon=folium.DivIcon(html=f"<div style='font-weight:700;font-size:12px;color:#111;"
                                         f"transform:translate(10px,-18px)'>{int(r['rank'])}위 {r['adong_nm']}</div>"),
            ).add_to(fmap)
    st_folium(fmap, height=height, use_container_width=True, returned_objects=[])

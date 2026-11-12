import streamlit as st
from src.config_loader import get_api_key
from src.analyzer import MarketAnalyzer
from src.db_manager import DatabaseManager
from src.collectors.pop_collector import PopulationAndBoundaryCollector
from update import run_all_updates

st.set_page_config(
    page_title="우리 가족 상권 분석기 (성남·안동·예천)",
    page_icon="🏪",
    layout="wide",
    initial_sidebar_state="expanded",
)


def check_family_password() -> bool:
    """FAMILY_PASSWORD가 설정되어 있을 때만 가족 비밀번호 확인창을 띄웁니다."""
    expected_pw = get_api_key("FAMILY_PASSWORD")
    if not expected_pw:
        return True

    if st.session_state.get("auth_ok", False):
        return True

    st.title("🔒 우리 가족 상권 분석기")
    st.info("가족 공유용 페이지입니다. 비밀번호를 입력해 주세요.")
    pw_input = st.text_input("가족 비밀번호", type="password", placeholder="비밀번호 입력 후 엔터")
    if pw_input:
        if pw_input == expected_pw:
            st.session_state["auth_ok"] = True
            st.rerun()
        else:
            st.error("비밀번호가 일치하지 않습니다.")
    return False


if not check_family_password():
    st.stop()


@st.cache_resource
def ensure_initial_boundaries():
    collector = PopulationAndBoundaryCollector()
    collector.build_fallback_boundaries_from_stores()
    return True


ensure_initial_boundaries()

db = DatabaseManager()
need_check, check_msg = db.needs_weekly_check()

st.title("🏪 공공데이터 기반 개인용 상권 분석 웹 앱")

if need_check:
    st.warning(f"⏰ **주간 업데이트 확인 알림:** {check_msg}")
    if st.button("🔄 지금 최신 데이터 확인 및 업데이트 실행", type="primary"):
        with st.spinner("공공데이터포털 및 로컬 폴더에서 최신 데이터를 확인 중입니다..."):
            res = run_all_updates(force=False)
        st.success(f"점검 완료! (신규 적재 {len(res['success'])}건 / 최신 유지 {len(res['skipped'])}건 / 실패 {len(res['failed'])}건)")
        st.rerun()
else:
    st.caption(f"✅ 자동 업데이트 상태 정상 ({check_msg})")

st.markdown(
    """
    **경기도 성남시 · 경상북도 안동시 · 경상북도 예천군**의 업종별 점포 현황, 밀집도, 유동인구, 입지 적합도를 분석하는 프로그램입니다.
    왼쪽 사이드바 메뉴에서 원하는 분석 페이지를 선택해 주세요.
    """
)

analyzer = MarketAnalyzer()
regions = analyzer.get_available_regions()

st.subheader("📌 현재 데이터베이스(DuckDB) 적재 현황")
cols = st.columns(len(regions))

for idx, (r_key, r_info) in enumerate(regions.items()):
    base_ym = analyzer.get_latest_base_ym(r_info["sigungu_codes"])
    with cols[idx]:
        if base_ym:
            dong_df = analyzer.get_adong_list(r_info["sigungu_codes"], base_ym)
            stores_df = analyzer.get_filtered_stores(r_info["sigungu_codes"], base_ym)
            st.success(f"**{r_info['name']}** (기준: `{base_ym}`)")
            st.metric("적재된 총 점포 수", f"{len(stores_df):,}개")
            st.caption(f"관할 행정동(읍면동) 수: {len(dong_df)}개")
        else:
            st.warning(f"**{r_info['name']}** (미수집)")
            st.metric("적재된 총 점포 수", "0개")
            st.caption("왼쪽 [5 데이터 현황 및 관리] 또는 [1 상권 현황 및 지도]에서 수집할 수 있습니다.")

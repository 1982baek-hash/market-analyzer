from datetime import datetime
from pathlib import Path
import pandas as pd
import streamlit as st

from src.config_loader import BASE_DIR, LOG_PATH, load_regions_config
from src.db_manager import DatabaseManager
from src.analyzer import MarketAnalyzer
from src.collectors.pop_collector import PopulationAndBoundaryCollector
from update import run_all_updates

st.set_page_config(page_title="데이터 현황 및 관리", page_icon="🔄", layout="wide")

db = DatabaseManager()
analyzer = MarketAnalyzer()
regions = load_regions_config()

st.title("🔄 데이터 현황 및 자동 업데이트 관리")

# ==========================================
# 1. 지금 업데이트 버튼 및 전체 요약
# ==========================================
col_btn1, col_btn2, col_info = st.columns([1.2, 1.2, 3])
with col_btn1:
    if st.button("🔄 지금 업데이트 (새 데이터만 수집)", type="primary", width="stretch"):
        with st.spinner("소스별 최신 데이터 확인 및 검증 중..."):
            res = run_all_updates(force=False)
        if res["failed"]:
            st.error(f"일부 소스 업데이트 실패 (기존 데이터 유지됨): {res['failed']}")
        else:
            st.success(f"업데이트 점검 완료! (신규 {len(res['success'])}건 / 최신유지 {len(res['skipped'])}건)")
        st.rerun()

with col_btn2:
    if st.button("⚡ 전체 API 강제 재수집", width="stretch"):
        with st.spinner("성남·안동·예천 API 데이터를 새로 받아오는 중입니다..."):
            res = run_all_updates(force=True)
        if res["failed"]:
            st.error(f"일부 실패: {res['failed']}")
        else:
            st.success(f"강제 재수집 완료! ({len(res['success'])}건 갱신)")
        st.rerun()

with col_info:
    need_chk, chk_msg = db.needs_weekly_check()
    if need_chk:
        st.warning(f"⏰ {chk_msg}")
    else:
        st.info(f"✅ {chk_msg}")

# ==========================================
# 2. 소스별 데이터 현황 관리표
# ==========================================
st.divider()
st.subheader("📋 데이터 소스별 수집·갱신 현황표")

status_df = db.get_data_status_df()

# 수동 수집이 필요하거나 갱신 주기가 지난 소스 알림 배너
manual_needed = status_df[status_df["needs_manual_action"]]
if not manual_needed.empty:
    with st.expander(f"📥 새 데이터를 받아 폴더에 넣어주세요 ({len(manual_needed)}건 갱신 필요 / 미등록)", expanded=True):
        for _, m_row in manual_needed.iterrows():
            st.markdown(
                f"- **{m_row['source_name']}** (`{m_row['update_cycle']}`): "
                f"새 데이터를 내려받아 **`{m_row['target_folder']}`** 폴더에 넣어주세요. "
                f"([내려받는 곳 바로가기]({m_row['download_url']}))"
            )

display_df = status_df[[
    "source_name", "region_scope", "collect_method", "update_cycle",
    "source_base_period", "row_count", "last_checked_at", "last_updated_at",
    "status", "error_message", "target_folder"
]].copy()

display_df["last_checked_at"] = display_df["last_checked_at"].apply(
    lambda x: pd.to_datetime(x).strftime("%Y-%m-%d %H:%M") if pd.notna(x) else "-"
)
display_df["last_updated_at"] = display_df["last_updated_at"].apply(
    lambda x: pd.to_datetime(x).strftime("%Y-%m-%d %H:%M") if pd.notna(x) else "-"
)
display_df["row_count"] = display_df["row_count"].apply(lambda x: f"{int(x):,}건" if pd.notna(x) else "0건")

display_df.columns = [
    "데이터 소스명", "대상 지역", "수집 방식", "갱신 주기",
    "원본 기준 시점", "적재 건수", "마지막 확인 일시", "마지막 수집 일시",
    "상태", "오류/안내 메시지", "수동 파일 넣을 폴더"
]
st.dataframe(display_df, width="stretch", hide_index=True)

# ==========================================
# 3. 수동 파일 웹에서 바로 올리기 (인구 CSV / 경계 파일)
# ==========================================
st.divider()
st.subheader("📤 수동 다운로드 파일 바로 등록하기 (인구·경계)")

up_col1, up_col2 = st.columns(2)

with up_col1:
    st.markdown("#### 1) 주민등록 인구 CSV 올리기")
    st.caption("공공데이터포털([15097972](https://www.data.go.kr/data/15097972/fileData.do))에서 받은 CSV 파일을 올리면 '인구 1천명당 점포 수'가 즉시 계산됩니다.")
    pop_file = st.file_uploader("주민등록 인구 CSV 파일 선택", type=["csv"], key="pop_up")
    if pop_file is not None and st.button("인구 데이터 검증 및 저장", key="btn_pop"):
        save_path = Path(BASE_DIR) / "data" / "raw" / "population" / pop_file.name
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(pop_file.getvalue())
        try:
            cnt = PopulationAndBoundaryCollector().load_population_csv(save_path)
            st.success(f"🎉 주민등록 인구 {cnt:,}개 행정동 저장 완료!")
            st.rerun()
        except Exception as e:
            st.error(f"파일 검증 실패 (기존 데이터 유지): {e}")

with up_col2:
    st.markdown("#### 2) 공식 행정동 경계(GeoJSON) 올리기")
    st.caption("통계청 SGIS([자료제공](https://sgis.mods.go.kr/view/pss/openDataIntrcn)) 등의 행정동 경계 `.geojson` 파일을 올리면 '면적(km²)당 점포 수'가 즉시 계산됩니다.")
    geo_file = st.file_uploader("행정동 경계 GeoJSON 파일 선택", type=["geojson", "json"], key="geo_up")
    if geo_file is not None and st.button("경계 및 면적 데이터 검증 및 저장", key="btn_geo"):
        save_path = Path(BASE_DIR) / "data" / "raw" / "boundaries" / geo_file.name
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(geo_file.getvalue())
        try:
            cnt = PopulationAndBoundaryCollector().load_official_boundary_file(save_path)
            now = datetime.now()
            with db.get_connection(read_only=False) as con:
                con.execute("""
                    INSERT OR REPLACE INTO data_status VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [
                    "boundaries_all", "통계청 SGIS 행정동 경계 (면적)", "공통 (세 지역)",
                    f"수동 파일 ({geo_file.name})", "연 1회", str(now.year),
                    cnt, now, now, "SUCCESS", ""
                ])
            st.success(f"🎉 공식 행정동 경계 {cnt:,}건 및 면적 반영 완료!")
            st.rerun()
        except Exception as e:
            st.error(f"파일 검증 실패 (기존 데이터 유지): {e}")

# ==========================================
# 4. 안동시·예천군 행정동 유동인구 수동 입력 기능
# ==========================================
st.divider()
st.subheader("✍️ 안동시·예천군 행정동별 유동인구 수동 입력 (상권정보시스템 조회값)")
st.caption(
    "안동시·예천군은 무료 공개 데이터에 행정동 단위 시간대·연령대 유동인구가 없으므로, "
    "소상공인 상권정보시스템(https://sg.sbiz.or.kr)에서 직접 조회하신 행정동별 요약값을 여기에 입력·저장하면 분석 화면에 함께 반영됩니다."
)

manual_region_key = st.selectbox(
    "대상 시군 선택",
    options=["andong", "yecheon"],
    format_func=lambda k: regions[k]["name"],
)
m_sgg_codes = regions[manual_region_key]["sigungu_codes"]
m_latest_ym = analyzer.get_latest_base_ym(m_sgg_codes)

if m_latest_ym:
    m_adong_df = analyzer.get_adong_list(m_sgg_codes, m_latest_ym)
    adong_dict = {row["adong_cd"]: f"{row['adong_nm']} ({row['adong_cd']})" for _, row in m_adong_df.iterrows()}

    with st.form("manual_floating_form"):
        f_col1, f_col2, f_col3 = st.columns(3)
        with f_col1:
            sel_m_adong = st.selectbox("행정동/읍면 선택", options=list(adong_dict.keys()), format_func=lambda k: adong_dict[k])
            input_ym = st.text_input("조회 기준연월 (YYYYMM)", value=datetime.now().strftime("%Y%m"))
            daily_avg = st.number_input("일평균 유동인구 (명)", min_value=0, value=10000, step=500)
        with f_col2:
            peak_slot = st.selectbox("피크 시간대", ["06~11시", "11~14시", "14~17시", "17~21시", "21~24시", "00~06시"])
            main_age = st.selectbox("주 이용 연령대", ["10대 이하", "20대", "30대", "40대", "50대", "60대 이상"])
            weekday_ratio = st.slider("주중(평일) 유동인구 비중 (%)", 0.0, 100.0, 70.0, 1.0)
        with f_col3:
            male_ratio = st.slider("남성 비율 (%)", 0.0, 100.0, 50.0, 1.0)
            female_ratio = round(100.0 - male_ratio, 1)
            st.text(f"여성 비율 (자동 계산): {female_ratio}%")
            note = st.text_input("메모 (조회 출처 등)", value="소상공인 상권정보시스템 직접 조회")

        submitted = st.form_submit_button("💾 수동 유동인구 저장하기", type="primary")
        if submitted:
            adong_nm_only = m_adong_df[m_adong_df["adong_cd"] == sel_m_adong]["adong_nm"].iloc[0]
            db.upsert_manual_floating({
                "adong_cd": sel_m_adong,
                "sigungu_cd": sel_m_adong[:5],
                "adong_nm": adong_nm_only,
                "base_ym": input_ym.strip(),
                "daily_avg_pop": daily_avg,
                "peak_time_slot": peak_slot,
                "main_age_group": main_age,
                "male_ratio": male_ratio,
                "female_ratio": female_ratio,
                "weekday_ratio": weekday_ratio,
                "note": note,
            })
            st.success(f"✅ {adong_nm_only} ({input_ym}) 유동인구 정보가 저장되었습니다!")

    with db.get_connection(read_only=True) as con:
        saved_manual = con.execute("SELECT * FROM manual_floating_input ORDER BY updated_at DESC").df()
    if not saved_manual.empty:
        st.markdown("**저장된 수동 유동인구 목록**")
        st.dataframe(saved_manual, width="stretch", hide_index=True)
else:
    st.info("먼저 해당 지역의 점포 데이터를 수집해 주세요.")

# ==========================================
# 5. 업데이트 실행 로그 보기
# ==========================================
st.divider()
st.subheader("📜 최근 업데이트 로그 (update_history.log)")
if Path(LOG_PATH).exists():
    lines = Path(LOG_PATH).read_text(encoding="utf-8").splitlines()
    st.code("\n".join(lines[-30:][::-1]) if lines else "로그 기록이 없습니다.", language="text")
else:
    st.info("아직 로그 파일이 생성되지 않았습니다.")

from pathlib import Path
import pandas as pd
import plotly.express as px
import streamlit as st

from src.config_loader import BASE_DIR
from src.analyzer import MarketAnalyzer
from src.db_manager import DatabaseManager
from src.collectors.float_collector import FloatingPopulationCollector

st.set_page_config(page_title="유동인구 분석", page_icon="🚶", layout="wide")

analyzer = MarketAnalyzer()
db = DatabaseManager()
regions = analyzer.get_available_regions()

st.title("🚶 유동인구 분석 및 업종 고객층 매칭 (기능 3)")

# ==========================================
# 1. 사이드바: 지역 및 업종 선택
# ==========================================
st.sidebar.header("🔍 분석 조건 선택")
selected_region_key = st.sidebar.selectbox(
    "1️⃣ 시군구 선택",
    options=list(regions.keys()),
    format_func=lambda k: regions[k]["name"],
)
region_info = regions[selected_region_key]
sgg_codes = region_info["sigungu_codes"]
latest_ym = analyzer.get_latest_base_ym(sgg_codes)

if not latest_ym:
    st.warning(f"먼저 [1 상권 현황 및 지도]에서 **{region_info['name']}** 점포 데이터를 수집해 주세요.")
    st.stop()

adong_df = analyzer.get_adong_list(sgg_codes, latest_ym)
adong_options = {"ALL": f"전체 행정동 ({len(adong_df)}개 읍면동)"}
for _, row in adong_df.iterrows():
    adong_options[row["adong_cd"]] = f"{row['sigungu_nm']} {row['adong_nm']}"

selected_adong_cd = st.sidebar.selectbox(
    "2️⃣ 행정동/읍면 선택",
    options=list(adong_options.keys()),
    format_func=lambda k: adong_options[k],
)

st.sidebar.divider()
st.sidebar.subheader("🏷️ 업종 연결 분석 (주 고객층 비교)")
cat_tree = analyzer.get_category_tree(sgg_codes, latest_ym)
lcls_df = cat_tree[["inds_lcls_cd", "inds_lcls_nm"]].drop_duplicates().sort_values("inds_lcls_nm")
lcls_options = dict(zip(lcls_df["inds_lcls_cd"], lcls_df["inds_lcls_nm"]))
sel_lcls = st.sidebar.selectbox("대분류", options=list(lcls_options.keys()), format_func=lambda k: lcls_options[k])

mcls_df = cat_tree[cat_tree["inds_lcls_cd"] == sel_lcls][["inds_mcls_cd", "inds_mcls_nm"]].drop_duplicates().sort_values("inds_mcls_nm")
mcls_options = dict(zip(mcls_df["inds_mcls_cd"], mcls_df["inds_mcls_nm"]))
sel_mcls = st.sidebar.selectbox("중분류", options=list(mcls_options.keys()), format_func=lambda k: mcls_options[k])

scls_df = cat_tree[cat_tree["inds_mcls_cd"] == sel_mcls][["inds_scls_cd", "inds_scls_nm"]].drop_duplicates().sort_values("inds_scls_nm")
scls_options = dict(zip(scls_df["inds_scls_cd"], scls_df["inds_scls_nm"]))
sel_scls = st.sidebar.selectbox("소분류", options=list(scls_options.keys()), format_func=lambda k: scls_options[k])

# 업종 매칭 기준 로드
cat_map_path = Path(BASE_DIR) / "config" / "category_mapping.csv"
cat_map_df = pd.read_csv(cat_map_path, dtype=str) if cat_map_path.exists() else pd.DataFrame()
sel_cat_text = f"{lcls_options[sel_lcls]} {mcls_options[sel_mcls]} {scls_options[sel_scls]}"
matched_profile = None
if not cat_map_df.empty:
    for _, m_row in cat_map_df.iterrows():
        kws = str(m_row["keyword"]).split("|")
        if any(kw in sel_cat_text for kw in kws):
            matched_profile = m_row
            break
    if matched_profile is None:
        sub = cat_map_df[cat_map_df["inds_lcls_cd"] == sel_lcls]
        if not sub.empty:
            matched_profile = sub.iloc[0]

# ==========================================
# 2. 지역별 유동인구 데이터 커버리지 명시 (추정·가짜 데이터 금지)
# ==========================================
if selected_region_key == "seongnam":
    st.info("📍 **경기도 성남시 데이터 커버리지:** 행정동 단위 시간대별·성별·연령대별·유입지 분석이 가능합니다. (원본: 경기데이터드림)")
elif selected_region_key == "andong":
    st.warning(
        "📍 **경상북도 안동시 데이터 커버리지:** **시군 단위까지만 제공** (행안부·통계청 인구감소지역 생활인구 / 한국관광 데이터랩). "
        "시간대×연령대 교차 히트맵은 **이 지역은 제공되지 않음**으로 표시되며, 아래 수동 입력값이 있으면 행정동 요약을 함께 보여드립니다."
    )
else:
    st.warning(
        "📍 **경상북도 예천군 데이터 커버리지:** 예천군은 인구감소지역 본 통계 제외 대상(인구감소관심지역)으로, "
        "한국관광 데이터랩 기준 **시군 단위까지만 제공**됩니다. 시간대별 24시간 추이 및 시간대×연령대 히트맵은 **이 지역은 제공되지 않음**입니다."
    )

# ==========================================
# 3. 탭 구성: 시간대 / 성별·연령 / 거주지·유입지 / 업종 매칭 / 데이터 올리기
# ==========================================
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "⏰ 시간대별 분석",
    "👥 연령대·성별 & 히트맵",
    "🧭 거주자 vs 외부 유입",
    "🎯 업종 고객층 매칭",
    "📤 유동인구 파일 올리기"
])

with db.get_connection(read_only=True) as con:
    fh_df = con.execute("SELECT * FROM floating_hourly").df()
    od_df = con.execute("SELECT * FROM floating_od").df()
    sg_df = con.execute("SELECT * FROM floating_sigungu WHERE region_key = ?", [selected_region_key]).df()
    manual_df = con.execute("SELECT * FROM manual_floating_input WHERE sigungu_cd IN (" + ",".join([f"'{c}'" for c in sgg_codes]) + ")").df()

# 성남시 행정동 필터링
if selected_region_key == "seongnam" and not fh_df.empty:
    if selected_adong_cd != "ALL":
        fh_target = fh_df[fh_df["adong_cd"] == selected_adong_cd]
    else:
        fh_target = fh_df
else:
    fh_target = pd.DataFrame()

if selected_region_key == "seongnam" and not od_df.empty:
    if selected_adong_cd != "ALL":
        od_target = od_df[od_df["dest_adong_cd"] == selected_adong_cd]
    else:
        od_target = od_df
else:
    od_target = pd.DataFrame()

# ------------------------------------------
# TAB 1: 시간대별 분석 (24시간 추이, 평일/주말, 피크)
# ------------------------------------------
with tab1:
    if selected_region_key == "seongnam":
        if fh_target.empty or (fh_target["hour"] < 0).all():
            st.info("성남시 시간대별 유동인구 데이터가 아직 등록되지 않았습니다. 오른쪽 **[📤 유동인구 파일 올리기]** 탭에서 경기데이터드림 CSV를 올려주세요.")
        else:
            hourly_sum = fh_target[fh_target["hour"] >= 0].groupby(["hour", "day_type"], as_index=False)["pop_count"].sum()
            peak_row = hourly_sum.groupby("hour")["pop_count"].sum().idxmax()
            st.metric("🔥 피크 시간대", f"{int(peak_row):02d}시 ~ {int(peak_row)+1:02d}시")
            fig_hr = px.line(
                hourly_sum, x="hour", y="pop_count", color="day_type", markers=True,
                labels={"hour": "시간대 (0~23시)", "pop_count": "유동인구 합계", "day_type": "요일 구분"},
                title="24시간 유동인구 추이 및 평일/주말 비교"
            )
            st.plotly_chart(fig_hr, width="stretch")
    else:
        if selected_region_key == "yecheon":
            st.error("🚫 **시간대별 24시간 추이:** 이 지역은 제공되지 않음 (예천군은 공공 시군 단위 시간대 통계 미제공)")
        else:
            st.warning("ℹ️ **안동시:** 공공 생활인구 통계는 **시군 단위까지만 제공**됩니다. (행정동 상세는 아래 수동 입력값 참조)")
            if not sg_df.empty:
                st.dataframe(sg_df, width="stretch", hide_index=True)

        if not manual_df.empty:
            st.subheader("✍️ 직접 입력하신 행정동별 유동인구 요약")
            show_m = manual_df if selected_adong_cd == "ALL" else manual_df[manual_df["adong_cd"] == selected_adong_cd]
            st.dataframe(show_m[["adong_nm", "base_ym", "daily_avg_pop", "peak_time_slot", "weekday_ratio", "note"]], width="stretch", hide_index=True)

# ------------------------------------------
# TAB 2: 연령대별·성별 구성비 및 시간대×연령대 히트맵
# ------------------------------------------
with tab2:
    if selected_region_key == "seongnam":
        if fh_target.empty:
            st.info("성남시 성·연령별 유동인구 파일이 아직 등록되지 않았습니다. **[📤 유동인구 파일 올리기]** 탭에서 등록해 주세요.")
        else:
            c1, c2 = st.columns(2)
            with c1:
                age_sum = fh_target.groupby("age_group", as_index=False)["pop_count"].sum()
                fig_age = px.bar(age_sum, x="age_group", y="pop_count", title="연령대별 유동인구 구성", labels={"age_group": "연령대", "pop_count": "유동인구"})
                st.plotly_chart(fig_age, width="stretch")
            with c2:
                gen_sum = fh_target.groupby("gender", as_index=False)["pop_count"].sum()
                fig_gen = px.pie(gen_sum, names="gender", values="pop_count", title="성별 구성비")
                st.plotly_chart(fig_gen, width="stretch")

            if (fh_target["hour"] >= 0).any() and fh_target["age_group"].nunique() > 1:
                hm_df = fh_target[fh_target["hour"] >= 0].pivot_table(
                    index="age_group", columns="hour", values="pop_count", aggfunc="sum", fill_value=0
                )
                fig_hm = px.imshow(hm_df, aspect="auto", color_continuous_scale="YlOrRd", title="시간대 × 연령대 유동인구 히트맵")
                st.plotly_chart(fig_hm, width="stretch")
    else:
        st.error("🚫 **시간대 × 연령대 히트맵:** 이 지역은 제공되지 않음 (공공 데이터 교차 집계 미제공)")
        if not sg_df.empty:
            st.subheader("📊 시군 단위 성별·연령대 분포 (시군 단위까지만 제공)")
            fig_sg = px.bar(sg_df, x="category_1", y="value", color="category_2", barmode="group")
            st.plotly_chart(fig_sg, width="stretch")
        if not manual_df.empty:
            st.subheader("✍️ 수동 입력된 행정동 주 이용 연령·성별")
            show_m = manual_df if selected_adong_cd == "ALL" else manual_df[manual_df["adong_cd"] == selected_adong_cd]
            st.dataframe(show_m[["adong_nm", "main_age_group", "male_ratio", "female_ratio"]], width="stretch", hide_index=True)

# ------------------------------------------
# TAB 3: 거주자 vs 외부 유입 비율 & 유입지 순위
# ------------------------------------------
with tab3:
    if selected_region_key == "seongnam":
        if od_target.empty:
            st.info("성남시 '행정동간 유출인구(O-D)' CSV 파일이 아직 등록되지 않았습니다. **[📤 유동인구 파일 올리기]** 탭에서 올려주세요.")
        else:
            od_calc = od_target.copy()
            od_calc["flow_type"] = od_calc.apply(
                lambda r: "거주자(내부 이동)" if str(r["origin_adong_cd"]) == str(r["dest_adong_cd"]) else "외부 유입", axis=1
            )
            ratio_df = od_calc.groupby("flow_type", as_index=False)["pop_count"].sum()
            col_o1, col_o2 = st.columns(2)
            with col_o1:
                fig_od = px.pie(ratio_df, names="flow_type", values="pop_count", title="거주자 vs 외부 유입 인구 비율")
                st.plotly_chart(fig_od, width="stretch")
            with col_o2:
                ext_df = od_calc[od_calc["flow_type"] == "외부 유입"].groupby("origin_name", as_index=False)["pop_count"].sum()
                ext_top10 = ext_df.sort_values("pop_count", ascending=False).head(10)
                fig_top = px.bar(ext_top10, x="pop_count", y="origin_name", orientation="h", title="외부 유입지 상위 TOP 10")
                fig_top.update_layout(yaxis={"categoryorder": "total ascending"})
                st.plotly_chart(fig_top, width="stretch")
    else:
        st.warning("ℹ️ **거주자 vs 외부 유입:** 공공 데이터는 **시군 단위까지만 제공**됩니다 (한국관광 데이터랩 현지인/외지인 방문자 및 유입 시군구 분포).")
        if not sg_df.empty:
            st.dataframe(sg_df, width="stretch", hide_index=True)
        else:
            st.info("아직 안동시·예천군 방문자 통계 파일이 등록되지 않았습니다. **[📤 유동인구 파일 올리기]** 탭에서 등록할 수 있습니다.")

# ------------------------------------------
# TAB 4: 선택한 업종의 주 고객층과 유동인구 매칭 해석
# ------------------------------------------
with tab4:
    st.subheader(f"🎯 업종 매칭 분석: `{scls_options[sel_scls]}`")
    if matched_profile is not None:
        m1, m2, m3 = st.columns(3)
        m1.metric("업종 주 타깃 연령대", str(matched_profile["target_ages"]).replace(";", ", "))
        m2.metric("업종 주 타깃 성별", str(matched_profile["target_gender"]))
        m3.metric("업종 핵심 피크 시간대", str(matched_profile["peak_hours"]).replace(";", ", "))
        st.caption(f"특성 메모: {matched_profile['note']}")

        if not fh_target.empty:
            top_age = fh_target.groupby("age_group")["pop_count"].sum().idxmax()
            target_age_list = str(matched_profile["target_ages"]).split(";")
            if any(t in str(top_age) for t in target_age_list):
                st.success(f"✅ **일치도 높음:** 선택 지역의 최다 유동인구 연령대(`{top_age}`)가 `{scls_options[sel_scls]}` 주 고객층({', '.join(target_age_list)})과 일치합니다.")
            else:
                st.warning(f"⚠️ **타깃 차이 있음:** 선택 지역의 최다 유동인구 연령대는 `{top_age}`이나, 해당 업종의 주 타깃은 `{', '.join(target_age_list)}`입니다.")
        elif not manual_df.empty and selected_adong_cd != "ALL":
            m_sub = manual_df[manual_df["adong_cd"] == selected_adong_cd]
            if not m_sub.empty:
                m_age = m_sub.iloc[0]["main_age_group"]
                m_peak = m_sub.iloc[0]["peak_time_slot"]
                st.info(f"✍️ 수동 입력된 `{adong_options[selected_adong_cd]}`의 주 이용 연령(`{m_age}`) 및 피크시간(`{m_peak}`)을 기준으로 비교했습니다.")
        else:
            st.info("실제 유동인구 데이터가 등록되면 업종 주 고객층과의 정량적 일치 여부를 자동으로 판정합니다.")
    else:
        st.info("해당 업종의 매칭 프로필이 `config/category_mapping.csv`에 등록되어 있지 않습니다.")

# ------------------------------------------
# TAB 5: 유동인구 파일 업로드 및 적재
# ------------------------------------------
with tab5:
    st.subheader("📤 유동인구 · 유출입 · 방문자 통계 파일 등록")
    st.markdown(
        """
        - **성남시 (경기데이터드림 `https://data.gg.go.kr`):** `유동인구_행정동 단위 집계` 및 `행정동간 유출인구` CSV
        - **안동시 (통계청 KOBIGS / 한국관광 데이터랩):** 인구감소지역 생활인구 CSV 또는 방문자 통계 XLSX/CSV
        - **예천군 (한국관광 데이터랩 `https://datalab.visitkorea.or.kr`):** 지역별 관광 현황 방문자 통계 XLSX/CSV
        """
    )
    upload_type = st.radio(
        "올리실 파일 종류 선택",
        [
            "성남시 - 행정동 단위 유동인구 집계 (CSV)",
            "성남시 - 행정동간 유출입 O-D 데이터 (CSV)",
            "안동시 - 인구감소지역 생활인구 / 관광데이터랩 (CSV/XLSX)",
            "예천군 - 한국관광 데이터랩 방문자 통계 (CSV/XLSX)",
        ]
    )
    up_file = st.file_uploader("파일 선택", type=["csv", "xlsx", "xls"], key="float_up")
    if up_file is not None and st.button("유동인구 데이터 검증 및 저장", type="primary"):
        save_path = Path(BASE_DIR) / "data" / "raw" / "floating" / up_file.name
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(up_file.getvalue())
        fc = FloatingPopulationCollector()
        try:
            if "유동인구 집계" in upload_type:
                cnt = fc.load_seongnam_hourly_csv(save_path)
            elif "유출입" in upload_type:
                cnt = fc.load_seongnam_od_csv(save_path)
            elif "안동시" in upload_type:
                cnt = fc.load_sigungu_stats_file(save_path, "andong", "생활인구/방문자통계")
            else:
                cnt = fc.load_sigungu_stats_file(save_path, "yecheon", "한국관광데이터랩")
            st.success(f"🎉 유동인구 데이터 {cnt:,}건이 검증을 통과해 저장되었습니다!")
            st.rerun()
        except Exception as e:
            st.error(f"❌ 파일 형식 검증 오류 (기존 데이터는 안전하게 유지되었습니다):\n\n{e}")

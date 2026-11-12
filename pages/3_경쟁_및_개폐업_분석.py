from pathlib import Path
import pandas as pd
import plotly.express as px
import streamlit as st

from src.config_loader import BASE_DIR
from src.analyzer import MarketAnalyzer
from src.db_manager import DatabaseManager
from src.collectors.license_collector import LicenseCollector

st.set_page_config(page_title="경쟁 및 개·폐업 분석", page_icon="📈", layout="wide")

analyzer = MarketAnalyzer()
db = DatabaseManager()
regions = analyzer.get_available_regions()

st.title("📈 경쟁 강도 · 개폐업 추이 · 프랜차이즈 비율 분석 (기능 4)")

# ==========================================
# 1. 사이드바: 지역 및 3단계 업종 선택
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
    st.warning(f"먼저 **{region_info['name']}** 점포 데이터를 수집해 주세요.")
    st.stop()

adong_df = analyzer.get_adong_list(sgg_codes, latest_ym)
adong_options = {"ALL": f"전체 행정동 ({len(adong_df)}개)"}
for _, r in adong_df.iterrows():
    adong_options[r["adong_cd"]] = f"{r['sigungu_nm']} {r['adong_nm']}"

selected_adong_cd = st.sidebar.selectbox(
    "2️⃣ 행정동/읍면 선택",
    options=list(adong_options.keys()),
    format_func=lambda k: adong_options[k],
)

st.sidebar.divider()
cat_tree = analyzer.get_category_tree(sgg_codes, latest_ym)
lcls_df = cat_tree[["inds_lcls_cd", "inds_lcls_nm"]].drop_duplicates().sort_values("inds_lcls_nm")
lcls_options = {"ALL": "전체 대분류", **dict(zip(lcls_df["inds_lcls_cd"], lcls_df["inds_lcls_nm"]))}
sel_lcls = st.sidebar.selectbox("대분류", options=list(lcls_options.keys()), format_func=lambda k: lcls_options[k])

mcls_sub = cat_tree[cat_tree["inds_lcls_cd"] == sel_lcls][["inds_mcls_cd", "inds_mcls_nm"]].drop_duplicates() if sel_lcls != "ALL" else pd.DataFrame(columns=["inds_mcls_cd", "inds_mcls_nm"])
mcls_options = {"ALL": "전체 중분류", **dict(zip(mcls_sub["inds_mcls_cd"], mcls_sub["inds_mcls_nm"]))}
sel_mcls = st.sidebar.selectbox("중분류", options=list(mcls_options.keys()), format_func=lambda k: mcls_options[k], disabled=(sel_lcls == "ALL"))

scls_sub = cat_tree[cat_tree["inds_mcls_cd"] == sel_mcls][["inds_scls_cd", "inds_scls_nm"]].drop_duplicates() if sel_mcls != "ALL" else pd.DataFrame(columns=["inds_scls_cd", "inds_scls_nm"])
scls_options = {"ALL": "전체 소분류", **dict(zip(scls_sub["inds_scls_cd"], scls_sub["inds_scls_nm"]))}
sel_scls = st.sidebar.selectbox("소분류", options=list(scls_options.keys()), format_func=lambda k: scls_options[k], disabled=(sel_mcls == "ALL"))

# ==========================================
# 2. 프랜차이즈 비율 분석 (현재 시점)
# ==========================================
st.subheader("🏢 프랜차이즈 vs 일반(개인) 점포 비율")
summary_df = analyzer.get_adong_summary_and_density(sgg_codes, latest_ym, sel_lcls, sel_mcls, sel_scls)
target_df = summary_df if selected_adong_cd == "ALL" else summary_df[summary_df["adong_cd"] == selected_adong_cd]

tot_cnt = int(target_df["store_count"].sum())
fr_cnt = int(target_df["franchise_count"].sum())
ind_cnt = tot_cnt - fr_cnt
fr_pct = round(fr_cnt * 100.0 / tot_cnt, 1) if tot_cnt > 0 else 0.0

c1, c2, c3 = st.columns(3)
c1.metric("전체 점포 수", f"{tot_cnt:,}개")
c2.metric("프랜차이즈(지점명 보유)", f"{fr_cnt:,}개", f"비율 {fr_pct}%")
c3.metric("일반(단독) 점포", f"{ind_cnt:,}개", f"비율 {round(100 - fr_pct, 1) if tot_cnt > 0 else 0.0}%")

col_fr1, col_fr2 = st.columns(2)
with col_fr1:
    if tot_cnt > 0:
        pie_df = pd.DataFrame({"구분": ["프랜차이즈(지점 보유)", "일반(단독)"], "점포수": [fr_cnt, ind_cnt]})
        fig_pie = px.pie(pie_df, names="구분", values="점포수", title="프랜차이즈 점유 비중")
        st.plotly_chart(fig_pie, width="stretch")
with col_fr2:
    top_fr = summary_df[summary_df["store_count"] > 0].sort_values("franchise_ratio", ascending=False).head(10)
    if not top_fr.empty:
        fig_bar = px.bar(top_fr, x="franchise_ratio", y="adong_nm", orientation="h", title="행정동별 프랜차이즈 비율 상위 10곳 (%)", labels={"franchise_ratio": "프랜차이즈 비율(%)", "adong_nm": "행정동"})
        fig_bar.update_layout(yaxis={"categoryorder": "total ascending"})
        st.plotly_chart(fig_bar, width="stretch")

# ==========================================
# 3. 방식 B: 상가정보 누적 시점별 점포 수 추이 (신규·소멸 비교)
# ==========================================
st.divider()
st.subheader("📊 [방식 B] 상가정보 시점별 점포 수 추이 (누적 보관 데이터 비교)")

sgg_sql = ",".join([f"'{c}'" for c in sgg_codes])
conds = [f"sigungu_cd IN ({sgg_sql})"]
params = []
if selected_adong_cd != "ALL":
    conds.append("adong_cd = ?")
    params.append(selected_adong_cd)
if sel_lcls != "ALL":
    conds.append("inds_lcls_cd = ?")
    params.append(sel_lcls)
if sel_mcls != "ALL":
    conds.append("inds_mcls_cd = ?")
    params.append(sel_mcls)
if sel_scls != "ALL":
    conds.append("inds_scls_cd = ?")
    params.append(sel_scls)

with db.get_connection(read_only=True) as con:
    trend_df = con.execute(f"""
        SELECT base_ym AS 기준연월, COUNT(*) AS 점포수,
               SUM(CASE WHEN is_franchise THEN 1 ELSE 0 END) AS 프랜차이즈수
        FROM stores_history
        WHERE {' AND '.join(conds)}
        GROUP BY base_ym
        ORDER BY base_ym
    """, params).df()

if len(trend_df) <= 1:
    st.info(
        f"현재 누적된 시점이 `{latest_ym}` 1개뿐입니다. 다음 분기/월 데이터가 자동 업데이트로 누적되면 "
        f"이곳에 시점별 점포 증감 추이 그래프와 신규 진입·소멸 점포 수가 자동으로 표시됩니다."
    )
    st.dataframe(trend_df, width="stretch", hide_index=True)
else:
    fig_tr = px.line(trend_df, x="기준연월", y="점포수", markers=True, title="시점별 동일 업종 점포 수 변화")
    st.plotly_chart(fig_tr, width="stretch")

    # 직전 시점과 최신 시점 간 신규 진입 / 소멸 점포 비교
    prev_ym, curr_ym = trend_df["기준연월"].iloc[-2], trend_df["기준연월"].iloc[-1]
    with db.get_connection(read_only=True) as con:
        prev_ids = set(con.execute(f"SELECT store_id FROM stores_history WHERE base_ym = ? AND {' AND '.join(conds)}", [prev_ym] + params).df()["store_id"])
        curr_ids = set(con.execute(f"SELECT store_id FROM stores_history WHERE base_ym = ? AND {' AND '.join(conds)}", [curr_ym] + params).df()["store_id"])
    new_cnt = len(curr_ids - prev_ids)
    closed_cnt = len(prev_ids - curr_ids)
    m1, m2 = st.columns(2)
    m1.metric(f"신규 진입 추정 ({prev_ym} → {curr_ym})", f"+{new_cnt:,}개")
    m2.metric(f"폐업/소멸 추정 ({prev_ym} → {curr_ym})", f"-{closed_cnt:,}개")

# ==========================================
# 4. 방식 A: 지방행정 인허가 데이터 기반 개업·폐업 추이
# ==========================================
st.divider()
st.subheader("🏛️ [방식 A] 지방행정 인허가 데이터 기반 연도별 개업·폐업 현황")

with db.get_connection(read_only=True) as con:
    lic_df = con.execute(f"SELECT * FROM licenses_history WHERE sigungu_cd IN ({sgg_sql}) OR sigungu_cd = ''").df()

if lic_df.empty:
    st.warning("아직 지방행정 인허가(개·폐업) CSV 파일이 등록되지 않았습니다. 공공데이터포털에서 인허가 CSV(예: 일반음식점, 휴게음식점, 미용업 등)를 내려받아 아래에 올려주세요.")
else:
    if selected_adong_cd != "ALL":
        lic_target = lic_df[lic_df["adong_cd"] == selected_adong_cd]
    else:
        lic_target = lic_df

    service_list = ["전체 인허가 업종"] + sorted(lic_target["service_nm"].dropna().unique().tolist())
    sel_srv = st.selectbox("인허가 업종 선택 (상가정보와 분류 체계가 다르므로 인허가 업종 기준으로 조회)", service_list)
    if sel_srv != "전체 인허가 업종":
        lic_target = lic_target[lic_target["service_nm"] == sel_srv]

    # 연도별 개업·폐업 집계 (최근 10년)
    open_years = lic_target[lic_target["open_date"].str.len() >= 4]["open_date"].str[:4].value_counts().rename("개업수")
    close_years = lic_target[lic_target["close_date"].str.len() >= 4]["close_date"].str[:4].value_counts().rename("폐업수")
    oc_df = pd.concat([open_years, close_years], axis=1).fillna(0).astype(int).reset_index()
    oc_df.columns = ["연도", "개업수", "폐업수"]
    oc_df = oc_df[oc_df["연도"] >= "2016"].sort_values("연도")

    if not oc_df.empty:
        fig_oc = px.bar(oc_df, x="연도", y=["개업수", "폐업수"], barmode="group", title=f"{sel_srv} 연도별 개업 vs 폐업 추이")
        st.plotly_chart(fig_oc, width="stretch")
    else:
        st.info("선택 조건에 해당하는 최근 개·폐업 이력이 없습니다.")

with st.expander("📤 지방행정 인허가 데이터(CSV) 올리기"):
    lic_file = st.file_uploader("인허가 CSV 파일 선택", type=["csv"], key="lic_up")
    if lic_file is not None and st.button("인허가 데이터 검증 및 저장", type="primary"):
        save_path = Path(BASE_DIR) / "data" / "raw" / "licenses" / lic_file.name
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(lic_file.getvalue())
        try:
            cnt = LicenseCollector().load_license_csv(save_path)
            st.success(f"🎉 인허가 데이터 {cnt:,}건 저장 완료!")
            st.rerun()
        except Exception as e:
            st.error(f"❌ 파일 형식 검증 오류: {e}")

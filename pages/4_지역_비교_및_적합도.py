import pandas as pd
import plotly.express as px
import streamlit as st

from src.analyzer import MarketAnalyzer
from src.db_manager import DatabaseManager

st.set_page_config(page_title="지역 비교 및 적합도 점수", page_icon="⚖️", layout="wide")

analyzer = MarketAnalyzer()
db = DatabaseManager()
regions = analyzer.get_available_regions()

st.title("⚖️ 지역 나란히 비교 & 상권 적합도 종합 점수 (기능 5 · 6)")

# ==========================================
# 1. 비교할 업종 선택 (3단계 전체 지원)
# ==========================================
st.sidebar.header("🏷️ 비교 기준 업종 선택")
first_sgg = list(regions.values())[0]["sigungu_codes"]
first_ym = analyzer.get_latest_base_ym(first_sgg)
if not first_ym:
    st.warning("먼저 최소 1개 지역의 점포 데이터를 수집해 주세요.")
    st.stop()

cat_tree = analyzer.get_category_tree(first_sgg, first_ym)
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
# 2. 기능 6: 종합 적합도 가중치 및 계산 방향 슬라이더
# ==========================================
st.sidebar.divider()
st.sidebar.header("🎛️ 종합 점수 기준 설정 (기능 6)")

score_mode = st.sidebar.radio(
    "점포 수(경쟁) 해석 방향",
    ["공백지 탐색형 (점포 적을수록 고득점)", "상권 검증형 (점포 모여있을수록 고득점)"],
)
w_comp = st.sidebar.slider("1) 동종 점포 경쟁/집적 가중치", 0, 100, 40, 5)
w_demand = st.sidebar.slider("2) 배후 수요(유동·주거인구) 가중치", 0, 100, 40, 5)
w_franchise = st.sidebar.slider("3) 프랜차이즈 침투율(낮을수록 유리) 가중치", 0, 100, 20, 5)

total_w = w_comp + w_demand + w_franchise
if total_w == 0:
    st.sidebar.error("가중치 합계가 0보다 커야 합니다.")
    st.stop()

# ==========================================
# 3. 세 지역 전체 행정동 지표 통합 수집
# ==========================================
all_rows = []
for r_key, r_info in regions.items():
    ym = analyzer.get_latest_base_ym(r_info["sigungu_codes"])
    if not ym:
        continue
    df_r = analyzer.get_adong_summary_and_density(r_info["sigungu_codes"], ym, sel_lcls, sel_mcls, sel_scls)
    df_r["region_key"] = r_key
    df_r["region_name"] = r_info["name"]
    all_rows.append(df_r)

if not all_rows:
    st.error("적재된 지역 데이터가 없습니다.")
    st.stop()

full_df = pd.concat(all_rows, ignore_index=True)

# 유동인구(성남) 및 수동입력(안동·예천) 결합
with db.get_connection(read_only=True) as con:
    fh_agg = con.execute("SELECT adong_cd, SUM(pop_count) AS float_pop FROM floating_hourly GROUP BY adong_cd").df()
    man_agg = con.execute("SELECT adong_cd, daily_avg_pop AS manual_pop FROM manual_floating_input").df()

full_df = full_df.merge(fh_agg, on="adong_cd", how="left").merge(man_agg, on="adong_cd", how="left")
full_df["effective_demand"] = full_df["float_pop"].fillna(full_df["manual_pop"]).fillna(full_df["population"]).fillna(full_df["total_all_stores"])
full_df["demand_source"] = full_df.apply(
    lambda r: "유동인구(공공)" if pd.notna(r["float_pop"]) else (
        "유동인구(수동입력)" if pd.notna(r["manual_pop"]) else (
            "주민등록인구" if pd.notna(r["population"]) else "전체상권활성도(총점포수)"
        )
    ),
    axis=1,
)
full_df["label"] = full_df["region_name"] + " > " + full_df["sigungu_nm"] + " " + full_df["adong_nm"]

# ==========================================
# 4. 기능 5: 2~3개 지역 나란히 비교
# ==========================================
st.subheader("🔍 기능 5: 2~3개 행정동(또는 상권) 나란히 비교")

default_picks = []
for rk in ["seongnam", "andong", "yecheon"]:
    sub = full_df[full_df["region_key"] == rk]
    if not sub.empty:
        default_picks.append(sub.iloc[0]["label"])

selected_labels = st.multiselect(
    "비교할 행정동을 2~3곳 선택하세요 (다른 시군구끼리도 비교 가능)",
    options=full_df["label"].tolist(),
    default=default_picks[:3],
    max_selections=3,
)

if len(selected_labels) < 2:
    st.info("2개 이상의 행정동을 선택하면 나란히 비교표와 점수 산출 근거가 표시됩니다.")
else:
    comp_df = full_df[full_df["label"].isin(selected_labels)].copy()

    # 정규화 점수 계산 (선택된 비교군 내 0~100점 스케일)
    def min_max_score(series: pd.Series, reverse: bool = False) -> pd.Series:
        s_min, s_max = series.min(), series.max()
        if s_max == s_min:
            return pd.Series([50.0] * len(series), index=series.index)
        norm = (series - s_min) / (s_max - s_min) * 100.0
        return (100.0 - norm) if reverse else norm

    is_avoid = "공백지 탐색형" in score_mode
    comp_df["score_comp"] = min_max_score(comp_df["store_count"], reverse=is_avoid).round(1)
    comp_df["score_demand"] = min_max_score(comp_df["effective_demand"], reverse=False).round(1)
    comp_df["score_fr"] = min_max_score(comp_df["franchise_ratio"], reverse=True).round(1)

    comp_df["total_score"] = (
        (comp_df["score_comp"] * w_comp + comp_df["score_demand"] * w_demand + comp_df["score_fr"] * w_franchise) / total_w
    ).round(1)
    comp_df = comp_df.sort_values("total_score", ascending=False)

    # 나란히 비교 카드
    cols = st.columns(len(comp_df))
    for i, (_, row) in enumerate(comp_df.iterrows()):
        with cols[i]:
            st.metric(f"🏆 {i+1}위: {row['adong_nm']}", f"{row['total_score']}점 / 100점", row["region_name"])
            st.write(f"• 선택 업종 점포 수: **{int(row['store_count']):,}개**")
            st.write(f"• 프랜차이즈 비율: **{row['franchise_ratio']}%**")
            st.write(f"• 인구 1천명당 점포: **{row['stores_per_1k_pop'] if pd.notna(row['stores_per_1k_pop']) else '미제공'}**")
            st.write(f"• 면적 1km²당 점포: **{row['stores_per_km2'] if pd.notna(row['stores_per_km2']) else '미제공'}**")
            st.caption(f"수요 기준: {row['demand_source']}")

    # 기능 6: 산출 근거 상세 표 & 누적 막대 그래프
    st.divider()
    st.subheader("🧮 기능 6: 상권 적합도 점수 산출 근거")
    st.caption(f"적용 기준: **{score_mode}** | 가중치 비율 = 경쟁({round(w_comp/total_w*100,1)}%) : 수요({round(w_demand/total_w*100,1)}%) : 프랜차이즈({round(w_franchise/total_w*100,1)}%)")

    basis_df = comp_df[[
        "label", "total_score", "score_comp", "score_demand", "score_fr",
        "store_count", "franchise_ratio", "demand_source"
    ]].rename(columns={
        "label": "지역 (시군구 > 행정동)",
        "total_score": "종합적합도(점)",
        "score_comp": "경쟁점수(0~100)",
        "score_demand": "수요점수(0~100)",
        "score_fr": "비프랜차이즈점수(0~100)",
        "store_count": "점포수(개)",
        "franchise_ratio": "프랜차이즈비율(%)",
        "demand_source": "수요지표 출처",
    })
    st.dataframe(basis_df, width="stretch", hide_index=True)

    fig_score = px.bar(
        comp_df,
        x="adong_nm",
        y=["score_comp", "score_demand", "score_fr"],
        barmode="group",
        labels={"value": "세부 항목 점수 (0~100)", "adong_nm": "행정동", "variable": "평가 항목"},
        title="비교 지역별 세부 평가 항목 점수 분포",
    )
    st.plotly_chart(fig_score, width="stretch")

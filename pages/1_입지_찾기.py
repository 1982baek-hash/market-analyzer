import pandas as pd
import streamlit as st

from src import scoring, ui

st.set_page_config(page_title="입지 찾기", page_icon="🎯", layout="wide")
ui.require_login()

st.title("🎯 입지 찾기")
st.caption("이 업종, 어느 동네가 좋을까? 같은 시·군 안 동네끼리 비교한 100점 만점 점수입니다.")

region = ui.pick_region()
regions = scoring.load_regions()

mode = st.sidebar.radio("업종", ["형님 매장 (커피 + 간식)", "다른 업종"], key="loc_mode")
if mode == "다른 업종":
    data = ui.region_data(region)
    cats = (data["stores"].groupby(["inds_lcls_nm", "inds_scls_cd", "inds_scls_nm"]).size()
            .rename("n").reset_index().query("n >= 10").sort_values(["inds_lcls_nm", "inds_scls_nm"]))
    labels = dict(zip(cats["inds_scls_cd"], cats["inds_lcls_nm"] + " › " + cats["inds_scls_nm"]))
    codes = list(labels)
    default = codes.index("I21201") if "I21201" in codes else 0
    scls = st.sidebar.selectbox("업종 검색 (소분류)", codes, index=default, format_func=lambda c: labels[c])
    dtype = st.sidebar.radio("손님이 주로 오는 곳", list(scoring.load_config()["demand_types"]),
                             help="생활밀착형: 동네 주민 · 오피스형: 직장인 · 방문·관광형: 외부 방문객")
    table, info = ui.category_scores(region, scls, dtype)
    comp_cols = {"comp_cnt": "동종 점포"}
else:
    table, info = ui.preset_scores(region)
    data = None
    comp_cols = {"cafe_cnt": "카페", "snack_cnt": "간식점"}

ui.period_badges(info["periods"])
axes_kr = {"demand": "수요", "competition": "경쟁", "stability": "안정성", "cost": "비용"}
st.caption("점수 구성 · " + " · ".join(f"{axes_kr[k]} {v}%" for k, v in info["used_axes"].items())
           + "  |  빠진 자료·대체 지표: " + ", ".join(dict.fromkeys(info["missing"])))

ranked = table[table["score"].notna()]
held = table[table["score"].isna()]

# ---------------- 1–3위 근거 카드
st.subheader(f"{regions[region]['name']} · {info['preset']} 추천 동네")
if ranked.empty:
    st.warning("점수를 매길 수 있는 동네가 없습니다. 모든 동네가 표본 부족으로 판단 보류입니다.")
cols = st.columns(3)
for i, (_, r) in enumerate(ranked.head(3).iterrows()):
    with cols[i]:
        with st.container(border=True):
            st.markdown(f"**{i + 1}위 · {r['adong_nm']}**")
            st.metric("입지 점수", f"{r['score']:.0f}점",
                      help=f"수요 {r['D']:.0f} · 경쟁 {r['C']:.0f}" + ("" if pd.isna(r.get('S')) else f" · 안정성 {r['S']:.0f}"))
            for line in r["reasons"]:
                st.markdown(f"- {line}")
            rl = ui.rent_line(r)
            st.caption(rl if rl else "💰 임대료: 부동산원 조사 상권이 아니라 제공되지 않음")

# ---------------- 지도 + 순위표
left, right = st.columns([5, 6])
with left:
    st.markdown("**점수 지도** · 초록일수록 좋음, 회색은 판단 보류")
    ui.dong_score_map(data or ui.region_data(region), table)
with right:
    st.markdown("**동네 순위표**")
    def txt(series, fmt="{:,.0f}"):
        """빈 값은 None 대신 '–'로 보여 주기 위해 글자로 바꿉니다."""
        return series.map(lambda v: "–" if pd.isna(v) else fmt.format(v))

    age_label = "10–29세" if mode != "다른 업종" else "타깃 연령"
    cols_map = {
        "순위": table["rank"].map(lambda v: "보류" if pd.isna(v) else f"{int(v)}"),
        "동네": table["adong_nm"],
        "점수": table["score"].round(0),
        "수요": txt(table["D"]),
        "경쟁": txt(table["C"]),
        "안정성": txt(table["S"]),
        **{v: txt(table[k]) for k, v in comp_cols.items()},
        age_label: txt(table["target_share"] * 100, "{:.0f}%"),
        "직장인": txt(table["worker"]),
        "3년 폐업률": txt(table["lic_closure3"] * 100, "{:.0f}%") if "lic_closure3" in table else None,
        "임대료(천원/㎡)": txt(table["rent_small"].fillna(table["rent_large"]), "{:.1f}") if "rent_small" in table else None,
        "판단 보류": table["hold_reason"],
    }
    show = pd.DataFrame({k: v for k, v in cols_map.items() if v is not None})
    st.dataframe(
        show, hide_index=True, use_container_width=True, height=420,
        column_config={"점수": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d")},
    )
    if len(held):
        st.caption(f"판단 보류 {len(held)}곳: 동종 점포가 3개 미만이거나 개·폐업 이력이 10건 미만이라 점수를 믿기 어려운 동네입니다.")

with st.expander("점수는 이렇게 계산해요"):
    st.markdown(
        """
- **수요**: 10–29세 비중, 학교·학원 근접(중·고등학교 수 60% + 학생 학원 수 40%), 직장인 수, 거주 인구, 유동(버스정류장 수)을 같은 시·군 동네끼리 백분위로 매겨 섞습니다.
- 동네 단위 유동인구는 무료로 공개되지 않아 **버스정류장 수**로 대신합니다. 최종 후보는 [소상공인365](https://bigdata.sbiz.or.kr)에서 시간대별 유동인구를 꼭 확인해 주세요.
- **경쟁**: 인구(주민+직장인) 1만 명당 카페·간식점이 적을수록 높습니다. 간식점이 시 평균보다 뚜렷이 적으면(LQ 0.7 미만) +10점, 카페 중 저가 브랜드 비율이 시 평균보다 높으면 −10점.
- **안정성**: 3~7년 전에 문을 연 카페·간식점(휴게음식점 인허가) 중 3년 안에 폐업한 비율이 낮을수록, 최근 1년 개업이 폐업보다 많을수록 높습니다.
- **임대료**는 한국부동산원이 조사하는 상권(안동구도심·옥동사거리·성남구시가지·모란·분당역세권·단대오거리역)에 걸친 동네만 표시하고, 점수에는 넣지 않았습니다. 상권 평균이라 실제 매물과 차이가 있으니 현장 시세로 꼭 확인해 주세요.
- 점수는 **기회가 보이는 곳**을 알려 줄 뿐 매출을 예측하지는 않습니다. 최종 결정 전에는 현장 방문과 임대 시세 확인이 필요합니다.
- 가중치는 저장소의 `config/business_types.yaml`에서 바꿀 수 있습니다.
"""
    )

import pandas as pd
import streamlit as st

from src import scoring, ui

st.set_page_config(page_title="업종 고르기", page_icon="🧭", layout="wide")
ui.require_login()

st.title("🧭 업종 고르기")
st.caption("이 동네에 뭐가 부족할까? 시 전체와 비교해 이 동네에만 유독 적은 업종(부족)과 몰려 있는 업종(포화)을 찾습니다.")

region = ui.pick_region()
data = ui.region_data(region)
dong_cd, dong_nm = ui.pick_dong(data)
ui.period_badges(data["periods"])

t = scoring.opportunity_table(data, dong_cd)
if t.empty:
    st.info("비교할 업종이 없습니다.")
    st.stop()

if t["opportunity"].isna().all():
    st.warning(f"{dong_nm}은(는) 점포가 30개 미만이라 업종별 비교가 흔들립니다. 아래 숫자는 참고만 해 주세요.")

# ---------------- 형님 매장 업종 먼저
cfg = scoring.load_config()["presets"]["coffee_snack"]
mine = t[t["inds_scls_cd"].isin(cfg["groups"]["cafe"]["scls"] + cfg["groups"]["snack"]["scls"])]
st.subheader(f"{dong_nm} · 커피·간식 업종 상태")
cols = st.columns(min(len(mine), 4) or 1)
for i, (_, r) in enumerate(mine.sort_values("lq").iterrows()):
    with cols[i % len(cols)]:
        icon = {"부족": "🟢", "포화": "🔴"}.get(r["status"], "⚪")
        st.metric(f"{icon} {r['inds_scls_nm']}", f"{int(r['dong_cnt'])}곳",
                  help="LQ = 이 동네에서 그 업종이 차지하는 비중 ÷ 시 전체 비중. 1보다 작으면 시 평균보다 적음.")
        st.caption(f"{r['status']} · LQ {r['lq']:.2f}")

fmt = {
    "inds_lcls_nm": "대분류", "inds_scls_nm": "업종", "dong_cnt": "이 동네", "city_cnt": "시 전체",
    "dong_per10k": "1만 명당(동네)", "city_per10k": "1만 명당(시)", "lq": "LQ", "status": "상태", "opportunity": "기회 점수",
}
colcfg = {
    "기회 점수": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d"),
    "LQ": st.column_config.NumberColumn(format="%.2f"),
    "1만 명당(동네)": st.column_config.NumberColumn(format="%.1f"),
    "1만 명당(시)": st.column_config.NumberColumn(format="%.1f"),
    "이 동네": st.column_config.NumberColumn(format="%d"),
    "시 전체": st.column_config.NumberColumn(format="%d"),
}

left, right = st.columns(2)
with left:
    st.subheader("🟢 기회 업종 (부족)")
    st.caption("시 전체에는 흔한데 이 동네에는 적은 업종. 기회 점수 = 시 전체 밀도 40% + 이 동네의 부족한 정도 60%")
    lcls = st.multiselect("대분류", sorted(t["inds_lcls_nm"].unique()), default=None, placeholder="전체")
    tt = t[t["status"] == "부족"]
    if lcls:
        tt = tt[tt["inds_lcls_nm"].isin(lcls)]
    st.dataframe(tt[list(fmt)].rename(columns=fmt).head(20), hide_index=True, use_container_width=True,
                 column_config=colcfg)
with right:
    st.subheader("🔴 포화 업종")
    st.caption("시 평균보다 1.5배 넘게 몰려 있는 업종. 새로 들어가면 경쟁이 셉니다.")
    sat = t[t["status"] == "포화"].sort_values("lq", ascending=False)
    st.dataframe(sat[list(fmt)].rename(columns=fmt).drop(columns=["기회 점수"]).head(20), hide_index=True,
                 use_container_width=True, column_config=colcfg)

st.caption("시 전체 점포가 10개 미만인 업종과 유흥주점·연료 소매는 비교에서 뺐습니다. 업종 이름을 고른 뒤 [입지 찾기 › 다른 업종]에서 동네 순위를 볼 수 있습니다.")

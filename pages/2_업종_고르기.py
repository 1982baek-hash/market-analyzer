import pandas as pd
import streamlit as st

from src import catsearch, scoring, ui

st.set_page_config(page_title="업종 고르기", page_icon="🧭", layout="wide")
ui.require_login()

st.title("🧭 업종 고르기")
st.caption("이 동네에 뭐가 부족할까? 시 전체와 비교해 이 동네에만 유독 적은 업종(부족)과 몰려 있는 업종(포화)을 찾습니다.")

region = ui.pick_region()
data = ui.region_data(region)
dong_cd, dong_nm = ui.pick_dong(data, main=True)
ui.period_badges(data["periods"])

t = scoring.opportunity_table(data, dong_cd)
if t.empty:
    st.info("비교할 업종이 없습니다.")
    st.stop()

# ---------------- 업종 검색
found: list[str] = []
q = st.text_input("🔎 업종 검색", key="cat_q", placeholder="예: 김밥, 만두, 커피, 안경 (쉼표로 여러 개)")
if q.strip():
    stores = data["stores"]
    cats = (stores.groupby("inds_scls_cd").agg(inds_scls_nm=("inds_scls_nm", "first"), inds_mcls_nm=("inds_mcls_nm", "first"),
                                               inds_lcls_nm=("inds_lcls_nm", "first"), n=("store_id", "size")).reset_index())
    found = catsearch.search(cats, q)
    if not found:
        st.info("찾는 업종이 없어요. 다른 말로 검색해 보세요 (예: '떡볶이' → 분식, '아메리카노' → 카페).")
    else:
        master = scoring.dong_master(data)
        here = stores[scoring.attach_dong_key(stores, master) == dong_cd]
        tt = t.set_index("inds_scls_cd")
        n_city = cats.set_index("inds_scls_cd")["n"]
        st.caption(f"{len(found)}개 업종 · {dong_nm} 기준 — 🟢 부족(기회) · ⚪ 보통 · 🔴 포화")
        cols = st.columns(3)
        for i, code in enumerate(found):
            c = cats[cats["inds_scls_cd"] == code].iloc[0]
            with cols[i % 3].container(border=True):
                if code in tt.index:
                    r = tt.loc[code]
                    icon = {"부족": "🟢", "포화": "🔴"}.get(r["status"], "⚪")
                    st.markdown(f"**{icon} {c['inds_scls_nm']}**  \n<small>{c['inds_lcls_nm']} › {c['inds_mcls_nm']}</small>",
                                unsafe_allow_html=True)
                    per = (f"1만 명당 {r['dong_per10k']:.1f}곳 (시 {r['city_per10k']:.1f}곳)"
                           if pd.notna(r["dong_per10k"]) else "")
                    st.markdown(f"{dong_nm} **{int(r['dong_cnt'])}곳** · 시 전체 {int(r['city_cnt'])}곳  \n"
                                f"{r['status']} (LQ {r['lq']:.2f})" + (f" · 기회 점수 **{r['opportunity']:.0f}**"
                                                                     if pd.notna(r["opportunity"]) else "")
                                + (f"  \n<small>{per}</small>" if per else ""), unsafe_allow_html=True)
                else:
                    n_here = int((here["inds_scls_cd"] == code).sum())
                    st.markdown(f"**{c['inds_scls_nm']}**  \n<small>{c['inds_lcls_nm']} › {c['inds_mcls_nm']}</small>",
                                unsafe_allow_html=True)
                    st.markdown(f"{dong_nm} **{n_here}곳** · 시 전체 {int(n_city[code])}곳  \n"
                                "<small>시 전체 가게가 적거나 비교에서 뺀 업종이라 부족·포화 판단은 생략</small>",
                                unsafe_allow_html=True)
                if int(n_city[code]) >= 10 and st.button("📍 어느 동네가 좋을까?", key=f"go_{code}",
                                                         help="입지 찾기에서 이 업종의 동네 순위를 봅니다"):
                    st.session_state["loc_mode"] = "다른 업종"
                    st.session_state["loc_scls"] = code
                    st.switch_page("pages/1_입지_찾기.py")
    st.divider()

if t["opportunity"].isna().all():
    st.warning(f"{dong_nm}은(는) 점포가 30개 미만이라 업종별 비교가 흔들립니다. 아래 숫자는 참고만 해 주세요.")

# ---------------- 업종 상태: 검색했으면 그 업종과 같은 중분류, 아니면 형님 매장(커피·간식)
if found:
    mc = data["stores"].drop_duplicates("inds_scls_cd").set_index("inds_scls_cd")["inds_mcls_nm"]
    groups = list(dict.fromkeys(mc.get(c) for c in found if c in mc.index))
    sib = [c for c in mc.index[mc.isin(groups)] if c in set(t["inds_scls_cd"])]
    focus = list(dict.fromkeys(found + sib))
    title = f"{dong_nm} · '{q.strip()}' 관련 업종 상태"
    note = "검색한 업종과 같은 묶음(" + ", ".join(groups) + ")의 업종까지 함께 보여 줍니다."
else:
    cfg = scoring.load_config()["presets"]["coffee_snack"]
    focus = cfg["groups"]["cafe"]["scls"] + cfg["groups"]["snack"]["scls"]
    title, note = f"{dong_nm} · 커피·간식 업종 상태", ""
mine = t[t["inds_scls_cd"].isin(focus)].copy()
mine["searched"] = mine["inds_scls_cd"].isin(found)
st.subheader(title)
tiles = [
    (f"{ {'부족': '🟢', '포화': '🔴'}.get(r['status'], '⚪') } {r['inds_scls_nm']}" + (" 🔎" if r["searched"] else ""),
     f"{int(r['dong_cnt'])}곳", f"{r['status']} · LQ {r['lq']:.2f} · 시 {int(r['city_cnt'])}곳")
    for _, r in mine.sort_values(["searched", "lq"], ascending=[False, True]).iterrows()
]
if found:
    here_cnt = data["stores"].loc[scoring.attach_dong_key(data["stores"], scoring.dong_master(data)) == dong_cd,
                                  "inds_scls_cd"].value_counts()
    nm = data["stores"].drop_duplicates("inds_scls_cd").set_index("inds_scls_cd")["inds_scls_nm"]
    tiles += [(f"⚪ {nm[c]} 🔎", f"{int(here_cnt.get(c, 0))}곳", "시 전체 가게가 적어 비교 생략")
              for c in found if c not in set(t["inds_scls_cd"]) and c in nm.index]
if tiles:
    ui.stat_tiles(tiles)
if note:
    st.caption(note + " 🔎 = 검색한 업종")
st.caption("LQ = 이 동네에서 그 업종이 차지하는 비중 ÷ 시 전체 비중. 1보다 작으면 시 평균보다 적음.")

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

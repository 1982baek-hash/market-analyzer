import pandas as pd
import streamlit as st

from src import catprof, mystore, pickmap, sbiz, scoring, spot, ui

st.set_page_config(page_title="입지 찾기", page_icon="🎯", layout="wide")
ui.require_login()

st.title("🎯 입지 찾기")
st.caption("이 업종, 어느 동네가 좋을까? 같은 시·군 안 동네끼리 비교한 100점 만점 점수입니다.")

region = ui.pick_region()
regions = scoring.load_regions()
data = ui.region_data(region)

# ---------------- ① 동네 → ② 업종
st.markdown("##### ① 동네")
dong_cd, dong_nm = ui.pick_dong(data, key="loc_dong", main=True)
st.markdown("##### ② 업종")
MODES = ["형님 매장 (커피 + 간식)", "다른 업종"]
if st.session_state.get("loc_mode") not in MODES:
    st.session_state["loc_mode"] = MODES[0]
mode = st.radio("업종", MODES, key="loc_mode", horizontal=True, label_visibility="collapsed")
if mode == "다른 업종":
    cats = (data["stores"].groupby(["inds_lcls_nm", "inds_scls_cd", "inds_scls_nm"]).size()
            .rename("n").reset_index().query("n >= 10").sort_values(["inds_lcls_nm", "inds_scls_nm"]))
    labels = dict(zip(cats["inds_scls_cd"], cats["inds_lcls_nm"] + " › " + cats["inds_scls_nm"]))
    codes = list(labels)
    if st.session_state.get("loc_scls") not in codes:
        st.session_state["loc_scls"] = "I21201" if "I21201" in codes else codes[0]
    c1, c2 = st.columns([3, 2])
    scls = c1.selectbox("🔍 업종 검색 (이름을 입력하세요 · 예: 치킨, 빵, 미용실)", codes,
                        format_func=lambda c: labels[c], key="loc_scls")
    dtype = c2.radio("손님이 주로 오는 곳", list(scoring.load_config()["demand_types"]), horizontal=True,
                     help="생활밀착형: 동네 주민 · 오피스형: 직장인 · 방문·관광형: 외부 방문객")
    table, info = ui.category_scores(region, scls, dtype)
    comp_cols = {"comp_cnt": "동종 점포"}
    spot_codes = {cats.set_index("inds_scls_cd").at[scls, "inds_scls_nm"]: [scls]}
else:
    table, info = ui.preset_scores(region)
    comp_cols = {"cafe_cnt": "카페", "snack_cnt": "간식점"}
    _pc = scoring.load_config()["presets"]["coffee_snack"]["groups"]
    spot_codes = {g["name"]: [str(c) for c in g["scls"]] for g in _pc.values()}

ui.period_badges(info["periods"])

# ---------------- 고른 동네의 점수
_sel = table[table["adong_cd"] == dong_cd]
if len(_sel):
    r0 = _sel.iloc[0]
    n_rank = int(table["score"].notna().sum())
    with st.container(border=True):
        st.markdown(f"**📍 {dong_nm}** · {info['preset']} 입지 점수 "
                    + ("**판단 보류** (" + str(r0["hold_reason"]) + ")" if pd.isna(r0["score"])
                       else f"**{r0['score']:.0f}점** — 점수를 매긴 {n_rank}개 동네 중 **{int(r0['rank'])}위**"))
        if r0["reasons"]:
            st.markdown("  \n".join(r0["reasons"]))

# ---------------- 점수 지도 (꾹 눌러 자리 고르기)
st.subheader("🗺️ 점수 지도 — 매장 열고 싶은 곳을 꾹 누르세요")
st.caption("동네 점은 초록일수록 좋음 · 회색은 판단 보류 · 휴대폰은 **길게 누르기**, PC는 **마우스를 꾹 누르거나 오른쪽 클릭**하면 그 자리 주변 상권을 보여 줘요.")
cent = scoring.dong_centroids(data)
tmap = table.set_index("adong_cd").join(cent, how="inner")
pts = []
for cd, r in tmap.iterrows():
    sc = r.get("score")
    top = pd.notna(r.get("rank")) and r["rank"] <= 3
    pts.append({"lat": float(r["lat"]), "lon": float(r["lon"]), "color": ui.score_color(sc),
                "size": 18 if pd.notna(sc) else 11, "ring": cd == dong_cd,
                "tip": f"{r['adong_nm']} · " + ("판단 보류" if pd.isna(sc) else f"{sc:.0f}점 ({int(r['rank'])}위)"),
                "label": (f"{int(r['rank'])}위 {r['adong_nm']}" if top else (r["adong_nm"] if cd == dong_cd else ""))})
center = (tmap.at[dong_cd, "lat"], tmap.at[dong_cd, "lon"]) if dong_cd in tmap.index else (tmap["lat"].mean(), tmap["lon"].mean())

pick_key = f"loc_pick_{region}"
radius = st.radio("고른 자리 반경", mystore.RADII, index=mystore.RADII.index(500) if 500 in mystore.RADII else 0,
                  format_func=lambda v: f"{v:,}m", horizontal=True, key="loc_radius")
pick = st.session_state.get(pick_key)
comps_pts = []
if pick:
    _a = spot.analyze(region, data, pick["lat"], pick["lon"], radius, spot_codes, table)
    palette = ["#e45756", "#f58518", "#4c78a8", "#54a24b"]
    gcol = {g: palette[i % 4] for i, g in enumerate(spot_codes)}
    for _, c in _a["comp"].iterrows():
        comps_pts.append({"lat": float(c["lat"]), "lon": float(c["lon"]), "color": gcol.get(c["group"], "#e45756"),
                          "tip": f"{c['store_name']} · {c['inds_scls_nm']} · {c['dist']:.0f}m"})
if pickmap.available():
    v = pickmap.pick_map(pts, center, zoom=14, pick=pick, radius=radius, comps=comps_pts, height=480,
                         key=f"pickmap_{region}")
    if v and v.get("t") != (pick or {}).get("t"):
        st.session_state[pick_key] = {"lat": float(v["lat"]), "lon": float(v["lon"]), "t": v.get("t"),
                                      "address": v.get("address")}
        st.rerun()
else:
    import folium
    from streamlit_folium import st_folium
    fmap = folium.Map(location=list(center), zoom_start=14)
    for p_ in pts:
        folium.CircleMarker([p_["lat"], p_["lon"]], radius=p_["size"] / 2, color=p_["color"], fill=True,
                            fill_color=p_["color"], fill_opacity=.85, weight=3 if p_["ring"] else 1, tooltip=p_["tip"]).add_to(fmap)
    if pick:
        folium.Circle([pick["lat"], pick["lon"]], radius=radius, color="#1f6feb", fill=True, fill_opacity=.07).add_to(fmap)
        folium.Marker([pick["lat"], pick["lon"]]).add_to(fmap)
        for c in comps_pts:
            folium.CircleMarker([c["lat"], c["lon"]], radius=4, color=c["color"], fill=True, tooltip=c["tip"]).add_to(fmap)
    out = st_folium(fmap, height=480, use_container_width=True, returned_objects=["last_clicked"], key=f"fol_{region}")
    lc = (out or {}).get("last_clicked")
    if lc and (not pick or abs(lc["lat"] - pick["lat"]) > 1e-7 or abs(lc["lng"] - pick["lon"]) > 1e-7):
        st.session_state[pick_key] = {"lat": float(lc["lat"]), "lon": float(lc["lng"]), "t": None}
        st.rerun()

# ---------------- 고른 자리 주변 상권
if pick:
    a = _a
    dnm = a["dong"][1] if a["dong"] else "?"
    addr = pick.get("address")
    if not addr and not a["near_all"].empty:  # 지도에서 주소를 못 받으면 가장 가까운 가게 주소로
        nb = a["near_all"].sort_values("dist").iloc[0]
        addr = f"{nb['rdnm_adr']} 근처" if isinstance(nb.get("rdnm_adr"), str) else None
    st.markdown(f"#### 📍 {addr or '고른 자리'} — 주변 상권")
    st.caption(f"{dnm} · 반경 {radius:,}m · 업종: {', '.join(spot_codes)} (위 ② 업종에서 바꿀 수 있어요)")
    tiles = []
    if a["row"] is not None:
        rr = a["row"]
        tiles.append((f"{dnm} 입지 점수", "판단 보류" if pd.isna(rr["score"]) else f"{rr['score']:.0f}점",
                      "" if pd.isna(rr["score"]) else f"점수 매긴 {a['n_ranked']}개 중 {int(rr['rank'])}위"))
    comp = a["comp"]
    for g in spot_codes:
        n = int((comp["group"] == g).sum()) if not comp.empty else 0
        low = int(comp.loc[comp["group"] == g, "lowcost"].sum()) if not comp.empty and "lowcost" in comp else 0
        tiles.append((f"반경 안 {g}", f"{n}곳", f"저가 커피 {low}곳" if low else ""))
    tiles += [("반경 안 전체 가게", f"{len(a['near_all'])}곳", "상가정보"),
              ("버스정류장 · 학교", f"{a['ctx']['bus']} · {a['ctx']['schools']}곳", "대학 포함")]
    if a["opened"] is not None:
        tiles.append(("최근 1년 새로 연 · 닫은 곳", f"{len(a['opened'])} · {len(a['closed'])}곳", "같은 업종 인허가"))
    ui.stat_tiles(tiles)
    st.markdown("\n".join(f"- {t}" for t in spot.lines(a, spot_codes, radius)))
    if a["row"] is not None:
        rl = ui.rent_line(a["row"])
        if rl:
            st.caption(rl)
    if not comp.empty:
        with st.expander(f"반경 안 같은 업종 가게 {len(comp)}곳 보기"):
            st.dataframe(comp.sort_values("dist").assign(거리=lambda d: d["dist"].round(-1).astype(int).astype(str) + "m")[
                ["store_name", "inds_scls_nm", "거리", "rdnm_adr"]].rename(
                columns={"store_name": "상호", "inds_scls_nm": "업종", "rdnm_adr": "주소"}),
                hide_index=True, use_container_width=True)
    b1, b2 = st.columns(2)
    if b1.button("📌 이 자리를 '내 매장'에서 자세히 보기", use_container_width=True,
                 help="경쟁점 5년 추이, 새로 생긴·문 닫은 가게, 자리 진단을 볼 수 있어요"):
        places = st.session_state.setdefault("my_session_places", [])
        places.append({"region": region, "name": f"지도에서 고른 자리 ({dnm})", "lat": pick["lat"], "lon": pick["lon"],
                       "radius": radius, "group": "both", **({"scls": next(iter(spot_codes.values()))[0]} if mode == "다른 업종" else {})})
        st.session_state["my_place_pick_last"] = True
        st.switch_page("pages/4_내_매장.py")
    if b2.button("✖ 고른 자리 지우기", use_container_width=True):
        st.session_state.pop(pick_key, None)
        st.rerun()

# ---------------- 업종 요약 카드
others = {rk: ui.region_data(rk) for rk in regions}
city_nm = regions[region]["name"].split()[-1]
if mode == "다른 업종":
    cp = catprof.profile(region, [scls], info["preset"], table, others, "comp_cnt")
    ui.summary_card(f"{city_nm} · {labels[scls]} — 이 업종 장사 성격", cp)
    if cp["lic_note"]:
        st.caption("개·폐업 숫자는 음식점 인허가 기준 — " + cp["lic_note"])
else:
    pc = scoring.load_config()["presets"]["coffee_snack"]
    c1, c2 = st.columns(2)
    for col, gk, cc in ((c1, "cafe", "cafe_cnt"), (c2, "snack", "snack_cnt")):
        g = pc["groups"][gk]
        with col:
            ui.summary_card(f"{city_nm} · {g['name']} — 이 업종 장사 성격",
                            catprof.profile(region, g["scls"], g["name"], table, others, cc, show_top=gk == "cafe"))
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

# ---------------- 순위표
st.markdown("**동네 순위표**")
if True:
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
        "가까운 대학": table["univ_near"].fillna("") if "univ_near" in table else None,
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

# ---------------- 상권 비교 (소상공인365에서 옮겨 적은 동네)
cmp = sbiz.compare_table(region, table if mode != "다른 업종" else None)
if len(cmp):
    st.markdown("**동네 비교 — 유동인구·카페 매출** (소상공인365)")
    st.dataframe(
        cmp, hide_index=True, use_container_width=True,
        column_config={
            "일평균 유동인구(명)": st.column_config.NumberColumn(format="localized"),
            "카페 월매출(만원)": st.column_config.NumberColumn(format="localized"),
            "매출 1년 증감 %": st.column_config.NumberColumn(format="%+d%%"),
            "배달 월매출(만원)": st.column_config.NumberColumn(format="localized"),
            "입지 점수": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d"),
        },
    )
    st.caption(f"소상공인365 상세분석 {sbiz.period(region)} 기준 (통신·카드 자료 기반 추정) · 카페 월매출은 한 곳당 평균 · "
               "동네마다 따로 저장한 값이라 점수에는 섞지 않았습니다. 동네를 고르면 '동네 리포트'에서 그 동네 자료를 자세히 볼 수 있어요.")

with st.expander("점수는 이렇게 계산해요"):
    st.markdown(
        """
- **수요**: 10–29세 비중 20%, 학교·학원 근접 15%(중·고등학교 수 60% + 학생 학원 수 40%), **대학 근접 15%**, 직장인 수 15%, 유동(버스정류장 수) 20%, 거주 인구 15%를 같은 시·군 동네끼리 매겨 섞습니다.
- **대학 근접**: 대학교·전문대·대학원·방송통신대 학습관·폴리텍이 동네 상권 중심에서 1.5km 안에 있으면 가까울수록 점수를 줍니다(캠퍼스 경계까지 거리). 4년제 본교 1, 전문대 0.6–0.7, 대학원·폴리텍 0.3, 방통대 학습관 0.2 크기로 더하고, 캠퍼스가 그 동네 안에 있으면 멀어도 절반은 줍니다. '다른 업종'은 20대가 타깃인 업종일 때만 넣습니다.
- 동네 단위 유동인구는 무료로 공개되지 않아 **버스정류장 수**로 대신합니다. 최종 후보는 [소상공인365](https://bigdata.sbiz.or.kr)에서 시간대별 유동인구를 꼭 확인해 주세요.
- **경쟁**: 인구(주민+직장인) 1만 명당 카페·간식점이 적을수록 높습니다. 간식점이 시 평균보다 뚜렷이 적으면(LQ 0.7 미만) +10점, 카페 중 저가 브랜드 비율이 시 평균보다 높으면 −10점.
- **안정성**: 3~7년 전에 문을 연 카페·간식점(휴게음식점 인허가) 중 3년 안에 폐업한 비율이 낮을수록, 최근 1년 개업이 폐업보다 많을수록 높습니다.
- **임대료**는 한국부동산원이 조사하는 상권(안동구도심·옥동사거리·성남구시가지·모란·분당역세권·단대오거리역)에 걸친 동네만 표시하고, 점수에는 넣지 않았습니다. 상권 평균이라 실제 매물과 차이가 있으니 현장 시세로 꼭 확인해 주세요.
- 점수는 **기회가 보이는 곳**을 알려 줄 뿐 매출을 예측하지는 않습니다. 최종 결정 전에는 현장 방문과 임대 시세 확인이 필요합니다.
- 가중치는 저장소의 `config/business_types.yaml`에서 바꿀 수 있습니다.
"""
    )

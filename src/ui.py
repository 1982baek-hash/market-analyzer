"""화면 공통: 가족 비밀번호, 지역·동네 선택, 데이터 캐시, 지도."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src import navermap, scoring
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


def pick_dong(data: dict, key: str = "dong", main: bool = False) -> tuple[str, str]:
    master = scoring.dong_master(data).sort_values(["sigungu_nm", "adong_nm"])
    labels = dict(zip(master["adong_cd"], master["adong_nm"]))
    if master["sigungu_nm"].nunique() > 1:  # 성남시는 구 이름도 함께
        labels = dict(zip(master["adong_cd"], master["sigungu_nm"].str.split().str[-1] + " " + master["adong_nm"]))
    codes = list(labels)
    current = st.session_state.get("sel_dong")
    box = st if main else st.sidebar
    sel = box.selectbox("🔍 동네 검색 (이름을 입력하세요)" if main else "동네", codes,
                        index=codes.index(current) if current in codes else 0,
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
    pts = []
    for _, r in t.iterrows():
        s = r.get("score")
        top = pd.notna(r.get("rank")) and r["rank"] <= 3
        pts.append({"lat": r["lat"], "lon": r["lon"], "color": score_color(s), "size": 18 if pd.notna(s) else 11,
                    "tooltip": f"{r['adong_nm']} · " + ("판단 보류" if pd.isna(s) else f"{s:.0f}점"),
                    "label": f"{int(r['rank'])}위 {r['adong_nm']}" if top else ""})
    if navermap.render(pts, height=height):
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


def rent_line(r) -> str:
    """조사된 상권이면 '㎡당 월 임대료와 10평(33㎡) 기준 대략 금액' 한 줄."""
    import math
    if not isinstance(r.get("rent_district"), str):
        return ""
    small, large = r.get("rent_small"), r.get("rent_large")
    val, kind = (small, "소규모") if small is not None and not (isinstance(small, float) and math.isnan(small)) else (large, "중대형")
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return ""
    return (f"💰 {kind} 상가 임대료 ㎡당 월 {val:.1f}천원 → 10평(33㎡) 약 {val * 33 / 10:.0f}만원 "
            f"({r['rent_district']} 상권 평균, {r.get('rent_period', '')})")


def stat_tiles(tiles: list[tuple]) -> None:
    """숫자 카드 묶음 [(제목, 값, 작은 설명)] — 휴대폰에서는 2칸, 넓은 화면에서는 한 줄.
    (st.metric을 st.columns에 넣으면 휴대폰에서 한 줄에 하나씩 쌓여 화면이 길어짐)"""
    import html as _h
    cells = "".join(
        f"<div class='mt'><div class='ml'>{_h.escape(str(a))}</div><div class='mv'>{_h.escape(str(b))}</div>"
        f"<div class='mn'>{_h.escape(str(c or ''))}</div></div>"
        for a, b, *rest in tiles for c in [rest[0] if rest else ""])
    st.markdown(
        "<style>.mtg{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin:4px 0 12px}"
        ".mt{border:1px solid rgba(128,128,128,.25);border-radius:10px;padding:10px 12px}"
        ".ml{font-size:.85rem;opacity:.75}.mv{font-size:1.6rem;font-weight:700;line-height:1.3}"
        ".mn{font-size:.72rem;opacity:.6;min-height:1em}</style>"
        f"<div class='mtg'>{cells}</div>", unsafe_allow_html=True)


def summary_card(title: str, prof: dict) -> None:
    """'이 동네 장사 성격' 요약 카드 (휴대폰에서도 한눈에)."""
    import html as _h
    e = _h.escape
    tags = "".join(f"<span class='sc-tag'>{e(t)}</span>" for t in prof.get("tags", []))
    facts = "".join(f"<div class='sc-f'><div class='sc-fl'>{e(a)}</div><div class='sc-fv'>{e(b)}</div>"
                    f"<div class='sc-fn'>{e(c)}</div></div>" for a, b, c in prof.get("facts", []))
    tips = "".join(f"<li>{e(t)}</li>" for t in prof.get("tips", []))
    warns = "".join(f"<li>{e(t)}</li>" for t in prof.get("warns", []))
    st.markdown(
        "<style>.sc{border:1px solid rgba(228,87,86,.35);border-radius:14px;padding:14px 16px;margin:6px 0 14px;"
        "background:rgba(228,87,86,.05)}.sc-t{font-size:.85rem;opacity:.7}.sc-h{font-size:1.35rem;font-weight:800;margin:2px 0 8px;line-height:1.35}"
        ".sc-tag{display:inline-block;padding:3px 9px;margin:0 6px 6px 0;border-radius:999px;background:rgba(128,128,128,.14);font-size:.82rem}"
        ".sc-fs{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin:6px 0}"
        ".sc-f{background:rgba(128,128,128,.07);border-radius:10px;padding:8px 10px}.sc-fl{font-size:.78rem;opacity:.7}"
        ".sc-fv{font-size:1.25rem;font-weight:700}.sc-fn{font-size:.72rem;opacity:.6}"
        ".sc ul{margin:6px 0 0 1.1em;padding:0}.sc li{margin:3px 0;font-size:.92rem}.sc-w li{color:#c0392b}"
        ".sc-s{font-weight:700;font-size:.85rem;margin-top:8px}</style>"
        f"<div class='sc'><div class='sc-t'>{e(title)}</div><div class='sc-h'>{e(prof.get('headline') or '')}</div>"
        f"<div>{tags}</div><div class='sc-fs'>{facts}</div>"
        + (f"<div class='sc-s'>💡 장사 힌트</div><ul>{tips}</ul>" if tips else "")
        + (f"<div class='sc-s'>⚠️ 주의</div><ul class='sc-w'>{warns}</ul>" if warns else "")
        + "</div>", unsafe_allow_html=True)


# ---------------------------------------------------------------- 상권 주변 뉴스
@st.cache_data(ttl=6 * 3600, show_spinner="최근 뉴스를 찾는 중입니다...")
def _news(queries: tuple, must: tuple, days: int, topic_only: bool, keys: tuple, ver: int = 3):
    from src import news
    items, errors = news.collect(list(queries), list(must) or None, days, keys if all(keys) else None)
    if errors and len(errors) >= len(queries) * (2 if all(keys) else 1):
        raise RuntimeError("; ".join(errors))  # 전부 실패한 결과는 캐시하지 않음 (다음에 다시 시도)
    if topic_only:
        items = [i for i in items if i["topic"]]
    return items, errors


NEWS_CACHE_VER = 4  # 뉴스 코드를 고치면 올려서 6시간 캐시를 비움


def _news_safe(*args):
    try:
        return _news(*args, ver=NEWS_CACHE_VER)
    except Exception as e:  # noqa: BLE001
        return [], [str(e)]


def _city_short(region_key: str) -> str:
    nm = scoring.load_regions()[region_key]["name"].split()[-1]
    return nm[:-1] if len(nm) > 2 and nm[-1] in "시군" else nm


def _dong_terms(dong_nm: str) -> list[str]:
    import re
    base = re.sub(r"\d+", "", dong_nm)
    base = base[:-1] if len(base) > 2 and base[-1] in "동면읍" else base
    return list(dict.fromkeys([dong_nm, base] if len(base) >= 2 else [dong_nm]))


def news_section(region_key: str, dong_nm: str, extra: list[str] | None = None, title: str = "📰 상권 주변 주요 뉴스",
                 limit: int = 6) -> None:
    """이 동네 이름이 들어간 기사 + 시 전체 상권·개발 기사 (최근 90일, 장사 관련만 추림)."""
    from src import news
    keys = (get_api_key("NAVER_SEARCH_CLIENT_ID"), get_api_key("NAVER_SEARCH_CLIENT_SECRET"))
    city = _city_short(region_key)
    terms = _dong_terms(dong_nm) + [e for e in (extra or []) if e]
    # 동 이름 그대로는 단독으로, 줄인 이름(예: 강남)은 시 이름(안동)과 함께 있을 때만 이 동네 기사로 봄
    must = tuple(t if (t == dong_nm or t in (extra or [])) else (t, city) for t in terms)
    dong_items, e1 = _news_safe(tuple(f"{city} {t}" for t in terms[:4]), must, 90, False, keys)
    city_q = f"{city} (상권 OR 상가 OR 개점 OR 착공 OR 개통 OR 입주 OR 분양 OR 전통시장 OR 축제 OR 대학)"
    city_items, e2 = _news_safe((city_q,), (city,), 60, True, keys)
    shown = {i["link"] for i in dong_items}
    city_items = [i for i in city_items if i["link"] not in shown]

    st.subheader(title)
    t1, t2 = st.tabs([f"{dong_nm} ({len(dong_items)})", f"{city} 전체 상권·개발 ({len(city_items)})"])

    def show(items, empty, q):
        if not items:
            st.caption(empty + f" · [Google 뉴스에서 직접 보기]({news.search_link(q)})")
            return

        def line(i):
            d = i["date"].strftime("%y.%m.%d") if i["date"] else ""
            tag = f"`{i['topic']}` " if i["topic"] else ""
            t = i["title"].replace("[", "(").replace("]", ")")
            return f"- {tag}[{t}]({i['link']})  \n  <small>{i['source']} · {d}</small>"

        st.markdown("\n".join(line(i) for i in items[:limit]), unsafe_allow_html=True)
        if len(items) > limit:
            with st.expander(f"기사 {len(items) - limit}개 더 보기"):
                st.markdown("\n".join(line(i) for i in items[limit:limit + 20]), unsafe_allow_html=True)

    with t1:
        show(dong_items, "최근 90일 동안 이 동네 이름이 들어간 장사 관련 기사가 없어요", f"{city} {dong_nm}")
    with t2:
        show(city_items, "최근 60일 동안 찾은 기사가 없어요", city_q)
    errs = e1 + e2
    st.caption("Google 뉴스" + (" · 네이버 뉴스" if all(keys) else "") + " 검색 결과에서 장사와 관련된 기사만 골라 분류했어요 "
               "(동 행정 소식·사건사고 제외 · 6시간마다 새로 찾음)."
)
    if errs:
        with st.expander("⚠️ 일부 뉴스를 불러오지 못했어요 (자세히)"):
            st.caption("\n\n".join(dict.fromkeys(errs)))

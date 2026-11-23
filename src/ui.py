"""화면 공통: 가족 비밀번호, 새 경쟁점 알림 팝업, 지역·동네 선택, 데이터 캐시, 지도."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src import navermap, scoring
from src.config_loader import get_api_key

DEFAULT_REGION = "andong"
ALERT_PAGE = "pages/6_새_경쟁점.py"
ALERT_POPUP_MAX = 6  # 팝업에 한 번에 보여 줄 가게 수 (나머지는 '자세히 보기'에서)


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


# ---------------------------------------------------------------- 새 경쟁점 알림 (앱을 열 때 뜨는 팝업)
@st.cache_data(show_spinner=False)
def _alert_items(signature: str, stores_key: tuple, day: str) -> pd.DataFrame:
    from src import alerts, mystore
    return alerts.popup_items(stores=mystore.load_my_stores(), loader=region_data)


def alert_items() -> pd.DataFrame:
    """팝업으로 알릴 새 경쟁점 (수집 자료·내 매장·날짜가 바뀌면 다시 계산)."""
    from datetime import date

    from src import mystore
    stores_key = tuple((str(s.get("name")), s.get("region"), round(s["lat"], 5), round(s["lon"], 5))
                       for s in mystore.load_my_stores())
    return _alert_items(scoring.mart_signature(), stores_key, date.today().isoformat())


def alert_link_label() -> str:
    """첫 화면의 '새 경쟁점' 바로가기 문구 (어느 매장 기준인지 함께 표시)."""
    from src import alerts, mystore
    stores = mystore.load_my_stores()
    if not stores:
        return "**새 경쟁점** — 내 매장 위치를 저장하면 주변에 새로 생긴 식당·카페·간식·편의점을 알려 드려요"
    names, n = alerts.store_names(stores), len(alert_items())
    cfg = alerts.settings()
    if n:
        return f"**새 경쟁점** — {names} 주변에 최근 {int(cfg['days'])}일 사이 새로 생긴 식당·카페·간식·편의점 {n}곳"
    return f"**새 경쟁점** — {names} 주변에 최근 새로 생긴 식당·카페·간식·편의점 없음 (눌러서 자세히 보기)"


def _alert_command(**cmd) -> None:
    """다음 화면 그리기 때 브라우저 저장소(확인한 알림 목록)에 반영할 명령을 걸어 둡니다."""
    import uuid
    st.session_state["_alert_cmd"] = {"nonce": uuid.uuid4().hex, **cmd}


def alert_reset() -> None:
    """이 기기의 '확인한 알림' 기록을 지워 팝업이 다시 뜨게 합니다 ('새 경쟁점' 화면의 버튼용)."""
    _alert_command(reset=True)
    st.session_state.pop("_alert_open", None)
    st.session_state.pop("_alert_done", None)


def _alert_close(ack: bool, ids: list | None = None) -> None:
    """팝업을 닫습니다. ack=True면 그 가게들을 이 기기에서 다시 알리지 않습니다."""
    ss = st.session_state
    open_ids = ss.pop("_alert_open", None)
    ids = ids if ids is not None else (open_ids or [])
    ss["_alert_done"] = True  # 이번 접속에서는 다시 띄우지 않음
    if ack and ids:
        _alert_command(ack=list(ids))


def _alert_body(items: pd.DataFrame) -> None:
    import html as _html

    from src import alerts
    cfg = alerts.settings()
    ids = list(items["mgmt_no"])
    names = list(dict.fromkeys(n for n in items["near_name"] if isinstance(n, str)))
    if not names:  # 좌표가 없어 가까운 매장을 못 붙인 가게만 있을 때
        from src import mystore
        names = [alerts.store_names(mystore.load_my_stores())]
    where = f"{' · '.join(names)} 반경 {int(cfg['radius_m']):,}m 안"
    st.markdown(f"**{where}**에 최근 **{int(cfg['days'])}일** 사이 새로 영업 신고한 식당·카페·간식·편의점이 "
                f"**{len(items)}곳** 있어요.")
    lines = []
    for _, r in items.head(ALERT_POPUP_MAX).iterrows():
        dist = alerts.dist_text(r)
        info = " · ".join(x for x in (alerts.group_label(r), f"{r['open_dt'].month}월 {r['open_dt'].day}일 신고", r["adong_nm"]) if x)
        name = _html.escape(str(r["biz_name"])).replace("[", "(").replace("]", ")")
        lines.append(f"- **{name}**  \n  {info}" + (f"  \n  📍 {_html.escape(dist)}" if dist else "")
                     + f"  \n  <small>{_html.escape(str(r['addr']))}</small>")
    st.markdown("\n".join(lines), unsafe_allow_html=True)
    if len(items) > ALERT_POPUP_MAX:
        st.caption(f"외 {len(items) - ALERT_POPUP_MAX}곳은 **자세히 보기**에서 볼 수 있어요.")
    c1, c2 = st.columns(2)
    if c1.button("확인했어요", type="primary", use_container_width=True, key="_alert_ok"):
        _alert_close(True, ids)
        st.rerun()
    if c2.button("자세히 보기", use_container_width=True, key="_alert_more"):
        _alert_close(True, ids)
        st.switch_page(ALERT_PAGE)
    st.caption("**확인했어요**를 누르면 이 가게들은 이 기기에서 다시 알리지 않아요. "
               "그냥 닫으면 다음에 앱을 열 때 다시 알려 드려요.")


def competitor_alert() -> None:
    """새 경쟁점이 생겼으면 팝업으로 알립니다. 모든 화면에서 require_login() 바로 다음에 부릅니다.

    확인한 가게는 브라우저(localStorage)에 적어 두어, 같은 기기에서는 한 번만 알립니다.
    """
    import inspect

    from src import alerts, seenstore
    ss = st.session_state
    try:
        if not alerts.settings().get("enabled", True):
            return
        cmd = ss.get("_alert_cmd")
        open_ids = ss.get("_alert_open")
        # 이번 접속에서 이미 알렸거나 알릴 가게가 없으면 아무것도 하지 않는다.
        # (브라우저 저장소를 읽으면 화면을 한 번 더 그리게 되므로 꼭 필요할 때만 읽는다)
        if not cmd and open_ids is None and ss.get("_alert_done"):
            return
        items = alert_items()
        if not cmd and items.empty:
            return
        seen = seenstore.sync(cmd)  # 브라우저가 대답하기 전(첫 실행)에는 None
        if seen is None:
            return
        if cmd and seen.get("applied") == cmd["nonce"]:
            ss.pop("_alert_cmd", None)
        elif cmd:
            return  # 방금 누른 '확인'·'다시 보기'가 브라우저에 반영된 뒤에 판단
        if open_ids is None:
            if ss.get("_alert_done"):
                return
            unseen = items[~items["mgmt_no"].isin(set(seen.get("seen") or []))]
            if unseen.empty:
                ss["_alert_done"] = True  # 모두 확인한 가게들: 이번 접속에서는 더 확인하지 않음
                return
            open_ids = ss["_alert_open"] = list(unseen["mgmt_no"])
        show = items[items["mgmt_no"].isin(open_ids)]
        if show.empty:  # 띄워 둔 사이 자료가 바뀌어 대상이 없어진 경우
            ss.pop("_alert_open", None)
            return

        dialog = getattr(st, "dialog", None) or getattr(st, "experimental_dialog", None)
        title = f"🆕 새 경쟁점 {len(show)}곳"
        if dialog is None:  # 아주 오래된 Streamlit: 팝업 대신 화면 맨 위 안내 상자
            ss.pop("_alert_open", None)
            ss["_alert_done"] = True
            st.warning(f"**{title}** — 왼쪽 메뉴의 **새 경쟁점** 화면에서 확인해 주세요.")
            return
        kwargs = {}
        if "on_dismiss" in inspect.signature(dialog).parameters:
            # X·바깥을 눌러 닫으면 이번 접속에서만 닫음 (다음에 앱을 열면 다시 알림).
            # 닫기 전까지는 화면이 다시 그려져도 _alert_open 덕분에 팝업이 계속 떠 있다.
            kwargs["on_dismiss"] = lambda: _alert_close(False)
        else:
            # 닫힘을 알 수 없는 예전 버전: 한 번 띄우고 나면 이번 접속에서는 다시 띄우지 않음
            ss.pop("_alert_open", None)
            ss["_alert_done"] = True

        def _competitor_alert_dialog():
            _alert_body(show)

        dialog(title, **kwargs)(_competitor_alert_dialog)()
    except Exception as e:  # noqa: BLE001  알림이 고장 나도 본 화면은 떠야 한다
        print(f"[새 경쟁점 알림 오류] {type(e).__name__}: {e}", flush=True)
        ss.pop("_alert_open", None)
        ss["_alert_done"] = True


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


def _home_store(region_key: str | None = None) -> dict | None:
    """기본으로 볼 매장 = 저장된 내 매장 중 첫 번째 (지역을 주면 그 지역의 첫 번째)."""
    from src import mystore
    stores = mystore.load_my_stores(region_key)
    return stores[0] if stores else None


def home_dong(data: dict) -> tuple[str, str] | None:
    """저장된 내 매장이 있는 동네 (행정동 코드, 이름). 그 지역에 매장이 없으면 None."""
    from src import mystore
    st_ = _home_store(data.get("region_key"))
    if not st_:
        return None
    try:
        return mystore.dong_of_point(data, st_["lat"], st_["lon"])
    except Exception:  # noqa: BLE001
        return None


def pick_region(key: str = "region") -> str:
    regions = scoring.load_regions()
    keys = list(regions)
    # 기본 지역 = 내 매장이 있는 지역. 화면을 옮겨 다녀도 고른 지역을 유지
    home = _home_store()
    default = home["region"] if home and home.get("region") in keys else (DEFAULT_REGION if DEFAULT_REGION in keys else keys[0])
    current = st.session_state.get("sel_region", default)
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
    # 기본 동네 = 내 매장(형님 가게)이 있는 동네. 맨 앞에 두고 매장 이름을 붙여 찾기 쉽게 한다
    home = home_dong(data)
    home_cd = home[0] if home and home[0] in labels else None
    if home_cd:
        shop = str((_home_store(data.get("region_key")) or {}).get("name") or "내 매장")
        labels[home_cd] = f"{labels[home_cd]} (📍 {shop})"
        codes = [home_cd] + [c for c in codes if c != home_cd]
    current = st.session_state.get("sel_dong")
    if current not in codes:
        current = home_cd or codes[0]
    box = st if main else st.sidebar
    sel = box.selectbox("🔍 동네 검색 (이름을 입력하세요)" if main else "동네", codes,
                        index=codes.index(current),
                        format_func=lambda c: labels[c], key=key)
    st.session_state["sel_dong"] = sel
    return sel, labels[sel].split(" (📍")[0]


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

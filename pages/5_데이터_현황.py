"""데이터 현황 — 무엇이 언제 갱신됐나? (GitHub Actions 자동 수집 결과 data/mart/_status.json 기준)"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from src import scoring, ui
from src.mart_sync import read_status

st.set_page_config(page_title="데이터 현황", page_icon="🔄", layout="wide")
ui.require_login()

KST = timezone(timedelta(hours=9))
REPO = "https://github.com/1982baek-hash/market-analyzer"
STATUS = {
    "SUCCESS": "✅ 정상",
    "FAIL": "❌ 실패 (이전 자료 유지)",
    "NOT_PROVIDED": "⏸ 받지 않음",
    "PENDING": "⏳ 대기",
}


def next_run(now: datetime) -> datetime:
    """자동 수집: 매주 월요일 03:17 (한국시간)"""
    days = (0 - now.weekday()) % 7
    cand = (now + timedelta(days=days)).replace(hour=3, minute=17, second=0, microsecond=0)
    return cand if cand > now else cand + timedelta(days=7)


def fmt_dt(v) -> str:
    if not v:
        return "–"
    try:
        return datetime.fromisoformat(v).astimezone(KST).strftime("%m/%d %H:%M")
    except ValueError:
        return str(v)


def fmt_period(v) -> str:
    v = str(v or "")
    if len(v) == 8:
        return f"{v[:4]}.{v[4:6]}.{v[6:]}"
    if len(v) == 6:
        return f"{v[:4]}.{v[4:]}"
    return v or "–"


status = read_status()
run = status.get("_run", {})
now = datetime.now(KST)
regions = scoring.load_regions()

st.title("🔄 데이터 현황")
st.caption("앱은 데이터를 직접 받지 않습니다. GitHub Actions가 **매주 월요일 새벽 3시 17분** 공공데이터를 받아 저장소에 올리고, 앱은 그 파일을 읽습니다.")

# ---------------------------------------------------------------- 요약
rows = []
for sid, s in status.items():
    if sid.startswith("_") or not isinstance(s, dict):
        continue
    kind = str(s.get("name", sid)).split(" (")[0]
    rows.append({
        "id": sid, "묶음": kind, "지역": regions.get(s.get("region"), {}).get("name", s.get("region", "-")),
        "region": s.get("region"), "상태": STATUS.get(s.get("status"), s.get("status", "–")),
        "code": s.get("status"), "기준 시점": fmt_period(s.get("base_period")),
        "마지막 갱신": fmt_dt(s.get("last_updated")), "마지막 확인": fmt_dt(s.get("last_checked")),
        "주기": s.get("cycle", "–"), "건수": int(s.get("rows") or 0), "메모": s.get("message", ""),
        "이름": s.get("name", sid),
    })
df = pd.DataFrame(rows)

n_fail = int((df["code"] == "FAIL").sum()) if not df.empty else 0
ui.stat_tiles([("마지막 자동 수집", fmt_dt(run.get("finished_at")), ""),
               ("다음 자동 수집", next_run(now).strftime("%m/%d(월) %H:%M"), "매주 월요일"),
               ("실패한 자료", f"{n_fail}개", "실패하면 이전 자료 유지")])

if df.empty:
    st.info(f"아직 자동 수집 기록이 없습니다. [GitHub Actions]({REPO}/actions)에서 **상권 데이터 자동 수집 → Run workflow**를 눌러 주세요.")
    st.stop()

if n_fail:
    st.error("**실패한 자료** — 이전에 받은 자료를 그대로 보여 주고 있습니다.\n\n" +
             "\n".join(f"- {r['이름']}: {r['메모']}" for _, r in df[df["code"] == "FAIL"].iterrows()))
not_prov = df[df["code"] == "NOT_PROVIDED"]
if not not_prov.empty:
    st.warning("**받지 않은 자료**\n\n" + "\n".join(f"- {r['이름']}: {r['메모']}" for _, r in not_prov.iterrows()))

st.markdown(f"[▶ 지금 바로 수집 실행하기 (GitHub Actions)]({REPO}/actions/workflows/collect.yml) — "
            "들어가서 **Run workflow** 버튼을 누르면 10–30분 뒤 앱에 반영됩니다.")

# ---------------------------------------------------------------- 지역별 표
st.divider()
st.subheader("자료별 기준 시점")
keys = list(regions)
tabs = st.tabs([regions[k]["name"].split()[-1] for k in keys])
for tab, rk in zip(tabs, keys):
    with tab:
        sub = df[df["region"] == rk].sort_values(["묶음", "이름"])
        if sub.empty:
            st.caption("수집 기록 없음")
            continue
        st.dataframe(
            sub[["묶음", "상태", "기준 시점", "마지막 갱신", "주기", "건수", "메모"]],
            hide_index=True, use_container_width=True,
            column_config={"건수": st.column_config.NumberColumn(format="%d"),
                           "메모": st.column_config.TextColumn(width="large")},
        )

# ---------------------------------------------------------------- 자동으로 못 받는 자료
st.divider()
st.subheader("자동으로 받지 못하는 자료")
rent_period = ""
try:
    import yaml
    with open(scoring.RENT_YAML, encoding="utf-8") as f:
        rent_period = (yaml.safe_load(f) or {}).get("period", "")
except Exception:  # noqa: BLE001
    pass
gg_ok = bool(not df[(df["id"] == "floating_seongnam") & (df["code"] == "SUCCESS")].empty)
items = [
    ("상가 임대료 (한국부동산원)", f"{rent_period or '설정 파일'} 기준 · 화면 표시만",
     "분기마다 `config/rent_districts.yaml` 숫자 갱신"),
    ("성남 유동인구 (경기데이터드림)",
     "✅ 자동 수집 중" if gg_ok else "인증키(GG_API_KEY) 등록됨 → 다음 수집부터 반영", ""),
    ("안동·예천 유동인구", "공개 API 없음 → 버스정류장 수로 대신 봄", "안동시 정보공개청구 결과가 오면 반영"),
    ("카드 매출", "아직 없음", "금융데이터거래소 무료 상품 받은 뒤 반영"),
    ("후보지 유동인구·추정매출", "앱 밖에서 확인", "[소상공인365](https://bigdata.sbiz.or.kr) 상권분석에서 후보지 조회"),
]
st.markdown("\n".join(f"- **{a}** — {b}" + (f"  \n  ↳ {c}" if c else "") for a, b, c in items))

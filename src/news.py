"""상권 주변 뉴스 — 동네 이름·시 이름으로 최근 기사를 찾아 장사에 관련된 것만 추려 보여 줍니다.

- 기본: Google 뉴스 RSS (인증키 없이 동작, 가족이 보는 개인용 화면에서만 사용)
- Streamlit Secrets에 NAVER_SEARCH_CLIENT_ID / NAVER_SEARCH_CLIENT_SECRET 가 있으면 네이버 뉴스 검색 API를 함께 씀
  (네이버클라우드 NAVER API HUB 앱의 Client ID/Secret. 예전 개발자센터 키도 동작)
- 결과는 6시간 동안 캐시
"""

from __future__ import annotations

import html
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (market-analyzer family app)"}

# 장사에 영향을 주는 기사 분류 (앞에 있을수록 먼저 보여 줌)
TOPICS = [
    ("🏪 상권·가게", ["상권", "상가", "상인", "개점", "오픈", "문 열", "폐업", "폐점", "임대", "공실", "카페", "프랜차이즈",
                    "전통시장", "시장", "골목", "먹거리", "맛집", "매출", "소상공인", "배달", "편의점", "마트", "백화점", "아울렛"]),
    ("🚧 개발·교통", ["착공", "준공", "개통", "도로", "철도", "역사", "KTX", "버스", "노선", "주차", "재개발", "재건축",
                    "도시재생", "신도시", "개발", "산업단지", "산단", "리모델링", "정비사업", "공사", "이전"]),
    ("🏢 아파트·인구", ["아파트", "입주", "분양", "세대", "주택", "인구", "전입", "청년", "신혼"]),
    ("🎓 학교·기관", ["대학", "의대", "캠퍼스", "학교", "기숙사", "청사", "병원", "공공기관", "연구원", "기업 유치", "공장"]),
    ("🎉 행사·관광", ["축제", "행사", "야시장", "페스티벌", "관광", "관광객", "방문객", "공연", "박람회"]),
]
# 동네 행정 소식 같은 장사와 거리가 먼 기사
NOISE = ["통장", "직무교육", "이웃돕기", "성금", "기탁", "기부", "봉사", "김장", "체육대회", "협의체", "새마을부녀",
         "부고", "인사", "동정", "선거", "의원", "의장", "표창", "수상", "후원", "경로당", "장학금", "사건", "사고", "경찰",
         "화재", "숨져", "검거", "재판"]


def _clean(t: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", t or "")).strip()


def classify(title: str) -> tuple[str | None, bool]:
    topic = next((name for name, kws in TOPICS if any(k in title for k in kws)), None)
    noise = any(k in title for k in NOISE)
    return topic, noise


def google(query: str, days: int = 90, timeout: int = 8) -> list[dict]:
    q = f"{query} when:{days}d"
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": "ko", "gl": "KR", "ceid": "KR:ko"})
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    out = []
    for it in ET.fromstring(r.content).iter("item"):
        title = _clean(it.findtext("title"))
        src = _clean(it.findtext("source")) or ""
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        try:
            dt = parsedate_to_datetime(it.findtext("pubDate")).astimezone(KST)
        except Exception:  # noqa: BLE001
            dt = None
        out.append({"title": title, "source": src, "date": dt, "link": it.findtext("link") or ""})
    return out


def naver(query: str, cid: str, secret: str, timeout: int = 8) -> list[dict]:
    """네이버 뉴스 검색. 2026년 7월부터 새 키는 네이버클라우드 'NAVER API HUB'에서 발급 → HUB 주소로 먼저 부르고,
    예전 개발자센터 키(2027년 6월까지 사용 가능)면 옛 주소로 다시 부릅니다."""
    params = {"query": query, "display": 50, "sort": "date"}
    r = requests.get("https://naverapihub.apigw.ntruss.com/search/v1/news", params={**params, "format": "json"},
                     headers={"X-NCP-APIGW-API-KEY-ID": cid, "X-NCP-APIGW-API-KEY": secret}, timeout=timeout)
    if r.status_code in (401, 403):
        hub = f"HUB {r.status_code} {r.text[:150]}"
        r = requests.get("https://openapi.naver.com/v1/search/news.json", params=params,
                         headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": secret}, timeout=timeout)
        if not r.ok:
            raise RuntimeError(f"{hub} | 개발자센터 {r.status_code} {r.text[:150]}")
    r.raise_for_status()
    out = []
    for it in r.json().get("items", []):
        try:
            dt = parsedate_to_datetime(it.get("pubDate")).astimezone(KST)
        except Exception:  # noqa: BLE001
            dt = None
        link = it.get("originallink") or it.get("link") or ""
        host = urllib.parse.urlparse(link).netloc.replace("www.", "")
        out.append({"title": _clean(it.get("title")), "source": host, "date": dt, "link": link})
    return out


def _norm(t: str) -> str:
    return re.sub(r"[\W_]+", "", t)[:40]


def collect(queries: list[str], must: list[str] | None = None, days: int = 90,
            naver_keys: tuple[str, str] | None = None) -> tuple[list[dict], list[str]]:
    """여러 검색어 결과를 모아 중복을 빼고 분류합니다. must: 제목에 이 말 중 하나가 있어야 함."""
    items, errors = [], []
    for q in queries:
        try:
            items += google(q, days)
        except Exception as e:  # noqa: BLE001
            errors.append(f"Google 뉴스 '{q}': {type(e).__name__} {getattr(getattr(e, 'response', None), 'status_code', '')}")
        if naver_keys and all(naver_keys):
            try:
                items += naver(q, *naver_keys)
            except Exception as e:  # noqa: BLE001
                resp = getattr(e, "response", None)
                body = (resp.text[:150].replace("\n", " ") if resp is not None else "")
                errors.append(f"네이버 뉴스 '{q}': {type(e).__name__} {getattr(resp, 'status_code', '')} {body or str(e)[:320]}")
    since = datetime.now(KST) - timedelta(days=days)
    seen, out = set(), []
    for it in sorted(items, key=lambda x: x["date"] or datetime.min.replace(tzinfo=KST), reverse=True):
        k = _norm(it["title"])
        if not k or k in seen:
            continue
        seen.add(k)
        if it["date"] and it["date"] < since:
            continue
        if must and not any(m in it["title"] for m in must):
            continue
        topic, noise = classify(it["title"])
        if noise and not topic:
            continue
        out.append({**it, "topic": topic})
    order = {name: i for i, (name, _) in enumerate(TOPICS)}
    out.sort(key=lambda x: (x["topic"] is None, x["date"] is None,
                            -(x["date"].timestamp() if x["date"] else 0), order.get(x["topic"], 99)))
    return out, errors


def search_link(query: str) -> str:
    return "https://news.google.com/search?" + urllib.parse.urlencode({"q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"})

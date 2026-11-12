"""경기도 유동인구 (행정동별·시간대별, 경기데이터드림 Open API) → data/mart/floating/seongnam.parquet

- 경기도 전체 자료만 페이지로 내려주므로 모두 받아서 성남시(행정동코드 4113…)만 남깁니다.
- 경기데이터드림 인증키(GitHub Secrets의 GG_API_KEY)가 있을 때만 받습니다.
- 갱신 주기가 정해지지 않은 자료라 한 달에 한 번만 확인합니다.
- 유동인구수는 '월별·시간대별·행정동별 일평균'입니다. 2022년 무렵 집계 방식이 바뀌어
  해마다 크기가 크게 다르므로, 화면과 점수에는 가장 최근 달만 씁니다.
"""

import time

import pandas as pd

from pipeline.common import MART_DIR, SourceError, get_json

URL = "https://openapi.gg.go.kr/TB25BPTGGPOPSIGDONGTMM"
OUT_DIR = MART_DIR / "floating"
PAGE = 1000
SIGUNGU_PREFIX = {"seongnam": "4113"}


def _rows(data) -> tuple[list, int]:
    """경기데이터드림 응답: {서비스명: [{head:[{list_total_count}, {RESULT}]}, {row:[...]}]}"""
    body = data.get("TB25BPTGGPOPSIGDONGTMM")
    if body is None:
        res = data.get("RESULT", {})
        if res.get("CODE") == "INFO-200":
            return [], 0
        raise SourceError(f"경기 유동인구 API 오류: {res.get('CODE')} {res.get('MESSAGE')}")
    head = body[0].get("head", [])
    total = int(head[0].get("list_total_count", 0)) if head else 0
    result = head[1].get("RESULT", {}) if len(head) > 1 else {}
    if result.get("CODE") not in (None, "INFO-000"):
        raise SourceError(f"경기 유동인구 API 오류: {result.get('CODE')} {result.get('MESSAGE')}")
    rows = body[1].get("row", []) if len(body) > 1 else []
    return rows, total


def collect_region(region_key: str, region: dict, gg_key: str, prev: dict, now) -> dict:
    month = now.strftime("%Y%m")
    base = {"name": f"경기도 유동인구 ({region['name']})", "region": region_key,
            "method": "경기데이터드림 Open API (GitHub Actions)", "cycle": "월 1회 확인", "last_checked": now.isoformat()}
    if not gg_key:
        return {**base, "status": "NOT_PROVIDED", "rows": 0,
                "message": "GitHub Secrets에 GG_API_KEY(경기데이터드림 인증키)가 없어 받지 않음"}
    out_path = OUT_DIR / f"{region_key}.parquet"
    if prev.get("collected_month") == month and out_path.exists():
        return {**prev, **base, "status": "SUCCESS", "message": f"이번 달 이미 확인 · 기준 {prev.get('base_period')}"}

    prefix = SIGUNGU_PREFIX[region_key]
    keep, page, total = [], 1, None
    while True:
        data = get_json(URL, {"KEY": gg_key, "Type": "json", "pIndex": str(page), "pSize": str(PAGE)}, tries=4, timeout=90)
        rows, t = _rows(data)
        total = t if total is None else total
        keep.extend(r for r in rows if str(r.get("ADSTRD_CD", "")).startswith(prefix))
        if not rows or page * PAGE >= (total or 0):
            break
        page += 1
        time.sleep(0.05)
    if not keep:
        raise SourceError(f"경기 유동인구에서 {region['name']} 행정동을 찾지 못했습니다 (전체 {total}건).")

    df = pd.DataFrame(keep).rename(columns={
        "STD_YM": "base_ym", "TMZON_CD": "tz", "ADSTRD_CD": "adong_cd", "ADSTRD_NM": "adong_nm",
        "DYNMC_POPLTN_CNT": "floating", "DYNMC_POPLTN_CNT_RATE": "share",
        "BFYY_SMMN_INCNDECR_RATE": "yoy_rate",
    })
    df = df[["base_ym", "tz", "adong_cd", "adong_nm", "floating", "share", "yoy_rate"]]
    for c in ("floating", "share", "yoy_rate"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["base_ym"] = df["base_ym"].astype(str).str[:6]
    df["adong_cd"] = df["adong_cd"].astype(str)
    latest = df["base_ym"].max()
    # 최근 12개월만 보관 (용량 절약)
    cut = (pd.Period(latest, "M") - 11).strftime("%Y%m")
    df = df[df["base_ym"] >= cut].sort_values(["base_ym", "adong_cd", "tz"]).reset_index(drop=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(out_path)
    return {**base, "status": "SUCCESS", "rows": len(df), "collected_month": month, "base_period": latest,
            "file": str(out_path.relative_to(MART_DIR)), "last_updated": now.isoformat(),
            "message": f"새 자료 저장 (기준 {latest}, 행정동 {df['adong_cd'].nunique()}곳)"}

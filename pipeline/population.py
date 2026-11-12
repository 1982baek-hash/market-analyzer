"""행정안전부 행정동별 성·연령별 주민등록 인구 → data/mart/population/{지역}_{통계년월}.parquet

매월 말일 기준으로 집계되며, 새 달 자료가 나왔을 때만 파일을 만듭니다.
"""

from datetime import date

import pandas as pd

from pipeline.common import MART_DIR, SourceError, check_result, extract_items, find_key, get_json

API_URL = "https://apis.data.go.kr/1741000/admmSexdAgePpltn/selectAdmmSexdAgePpltn"
OUT_DIR = MART_DIR / "population"
AGES = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]


def _ym_minus(d: date, months: int) -> str:
    y, m = d.year, d.month - months
    while m <= 0:
        y, m = y - 1, m + 12
    return f"{y}{m:02d}"


def fetch_sigungu(api_key: str, sigungu_cd: str, fr_ym: str, to_ym: str) -> list:
    """시군구 아래 읍면동별 인구를 받아 옵니다 (lv=3: 읍면동 단위)."""
    rows, page = [], 1
    while True:
        data = get_json(API_URL, {
            "serviceKey": api_key, "admmCd": f"{sigungu_cd}00000",
            "srchFrYm": fr_ym, "srchToYm": to_ym, "lv": "3", "regSeCd": "1",
            "type": "JSON", "numOfRows": "100", "pageNo": str(page),
        })
        if not check_result(data, f"주민등록 인구 {sigungu_cd}"):
            break
        items = extract_items(data)
        rows.extend(items)
        total = int(str(find_key(data, "totalCount") or 0) or 0)
        if not items or page * 100 >= total:
            break
        page += 1
    return rows


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(",", ""), errors="coerce").fillna(0).astype("int64")


def normalize(rows: list) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    need = ["statsYm", "admmCd", "dongNm", "totNmprCnt"]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise SourceError(f"주민등록 인구 응답 형식이 바뀌었습니다. 없는 항목: {missing}")
    df["admmCd"] = df["admmCd"].astype(str).str.strip()
    # 시군구 합계 행(뒤 5자리 00000)은 빼고 읍면동만 남긴다
    df = df[(df["admmCd"].str.len() == 10) & (df["admmCd"].str[5:] != "00000")].copy()

    out = pd.DataFrame({
        "base_ym": df["statsYm"].astype(str).str.replace("-", "").str[:6],
        "sigungu_cd": df["admmCd"].str[:5],
        "adong_cd": df["admmCd"],
        "sido_nm": df.get("ctpvNm", ""),
        "sigungu_nm": df.get("sggNm", ""),
        "adong_nm": df["dongNm"].astype(str).str.strip(),
        "population": _num(df["totNmprCnt"]),
        "male": _num(df.get("maleNmprCnt", pd.Series(0, index=df.index))),
        "female": _num(df.get("femlNmprCnt", pd.Series(0, index=df.index))),
    })
    for a in AGES:
        m = _num(df[f"male{a}AgeNmprCnt"]) if f"male{a}AgeNmprCnt" in df else 0
        f = _num(df[f"feml{a}AgeNmprCnt"]) if f"feml{a}AgeNmprCnt" in df else 0
        out[f"m_{a}"] = m
        out[f"f_{a}"] = f
        out[f"age_{a}"] = m + f
    return out.reset_index(drop=True)


def collect_region(region_key: str, region: dict, api_key: str, prev: dict, now) -> dict:
    codes = [str(c) for c in region["sigungu_codes"]]
    fr_ym, to_ym = _ym_minus(now.date(), 3), _ym_minus(now.date(), 1)

    rows = []
    for code in codes:
        got = fetch_sigungu(api_key, code, fr_ym, to_ym)
        if not got:
            raise SourceError(f"주민등록 인구가 비어 있습니다 (시군구 {code}, {fr_ym}~{to_ym}).")
        rows.extend(got)

    df = normalize(rows)
    latest = df["base_ym"].max()
    df = df[df["base_ym"] == latest].reset_index(drop=True)
    missing = set(codes) - set(df["sigungu_cd"])
    if missing:
        raise SourceError(f"{latest} 기준 일부 시군구 인구가 없습니다: {sorted(missing)}")

    result = {
        "name": f"주민등록 성·연령별 인구 ({region['name']})",
        "region": region_key, "method": "API 자동 (GitHub Actions)", "cycle": "월 1회",
        "last_checked": now.isoformat(), "status": "SUCCESS",
    }
    out = OUT_DIR / f"{region_key}_{latest}.parquet"
    if prev.get("base_period") == latest and out.exists():
        result.update({k: prev.get(k) for k in ("base_period", "rows", "file", "last_updated")})
        result["message"] = f"변경 없음 ({latest} 유지)"
        return result

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(out)
    result.update({
        "base_period": latest, "rows": len(df), "file": str(out.relative_to(MART_DIR)),
        "last_updated": now.isoformat(),
        "message": f"새 자료 저장 ({latest}, 읍면동 {len(df)}곳, 인구 {int(df['population'].sum()):,}명)",
    })
    return result

"""통계청 SGIS 사업체·종사자 수, 가구 수 (읍면동) → data/mart/sgis/{지역}_{연도}.parquet

SGIS는 행정안전부와 다른 자체 지역 코드를 쓰므로, 이름으로 시군구 코드를 찾은 뒤 조회합니다.
읍면동 이름(adm_nm)은 앱에서 행정안전부 행정동 이름과 맞춰 씁니다.
"""

import pandas as pd

from pipeline.common import MART_DIR, SourceError, get_json

BASE = "https://sgisapi.mods.go.kr/OpenAPI3"
OUT_DIR = MART_DIR / "sgis"

# 행정안전부 시군구 코드 → SGIS에서 찾을 이름
SIGUNGU_NAMES = {
    "41131": "성남시 수정구", "41133": "성남시 중원구", "41135": "성남시 분당구",
    "47170": "안동시", "47900": "예천군",
}


def _call(path: str, params: dict) -> list:
    data = get_json(f"{BASE}/{path}", params)
    err = str(data.get("errCd", "0")).strip()
    if err not in ("0", ""):
        if err == "-100":  # 검색 결과 없음
            return []
        raise SourceError(f"SGIS {path} 오류 {err}: {data.get('errMsg', '')}")
    result = data.get("result") or []
    return result if isinstance(result, list) else [result]


def get_token(key: str, secret: str) -> str:
    data = get_json(f"{BASE}/auth/authentication.json", {"consumer_key": key, "consumer_secret": secret})
    err = str(data.get("errCd", "0")).strip()
    token = (data.get("result") or {}).get("accessToken") if isinstance(data.get("result"), dict) else None
    if err not in ("0", "") or not token:
        raise SourceError(f"SGIS 인증 실패 {err}: {data.get('errMsg', '')} (서비스 ID·보안 Key 확인)")
    return token


def _match(name: str, target: str) -> bool:
    name, target = name.replace(" ", ""), target.replace(" ", "")
    return name == target or name.endswith(target) or target.endswith(name)


def find_sigungu_codes(token: str, region: dict) -> dict:
    """{행안부 시군구 코드: SGIS 시군구 코드}"""
    sido_name = region["sido_name"]
    sidos = _call("addr/stage.json", {"accessToken": token})
    sido = next((s for s in sidos if _match(str(s.get("addr_name", "")), sido_name)), None)
    if not sido:
        raise SourceError(f"SGIS에서 '{sido_name}'을 찾지 못했습니다.")
    sggs = _call("addr/stage.json", {"accessToken": token, "cd": sido["cd"]})
    found = {}
    for code in [str(c) for c in region["sigungu_codes"]]:
        target = SIGUNGU_NAMES.get(code, "")
        hit = next((s for s in sggs if target and _match(str(s.get("addr_name", "")), target)), None)
        if not hit:
            names = [s.get("addr_name") for s in sggs][:30]
            raise SourceError(f"SGIS에서 '{target}'을 찾지 못했습니다. 후보: {names}")
        found[code] = str(hit["cd"])
    return found


def _household_count(row: dict):
    for k in ("household_cnt", "tot_household", "household"):
        if k in row:
            return row[k]
    for k, v in row.items():
        if "household" in k and "avg" not in k:
            return v
    return None


def collect_region(region_key: str, region: dict, key: str, secret: str, prev: dict, now) -> dict:
    if not key or not secret:
        raise SourceError("GitHub Secrets에 SGIS_CONSUMER_KEY / SGIS_CONSUMER_SECRET이 없습니다.")
    token = get_token(key, secret)
    sgis_codes = find_sigungu_codes(token, region)

    frames, used_year = [], None
    for year in range(now.year - 1, now.year - 5, -1):
        frames = []
        for mois_cd, sgis_cd in sgis_codes.items():
            company = _call("stats/company.json",
                            {"accessToken": token, "year": str(year), "adm_cd": sgis_cd, "low_search": "1"})
            if not company:
                frames = []
                break
            cdf = pd.DataFrame(company)
            house = _call("stats/household.json",
                          {"accessToken": token, "year": str(year), "adm_cd": sgis_cd, "low_search": "1"})
            hmap = {str(h.get("adm_cd")): _household_count(h) for h in house}
            frames.append(pd.DataFrame({
                "base_year": str(year),
                "sigungu_cd": mois_cd,
                "sgis_adm_cd": cdf["adm_cd"].astype(str),
                "adm_nm": cdf["adm_nm"].astype(str).str.strip(),
                "corp_cnt": pd.to_numeric(cdf.get("corp_cnt"), errors="coerce"),
                "tot_worker": pd.to_numeric(cdf.get("tot_worker"), errors="coerce"),
                "household_cnt": pd.to_numeric(cdf["adm_cd"].astype(str).map(hmap), errors="coerce"),
            }))
        if frames:
            used_year = str(year)
            break
    if not frames:
        raise SourceError(f"SGIS 사업체 통계가 최근 4년({now.year - 4}~{now.year - 1}) 모두 비어 있습니다.")

    df = pd.concat(frames, ignore_index=True)
    result = {
        "name": f"SGIS 사업체·종사자·가구 ({region['name']})",
        "region": region_key, "method": "API 자동 (GitHub Actions)", "cycle": "연 1회",
        "last_checked": now.isoformat(), "status": "SUCCESS",
    }
    out = OUT_DIR / f"{region_key}_{used_year}.parquet"
    if prev.get("base_period") == used_year and out.exists():
        result.update({k: prev.get(k) for k in ("base_period", "rows", "file", "last_updated")})
        result["message"] = f"변경 없음 ({used_year}년 자료 유지)"
        return result

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(out)
    hh_note = "" if df["household_cnt"].notna().any() else ", 가구 수 없음"
    result.update({
        "base_period": used_year, "rows": len(df), "file": str(out.relative_to(MART_DIR)),
        "last_updated": now.isoformat(),
        "message": f"새 자료 저장 ({used_year}년, 읍면동 {len(df)}곳, 종사자 {int(df['tot_worker'].sum()):,}명{hh_note})",
    })
    return result

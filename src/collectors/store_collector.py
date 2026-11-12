import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path
import pandas as pd
import requests

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.config_loader import get_api_key, load_regions_config
from src.code_normalizer import AdminCodeNormalizer
from src.db_manager import DatabaseManager

COLUMN_MAP_TO_STD = {
    "상가업소번호": "store_id",
    "상호명": "store_name",
    "지점명": "branch_name",
    "상권업종대분류코드": "inds_lcls_cd",
    "업종대분류코드": "inds_lcls_cd",
    "상권업종대분류명": "inds_lcls_nm",
    "업종대분류명": "inds_lcls_nm",
    "상권업종중분류코드": "inds_mcls_cd",
    "업종중분류코드": "inds_mcls_cd",
    "상권업종중분류명": "inds_mcls_nm",
    "업종중분류명": "inds_mcls_nm",
    "상권업종소분류코드": "inds_scls_cd",
    "업종소분류코드": "inds_scls_cd",
    "상권업종소분류명": "inds_scls_nm",
    "업종소분류명": "inds_scls_nm",
    "표준산업분류코드": "ksic_cd",
    "표준산업분류명": "ksic_nm",
    "시도코드": "sido_cd",
    "시도명": "sido_nm",
    "시군구코드": "sigungu_cd",
    "시군구명": "sigungu_nm",
    "행정동코드": "adong_cd",
    "행정동명": "adong_nm",
    "법정동코드": "ldong_cd",
    "법정동명": "ldong_nm",
    "지번주소": "lno_adr",
    "도로명주소": "rdnm_adr",
    "경도": "lon",
    "위도": "lat",
    "bizesId": "store_id",
    "bizesNm": "store_name",
    "brchNm": "branch_name",
    "indsLclsCd": "inds_lcls_cd",
    "indsLclsNm": "inds_lcls_nm",
    "indsMclsCd": "inds_mcls_cd",
    "indsMclsNm": "inds_mcls_nm",
    "indsSclsCd": "inds_scls_cd",
    "indsSclsNm": "inds_scls_nm",
    "ksicCd": "ksic_cd",
    "ksicNm": "ksic_nm",
    "ctprvnCd": "sido_cd",
    "ctprvnNm": "sido_nm",
    "signguCd": "sigungu_cd",
    "signguNm": "sigungu_nm",
    "adongCd": "adong_cd",
    "adongNm": "adong_nm",
    "ldongCd": "ldong_cd",
    "ldongNm": "ldong_nm",
    "lnoAdr": "lno_adr",
    "rdnmAdr": "rdnm_adr",
    "lon": "lon",
    "lat": "lat",
}

API_BASE_URL = "https://apis.data.go.kr/B553077/api/open/sdsc2/storeListInDong"


class StoreCollector:
    """상가(상권)정보 데이터를 오픈API 또는 로컬 CSV 파일에서 수집·전처리하는 클래스"""

    def __init__(self):
        self.regions = load_regions_config()
        self.normalizer = AdminCodeNormalizer()
        self.db = DatabaseManager()

    def _detect_franchise(self, df: pd.DataFrame) -> pd.Series:
        branch_filled = (
            df["branch_name"]
            .fillna("")
            .astype(str)
            .str.strip()
            .apply(lambda x: len(x) > 0 and x.lower() != "nan")
        )
        return branch_filled

    def preprocess_dataframe(self, raw_df: pd.DataFrame) -> pd.DataFrame:
        rename_dict = {col: COLUMN_MAP_TO_STD[col] for col in raw_df.columns if col in COLUMN_MAP_TO_STD}
        df = raw_df.rename(columns=rename_dict).copy()

        essential = ["store_id", "store_name", "inds_lcls_nm", "inds_mcls_nm", "inds_scls_nm", "sigungu_cd", "adong_cd", "adong_nm", "lon", "lat"]
        missing = [c for c in essential if c not in df.columns]
        if missing:
            raise ValueError(
                f"원본 데이터의 컬럼 형식이 예상과 다릅니다. 누락된 표준 컬럼: {missing}\n"
                f"현재 파일의 실제 컬럼 목록: {list(raw_df.columns)}"
            )

        df["sigungu_cd"] = df["sigungu_cd"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
        df["adong_cd"] = self.normalizer.normalize_adong_code(df["adong_cd"], df["adong_nm"])

        if "branch_name" not in df.columns:
            df["branch_name"] = ""
        else:
            df["branch_name"] = df["branch_name"].fillna("").astype(str).str.strip()

        df["is_franchise"] = self._detect_franchise(df)
        return df

    def collect_from_csv(self, region_key: str, csv_path: Path, base_ym: str = None) -> int:
        if region_key not in self.regions:
            raise KeyError(f"config/regions.yaml에 '{region_key}' 지역이 없습니다.")

        region_info = self.regions[region_key]
        sgg_codes = [str(c) for c in region_info["sigungu_codes"]]

        if not csv_path.exists():
            raise FileNotFoundError(f"CSV 파일을 찾을 수 없습니다: {csv_path}")

        if not base_ym:
            match = re.search(r"(20\d{4})", csv_path.name)
            base_ym = match.group(1) if match else datetime.now().strftime("%Y%m")

        print(f"[CSV 로드 시작] 파일: {csv_path.name} (대상: {region_info['name']}, 시군구코드: {sgg_codes})")
        try:
            raw_df = pd.read_csv(csv_path, encoding="utf-8", low_memory=False, dtype=str)
        except UnicodeDecodeError:
            raw_df = pd.read_csv(csv_path, encoding="cp949", low_memory=False, dtype=str)

        df = self.preprocess_dataframe(raw_df)
        df_filtered = df[df["sigungu_cd"].isin(sgg_codes)].copy()

        saved_count = self.db.validate_and_upsert_stores(
            df=df_filtered,
            base_ym=base_ym,
            region_key=region_key,
            expected_sigungu_codes=sgg_codes,
            collect_method=f"CSV 파일 ({csv_path.name})",
        )
        print(f"[완료] {region_info['name']} 점포 {saved_count:,}건이 DuckDB에 저장되었습니다. (기준시점: {base_ym})")
        return saved_count

    def collect_from_api(self, region_key: str, base_ym: str = None) -> int:
        if region_key not in self.regions:
            raise KeyError(f"config/regions.yaml에 '{region_key}' 지역이 없습니다.")

        api_key = get_api_key("DATA_GO_KR_API_KEY")
        if not api_key or "여기에_" in api_key:
            raise ValueError(
                ".env 파일에 유효한 DATA_GO_KR_API_KEY가 설정되지 않았습니다.\n"
                "API 키를 넣거나, --mode csv 옵션으로 CSV 파일을 사용해 주세요."
            )

        region_info = self.regions[region_key]
        sgg_codes = [str(c) for c in region_info["sigungu_codes"]]
        if not base_ym:
            base_ym = datetime.now().strftime("%Y%m")

        all_items = []
        for sgg_cd in sgg_codes:
            page_no = 1
            num_of_rows = 1000
            print(f"[API 수집 시작] {region_info['name']} (시군구코드: {sgg_cd})...")

            while True:
                params = {
                    "serviceKey": api_key,
                    "divId": "signguCd",
                    "key": sgg_cd,
                    "numOfRows": str(num_of_rows),
                    "pageNo": str(page_no),
                    "type": "json",
                }
                resp = requests.get(API_BASE_URL, params=params, timeout=30)
                if resp.status_code != 200:
                    raise RuntimeError(f"API HTTP 오류 (상태코드 {resp.status_code}): {resp.text[:200]}")

                try:
                    data = resp.json()
                except Exception:
                    raise RuntimeError(f"API 응답이 JSON 형식이 아닙니다 (인증키 확인 필요): {resp.text[:300]}")

                header = data.get("header", {})
                result_code = header.get("resultCode", "")
                if result_code != "00":
                    raise RuntimeError(f"API 응답 오류 (코드 {result_code}): {header.get('resultMsg')}")

                body = data.get("body", {})
                total_count = int(body.get("totalCount", 0))
                items = body.get("items", [])

                if not items:
                    break

                all_items.extend(items)
                max_page = (total_count + num_of_rows - 1) // num_of_rows
                print(f"  - 시군구 {sgg_cd}: {page_no}/{max_page} 페이지 완료 (누적 {len(all_items):,}건 / 전체 {total_count:,}건)")

                if page_no >= max_page:
                    break
                page_no += 1
                time.sleep(0.2)

        raw_df = pd.DataFrame(all_items)
        backup_dir = BASE_DIR / "data" / "raw" / "stores"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_file = backup_dir / f"api_stores_{region_key}_{base_ym}.csv"
        raw_df.to_csv(backup_file, index=False, encoding="utf-8-sig")

        df = self.preprocess_dataframe(raw_df)
        saved_count = self.db.validate_and_upsert_stores(
            df=df,
            base_ym=base_ym,
            region_key=region_key,
            expected_sigungu_codes=sgg_codes,
            collect_method="OpenAPI (storeListInDong)",
        )
        print(f"[완료] {region_info['name']} 점포 {saved_count:,}건이 DuckDB에 저장되었습니다. (기준시점: {base_ym})")
        return saved_count


def print_summary(region_key: str):
    db = DatabaseManager()
    regions = load_regions_config()
    sgg_codes = [f"'{c}'" for c in regions[region_key]["sigungu_codes"]]
    sgg_filter = ",".join(sgg_codes)

    with db.get_connection() as con:
        print("\n" + "=" * 65)
        print(f"📊 [{regions[region_key]['name']}] 적재 데이터 검증 요약")
        print("=" * 65)

        dong_df = con.execute(f"""
            SELECT sigungu_nm AS 시군구, adong_cd AS 행정동코드, adong_nm AS 행정동명,
                   COUNT(*) AS 점포수,
                   SUM(CASE WHEN is_franchise THEN 1 ELSE 0 END) AS 프랜차이즈추정수
            FROM stores_history
            WHERE sigungu_cd IN ({sgg_filter})
            GROUP BY sigungu_nm, adong_cd, adong_nm
            ORDER BY 점포수 DESC
            LIMIT 10
        """).df()
        print("\n[1] 행정동별 점포 수 TOP 10:")
        print(dong_df.to_string(index=False))

        cat_df = con.execute(f"""
            SELECT inds_lcls_cd AS 대분류코드, inds_lcls_nm AS 대분류명,
                   COUNT(DISTINCT inds_mcls_cd) AS 중분류수,
                   COUNT(DISTINCT inds_scls_cd) AS 소분류수,
                   COUNT(*) AS 점포수
            FROM stores_history
            WHERE sigungu_cd IN ({sgg_filter})
            GROUP BY inds_lcls_cd, inds_lcls_nm
            ORDER BY 점포수 DESC
        """).df()
        print("\n[2] 업종 3단계 분류 체계 및 점포 수 현황:")
        print(cat_df.to_string(index=False))
        print("=" * 65)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="소상공인 상가(상권)정보 수집 및 전처리 스크립트")
    parser.add_argument("--region", type=str, default="seongnam", help="대상 지역 키 (seongnam, andong, yecheon)")
    parser.add_argument("--mode", type=str, choices=["api", "csv"], default="api", help="수집 방식 (api 또는 csv)")
    parser.add_argument("--csv-path", type=str, default="", help="mode가 csv일 때 읽어들일 CSV 파일 경로")
    parser.add_argument("--base-ym", type=str, default="", help="데이터 기준연월 (예: 202609)")
    args = parser.parse_args()

    collector = StoreCollector()
    if args.mode == "api":
        collector.collect_from_api(region_key=args.region, base_ym=args.base_ym or None)
    else:
        if not args.csv_path:
            raise ValueError("--mode csv 사용 시 --csv-path 에 CSV 파일 경로를 지정해야 합니다.")
        collector.collect_from_csv(
            region_key=args.region,
            csv_path=Path(args.csv_path),
            base_ym=args.base_ym or None,
        )

    print_summary(args.region)

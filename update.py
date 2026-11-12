import argparse
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.config_loader import load_regions_config
from src.db_manager import DatabaseManager
from src.collectors.store_collector import StoreCollector
from src.collectors.pop_collector import PopulationAndBoundaryCollector


def run_all_updates(force: bool = False) -> dict:
    """
    모든 데이터 소스를 점검하여 새 데이터가 있을 때만 안전하게 수집·검증·적재합니다.
    실패 시 기존 데이터를 유지하고 로그 및 data_status에 실패 원인을 기록합니다.
    """
    db = DatabaseManager()
    regions = load_regions_config()
    store_collector = StoreCollector()
    pop_collector = PopulationAndBoundaryCollector()

    current_ym = datetime.now().strftime("%Y%m")
    results = {"success": [], "skipped": [], "failed": []}

    print(f"=== [상권 데이터 자동 업데이트 시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ===")

    # 1. API 자동 수집 대상: 성남시·안동시·예천군 상가(상권)정보
    for r_key, r_info in regions.items():
        source_id = f"stores_{r_key}"
        try:
            with db.get_connection(read_only=True) as con:
                row = con.execute(
                    "SELECT source_base_period, status FROM data_status WHERE source_id = ?",
                    [source_id],
                ).fetchone()

            last_period = row[0] if row else "-"
            last_status = row[1] if row else "PENDING"

            # 현재 연월과 동일하고 이미 성공한 상태라면(force=False) 중복 수집 생략
            if not force and last_status == "SUCCESS" and last_period == current_ym:
                msg = f"{r_info['name']} 이미 최신 기준({current_ym})이 적재되어 있어 확인만 기록합니다."
                db.mark_checked_only(source_id, "최신 상태 유지")
                print(f"[유지] {msg}")
                results["skipped"].append(source_id)
                continue

            print(f"[수집] {r_info['name']} 상가(상권)정보 API 업데이트 시도...")
            cnt = store_collector.collect_from_api(region_key=r_key, base_ym=current_ym)
            results["success"].append(f"{source_id} ({cnt:,}건)")
        except Exception as e:
            err_msg = str(e)
            print(f"[실패] {source_id}: {err_msg}")
            db._write_log("FAIL", source_id, err_msg)
            results["failed"].append(f"{source_id}: {err_msg}")

    # 2. 권역 경계(SHP/GeoJSON) 폴더 확인 -> 공식 파일이 새로 들어왔으면 적재, 없으면 기본 권역 보완
    bound_dir = BASE_DIR / "data" / "raw" / "boundaries"
    bound_files = list(bound_dir.glob("*.shp")) + list(bound_dir.glob("*.geojson"))
    if bound_files:
        latest_bound = max(bound_files, key=lambda p: p.stat().st_mtime)
        try:
            cnt = pop_collector.load_official_boundary_file(latest_bound)
            with db.get_connection(read_only=False) as con:
                now = datetime.now()
                con.execute("""
                    INSERT OR REPLACE INTO data_status VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [
                    "boundaries_all", "통계청 SGIS 행정동 경계 (면적)", "공통 (세 지역)",
                    f"수동 파일 ({latest_bound.name})", "연 1회", str(now.year),
                    cnt, now, now, "SUCCESS", ""
                ])
            results["success"].append(f"boundaries_all ({cnt:,}건)")
        except Exception as e:
            err_msg = str(e)
            print(f"[실패] boundaries_all: {err_msg}")
            db._write_log("FAIL", "boundaries_all", err_msg)
            results["failed"].append(f"boundaries_all: {err_msg}")
    else:
        pop_collector.build_fallback_boundaries_from_stores()

    # 3. 주민등록 인구(CSV) 폴더 확인 -> 새 파일이 있으면 자동 적재
    pop_dir = BASE_DIR / "data" / "raw" / "population"
    pop_files = list(pop_dir.glob("*.csv"))
    if pop_files:
        latest_pop = max(pop_files, key=lambda p: p.stat().st_mtime)
        try:
            cnt = pop_collector.load_population_csv(latest_pop)
            results["success"].append(f"demographics_all ({cnt:,}건)")
        except Exception as e:
            err_msg = str(e)
            print(f"[실패] demographics_all: {err_msg}")
            db._write_log("FAIL", "demographics_all", err_msg)
            results["failed"].append(f"demographics_all: {err_msg}")

    print("=== [자동 업데이트 종료] ===")
    print(f"성공: {len(results['success'])}건 / 최신유지: {len(results['skipped'])}건 / 실패: {len(results['failed'])}건")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="상권 분석 데이터 주간 자동 업데이트 스크립트")
    parser.add_argument("--force", action="store_true", help="기준연월이 같아도 강제로 재수집")
    args = parser.parse_args()
    run_all_updates(force=args.force)

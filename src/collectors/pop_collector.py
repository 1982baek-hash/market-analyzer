import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
import geopandas as gpd
import pandas as pd
from shapely.geometry import MultiPoint, mapping

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.config_loader import load_regions_config
from src.code_normalizer import AdminCodeNormalizer
from src.db_manager import DatabaseManager


class PopulationAndBoundaryCollector:
    """
    1) 행안부 주민등록 인구 CSV 파일을 읽어 행정동별 인구를 적재하고,
    2) 공식 행정동 경계(.shp/.geojson) 파일이 있으면 정밀 폴리곤과 면적(km²)을 적재하며,
    3) 경계 파일이 아직 없을 때는 적재된 점포 좌표들의 외곽 경계(Convex Hull + 버퍼)로
       시각화용 권역 폴리곤을 생성해 단계구분도가 즉시 작동하도록 지원합니다.
       (단, 공식 경계 파일이 없을 때는 면적당 밀집도를 추정하지 않고 NULL로 유지하여 데이터 원칙을 지킵니다.)
    """

    def __init__(self):
        self.regions = load_regions_config()
        self.normalizer = AdminCodeNormalizer()
        self.db = DatabaseManager()

    def build_fallback_boundaries_from_stores(self):
        """
        공식 경계 shp 파일이 없는 행정동에 대해, 실제 점포 위치들을 감싸는
        시각화용 권역 경계(GeoJSON)를 생성합니다. (source_type='STORE_CONVEX_HULL'로 명시)
        """
        with self.db.get_connection() as con:
            stores_df = con.execute("""
                SELECT sigungu_cd, adong_cd, adong_nm, lon, lat
                FROM stores_history
                WHERE lon IS NOT NULL AND lat IS NOT NULL
            """).df()

            existing_boundaries = set(
                con.execute("SELECT adong_cd FROM region_boundaries WHERE source_type = 'OFFICIAL_SHP'").df()["adong_cd"].tolist()
            )

        if stores_df.empty:
            print("[안내] 아직 적재된 점포 데이터가 없어 권역 경계를 생성하지 않았습니다.")
            return 0

        now = datetime.now()
        records = []
        for (sgg_cd, adong_cd, adong_nm), group in stores_df.groupby(["sigungu_cd", "adong_cd", "adong_nm"]):
            if adong_cd in existing_boundaries:
                continue
            pts = list(zip(group["lon"], group["lat"]))
            if len(pts) >= 3:
                poly = MultiPoint(pts).convex_hull.buffer(0.0015)
            else:
                poly = MultiPoint(pts).buffer(0.003)

            geojson_str = json.dumps(mapping(poly))
            # 주의: 공식 경계 파일이 아니므로 area_km2는 None으로 두어 가짜 면적 밀집도 계산을 방지함
            records.append({
                "adong_cd": str(adong_cd),
                "sigungu_cd": str(sgg_cd),
                "adong_nm": str(adong_nm),
                "area_km2": None,
                "geojson_str": geojson_str,
                "source_type": "STORE_CONVEX_HULL",
                "updated_at": now,
            })

        if records:
            df_bound = pd.DataFrame(records)
            with self.db.get_connection() as con:
                con.register("bound_view", df_bound)
                con.execute("INSERT OR REPLACE INTO region_boundaries SELECT * FROM bound_view")
                con.unregister("bound_view")
            print(f"[완료] 행정동 {len(records)}곳의 지도 시각화용 권역 경계가 준비되었습니다.")
        return len(records)

    def load_official_boundary_file(self, file_path: Path):
        """
        통계청 SGIS 또는 국토부 센서스 행정동 경계 파일(.shp 또는 .geojson)을 읽어
        정밀 폴리곤과 실제 면적(km²)을 계산해 DuckDB에 저장합니다.
        """
        if not file_path.exists():
            raise FileNotFoundError(f"경계 파일을 찾을 수 없습니다: {file_path}")

        gdf = gpd.read_file(file_path)
        if gdf.crs is None:
            gdf = gdf.set_crs(epsg=5179)

        gdf_meter = gdf.to_crs(epsg=5179)
        gdf["area_km2"] = (gdf_meter.geometry.area / 1_000_000.0).round(3)

        gdf_wgs84 = gdf.to_crs(epsg=4326)

        code_col = next((c for c in gdf_wgs84.columns if c.upper() in ["ADM_CD", "ADM_DR_CD", "ADONG_CD", "EMD_CD", "HCODE"]), None)
        name_col = next((c for c in gdf_wgs84.columns if c.upper() in ["ADM_NM", "ADM_DR_NM", "ADONG_NM", "EMD_NM"]), None)
        if not code_col or not name_col:
            raise ValueError(f"경계 파일에서 행정동 코드/이름 컬럼을 찾을 수 없습니다. 실제 컬럼: {list(gdf_wgs84.columns)}")

        gdf_wgs84["std_adong_cd"] = self.normalizer.normalize_adong_code(gdf_wgs84[code_col], gdf_wgs84[name_col])
        gdf_wgs84["sigungu_cd"] = gdf_wgs84["std_adong_cd"].str[:5]

        now = datetime.now()
        records = []
        for _, row in gdf_wgs84.iterrows():
            records.append({
                "adong_cd": str(row["std_adong_cd"]),
                "sigungu_cd": str(row["sigungu_cd"]),
                "adong_nm": str(row[name_col]),
                "area_km2": float(row["area_km2"]),
                "geojson_str": json.dumps(mapping(row.geometry)),
                "source_type": "OFFICIAL_SHP",
                "updated_at": now,
            })

        df_bound = pd.DataFrame(records).drop_duplicates(subset=["adong_cd"])
        with self.db.get_connection() as con:
            con.register("bound_view", df_bound)
            con.execute("INSERT OR REPLACE INTO region_boundaries SELECT * FROM bound_view")
            con.unregister("bound_view")
            con.execute("""
                UPDATE region_demographics AS d
                SET area_km2 = b.area_km2
                FROM region_boundaries AS b
                WHERE d.adong_cd = b.adong_cd AND b.area_km2 IS NOT NULL
            """)
        print(f"[완료] 공식 행정동 경계 {len(df_bound)}건 및 면적(km²) 반영 완료!")
        return len(df_bound)

    def load_population_csv(self, csv_path: Path, base_ym: str = None):
        """
        행정안전부 주민등록 인구통계 CSV 파일을 읽어 행정동별 총인구수를 적재합니다.
        """
        if not csv_path.exists():
            raise FileNotFoundError(f"인구 CSV 파일을 찾을 수 없습니다: {csv_path}")

        try:
            raw_df = pd.read_csv(csv_path, encoding="cp949", dtype=str)
        except UnicodeDecodeError:
            raw_df = pd.read_csv(csv_path, encoding="utf-8", dtype=str)

        if not base_ym:
            m = re.search(r"(20\d{4})", csv_path.name)
            base_ym = m.group(1) if m else datetime.now().strftime("%Y%m")

        if "행정기관" in raw_df.columns:
            extracted = raw_df["행정기관"].str.extract(r"(.+)\((\d{10})\)")
            raw_df["adong_nm_full"] = extracted[0].str.strip()
            raw_df["adong_cd"] = extracted[1].str.strip()
            raw_df["adong_nm"] = raw_df["adong_nm_full"].apply(lambda x: str(x).split()[-1] if pd.notna(x) else "")
        elif "행정동코드" in raw_df.columns or "행정기관코드" in raw_df.columns:
            c_col = "행정동코드" if "행정동코드" in raw_df.columns else "행정기관코드"
            n_col = next((c for c in ["행정동명", "읍면동명", "행정기관명"] if c in raw_df.columns), c_col)
            raw_df["adong_cd"] = raw_df[c_col].str.strip()
            raw_df["adong_nm"] = raw_df[n_col].str.strip()
        else:
            raise ValueError(f"인구 CSV 컬럼 형식을 인식할 수 없습니다. 실제 컬럼: {list(raw_df.columns)}")

        pop_col = next((c for c in raw_df.columns if "총인구수" in c or c in ["인구수", "계"]), None)
        if not pop_col:
            raise ValueError(f"총인구수 컬럼을 찾을 수 없습니다. 실제 컬럼: {list(raw_df.columns)}")

        df = raw_df.dropna(subset=["adong_cd"]).copy()
        df["adong_cd"] = self.normalizer.normalize_adong_code(df["adong_cd"], df["adong_nm"])
        df = df[df["adong_cd"].str.len() == 10]
        df = df[~df["adong_cd"].str.endswith("00000")].copy()
        df["sigungu_cd"] = df["adong_cd"].str[:5]
        df["population"] = (
            df[pop_col].astype(str).str.replace(",", "").str.strip()
        )
        df["population"] = pd.to_numeric(df["population"], errors="coerce").fillna(0).astype(int)

        count = self.db.upsert_demographics(df, base_ym=base_ym, collect_method=f"CSV ({csv_path.name})")
        print(f"[완료] 행정동별 주민등록 인구 {count:,}건 적재 완료! (기준시점: {base_ym})")
        return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="인구 및 행정동 경계 데이터 처리기")
    parser.add_argument("--init-boundaries", action="store_true", help="적재된 점포 좌표로 기본 권역 경계 생성")
    parser.add_argument("--pop-csv", type=str, default="", help="행안부 주민등록 인구 CSV 파일 경로")
    parser.add_argument("--shp-path", type=str, default="", help="공식 행정동 경계 .shp 또는 .geojson 경로")
    args = parser.parse_args()

    collector = PopulationAndBoundaryCollector()
    if args.init_boundaries:
        collector.build_fallback_boundaries_from_stores()
    if args.pop_csv:
        collector.load_population_csv(Path(args.pop_csv))
    if args.shp_path:
        collector.load_official_boundary_file(Path(args.shp_path))
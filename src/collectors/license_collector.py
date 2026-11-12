import json
from datetime import datetime
from pathlib import Path
import geopandas as gpd
import pandas as pd
from shapely.geometry import shape

from src.config_loader import load_regions_config
from src.db_manager import DatabaseManager


class LicenseCollector:
    """
    지방행정 인허가 데이터(CSV)를 읽어 성남시·안동시·예천군 데이터를 필터링하고,
    좌표 공간조인(1순위) 또는 주소 매칭(2순위)으로 행정동 코드를 부여해 적재합니다.
    """

    def __init__(self):
        self.db = DatabaseManager()
        self.regions = load_regions_config()

    def load_license_csv(self, file_path: Path) -> int:
        if not file_path.exists():
            raise FileNotFoundError(f"인허가 CSV 파일을 찾을 수 없습니다: {file_path}")

        try:
            raw_df = pd.read_csv(file_path, encoding="cp949", low_memory=False, dtype=str)
        except UnicodeDecodeError:
            raw_df = pd.read_csv(file_path, encoding="utf-8", low_memory=False, dtype=str)

        required = ["관리번호", "인허가일자", "영업상태명", "사업장명"]
        missing = [c for c in required if c not in raw_df.columns]
        if missing:
            raise ValueError(
                f"인허가 데이터 필수 컬럼 누락: {missing}\n"
                f"파일 안의 실제 컬럼 목록: {list(raw_df.columns)}"
            )

        addr_col = "도로명전체주소" if "도로명전체주소" in raw_df.columns else (
            "소재지전체주소" if "소재지전체주소" in raw_df.columns else None
        )
        if not addr_col:
            raise ValueError(f"주소 컬럼(소재지전체주소 또는 도로명전체주소)이 없습니다: {list(raw_df.columns)}")

        # 대상 시군구(성남시, 안동시, 예천군) 주소 필터링
        df = raw_df.copy()
        df["addr"] = df[addr_col].fillna(df.get("소재지전체주소", "")).fillna("").astype(str)
        mask = df["addr"].str.contains("성남시|안동시|예천군", na=False)
        df = df[mask].copy()

        if df.empty:
            raise ValueError("해당 인허가 파일에 성남시·안동시·예천군 데이터가 없습니다.")

        # 행정동 경계 및 이름 목록 로드
        with self.db.get_connection(read_only=True) as con:
            bound_df = con.execute("SELECT adong_cd, sigungu_cd, adong_nm, geojson_str FROM region_boundaries").df()
            store_dongs = con.execute("SELECT DISTINCT sigungu_cd, adong_cd, adong_nm FROM stores_history").df()

        df["service_nm"] = df["개방서비스명"].fillna(file_path.stem) if "개방서비스명" in df.columns else file_path.stem
        df["mgmt_no"] = df["관리번호"].astype(str)
        df["biz_name"] = df["사업장명"].fillna("").astype(str)
        df["open_date"] = df["인허가일자"].fillna("").astype(str).str.replace("-", "").str[:8]
        df["close_date"] = df["폐업일자"].fillna("").astype(str).str.replace("-", "").str[:8] if "폐업일자" in df.columns else ""
        df["status_nm"] = df["영업상태명"].fillna("").astype(str)

        # 좌표 변환 (인허가 데이터는 중부원점 EPSG:5174 또는 EPSG:2097 좌표계 사용이 일반적)
        x_col = next((c for c in df.columns if "좌표정보" in c and ("X" in c.upper() or "x" in c)), None)
        y_col = next((c for c in df.columns if "좌표정보" in c and ("Y" in c.upper() or "y" in c)), None)

        df["lon"] = None
        df["lat"] = None
        df["adong_cd"] = ""
        df["adong_nm"] = ""
        df["sigungu_cd"] = ""

        if x_col and y_col and not bound_df.empty:
            df_xy = df.copy()
            df_xy["_x"] = pd.to_numeric(df_xy[x_col], errors="coerce")
            df_xy["_y"] = pd.to_numeric(df_xy[y_col], errors="coerce")
            valid_xy = df_xy.dropna(subset=["_x", "_y"])
            if not valid_xy.empty:
                gdf_pts = gpd.GeoDataFrame(
                    valid_xy[["mgmt_no"]],
                    geometry=gpd.points_from_xy(valid_xy["_x"], valid_xy["_y"]),
                    crs="EPSG:5174",
                ).to_crs(epsg=4326)

                geoms = [shape(json.loads(g)) for g in bound_df["geojson_str"]]
                gdf_poly = gpd.GeoDataFrame(bound_df[["adong_cd", "sigungu_cd", "adong_nm"]], geometry=geoms, crs="EPSG:4326")
                joined = gpd.sjoin(gdf_pts, gdf_poly, how="left", predicate="within")
                joined = joined.drop_duplicates(subset=["mgmt_no"])
                joined_map = joined.set_index("mgmt_no")[["adong_cd", "sigungu_cd", "adong_nm"]].to_dict("index")

                df["lon"] = df["mgmt_no"].map(dict(zip(gdf_pts["mgmt_no"], gdf_pts.geometry.x)))
                df["lat"] = df["mgmt_no"].map(dict(zip(gdf_pts["mgmt_no"], gdf_pts.geometry.y)))
                df["adong_cd"] = df["mgmt_no"].map(lambda k: str(joined_map.get(k, {}).get("adong_cd", "") or ""))
                df["sigungu_cd"] = df["mgmt_no"].map(lambda k: str(joined_map.get(k, {}).get("sigungu_cd", "") or ""))
                df["adong_nm"] = df["mgmt_no"].map(lambda k: str(joined_map.get(k, {}).get("adong_nm", "") or ""))

        # 좌표 매칭이 안 된 행은 주소의 시군구/읍면동명 문자열로 보완 매핑
        if not store_dongs.empty:
            for idx, row in df[df["adong_cd"] == ""].iterrows():
                addr_str = row["addr"]
                for _, d_row in store_dongs.iterrows():
                    if d_row["adong_nm"] in addr_str:
                        df.at[idx, "adong_cd"] = d_row["adong_cd"]
                        df.at[idx, "sigungu_cd"] = d_row["sigungu_cd"]
                        df.at[idx, "adong_nm"] = d_row["adong_nm"]
                        break

        df["updated_at"] = datetime.now()
        save_cols = [
            "mgmt_no", "service_nm", "biz_name", "open_date", "close_date",
            "status_nm", "sigungu_cd", "adong_cd", "adong_nm", "addr", "lon", "lat", "updated_at"
        ]
        save_df = df[save_cols].drop_duplicates(subset=["mgmt_no"])

        with self.db.get_connection(read_only=False) as con:
            con.register("lic_view", save_df)
            con.execute("INSERT OR REPLACE INTO licenses_history SELECT * FROM lic_view")
            con.unregister("lic_view")

            now = datetime.now()
            con.execute("""
                INSERT OR REPLACE INTO data_status VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                "licenses_all", "지방행정 인허가 데이터 (개·폐업)", "공통 (세 지역)",
                f"수동 파일 ({file_path.name})", "월 1회", now.strftime("%Y%m"),
                len(save_df), now, now, "SUCCESS", ""
            ])
        return len(save_df)

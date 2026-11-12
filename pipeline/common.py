"""파이프라인 공통 도구: 경로, 키 읽기, HTTP 재시도, 수집 상태 기록."""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
MART_DIR = ROOT / "data" / "mart"
STATUS_PATH = MART_DIR / "_status.json"
REGIONS_YAML = ROOT / "config" / "regions.yaml"
KST = ZoneInfo("Asia/Seoul")


def now_kst() -> datetime:
    return datetime.now(KST).replace(microsecond=0)


def load_regions() -> dict:
    with open(REGIONS_YAML, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)["regions"]


def get_secret(name: str) -> str:
    """환경변수(GitHub Secrets)에서 키를 읽습니다.

    공공데이터포털은 Encoding 키(%2B 등 포함)와 Decoding 키를 함께 주는데,
    requests가 다시 인코딩하므로 어느 쪽을 넣어도 Decoding 형태로 맞춥니다.
    """
    value = os.environ.get(name, "").strip().strip('"').strip("'")
    if "%" in value:
        value = unquote(value)
    return value


class SourceError(RuntimeError):
    """수집 실패. 메시지는 데이터 현황 화면에 그대로 보여 줍니다."""


def get_json(url: str, params: dict, tries: int = 4, timeout: int = 60) -> dict:
    """일시적인 네트워크·서버 오류는 최대 4번까지 다시 시도합니다 (공공데이터포털이 가끔 느림)."""
    last_err = None
    for attempt in range(1, tries + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code >= 500:
                raise SourceError(f"서버 오류 HTTP {resp.status_code}")
            if resp.status_code != 200:
                # 4xx는 다시 시도해도 같으므로 바로 실패
                raise SourceError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            try:
                return resp.json()
            except ValueError:
                # 인증키 오류는 JSON이 아닌 XML로 오는 경우가 많다
                raise SourceError(f"JSON이 아닌 응답 (인증키 확인 필요): {resp.text[:200]}")
        except (requests.ConnectionError, requests.Timeout) as e:
            last_err = SourceError(f"네트워크 오류: {e}")
        except SourceError as e:
            if "HTTP 4" in str(e) or "JSON이 아닌" in str(e):
                raise
            last_err = e
        if attempt < tries:
            time.sleep(2 * attempt)
    raise last_err


def find_key(obj, key):
    """중첩된 JSON에서 key를 처음 만나는 값을 찾습니다 (기관마다 감싸는 구조가 달라서)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.strip() == key:
                return v
        for v in obj.values():
            found = find_key(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_key(v, key)
            if found is not None:
                return found
    return None


def extract_items(data) -> list:
    """공공데이터포털 응답에서 item 목록을 꺼냅니다. 1건이면 dict로 오는 경우도 처리합니다."""
    items = find_key(data, "item")
    if items is None:
        items = find_key(data, "items")
        if isinstance(items, dict):
            items = items.get("item", [])
    if items is None or items == "":
        return []
    if isinstance(items, dict):
        items = [items]
    return [{k.strip(): v for k, v in it.items()} for it in items if isinstance(it, dict)]


OK_CODES = {"0", "00", "000", "INFO-000"}
NODATA_CODES = {"03", "INFO-200"}


def check_result(data, context: str) -> bool:
    """정상이면 True, 자료 없음이면 False, 오류면 SourceError."""
    code = find_key(data, "resultCode")
    code = "" if code is None else str(code).strip()
    if code in OK_CODES or (code == "" and extract_items(data)):
        return True
    if code in NODATA_CODES:
        return False
    msg = find_key(data, "resultMsg") or ""
    keys = list(data.keys())[:5] if isinstance(data, dict) else type(data).__name__
    raise SourceError(f"{context}: API 오류 코드 '{code}' {msg} (응답 키: {keys})")


def load_status() -> dict:
    if STATUS_PATH.exists():
        with open(STATUS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_status(status: dict) -> None:
    MART_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATUS_PATH, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")

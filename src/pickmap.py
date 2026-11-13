"""꾹 눌러 자리 고르는 네이버 지도 (Streamlit 양방향 컴포넌트, 빌드 없이 HTML 하나).

- 휴대폰: 길게 누르기(longtap) · PC: 마우스를 0.6초 누르고 있기 또는 오른쪽 클릭
- 고른 자리 {lat, lon, t} 를 돌려줍니다. 네이버 지도 키가 없으면 None (호출한 쪽이 folium 지도를 씀)
"""

from __future__ import annotations

from pathlib import Path

import streamlit.components.v1 as components

from src import navermap

_comp = components.declare_component("pickmap", path=str(Path(__file__).parent / "pickmap_frontend"))


def available() -> bool:
    return bool(navermap.client_id())


def pick_map(points: list[dict], center: tuple[float, float], zoom: int = 14, pick: dict | None = None,
             radius: int = 500, comps: list[dict] | None = None, height: int = 480, key: str = "pickmap"):
    return _comp(client_id=navermap.client_id(), points=points, center=[float(center[0]), float(center[1])],
                 zoom=int(zoom), pick=pick, radius=int(radius), comps=comps or [], height=int(height),
                 key=key, default=None)

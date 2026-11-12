"""네이버 지도 (NAVER Cloud Platform Maps · Web Dynamic Map).

Streamlit Secrets에 NAVER_MAP_CLIENT_ID가 있으면 네이버 지도로, 없으면 None을 돌려줘
호출한 쪽이 기존 지도(folium)를 쓰게 합니다.

NCP 콘솔의 Maps Application에서
- Dynamic Map을 켜고
- Web 서비스 URL에 앱 주소(예: https://xxxx.streamlit.app, 경로·포트 없이)를 등록해야 지도가 뜹니다.
"""

from __future__ import annotations

import hashlib
import html
import json
import time
from pathlib import Path

import streamlit.components.v1 as components

from src.config_loader import BASE_DIR, get_api_key

# 네이버 지도는 페이지 주소(도메인)로 인증하므로, components.html(about:srcdoc)로는 인증이 안 된다.
# 그래서 지도 HTML을 static/maps/에 파일로 쓰고, 앱 도메인의 /app/static/ 주소로 iframe에 띄운다.
MAP_DIR = Path(BASE_DIR) / "static" / "maps"


def client_id() -> str:
    return get_api_key("NAVER_MAP_CLIENT_ID")


def render(points: list[dict], height: int = 420, zoom: int | None = None) -> bool:
    """points: [{lat, lon, color, size, tooltip, label}] — label이 있으면 점 옆에 글자를 붙입니다.

    네이버 지도 키가 없으면 False를 돌려줍니다 (그리기 안 함).
    """
    cid = client_id()
    if not cid or not points:
        return False
    lats = [p["lat"] for p in points]
    lons = [p["lon"] for p in points]
    data = json.dumps([
        {"lat": float(p["lat"]), "lon": float(p["lon"]), "color": p.get("color", "#3366cc"),
         "size": int(p.get("size", 14)), "tip": html.escape(str(p.get("tooltip", ""))),
         "label": html.escape(str(p.get("label") or ""))}
        for p in points
    ], ensure_ascii=False)
    bounds = json.dumps([min(lats), min(lons), max(lats), max(lons)])
    zoom_js = "null" if zoom is None else str(int(zoom))
    page = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>html,body{{margin:0;padding:0}}</style></head><body>
<div id="map" style="width:100%;height:{height - 8}px;border-radius:8px;"></div>
<div id="err" style="display:none;padding:12px;font:14px sans-serif;color:#b00020;background:#fff4f4;border-radius:8px;"></div>
<script>
window.navermap_authFailure = function () {{
  document.getElementById('map').style.display = 'none';
  const e = document.getElementById('err');
  e.style.display = 'block';
  e.innerHTML = '네이버 지도 인증에 실패했습니다. NCP 콘솔 › Maps › Application에서 <b>Dynamic Map</b>이 켜져 있는지, '
    + '<b>Web 서비스 URL</b>에 이 앱 주소(' + location.origin + ')가 '
    + '경로 없이 등록돼 있는지 확인해 주세요.';
}};
function draw() {{
  const pts = {data};
  const b = {bounds};
  const map = new naver.maps.Map('map', {{
    center: new naver.maps.LatLng((b[0] + b[2]) / 2, (b[1] + b[3]) / 2),
    zoom: {zoom_js} || 12, scaleControl: true, mapDataControl: false,
    zoomControl: true, zoomControlOptions: {{ position: naver.maps.Position.TOP_RIGHT, style: naver.maps.ZoomControlStyle.SMALL }}
  }});
  if ({zoom_js} === null && pts.length > 1) {{
    map.fitBounds(new naver.maps.LatLngBounds(new naver.maps.LatLng(b[0], b[1]), new naver.maps.LatLng(b[2], b[3])),
                  {{ top: 30, right: 30, bottom: 30, left: 30 }});
  }}
  const tip = new naver.maps.InfoWindow({{ content: '', borderWidth: 0, backgroundColor: 'transparent',
                                         disableAnchor: true, pixelOffset: new naver.maps.Point(0, -6) }});
  pts.forEach(p => {{
    const s = p.size;
    const lab = p.label ? `<span style="position:absolute;left:${{s + 4}}px;top:-4px;white-space:nowrap;font:700 12px sans-serif;color:#111;text-shadow:0 0 3px #fff,0 0 3px #fff">${{p.label}}</span>` : '';
    const m = new naver.maps.Marker({{
      position: new naver.maps.LatLng(p.lat, p.lon), map: map,
      icon: {{ content: `<div style="position:relative;width:${{s}}px;height:${{s}}px;border-radius:50%;background:${{p.color}};opacity:.9;border:1px solid #fff;box-shadow:0 0 2px rgba(0,0,0,.4)">${{lab}}</div>`,
              anchor: new naver.maps.Point(s / 2, s / 2) }}
    }});
    if (p.tip) {{
      const show = () => {{ tip.setContent(`<div style="padding:4px 8px;background:#fff;border:1px solid #ccc;border-radius:4px;font:13px sans-serif;white-space:nowrap">${{p.tip}}</div>`); tip.open(map, m); }};
      naver.maps.Event.addListener(m, 'mouseover', show);
      naver.maps.Event.addListener(m, 'click', show);
      naver.maps.Event.addListener(m, 'mouseout', () => tip.close());
    }}
  }});
}}
</script>
<script src="https://oapi.map.naver.com/openapi/v3/maps.js?ncpKeyId={html.escape(cid)}"></script>
<script>
// 지도 모듈이 다 준비될 때까지 기다렸다가 그립니다 (callback 방식은 iframe 안에서 너무 일찍 불리는 경우가 있음)
(function wait(n) {{
  if (window.naver && naver.maps && naver.maps.Map && naver.maps.LatLngBounds) {{ draw(); return; }}
  if (n > 100) {{ document.getElementById('map').innerHTML = '<p style="font:14px sans-serif;padding:12px">네이버 지도를 불러오지 못했습니다.</p>'; return; }}
  setTimeout(function () {{ wait(n + 1); }}, 100);
}})(0);
</script>
</body></html>
"""
    name = hashlib.sha1(page.encode("utf-8")).hexdigest()[:20] + ".html"
    MAP_DIR.mkdir(parents=True, exist_ok=True)
    target = MAP_DIR / name
    if not target.exists():
        target.write_text(page, encoding="utf-8")
        _cleanup()
    # 상대 주소: 로컬(/app/static/…)과 Streamlit Cloud(/~/+/app/static/…) 모두에서 앱 도메인으로 열린다
    components.iframe(f"app/static/maps/{name}", height=height)
    return True


def _cleanup(max_age_hours: int = 24) -> None:
    """하루 지난 지도 파일은 지웁니다."""
    cutoff = time.time() - max_age_hours * 3600
    for f in MAP_DIR.glob("*.html"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass

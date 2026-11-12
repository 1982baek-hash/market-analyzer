"""네이버 지도 (NAVER Cloud Platform Maps · Web Dynamic Map).

Streamlit Secrets에 NAVER_MAP_CLIENT_ID가 있으면 네이버 지도로, 없으면 None을 돌려줘
호출한 쪽이 기존 지도(folium)를 쓰게 합니다.

NCP 콘솔의 Maps Application에서
- Dynamic Map을 켜고
- Web 서비스 URL에 앱 주소(예: https://xxxx.streamlit.app, 경로·포트 없이)를 등록해야 지도가 뜹니다.
"""

from __future__ import annotations

import html
import json

import streamlit.components.v1 as components

from src.config_loader import get_api_key


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
    page = f"""
<div id="map" style="width:100%;height:{height - 8}px;border-radius:8px;"></div>
<div id="err" style="display:none;padding:12px;font:14px sans-serif;color:#b00020;background:#fff4f4;border-radius:8px;"></div>
<script>
window.navermap_authFailure = function () {{
  document.getElementById('map').style.display = 'none';
  const e = document.getElementById('err');
  e.style.display = 'block';
  e.innerHTML = '네이버 지도 인증에 실패했습니다. NCP 콘솔 › Maps › Application에서 <b>Dynamic Map</b>이 켜져 있는지, '
    + '<b>Web 서비스 URL</b>에 이 앱 주소(' + (document.referrer ? new URL(document.referrer).origin : '앱 주소') + ')가 '
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
<script src="https://oapi.map.naver.com/openapi/v3/maps.js?ncpKeyId={html.escape(cid)}&callback=draw"></script>
"""
    components.html(page, height=height)
    return True

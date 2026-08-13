# -*- coding: utf-8 -*-
"""
이동경로 지도 시각화 (pydeck).

트럭 구간(직송/첫마일/막판마일)은 카카오맵이 실제로 반환한 도로 경로
좌표를 그대로 그려서 실제 도로를 따라가는 곡선으로 표시한다. 좌표가
주어지지 않으면(예: API 실패) 시작-끝 직선으로 폴백한다.

⚠️ 철도 구간은 실제 선로 좌표 데이터가 없어 역-역 직선으로 표시한다 —
   실제 선로 곡률과는 다르다.
"""

import pydeck as pdk

TRUCK_COLOR = [255, 140, 0]
DRAYAGE_COLOR = [34, 139, 34]
RAIL_COLOR = [30, 90, 220]
ORIGIN_COLOR = [0, 102, 255]
DEST_COLOR = [220, 20, 60]
NODE_COLOR = [34, 139, 34]


def _to_pydeck_path(points: list[tuple[float, float]]) -> list[list[float]]:
    """[(lat, lng), ...] -> pydeck이 요구하는 [[lng, lat], ...] 변환."""
    return [[lng, lat] for lat, lng in points]


def build_route_map(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    truck_only_path: list[tuple[float, float]] | None = None,
    origin_node: tuple[float, float, str] | None = None,
    dest_node: tuple[float, float, str] | None = None,
    first_mile_path: list[tuple[float, float]] | None = None,
    last_mile_path: list[tuple[float, float]] | None = None,
    show_truck_line: bool = True,
) -> pdk.Deck:
    """origin_node/dest_node를 주면 철도 경로도 함께 표시.
    *_path는 카카오맵 실제 도로 좌표 [(lat, lng), ...] — 없으면 직선 폴백.
    show_truck_line=False면 '트럭 직송' 비교선을 그리지 않는다 — 이미
    철도 통합운송으로 확정된 화물 추적 화면처럼, 트럭 대안을 보여줄
    필요가 없는 곳에서 쓴다."""
    markers = [
        {"lat": origin_lat, "lng": origin_lng, "label": "출발지", "color": ORIGIN_COLOR},
        {"lat": dest_lat, "lng": dest_lng, "label": "도착지", "color": DEST_COLOR},
    ]

    paths = []
    if show_truck_line:
        truck_pts = truck_only_path or [(origin_lat, origin_lng), (dest_lat, dest_lng)]
        paths.append({"path": _to_pydeck_path(truck_pts), "color": TRUCK_COLOR, "label": "트럭 직송"})

    if origin_node and dest_node:
        on_lat, on_lng, on_name = origin_node
        dn_lat, dn_lng, dn_name = dest_node
        markers.append({"lat": on_lat, "lng": on_lng, "label": on_name, "color": NODE_COLOR})
        markers.append({"lat": dn_lat, "lng": dn_lng, "label": dn_name, "color": NODE_COLOR})

        fm_pts = first_mile_path or [(origin_lat, origin_lng), (on_lat, on_lng)]
        lm_pts = last_mile_path or [(dn_lat, dn_lng), (dest_lat, dest_lng)]

        paths.append({"path": _to_pydeck_path(fm_pts), "color": DRAYAGE_COLOR, "label": "첫마일(트럭)"})
        paths.append({"path": [[on_lng, on_lat], [dn_lng, dn_lat]], "color": RAIL_COLOR, "label": "철도 (직선 근사)"})
        paths.append({"path": _to_pydeck_path(lm_pts), "color": DRAYAGE_COLOR, "label": "막판마일(트럭)"})

    path_layer = pdk.Layer(
        "PathLayer",
        data=paths,
        get_path="path",
        get_color="color",
        get_width=5,
        width_min_pixels=3,
        pickable=True,
    )
    marker_layer = pdk.Layer(
        "ScatterplotLayer",
        data=markers,
        get_position=["lng", "lat"],
        get_fill_color="color",
        get_radius=6000,
        pickable=True,
    )
    text_layer = pdk.Layer(
        "TextLayer",
        data=markers,
        get_position=["lng", "lat"],
        get_text="label",
        get_size=14,
        get_color=[20, 20, 20],
        get_pixel_offset=[0, -14],
    )

    view_state = pdk.ViewState(
        latitude=(origin_lat + dest_lat) / 2,
        longitude=(origin_lng + dest_lng) / 2,
        zoom=6.2,
    )

    return pdk.Deck(
        layers=[path_layer, marker_layer, text_layer],
        initial_view_state=view_state,
        tooltip={"text": "{label}"},
    )

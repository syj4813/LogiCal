# -*- coding: utf-8 -*-
"""Google Geocoding API 래퍼 — 주소/장소명 -> (lat, lng) 및 정제된 주소."""

import requests

GOOGLE_MAPS_API_KEY = ""  # TODO: Streamlit secrets 등으로 주입


def _geocode_raw(address: str) -> dict | None:
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": address, "key": GOOGLE_MAPS_API_KEY, "language": "ko"}
    resp = requests.get(url, params=params, timeout=5)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "OK" or not data.get("results"):
        return None
    return data["results"][0]


def geocode_address(address: str) -> tuple[float, float] | None:
    result = _geocode_raw(address)
    if result is None:
        return None
    loc = result["geometry"]["location"]
    return loc["lat"], loc["lng"]


def geocode_to_formatted_address(text: str) -> str | None:
    """장소명/불완전한 주소(예: "부산역 근처") -> 정제된 정식 주소 문자열.

    챗봇이 파악한 출발지/도착지가 정확한 주소가 아닐 수 있어, 폼에 채워
    넣기 전에 이 함수로 한 번 정규화한다. 매칭 실패 시 None — 호출부에서
    원본 텍스트를 그대로 쓰도록 폴백 처리해야 함.
    """
    result = _geocode_raw(text)
    if result is None:
        return None
    return result.get("formatted_address")

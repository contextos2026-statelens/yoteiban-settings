"""Build map links without making a network request or saving location data.

Input JSON fields: origin (optional), destination (required), datetime (optional
ISO-8601 local date/time), time_kind (departure/arrival), mode
(transit/walking/driving/bicycling), maps_primary (google/yahoo), and
transit_time_fallback (boolean). Yahoo is included only for timed transit.
"""
from __future__ import annotations

from datetime import datetime
from urllib.parse import urlencode


GOOGLE_MAPS = "https://www.google.com/maps/dir/?api=1"
YAHOO_TRANSIT = "https://transit.yahoo.co.jp/search/result"
TRAVEL_MODES = {"transit", "walking", "driving", "bicycling"}


def _location(value, name: str, *, optional: bool = False) -> str:
    if value is None and optional:
        return ""
    if isinstance(value, dict):
        # Coordinates are used in the link only; this function never persists them.
        if "lat" in value and "lon" in value:
            value = f"{value['lat']},{value['lon']}"
        else:
            raise ValueError(f"{name} は地名か lat/lon 座標で指定してください")
    if not isinstance(value, str) or not value.strip():
        if optional and value == "":
            return ""
        raise ValueError(f"{name} が必要です")
    return value.strip()


def build_route_links(data: dict) -> dict:
    """Return provider links only; no location, route, or time is stored."""
    if not isinstance(data, dict):
        raise ValueError("入力はJSONオブジェクトである必要があります")
    origin = _location(data.get("origin"), "origin", optional=True)
    destination = _location(data.get("destination"), "destination")
    mode = data.get("mode", "transit")
    if mode not in TRAVEL_MODES:
        raise ValueError("mode は transit/walking/driving/bicycling のいずれかです")
    primary = data.get("maps_primary", "google")
    if primary not in {"google", "yahoo"}:
        raise ValueError("maps_primary は google または yahoo です")
    fallback = data.get("transit_time_fallback", True)
    if not isinstance(fallback, bool):
        raise ValueError("transit_time_fallback は真偽値です")

    google_params = {}
    if origin:
        google_params["origin"] = origin
    google_params.update({"destination": destination, "travelmode": mode})
    google_url = GOOGLE_MAPS + "&" + urlencode(google_params)

    yahoo_url = None
    raw_datetime = data.get("datetime")
    if mode == "transit" and raw_datetime and (fallback or primary == "yahoo"):
        time_kind = data.get("time_kind")
        if time_kind not in {"departure", "arrival"}:
            raise ValueError("日時がある場合、time_kind は departure または arrival です")
        try:
            when = datetime.fromisoformat(raw_datetime)
        except (TypeError, ValueError) as exc:
            raise ValueError("datetime は ISO 8601 の日時で指定してください") from exc
        yahoo_params = {"from": origin, "to": destination}
        yahoo_params.update({
            "y": f"{when.year:04d}", "m": f"{when.month:02d}", "d": f"{when.day:02d}",
            "hh": f"{when.hour:02d}", "m1": str(when.minute // 10), "m2": str(when.minute % 10),
            "type": "1" if time_kind == "departure" else "4",
        })
        yahoo_url = YAHOO_TRANSIT + "?" + urlencode(yahoo_params)

    providers = []
    if yahoo_url and primary == "yahoo":
        providers.append({"provider": "yahoo", "primary": True, "url": yahoo_url})
        providers.append({"provider": "google", "primary": False, "url": google_url})
    else:
        providers.append({"provider": "google", "primary": True, "url": google_url})
        if yahoo_url:
            providers.append({"provider": "yahoo", "primary": False, "url": yahoo_url})
    return {"links": providers}

"""Optional OpenStreetMap / Overpass import.

Live requests are not used by unit tests. Callers pass an HTTP client.
Downloaded documents can be cached so the same bbox is not fetched twice.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Protocol

from app.domain.importers import import_geojson

HIGHWAY_FILTER = (
    "motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|service"
)


class HttpClient(Protocol):
    def post(self, url: str, *, data: dict[str, str], timeout: float) -> Any: ...


class OverpassError(Exception):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def validate_bbox(bbox: list[float], max_span: float) -> None:
    if len(bbox) != 4:
        raise OverpassError("A bbox needs west, south, east, and north.", "invalid_bbox")
    west, south, east, north = bbox
    if not all(isinstance(value, int | float) for value in bbox):
        raise OverpassError("Bbox values must be numbers.", "invalid_bbox")
    if west >= east or south >= north:
        raise OverpassError("Bbox west/south must be less than east/north.", "invalid_bbox")
    if west < -180 or east > 180 or south < -90 or north > 90:
        raise OverpassError("Bbox is outside the valid longitude/latitude range.", "invalid_bbox")
    if east - west > max_span or north - south > max_span:
        raise OverpassError(
            f"Bbox span exceeds the limit of {max_span} degrees. Choose a smaller study area.",
            "bbox_too_large",
        )


def fetch_osm_network(
    bbox: list[float],
    *,
    client: HttpClient,
    overpass_url: str,
    timeout_s: float,
    max_retries: int,
    max_span: float,
    cache_dir: Path | None = None,
    sleeper: Any = time.sleep,
) -> dict[str, Any]:
    validate_bbox(bbox, max_span)
    cache_key = hashlib.sha256(f"{overpass_url}|{bbox}".encode()).hexdigest()
    if cache_dir is not None:
        cache_path = cache_dir / f"{cache_key}.json"
        if cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            cached["cache_hit"] = True
            return cached
    query = (
        f"[out:json][timeout:{int(timeout_s)}];"
        f"(way['highway'~'^{HIGHWAY_FILTER}$']({bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]}););"
        "out geom;"
    )
    response = _post_with_retry(client, overpass_url, query, timeout_s, max_retries, sleeper)
    payload = response.json()
    if not isinstance(payload, dict):
        raise OverpassError("Overpass returned a response that was not a JSON object.", "invalid_response")
    geojson, warnings = overpass_to_geojson(payload)
    if not geojson["features"]:
        raise OverpassError("The Overpass response did not include any roads in this bbox.", "no_roads")
    imported = import_geojson(geojson)
    warnings.extend(imported["warnings"])
    result = {
        "network": imported["network"],
        "warnings": warnings,
        "cache_hit": False,
        "attribution": "© OpenStreetMap contributors. Data retrieved from the Overpass API and licensed under ODbL.",
        "source": {
            "provider": "OpenStreetMap",
            "endpoint_host": _host(overpass_url),
            "bbox": bbox,
            "element_count": len(payload.get("elements") or []),
        },
    }
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"{cache_key}.json"
        cache_path.write_text(json.dumps(result), encoding="utf-8")
    return result


def overpass_to_geojson(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    features = []
    warnings: list[dict[str, str]] = []
    for element in payload.get("elements") or []:
        if element.get("type") != "way":
            continue
        geometry = element.get("geometry") or []
        coordinates = []
        for point in geometry:
            if "lon" not in point or "lat" not in point:
                continue
            coordinates.append([point["lon"], point["lat"]])
        if len(coordinates) < 2:
            continue
        tags = element.get("tags") or {}
        oneway = str(tags.get("oneway", "no")).lower() in {"yes", "true", "1"}
        if str(tags.get("oneway", "")).lower() == "-1":
            coordinates = list(reversed(coordinates))
            oneway = True
        speed, speed_warning = _parse_maxspeed(tags.get("maxspeed"), element.get("id"))
        if speed_warning:
            warnings.append(speed_warning)
        properties: dict[str, Any] = {
            "road_id": f"W{element.get('id')}",
            "name": tags.get("name") or f"Way {element.get('id')}",
            "highway": tags.get("highway") or "unclassified",
            "oneway": oneway,
        }
        if tags.get("lanes") and str(tags.get("lanes")).isdigit():
            properties["lanes"] = int(tags["lanes"])
        if speed is not None:
            properties["speed_kmh"] = speed
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coordinates},
                "properties": properties,
            }
        )
    return {"type": "FeatureCollection", "features": features}, warnings


def _parse_maxspeed(value: Any, way_id: Any) -> tuple[float | None, dict[str, str] | None]:
    if value is None:
        return None, None
    text = str(value).strip().lower()
    if text in {"signals", "none", "variable"}:
        return None, {
            "code": "speed_unparsed",
            "message": f"Way {way_id} maxspeed '{value}' was not converted. A type default will be used.",
        }
    parts = text.replace("km/h", "kmh").split()
    try:
        speed = float(parts[0])
    except ValueError:
        return None, {
            "code": "speed_unparsed",
            "message": f"Way {way_id} maxspeed '{value}' was not converted. A type default will be used.",
        }
    if "mph" in text:
        speed *= 1.609344
    return speed, None


def _post_with_retry(client: HttpClient, url: str, query: str, timeout_s: float, max_retries: int, sleeper: Any) -> Any:
    attempt = 0
    while True:
        try:
            response = client.post(url, data={"data": query}, timeout=timeout_s)
        except Exception as exc:  # noqa: BLE001 - provider exceptions vary by client
            name = exc.__class__.__name__
            if "Timeout" in name and attempt < max_retries:
                attempt += 1
                sleeper(0.2 * attempt)
                continue
            if "Timeout" in name:
                raise OverpassError("The Overpass request timed out.", "timeout") from exc
            raise OverpassError("The Overpass request failed before a response was received.", "request_failed") from exc
        status = getattr(response, "status_code", 200)
        if status in {429, 503} and attempt < max_retries:
            attempt += 1
            sleeper(0.2 * attempt)
            continue
        if status == 429:
            raise OverpassError("The Overpass service rate-limited the request. Try again later.", "rate_limited")
        if status >= 400:
            raise OverpassError(f"The Overpass service returned HTTP {status}.", "upstream_error")
        return response


def _host(url: str) -> str:
    without_scheme = url.split("://", 1)[-1]
    return without_scheme.split("/", 1)[0]

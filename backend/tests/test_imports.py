"""GeoJSON and Overpass adapters. These tests never call a live map service."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.domain.importers import ImportError_, export_geojson, import_geojson
from app.domain.osm import OverpassError, fetch_osm_network, validate_bbox
from app.domain.sample import build_sample_network


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, payload, status_code=200, error=None):
        self.payload = payload
        self.status_code = status_code
        self.error = error
        self.calls = 0

    def post(self, url, *, data, timeout):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return FakeResponse(self.payload, self.status_code)


OVERPASS = {
    "elements": [
        {
            "type": "way",
            "id": 10,
            "tags": {"highway": "residential", "name": "Sample Way", "maxspeed": "30", "lanes": "1"},
            "geometry": [{"lon": 1.0, "lat": 2.0}, {"lon": 1.001, "lat": 2.0}],
        }
    ]
}


def test_geojson_round_trip_and_rejection():
    exported = export_geojson(build_sample_network())
    imported = import_geojson(exported)
    assert len(imported["network"]["edges"]) == 14
    again = export_geojson(imported["network"])
    assert again["type"] == "FeatureCollection"
    with pytest.raises(ImportError_):
        import_geojson({"type": "Feature"})
    with pytest.raises(ImportError_):
        import_geojson({"type": "FeatureCollection", "features": []})


def test_osm_cache_bbox_and_timeout(tmp_path: Path):
    with pytest.raises(OverpassError) as invalid:
        validate_bbox([1, 1, 0, 2], 0.2)
    assert invalid.value.code == "invalid_bbox"
    with pytest.raises(OverpassError) as large:
        validate_bbox([0, 0, 1, 1], 0.2)
    assert large.value.code == "bbox_too_large"

    client = FakeClient(OVERPASS)
    first = fetch_osm_network(
        [1.0, 2.0, 1.01, 2.01],
        client=client,
        overpass_url="https://example.invalid/interpreter",
        timeout_s=5,
        max_retries=1,
        max_span=0.2,
        cache_dir=tmp_path,
        sleeper=lambda _seconds: None,
    )
    assert client.calls == 1
    assert first["cache_hit"] is False
    assert "OpenStreetMap" in first["attribution"]
    assert first["network"]["edges"]
    second = fetch_osm_network(
        [1.0, 2.0, 1.01, 2.01],
        client=FakeClient(OVERPASS),
        overpass_url="https://example.invalid/interpreter",
        timeout_s=5,
        max_retries=1,
        max_span=0.2,
        cache_dir=tmp_path,
        sleeper=lambda _seconds: None,
    )
    assert second["cache_hit"] is True
    cached = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert cached["source"]["provider"] == "OpenStreetMap"

    class Timeout(Exception):
        pass

    timing = FakeClient(None, error=Timeout("slow"))
    with pytest.raises(OverpassError) as timed:
        fetch_osm_network(
            [1.0, 2.0, 1.01, 2.01],
            client=timing,
            overpass_url="https://example.invalid/interpreter",
            timeout_s=5,
            max_retries=1,
            max_span=0.2,
            cache_dir=None,
            sleeper=lambda _seconds: None,
        )
    assert timed.value.code == "timeout"
    assert timing.calls == 2

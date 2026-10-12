"use client";

import maplibregl, { type GeoJSONSource, type Map } from "maplibre-gl";
import { useEffect, useRef } from "react";

import { colorForSpeedRatio, speedRatio } from "@/lib/layers";
import type { MapLayers } from "@/lib/types";

const TILE_URL = process.env.NEXT_PUBLIC_MAP_TILE_URL || "";

function styleDocument(): maplibregl.StyleSpecification {
  const sources: maplibregl.StyleSpecification["sources"] = {};
  const layers: maplibregl.LayerSpecification[] = [
    { id: "background", type: "background", paint: { "background-color": "#d5e3ea" } },
  ];
  if (TILE_URL) {
    sources.basemap = {
      type: "raster",
      tiles: [TILE_URL],
      tileSize: 256,
      attribution:
        "Configured raster tiles. If the URL serves OpenStreetMap data, attribute © OpenStreetMap contributors and follow that provider's tile policy.",
    };
    layers.push({ id: "basemap", type: "raster", source: "basemap" });
  }
  return { version: 8, sources, layers };
}

export function MapCanvas({
  layers,
  onSelect,
}: {
  layers: MapLayers | null;
  onSelect: (featureId: string) => void;
}) {
  const container = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<Map | null>(null);
  const selectRef = useRef(onSelect);
  selectRef.current = onSelect;

  useEffect(() => {
    if (!container.current || mapRef.current) {
      return;
    }
    const map = new maplibregl.Map({
      container: container.current,
      style: styleDocument(),
      center: [0.015, 0.015],
      zoom: 12,
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.on("load", () => {
      map.addSource("roads", { type: "geojson", data: emptyCollection() });
      map.addSource("nodes", { type: "geojson", data: emptyCollection() });
      map.addSource("changes", { type: "geojson", data: emptyCollection() });
      map.addLayer({
        id: "roads",
        type: "line",
        source: "roads",
        paint: {
          "line-color": ["coalesce", ["get", "color"], "#8aa0b8"],
          "line-width": ["case", ["==", ["get", "changed"], true], 6, 4],
        },
      });
      map.addLayer({
        id: "nodes",
        type: "circle",
        source: "nodes",
        paint: {
          "circle-radius": 6,
          "circle-color": "#1f3b4d",
          "circle-stroke-width": 1,
          "circle-stroke-color": "#ffffff",
        },
      });
      map.addLayer({
        id: "signals",
        type: "circle",
        source: "nodes",
        filter: ["!=", ["get", "signal_id"], ""],
        paint: {
          "circle-radius": 7,
          "circle-color": "#d9782d",
          "circle-stroke-width": 1,
          "circle-stroke-color": "#ffffff",
        },
      });
      map.addLayer({
        id: "changes",
        type: "circle",
        source: "changes",
        paint: { "circle-radius": 5, "circle-color": "#7a2f9b", "circle-stroke-width": 1, "circle-stroke-color": "#fff" },
      });
      map.on("click", "roads", (event) => {
        const featureId = event.features?.[0]?.properties?.road_id;
        if (featureId) {
          selectRef.current(String(featureId));
        }
      });
      map.on("click", "nodes", (event) => {
        const featureId = event.features?.[0]?.properties?.node_id;
        if (featureId) {
          selectRef.current(String(featureId));
        }
      });
      map.on("mouseenter", "roads", () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", "roads", () => {
        map.getCanvas().style.cursor = "";
      });
    });
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !layers) {
      return;
    }
    const apply = () => {
      const roads = {
        ...layers.roads,
        features: layers.roads.features.map((feature) => {
          const properties = feature.properties || {};
          const ratio = speedRatio(
            properties.simulated_speed_kmh as number | null,
            properties.free_flow_speed_kmh as number | null,
          );
          return { ...feature, properties: { ...properties, color: colorForSpeedRatio(ratio) } };
        }),
      };
      (map.getSource("roads") as GeoJSONSource | undefined)?.setData(roads as Parameters<GeoJSONSource["setData"]>[0]);
      const nodes = {
        ...layers.nodes,
        features: layers.nodes.features.map((feature) => ({
          ...feature,
          properties: { ...feature.properties, signal_id: feature.properties.signal_id || "" },
        })),
      };
      (map.getSource("nodes") as GeoJSONSource | undefined)?.setData(nodes as Parameters<GeoJSONSource["setData"]>[0]);
      (map.getSource("changes") as GeoJSONSource | undefined)?.setData(
        layers.changes as Parameters<GeoJSONSource["setData"]>[0],
      );
      if (layers.bbox) {
        const [west, south, east, north] = layers.bbox;
        map.fitBounds(
          [
            [west, south],
            [east, north],
          ],
          { padding: 40, duration: 0, maxZoom: 15 },
        );
      }
    };
    if (map.isStyleLoaded() && map.getSource("roads")) {
      apply();
    } else {
      map.once("load", apply);
    }
  }, [layers]);

  return <div ref={container} className="map-canvas" role="application" aria-label="Road network map" />;
}

function emptyCollection() {
  return { type: "FeatureCollection" as const, features: [] };
}

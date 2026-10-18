from pathlib import Path
import math
import sqlite3
import unicodedata

import geopandas as gpd
from flask import Flask, Response, jsonify, request, send_file, send_from_directory
from rapidfuzz import fuzz

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"
MBTILES_PATH = BASE_DIR / "El_Salvador.mbtiles"
ROADS_PATH = BASE_DIR / "roads.gpkg"

app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="/frontend")

roads_gdf = None
search_records = None


def normalize_text(value):
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.lower().strip().split())


def load_roads():
    global roads_gdf

    if roads_gdf is not None:
        return roads_gdf

    if not ROADS_PATH.exists():
        return None

    roads_gdf = gpd.read_file(ROADS_PATH)

    if roads_gdf.crs is None:
        roads_gdf = roads_gdf.set_crs(4326)

    return roads_gdf


def geometry_point(geom):
    if geom is None or geom.is_empty:
        return None

    geom_type = geom.geom_type

    if geom_type == "Point":
        return geom

    if geom_type in ("LineString", "LinearRing"):
        return geom.interpolate(0.5, normalized=True)

    if geom_type == "MultiLineString":
        parts = [g for g in geom.geoms if not g.is_empty]

        if not parts:
            return None

        longest = max(parts, key=lambda g: g.length)
        return longest.interpolate(0.5, normalized=True)

    if geom_type in ("Polygon", "MultiPolygon"):
        return geom.representative_point()

    if geom_type == "GeometryCollection":
        linear_parts = []

        for part in geom.geoms:
            if part.is_empty:
                continue

            if part.geom_type in ("LineString", "LinearRing"):
                linear_parts.append(part)

            elif part.geom_type == "MultiLineString":
                linear_parts.extend(
                    g for g in part.geoms
                    if not g.is_empty
                )

        if linear_parts:
            longest = max(linear_parts, key=lambda g: g.length)
            return longest.interpolate(0.5, normalized=True)

        return geom.representative_point()

    return geom.representative_point()


def build_search_index():
    global search_records

    if search_records is not None:
        return search_records

    gdf = load_roads()

    if gdf is None or gdf.empty:
        search_records = []
        return search_records

    wgs = gdf.to_crs(4326)

    records = []
    seen = set()

    for _, row in wgs.iterrows():
        geom = row.geometry

        if geom is None or geom.is_empty:
            continue

        name = ""

        for column in ["name", "road_name", "street", "ref", "highway"]:
            if column not in wgs.columns:
                continue

            value = row.get(column)

            if value is not None and str(value).strip():
                name = str(value).strip()
                break

        if not name:
            continue

        normalized = normalize_text(name)

        if not normalized or normalized in seen:
            continue

        point = geometry_point(geom)

        if point is None:
            continue

        seen.add(normalized)

        records.append({
            "name": name,
            "normalized": normalized,
            "lat": float(point.y),
            "lon": float(point.x),
        })

    search_records = records
    return search_records


@app.get("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.get("/styles.css")
def styles():
    return send_from_directory(FRONTEND_DIR, "styles.css")


@app.get("/app.js")
def javascript():
    return send_from_directory(FRONTEND_DIR, "app.js")


@app.get("/Navia.svg")
def logo():
    return send_file(FRONTEND_DIR / "assets" / "Navia.svg", mimetype="image/svg+xml")


@app.get("/tiles/<int:z>/<int:x>/<int:y>.pbf")
def tile(z, x, y):
    if not MBTILES_PATH.exists():
        return "", 404

    tms_y = (1 << z) - 1 - y

    with sqlite3.connect(MBTILES_PATH) as db:
        row = db.execute(
            "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
            (z, x, tms_y),
        ).fetchone()

    if row is None:
        return "", 404

    return Response(
        row[0],
        status=200,
        content_type="application/vnd.mapbox-vector-tile",
        headers={
            "Content-Encoding": "gzip",
            "Cache-Control": "public, max-age=86400",
            "Access-Control-Allow-Origin": "*",
        },
    )


@app.get("/search")
def search():
    query = request.args.get("q", "").strip()

    if len(query) < 2:
        return jsonify([])

    records = build_search_index()

    if not records:
        return jsonify([])

    q = normalize_text(query)
    ranked = []

    for record in records:
        name = record["normalized"]

        if q == name:
            score = 100
        elif q in name:
            score = 96 if name.startswith(q) else 91
        elif name in q:
            score = 88
        else:
            score = max(
                fuzz.WRatio(q, name),
                fuzz.partial_ratio(q, name),
                fuzz.token_set_ratio(q, name),
            )

        ranked.append((float(score), record))

    ranked.sort(key=lambda item: item[0], reverse=True)

    best_score = ranked[0][0]

    if best_score < 42:
        return jsonify([])

    results = []

    for score, record in ranked[:8]:
        if score < max(42, best_score - 28):
            continue

        results.append({
            "name": record["name"],
            "label": "El Salvador",
            "lat": record["lat"],
            "lon": record["lon"],
            "score": round(score, 1),
        })

    if not results and ranked:
        score, record = ranked[0]

        results.append({
            "name": record["name"],
            "label": "El Salvador",
            "lat": record["lat"],
            "lon": record["lon"],
            "score": round(score, 1),
        })

    return jsonify(results)


def nearest_segment_point(gdf, lon, lat):
    projected = gdf

    if projected.crs is None:
        projected = projected.set_crs(4326)

    if projected.crs.is_geographic:
        projected = projected.to_crs(32616)

    point = gpd.GeoSeries.from_xy(
        [lon],
        [lat],
        crs=4326
    ).to_crs(projected.crs).iloc[0]

    line_indices = [
        idx
        for idx, geom in projected.geometry.items()
        if geom is not None
        and not geom.is_empty
        and geom.geom_type in ("LineString", "MultiLineString", "LinearRing")
    ]

    if not line_indices:
        raise ValueError("roads.gpkg no contiene geometrías lineales utilizables")

    linear = projected.loc[line_indices].copy()

    distances = linear.geometry.distance(point)
    idx = distances.idxmin()
    geom = linear.loc[idx].geometry

    if geom.geom_type == "MultiLineString":
        parts = [g for g in geom.geoms if not g.is_empty]

        if not parts:
            raise ValueError("Geometría MultiLineString vacía")

        geom = min(parts, key=lambda g: g.distance(point))

    snapped = geom.interpolate(geom.project(point))

    return gpd.GeoSeries(
        [snapped],
        crs=projected.crs
    ).to_crs(4326).iloc[0]


@app.post("/route")
def route():
    payload = request.get_json(silent=True) or {}
    start = payload.get("start")
    end = payload.get("end")

    if not start or not end:
        return jsonify({"routes": []}), 400

    gdf = load_roads()

    if gdf is None or gdf.empty:
        return jsonify({"routes": []}), 404

    try:
        start_snap = nearest_segment_point(gdf, start[0], start[1])
        end_snap = nearest_segment_point(gdf, end[0], end[1])
    except ValueError as error:
        return jsonify({
            "routes": [],
            "error": str(error),
        }), 500

    distance_km = haversine_km(
        start_snap.y,
        start_snap.x,
        end_snap.y,
        end_snap.x,
    )

    minutes = max(1, round(distance_km / 30 * 60))

    return jsonify({
        "routes": [{
            "name": "Ruta 1",
            "minutes": minutes,
            "distance_km": distance_km,
            "tag": "Base",
            "geometry": [
                [start_snap.x, start_snap.y],
                [end_snap.x, end_snap.y],
            ],
        }]
    })


def haversine_km(lat1, lon1, lat2, lon2):
    radius = 6371.0088
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    )

    return 2 * radius * math.asin(math.sqrt(a))


if __name__ == "__main__":
    load_roads()
    build_search_index()
    app.run(host="127.0.0.1", port=8000, debug=True)

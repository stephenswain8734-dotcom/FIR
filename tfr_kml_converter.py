"""Convert FAA Temporary Flight Restriction data to KML and KMZ.

The default source is the FAA GeoServer feed used by the public Graphic TFRs
site. You can still pass the public export page URL; the script rewrites it to
the actual JSON feed before fetching.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

KML_NS = "http://www.opengis.net/kml/2.2"
ET.register_namespace("", KML_NS)

PUBLIC_FAA_EXPORT_URL = "https://tfr.faa.gov/tfr3/export/json"
FAA_GEOSERVER_JSON_URL = (
    "https://tfr.faa.gov/geoserver/TFR/ows"
    "?service=WFS&version=1.1.0&request=GetFeature&typeName=TFR:V_TFR_LOC"
    "&maxFeatures=300&outputFormat=application/json&srsname=EPSG:4326"
)


def qn(tag: str) -> str:
    return f"{{{KML_NS}}}{tag}"


@dataclass
class TfrFeature:
    name: str
    description: str = ""
    polygons: Optional[List[List[Tuple[float, float]]]] = None
    line_strings: Optional[List[List[Tuple[float, float]]]] = None
    point: Optional[Tuple[float, float]] = None
    begin: Optional[str] = None
    end: Optional[str] = None
    source_url: Optional[str] = None
    last_modified: Optional[datetime] = None


class ConversionError(RuntimeError):
    pass


def env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name, default)
    return value if value not in ("", None) else default


def resolve_source_url(source_url: str) -> str:
    if source_url.rstrip("/") == PUBLIC_FAA_EXPORT_URL:
        return FAA_GEOSERVER_JSON_URL
    return source_url


def clamp_text(text: str, max_len: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "..."


def fetch_source(url: str) -> Tuple[bytes, str]:
    timeout = int(env("REQUEST_TIMEOUT", "30") or "30")
    user_agent = env("USER_AGENT", "tfr-kml-converter/1.0") or "tfr-kml-converter/1.0"
    request = Request(url, headers={"User-Agent": user_agent, "Accept": "*/*"})
    try:
        with urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            return response.read(), content_type
    except HTTPError as exc:
        raise ConversionError(f"Source returned HTTP {exc.code}: {url}") from exc
    except URLError as exc:
        raise ConversionError(f"Could not reach source {url}: {exc.reason}") from exc


def guess_kind(url: str, content_type: str, data: bytes) -> str:
    path = urlparse(url).path.lower()
    if path.endswith(".kmz") or content_type in ("application/vnd.google-earth.kmz", "application/zip"):
        return "kmz"
    if path.endswith(".kml") or content_type in ("application/vnd.google-earth.kml+xml", "application/xml", "text/xml"):
        return "kml"
    if path.endswith((".json", ".geojson")) or content_type in ("application/json", "application/geo+json", "text/json"):
        return "json"
    if data.lstrip()[:1] in (b"{", b"["):
        return "json"
    if data.lstrip().startswith(b"<"):
        return "xml"
    return "unknown"


def parse_geojson(data: bytes, source_url: str) -> List[TfrFeature]:
    obj = json.loads(data.decode("utf-8", errors="replace"))
    features = obj.get("features", []) if isinstance(obj, dict) else []
    out: List[TfrFeature] = []
    max_name_len = int(env("MAX_NAME_LEN", "120") or "120")

    for idx, feat in enumerate(features, start=1):
        if not isinstance(feat, dict):
            continue
        props = feat.get("properties", {}) if isinstance(feat.get("properties"), dict) else {}
        geom = feat.get("geometry", {}) if isinstance(feat.get("geometry"), dict) else {}
        name = first_present(
            props,
            ["TITLE", "title", "name", "NOTAM_KEY", "id", "tfr", "LEGAL"],
            f"TFR {idx}",
        )
        desc = build_description(props)
        begin = first_present(props, ["BEGIN", "begin", "effective_from", "start"], None)
        end = first_present(props, ["END", "end", "effective_to", "stop"], None)
        last_modified = parse_faa_timestamp(
            first_present(props, ["LAST_MODIFICATION_DATETIME", "last_modification_datetime"], None)
        )

        polygons: List[List[Tuple[float, float]]] = []
        lines: List[List[Tuple[float, float]]] = []
        point: Optional[Tuple[float, float]] = None

        gtype = (geom.get("type") or "").lower()
        coords = geom.get("coordinates")
        if gtype == "polygon" and isinstance(coords, list):
            polygons.extend(_geojson_polygon_coords(coords))
        elif gtype == "multipolygon" and isinstance(coords, list):
            for poly in coords:
                polygons.extend(_geojson_polygon_coords(poly))
        elif gtype == "linestring" and isinstance(coords, list):
            lines.append(_geojson_positions(coords))
        elif gtype == "multilinestring" and isinstance(coords, list):
            for line in coords:
                lines.append(_geojson_positions(line))
        elif gtype == "point" and isinstance(coords, list) and len(coords) >= 2:
            point = (float(coords[0]), float(coords[1]))

        out.append(
            TfrFeature(
                name=clamp_text(str(name), max_name_len),
                description=desc,
                polygons=polygons or None,
                line_strings=lines or None,
                point=point,
                begin=str(begin) if begin else None,
                end=str(end) if end else None,
                source_url=source_url,
                last_modified=last_modified,
            )
        )
    return out


def first_present(props: dict[str, Any], keys: Sequence[str], default: Any) -> Any:
    for key in keys:
        value = props.get(key)
        if value not in ("", None):
            return value
    return default


def build_description(props: dict[str, Any]) -> str:
    preferred = [
        ("NOTAM", first_present(props, ["NOTAM_KEY", "notam_key"], None)),
        ("Location", first_present(props, ["CNS_LOCATION_ID", "cns_location_id"], None)),
        ("State", first_present(props, ["STATE", "state"], None)),
        ("Type", first_present(props, ["LEGAL", "legal"], None)),
        ("Last Modified", first_present(props, ["LAST_MODIFICATION_DATETIME", "last_modification_datetime"], None)),
    ]
    lines = [f"{label}: {value}" for label, value in preferred if value]
    extra_description = first_present(props, ["description", "summary"], None)
    if extra_description:
        lines.insert(0, str(extra_description))
    return "\n".join(lines)


def parse_faa_timestamp(value: Any) -> Optional[datetime]:
    if value in ("", None):
        return None
    text = re.sub(r"\D", "", str(value))
    for fmt, length in (("%Y%m%d%H%M", 12), ("%Y%m%d%H%M%S", 14), ("%Y%m%d", 8)):
        if len(text) == length:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
    return None


def _geojson_positions(coords: Sequence[Any]) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    for pair in coords:
        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
            out.append((float(pair[0]), float(pair[1])))
    return out


def _geojson_polygon_coords(coords: Sequence[Any]) -> List[List[Tuple[float, float]]]:
    out: List[List[Tuple[float, float]]] = []
    for ring in coords:
        if isinstance(ring, (list, tuple)):
            pos = _geojson_positions(ring)
            if len(pos) >= 3:
                out.append(pos)
    return out


def parse_existing_kml(data: bytes, source_url: str) -> List[TfrFeature]:
    root = ET.fromstring(data)
    features: List[TfrFeature] = []
    max_name_len = int(env("MAX_NAME_LEN", "120") or "120")

    for idx, placemark in enumerate(root.findall(f".//{{{KML_NS}}}Placemark"), start=1):
        name = placemark.findtext(f"{{{KML_NS}}}name") or f"TFR {idx}"
        desc = placemark.findtext(f"{{{KML_NS}}}description") or ""
        begin = placemark.findtext(f".//{{{KML_NS}}}begin")
        end = placemark.findtext(f".//{{{KML_NS}}}end")
        polygons: List[List[Tuple[float, float]]] = []
        lines: List[List[Tuple[float, float]]] = []
        point = None

        for poly in placemark.findall(f".//{{{KML_NS}}}Polygon"):
            outer = poly.find(f".//{{{KML_NS}}}outerBoundaryIs//{{{KML_NS}}}coordinates")
            if outer is not None and outer.text:
                coords = _parse_kml_coordinates(outer.text)
                if len(coords) >= 3:
                    polygons.append(coords)
        for line in placemark.findall(f".//{{{KML_NS}}}LineString"):
            coord_node = line.find(f".//{{{KML_NS}}}coordinates")
            if coord_node is not None and coord_node.text:
                coords = _parse_kml_coordinates(coord_node.text)
                if len(coords) >= 2:
                    lines.append(coords)
        pt = placemark.find(f".//{{{KML_NS}}}Point//{{{KML_NS}}}coordinates")
        if pt is not None and pt.text:
            coords = _parse_kml_coordinates(pt.text)
            if coords:
                point = coords[0]

        features.append(
            TfrFeature(
                name=clamp_text(name, max_name_len),
                description=desc,
                polygons=polygons or None,
                line_strings=lines or None,
                point=point,
                begin=begin,
                end=end,
                source_url=source_url,
            )
        )
    return features


def _parse_kml_coordinates(text: str) -> List[Tuple[float, float]]:
    coords: List[Tuple[float, float]] = []
    for token in re.split(r"\s+", text.strip()):
        if not token:
            continue
        parts = token.split(",")
        if len(parts) >= 2:
            coords.append((float(parts[0]), float(parts[1])))
    return coords


def parse_xml_generic(data: bytes, source_url: str) -> List[TfrFeature]:
    root = ET.fromstring(data)
    features: List[TfrFeature] = []
    candidates = list(root.findall(".//item")) or list(root.findall(".//entry")) or list(root)
    max_name_len = int(env("MAX_NAME_LEN", "120") or "120")

    for idx, node in enumerate(candidates, start=1):
        text = " ".join([t.strip() for t in node.itertext() if t and t.strip()])
        name = _find_first_text(node, ["title", "name", "id", "identifier"]) or f"TFR {idx}"
        desc = _find_first_text(node, ["description", "summary", "content"]) or text
        begin = _find_first_text(node, ["begin", "start", "effectiveFrom", "effective_from"])
        end = _find_first_text(node, ["end", "stop", "effectiveTo", "effective_to"])

        coords = _extract_coordinates_from_text(text)
        polygons = [coords] if len(coords) >= 3 else None
        lines = [coords] if len(coords) == 2 else None
        point = coords[0] if len(coords) == 1 else None

        features.append(
            TfrFeature(
                name=clamp_text(name, max_name_len),
                description=desc,
                polygons=polygons,
                line_strings=lines,
                point=point,
                begin=begin,
                end=end,
                source_url=source_url,
            )
        )
    return features


def _find_first_text(node: ET.Element, names: Sequence[str]) -> Optional[str]:
    for name in names:
        found = node.find(f".//{name}")
        if found is not None and found.text and found.text.strip():
            return found.text.strip()
    return None


def _extract_coordinates_from_text(text: str) -> List[Tuple[float, float]]:
    pairs: List[Tuple[float, float]] = []
    tokens = re.findall(r"[-+]?\d+(?:\.\d+)?\s*,\s*[-+]?\d+(?:\.\d+)?", text)
    for token in tokens:
        a, b = [float(v.strip()) for v in token.split(",", 1)]
        if abs(a) <= 180 and abs(b) <= 90:
            lon, lat = a, b
        elif abs(b) <= 180 and abs(a) <= 90:
            lon, lat = b, a
        else:
            lon, lat = a, b
        pairs.append((lon, lat))
    return pairs


def parse_feed(data: bytes, kind: str, source_url: str) -> List[TfrFeature]:
    if kind == "json":
        return parse_geojson(data, source_url)
    if kind == "kml":
        return parse_existing_kml(data, source_url)
    if kind == "xml":
        return parse_xml_generic(data, source_url)
    if kind == "kmz":
        return parse_kmz(data, source_url)
    raise ConversionError(f"Unsupported source kind: {kind}")


def parse_kmz(data: bytes, source_url: str) -> List[TfrFeature]:
    with tempfile.TemporaryDirectory() as tmpdir:
        kmz_path = Path(tmpdir) / "source.kmz"
        kmz_path.write_bytes(data)
        with zipfile.ZipFile(kmz_path, "r") as zf:
            kml_names = [name for name in zf.namelist() if name.lower().endswith(".kml")]
            if not kml_names:
                raise ConversionError("KMZ does not contain a KML file")
            kml_bytes = zf.read(kml_names[0])
        return parse_existing_kml(kml_bytes, source_url)


def build_kml(features: Sequence[TfrFeature]) -> bytes:
    kml = ET.Element(qn("kml"))
    doc = ET.SubElement(kml, qn("Document"))

    ET.SubElement(doc, qn("name")).text = "Active TFRs"
    ET.SubElement(doc, qn("description")).text = "Current Temporary Flight Restrictions converted for operational mapping."

    add_style(doc, "tfrRecentStyle", line_color="ff00ff00", poly_color="6600ff00")
    add_style(doc, "tfrSixHourStyle", line_color="ff00ffff", poly_color="6600ffff")
    add_style(doc, "tfrDefaultStyle", line_color="ff0000ff", poly_color="660000ff")

    now = datetime.now(timezone.utc)

    for feature in features:
        placemark = ET.SubElement(doc, qn("Placemark"))
        ET.SubElement(placemark, qn("name")).text = feature.name
        desc_lines = [feature.description.strip()] if feature.description else []
        if feature.begin:
            desc_lines.append(f"Begin: {feature.begin}")
        if feature.end:
            desc_lines.append(f"End: {feature.end}")
        if feature.source_url:
            desc_lines.append(f"Source: {feature.source_url}")
        ET.SubElement(placemark, qn("description")).text = "\n".join([line for line in desc_lines if line])
        ET.SubElement(placemark, qn("styleUrl")).text = f"#{style_id_for_feature(feature, now)}"

        if feature.polygons:
            for ring in feature.polygons:
                poly_el = ET.SubElement(placemark, qn("Polygon"))
                ET.SubElement(poly_el, qn("extrude")).text = "1"
                ET.SubElement(poly_el, qn("altitudeMode")).text = "clampToGround"
                outer = ET.SubElement(poly_el, qn("outerBoundaryIs"))
                lr = ET.SubElement(outer, qn("LinearRing"))
                ET.SubElement(lr, qn("coordinates")).text = _coords_to_kml(ring)
        elif feature.line_strings:
            for line_coords in feature.line_strings:
                ls = ET.SubElement(placemark, qn("LineString"))
                ET.SubElement(ls, qn("tessellate")).text = "1"
                ET.SubElement(ls, qn("altitudeMode")).text = "clampToGround"
                ET.SubElement(ls, qn("coordinates")).text = _coords_to_kml(line_coords)
        elif feature.point:
            pt = ET.SubElement(placemark, qn("Point"))
            ET.SubElement(pt, qn("coordinates")).text = f"{feature.point[0]},{feature.point[1]},0"

    return ET.tostring(kml, encoding="utf-8", xml_declaration=True)


def add_style(doc: ET.Element, style_id: str, line_color: str, poly_color: str) -> None:
    style = ET.SubElement(doc, qn("Style"), id=style_id)
    line = ET.SubElement(style, qn("LineStyle"))
    ET.SubElement(line, qn("color")).text = line_color
    ET.SubElement(line, qn("width")).text = "3"
    poly = ET.SubElement(style, qn("PolyStyle"))
    ET.SubElement(poly, qn("color")).text = poly_color


def style_id_for_feature(feature: TfrFeature, now: datetime) -> str:
    if not feature.last_modified:
        return "tfrDefaultStyle"
    age_hours = (now - feature.last_modified).total_seconds() / 3600
    if 0 <= age_hours <= 1:
        return "tfrRecentStyle"
    if 1 < age_hours <= 6:
        return "tfrSixHourStyle"
    return "tfrDefaultStyle"


def _coords_to_kml(coords: Sequence[Tuple[float, float]]) -> str:
    parts = [f"{lon},{lat},0" for lon, lat in coords]
    if len(coords) >= 3 and coords[0] != coords[-1]:
        parts.append(f"{coords[0][0]},{coords[0][1]},0")
    return " ".join(parts)


def write_kmz(kml_bytes: bytes, kmz_path: Path) -> None:
    with zipfile.ZipFile(kmz_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("doc.kml", kml_bytes)


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    default_output_dir = script_dir / "output"
    source_url = resolve_source_url(env("SOURCE_URL", PUBLIC_FAA_EXPORT_URL) or PUBLIC_FAA_EXPORT_URL)
    output_kml = Path(env("OUTPUT_KML", str(default_output_dir / "active_tfrs.kml")) or "")
    output_kmz = Path(env("OUTPUT_KMZ", str(default_output_dir / "active_tfrs.kmz")) or "")
    create_kmz = env("CREATE_KMZ", "1") not in ("0", "false", "False", "no", "NO")

    output_kml.parent.mkdir(parents=True, exist_ok=True)
    if create_kmz:
        output_kmz.parent.mkdir(parents=True, exist_ok=True)

    raw, content_type = fetch_source(source_url)
    kind = guess_kind(source_url, content_type, raw)
    features = parse_feed(raw, kind, source_url)
    if not features:
        raise ConversionError("No features were found in the source feed")

    kml_bytes = build_kml(features)
    output_kml.write_bytes(kml_bytes)
    print(f"Wrote {output_kml} with {len(features)} features")

    if create_kmz:
        write_kmz(kml_bytes, output_kmz)
        print(f"Wrote {output_kmz}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)

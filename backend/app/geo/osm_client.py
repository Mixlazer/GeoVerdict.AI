from __future__ import annotations

import asyncio
from math import asin, cos, radians, sin, sqrt
from random import Random
import re
from urllib.parse import quote

import httpx

from app.config import settings
from app.models.schemas import (
    AddressInfo,
    BuildingCandidate,
    BuildingInsight,
    GeoContext,
    Poi,
    StreetInsight,
)


BUILDING_TYPE_LABELS = {
    "yes": "Здание",
    "building": "Здание",
    "commercial": "Коммерческое здание",
    "retail": "Торговое здание",
    "mixed_use": "Смешанное использование",
    "office": "Офисное здание",
    "apartments": "Апартаменты",
    "residential": "Жилой дом",
    "house": "Дом",
    "college": "Колледж",
    "school": "Школа",
    "university": "Университет",
    "industrial": "Промышленное здание",
    "warehouse": "Склад",
    "supermarket": "Супермаркет",
    "mall": "Торговый центр",
    "civic": "Общественное здание",
    "public": "Общественное здание",
    "hospital": "Медицинское здание",
    "train_station": "Вокзал",
    "transportation": "Транспортный объект",
    "service": "Сервисное здание",
    "garage": "Гараж",
    "kiosk": "Киоск",
}

LOW_PRIORITY_BUILDINGS = {"garage", "shed", "hut", "roof", "religious", "chapel", "church", "cathedral", "barn"}
OVERPASS_ENDPOINTS = [
    settings.overpass_url,
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6_371_000
    d_lat = radians(lat2 - lat1)
    d_lng = radians(lng2 - lng1)
    a = sin(d_lat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(d_lng / 2) ** 2
    return 2 * radius * asin(sqrt(a))


def _classify_street(road: str | None) -> str:
    if not road:
        return "mixed"
    road_l = road.lower()
    if any(token in road_l for token in ("проспект", "шоссе", "avenue", "boulevard", "тракт", "набережн")):
        return "arterial"
    if any(token in road_l for token in ("улица", "street", "lane", "переулок", "проезд")):
        return "local"
    return "mixed"


def _classify_neighborhood(
    street_type: str,
    transit_count: int,
    anchor_count: int,
    district: str | None = None,
    city: str | None = None,
) -> tuple[str, str]:
    district_l = (district or "").lower()
    city_l = (city or "").lower()
    central_markers = {
        "москва": ("преснен", "тверск", "басман", "арбат", "хамовник", "замосквореч", "красносель"),
        "санкт-петербург": ("центральн", "адмиралт", "петроград"),
    }
    if any(marker in district_l for marker in central_markers.get(city_l, ())):
        return "центр", "центральный городской район с плотным уличным контуром и высокой деловой активностью"
    if transit_count >= 4 and anchor_count >= 4 and street_type in {"arterial", "mixed"}:
        return "центр", "сильный транзитный поток и насыщенный городской фронт по красным линиям улиц"
    if anchor_count >= 3 and transit_count >= 2 and street_type == "local":
        return "спальный район", "плотная повседневная застройка и регулярный локальный поток жителей"
    if transit_count >= 1 and anchor_count >= 1:
        return "окраина", "район не центральный, но вокруг уже есть базовый городской поток и повседневная активность"
    if transit_count == 0 and anchor_count <= 1 and street_type == "local":
        return "окраина", "слабее насыщенность коммерцией и ниже транзитность улиц"
    return "пригород", "точка больше похожа на периферийный или малоплотный городской контур"


def _poi_address(tags: dict, fallback_city: str) -> str | None:
    street = tags.get("addr:street")
    house = tags.get("addr:housenumber")
    city = tags.get("addr:city") or fallback_city
    if street and house:
        return f"{street}, {house}, {city}"
    if street:
        return f"{street}, {city}"
    return None


def _parse_preferred_address(preferred_address: str | None, city_hint: str) -> AddressInfo | None:
    if not preferred_address:
        return None
    parts = [part.strip() for part in preferred_address.split(",") if part.strip()]
    if not parts:
        return None
    road = parts[0]
    house_number = parts[1] if len(parts) > 1 and any(char.isdigit() for char in parts[1]) else None
    district = next((part for part in parts if "район" in part.lower()), None)
    city = next((part for part in parts if part.lower() == city_hint.lower()), city_hint)
    return AddressInfo(
        display_name=preferred_address,
        city=city or city_hint,
        district=district,
        road=road,
        house_number=house_number,
    )


def _apply_preferred_address(address: AddressInfo, preferred_address: str | None, city_hint: str) -> AddressInfo:
    preferred = _parse_preferred_address(preferred_address, city_hint)
    if preferred is None:
        return address
    return AddressInfo(
        display_name=preferred.display_name or address.display_name,
        city=preferred.city or address.city,
        district=preferred.district or address.district,
        road=preferred.road or address.road,
        house_number=preferred.house_number or address.house_number,
    )


def _sanitize_llm_summary(text: str | None) -> str | None:
    if not text:
        return text
    cleaned = text.replace("**", "")
    cleaned = re.sub(r"^#+\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _summarize_anchor_pois(anchor_pois: list[Poi]) -> str:
    if not anchor_pois:
        return "без выраженного коммерческого контура рядом"
    top = []
    for poi in anchor_pois[:3]:
        label = poi.name
        if label.lower() == poi.kind.lower():
            label = poi.kind.replace("_", " ")
        top.append(label)
    return ", ".join(top)


async def _fetch_wikipedia_summary(query: str) -> str | None:
    road_hint = query.split(",")[0].strip().lower()
    road_tokens = [token for token in re.findall(r"[а-яa-z0-9-]+", road_hint) if len(token) > 2 and token not in {"улица", "проспект", "проезд", "переулок", "шоссе"}]
    search_url = "https://ru.wikipedia.org/w/api.php"
    headers = {"User-Agent": settings.user_agent}
    async with httpx.AsyncClient(timeout=6) as client:
        search_response = await client.get(
            search_url,
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "format": "json",
                "utf8": 1,
                "srlimit": 1,
            },
            headers=headers,
        )
        search_response.raise_for_status()
        search_payload = search_response.json()
        hits = ((search_payload.get("query") or {}).get("search")) or []
        if not hits:
            return None
        title = hits[0].get("title")
        if not title:
            return None
        title_l = title.lower()
        if road_tokens and not any(token in title_l for token in road_tokens):
            return None
        summary_response = await client.get(
            f"https://ru.wikipedia.org/api/rest_v1/page/summary/{quote(title)}",
            headers=headers,
        )
        summary_response.raise_for_status()
        summary_payload = summary_response.json()
        extract = (summary_payload.get("extract") or "").strip()
        extract_l = extract.lower()
        if road_tokens and not any(token in extract_l or token in title_l for token in road_tokens):
            return None
        return extract if extract else None


async def reverse_geocode(lat: float, lng: float) -> AddressInfo:
    params = {"format": "jsonv2", "lat": lat, "lon": lng, "addressdetails": 1}
    headers = {"User-Agent": settings.user_agent}
    async with httpx.AsyncClient(timeout=settings.geo_request_timeout_seconds) as client:
        response = await client.get(settings.nominatim_url, params=params, headers=headers)
        response.raise_for_status()
        payload = response.json()
    address = payload.get("address", {})
    city = (
        address.get("city")
        or address.get("town")
        or address.get("municipality")
        or address.get("state")
        or settings.default_city
    )
    district = address.get("city_district") or address.get("suburb")
    road = address.get("road")
    house_number = address.get("house_number")
    return AddressInfo(
        display_name=payload.get("display_name", f"{city}, Россия"),
        city=city,
        district=district,
        road=road,
        house_number=house_number,
    )


async def _overpass_json(query: str) -> dict:
    headers = {"User-Agent": settings.user_agent}
    last_error: Exception | None = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            async with httpx.AsyncClient(timeout=settings.geo_request_timeout_seconds) as client:
                response = await client.post(endpoint, data=query, headers=headers)
                response.raise_for_status()
                return response.json()
        except Exception as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    raise RuntimeError("Overpass query failed")


async def fetch_pois(lat: float, lng: float, radius_m: int = 500) -> list[Poi]:
    query = f"""
    [out:json][timeout:25];
    (
      node(around:{radius_m},{lat},{lng})[amenity];
      node(around:{radius_m},{lat},{lng})[shop];
      node(around:{radius_m},{lat},{lng})[public_transport];
      node(around:{radius_m},{lat},{lng})[railway=station];
      node(around:{radius_m},{lat},{lng})[highway=bus_stop];
    );
    out center 60;
    """
    payload = await _overpass_json(query)

    pois: list[Poi] = []
    for element in payload.get("elements", []):
        tags = element.get("tags", {})
        poi_lat = element.get("lat")
        poi_lng = element.get("lon")
        if poi_lat is None or poi_lng is None:
            continue
        kind = (
            tags.get("shop")
            or tags.get("amenity")
            or tags.get("public_transport")
            or tags.get("railway")
            or tags.get("highway")
            or "poi"
        )
        name = tags.get("name") or tags.get("brand") or kind.replace("_", " ")
        pois.append(
            Poi(
                name=name,
                kind=kind,
                address=_poi_address(tags, settings.default_city),
                lat=poi_lat,
                lng=poi_lng,
                distance_m=round(haversine_m(lat, lng, poi_lat, poi_lng), 1),
                weight=1.0,
            )
        )
    return sorted(pois, key=lambda item: item.distance_m)


def _format_building_address(tags: dict, fallback_city: str) -> str:
    street = tags.get("addr:street") or tags.get("street")
    house = tags.get("addr:housenumber")
    city = tags.get("addr:city") or fallback_city
    if street and house:
        return f"{street}, {house}, {city}"
    if street:
        return f"{street}, {city}"
    return f"{city}, Россия"


def _normalize_building_type(tags: dict) -> tuple[str, str]:
    raw = (tags.get("building") or "").strip().lower() or "building"
    if raw == "yes":
        if tags.get("shop"):
            raw = "retail"
        elif tags.get("amenity") in {"college", "school", "university"}:
            raw = tags.get("amenity")
        elif tags.get("amenity") == "hospital":
            raw = "hospital"
        else:
            raw = "building"
    label = BUILDING_TYPE_LABELS.get(raw, raw.replace("_", " ").capitalize())
    return raw, label


def _is_generic_name(name: str | None, category_label: str) -> bool:
    if not name:
        return True
    normalized = name.strip().lower()
    return normalized in {
        category_label.strip().lower(),
        "building",
        "здание",
        "apartments",
        "yes",
    }


def _display_building_name(address: str, tags: dict, category_label: str) -> str:
    explicit_name = tags.get("name") or tags.get("brand") or tags.get("addr:housename")
    if address and address != "Россия":
        return address
    if explicit_name and not _is_generic_name(explicit_name, category_label):
        return explicit_name
    return f"{category_label}, {tags.get('addr:city') or settings.default_city}"


def _fallback_buildings(lat: float, lng: float, city: str) -> list[BuildingCandidate]:
    seed = int(abs(lat * 100_000) + abs(lng * 100_000))
    rng = Random(seed)
    buildings: list[BuildingCandidate] = []
    categories = ["Коммерческое здание", "Смешанное использование", "Жилой дом"]
    for index in range(8):
        shift_lat = rng.uniform(-0.00045, 0.00045)
        shift_lng = rng.uniform(-0.00055, 0.00055)
        candidate_lat = round(lat + shift_lat, 6)
        candidate_lng = round(lng + shift_lng, 6)
        address = f"Улица {index + 1}, {city}"
        category_label = categories[index % len(categories)]
        buildings.append(
            BuildingCandidate(
                osm_id=f"fallback-{index + 1}",
                name=address,
                building_type=category_label,
                category_label=category_label,
                address=address,
                year_built=None,
                levels=None,
                lat=candidate_lat,
                lng=candidate_lng,
                distance_m=round(haversine_m(lat, lng, candidate_lat, candidate_lng), 1),
                source="fallback",
                match_score=0.4,
            )
        )
    return sorted(buildings, key=lambda item: item.distance_m)


def _candidate_limit(radius_m: int) -> int:
    if radius_m <= 100:
        return 2
    if radius_m <= 300:
        return 7
    if radius_m <= 500:
        return 10
    if radius_m <= 700:
        return 12
    return 15


async def fetch_buildings(lat: float, lng: float, city_hint: str, radius_m: int = 120) -> list[BuildingCandidate]:
    query = f"""
    [out:json][timeout:30];
    (
      way(around:{radius_m},{lat},{lng})["building"];
      relation(around:{radius_m},{lat},{lng})["building"];
    );
    out center tags 200;
    """
    try:
        reverse_task = asyncio.create_task(reverse_geocode(lat, lng))
        overpass_task = asyncio.create_task(_overpass_json(query))
        payload = await overpass_task
        try:
            preferred_address = await reverse_task
        except Exception:
            preferred_address = None
        preferred_road = (preferred_address.road or "").strip().lower() if preferred_address else ""
        preferred_house = (preferred_address.house_number or "").strip().lower() if preferred_address else ""
        buildings: list[tuple[float, BuildingCandidate]] = []
        for element in payload.get("elements", []):
            center = element.get("center") or {}
            candidate_lat = center.get("lat")
            candidate_lng = center.get("lon")
            if candidate_lat is None or candidate_lng is None:
                continue
            tags = element.get("tags", {})
            raw_type, category_label = _normalize_building_type(tags)
            if raw_type in LOW_PRIORITY_BUILDINGS:
                continue
            address = _format_building_address(tags, city_hint)
            distance_m = round(haversine_m(lat, lng, candidate_lat, candidate_lng), 1)
            match_bonus = 0.0
            if tags.get("addr:housenumber"):
                match_bonus += 0.16
            if preferred_road and (tags.get("addr:street") or "").strip().lower() == preferred_road:
                match_bonus += 0.24
            if preferred_house and (tags.get("addr:housenumber") or "").strip().lower() == preferred_house:
                match_bonus += 0.34
            if tags.get("shop") or tags.get("amenity") or raw_type in {"commercial", "retail", "office", "mixed_use"}:
                match_bonus += 0.18
            if raw_type in {"apartments", "residential", "house"}:
                match_bonus += 0.06
            if tags.get("name"):
                match_bonus += 0.08
            candidate = BuildingCandidate(
                osm_id=f"{element.get('type', 'way')}-{element.get('id')}",
                name=_display_building_name(address, tags, category_label),
                building_type=category_label,
                category_label=category_label,
                address=address,
                year_built=str(
                    tags.get("start_date")
                    or tags.get("building:year_built")
                    or tags.get("construction_date")
                )
                if (tags.get("start_date") or tags.get("building:year_built") or tags.get("construction_date"))
                else None,
                levels=str(tags.get("building:levels")) if tags.get("building:levels") else None,
                lat=round(candidate_lat, 6),
                lng=round(candidate_lng, 6),
                distance_m=distance_m,
                source="live",
                match_score=0.0,
            )
            distance_score = max(0.08, 1 - min(distance_m, radius_m) / max(radius_m, 50))
            candidate.match_score = round(max(0.1, min(0.99, distance_score + match_bonus)), 2)
            rank_score = distance_m - (candidate.match_score * 120)
            buildings.append((rank_score, candidate))
        if buildings:
            unique: dict[str, BuildingCandidate] = {}
            for _, item in sorted(buildings, key=lambda pair: pair[0]):
                unique.setdefault(item.osm_id, item)
            limit = _candidate_limit(radius_m)
            return list(unique.values())[:limit]
    except Exception:
        return []
    return []


async def _fetch_building_tags(lat: float, lng: float) -> dict:
    query = f"""
    [out:json][timeout:25];
    (
      way(around:20,{lat},{lng})["building"];
      relation(around:20,{lat},{lng})["building"];
    );
    out tags center 5;
    """
    payload = await _overpass_json(query)
    elements = payload.get("elements", [])
    if not elements:
        return {}
    return elements[0].get("tags", {})


async def _search_web_snippets(query: str) -> list[str]:
    search_url = "https://duckduckgo.com/html/"
    params = {"q": query}
    headers = {"User-Agent": settings.user_agent}
    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.get(search_url, params=params, headers=headers)
        response.raise_for_status()
        html = response.text
    snippets: list[str] = []
    for chunk in html.split('result__snippet"')[:8]:
        if ">" not in chunk:
            continue
        snippet = chunk.split(">", 1)[-1].split("<", 1)[0].strip()
        snippet = re.sub(r"\s+", " ", snippet)
        if snippet and snippet not in snippets:
            snippets.append(snippet)
    return snippets[:5]


async def analyze_building_insight(
    lat: float,
    lng: float,
    name: str | None,
    address: str | None,
    city: str,
    include_web_search: bool = True,
) -> BuildingInsight:
    tags = {}
    try:
        tags = await _fetch_building_tags(lat, lng)
    except Exception:
        tags = {}

    year_built = tags.get("start_date") or tags.get("building:year_built") or tags.get("construction_date")
    levels = tags.get("building:levels")
    heritage = tags.get("heritage")

    snippets: list[str] = []
    search_terms = []
    if name:
        search_terms.append(f'"{name}" {city} отзывы состояние здания реконструкция')
    if address:
        search_terms.append(f'"{address}" {city} капремонт вход парковка')
    if include_web_search:
        for term in search_terms[:1]:
            try:
                snippets.extend(await _search_web_snippets(term))
            except Exception:
                continue

    reconstruction_notes = [item for item in snippets if any(token in item.lower() for token in ("рекон", "капрем", "ремонт", "обновл"))][:2]
    review_signals = [item for item in snippets if any(token in item.lower() for token in ("состоя", "фасад", "вход", "парков", "доступ"))][:3]
    risks: list[str] = []
    if heritage:
        risks.append("охранный статус может ограничивать фасадные изменения и размещение вывески")
    if year_built:
        try:
            if int(str(year_built)[:4]) < 1975:
                risks.append("старый фонд может усложнить инженерные доработки и входную группу")
        except Exception:
            pass
    if tags.get("building") in {"apartments", "residential"}:
        risks.append("жилой контур требует внимательной проверки ограничений по режиму работы и вывеске")

    summary_parts = []
    if year_built:
        summary_parts.append(f"год постройки/начала эксплуатации: {year_built}")
    if levels:
        summary_parts.append(f"этажность: {levels}")
    if reconstruction_notes:
        summary_parts.append("есть сигналы о ремонте или реконструкции")
    if not summary_parts:
        summary_parts.append("Справочная оценка: явных сигналов проблем нет, но данных о техническом состоянии немного.")

    source_notes = []
    if tags:
        source_notes.append("OSM building tags")
    if snippets:
        source_notes.append("web snippets")
    if not source_notes:
        source_notes.append("fallback heuristics")

    return BuildingInsight(
        year_built=str(year_built) if year_built else None,
        reconstruction_notes=reconstruction_notes,
        review_signals=review_signals,
        condition_summary=_sanitize_llm_summary("; ".join(summary_parts)),
        building_risks=risks,
        source_notes=source_notes,
    )


async def analyze_street_insight(geo_context: GeoContext, business_type: str) -> StreetInsight:
    road = geo_context.address.road or "улица без уточнённого названия"
    snippets: list[str] = []
    wiki_summary: str | None = None
    try:
        snippets = await _search_web_snippets(
            f'"{road}" {geo_context.address.city} магазины кафе ритейл пешеходы район'
        )
    except Exception:
        snippets = []
    try:
        wiki_summary = await _fetch_wikipedia_summary(f"{road} {geo_context.address.city}")
    except Exception:
        wiki_summary = None

    transit_factor = min(4, len(geo_context.transit_stops))
    anchor_factor = min(5, len(geo_context.anchor_pois))
    local_bonus = 1 if geo_context.street_type == "arterial" else 0 if geo_context.street_type == "mixed" else -1
    snippet_bonus = min(2, sum(1 for item in snippets if any(token in item.lower() for token in ("магаз", "кофе", "ритейл", "пешеход", "витрин"))))
    wiki_bonus = 1 if wiki_summary and any(token in wiki_summary.lower() for token in ("магистрал", "делов", "истор", "транспорт")) else 0
    pedestrian_flow = max(1, min(10, 3 + transit_factor + anchor_factor // 2 + max(local_bonus, 0) + wiki_bonus))
    retail_attractiveness = max(1, min(10, 3 + anchor_factor + local_bonus + snippet_bonus + wiki_bonus))
    explanation = (
        f"Улица '{road}' оценивается как {retail_attractiveness}/10 по торговой привлекательности: "
        f"учли транспорт, плотность трафикогенерирующих объектов и открытые веб-сигналы."
    )
    if business_type in {"coffee", "pharmacy"}:
        explanation += " Для малого ежедневного спроса здесь особенно важен пеший доступ."
    elif any(token in business_type for token in ("фитнес", "fitness")):
        explanation += " Для фитнес-формата усиливаем роль общественного транспорта."
    elif any(token in business_type for token in ("гипермаркет", "hypermarket", "cash&carry")):
        explanation += " Для крупного формата сильнее смотрим на магистральный подъезд."

    return StreetInsight(
        street_name=road,
        retail_attractiveness_score=retail_attractiveness,
        pedestrian_flow_score=pedestrian_flow,
        supporting_signals=snippets[:3] or [geo_context.neighborhood_reason or "контекст улицы оценён по плотности POI и транспорту"],
        source_facts=([wiki_summary] if wiki_summary else []) + snippets[:2],
        explanation=_sanitize_llm_summary(explanation),
    )


def _fallback_context(lat: float, lng: float, city: str) -> GeoContext:
    seed = int(abs(lat * 10_000) + abs(lng * 10_000))
    rng = Random(seed)
    address = AddressInfo(
        display_name=f"Центральная улица, {city}",
        city=city,
        district="Центральный",
        road="Центральная улица",
    )
    transit = [
        Poi(
            name=f"Остановка {idx + 1}",
            kind="bus_stop",
            lat=lat + rng.uniform(-0.001, 0.001),
            lng=lng + rng.uniform(-0.001, 0.001),
            distance_m=round(rng.uniform(40, 220), 1),
            weight=1.3,
        )
        for idx in range(rng.randint(2, 4))
    ]
    anchors = [
        Poi(
            name=name,
            kind=kind,
            lat=lat + rng.uniform(-0.0015, 0.0015),
            lng=lng + rng.uniform(-0.0015, 0.0015),
            distance_m=round(rng.uniform(60, 320), 1),
            weight=1.6,
        )
        for name, kind in [("Супермаркет", "supermarket"), ("БЦ Форум", "office"), ("Кофейня", "cafe")]
    ]
    neighborhood_type, neighborhood_reason = _classify_neighborhood("mixed", len(transit), len(anchors), address.district, city)
    return GeoContext(
        address=address,
        street_type="mixed",
        neighborhood_type=neighborhood_type,
        neighborhood_reason=neighborhood_reason,
        transit_stops=transit,
        anchor_pois=anchors,
        nearby_pois=[*transit, *anchors],
        data_source="fallback",
        confidence_penalty=0.12,
    )


async def build_geo_context(lat: float, lng: float, city_hint: str, preferred_address: str | None = None) -> GeoContext:
    address: AddressInfo | None = None
    pois: list[Poi] = []
    try:
        address = await reverse_geocode(lat, lng)
    except Exception:
        address = None
    try:
        pois = await fetch_pois(lat, lng)
    except Exception:
        pois = []

    preferred = _parse_preferred_address(preferred_address, city_hint)
    if address is None and preferred is not None:
        address = preferred
    elif address is not None:
        address = _apply_preferred_address(address, preferred_address, city_hint)

    if address is None:
        return _fallback_context(lat, lng, city_hint)

    transit = [poi for poi in pois if poi.kind in {"bus_stop", "station", "stop_position"}][:6]
    anchors = [
        poi
        for poi in pois
        if poi.kind in {"mall", "supermarket", "cafe", "restaurant", "office", "pharmacy", "clinic", "bank"}
    ][:10]
    street_type = _classify_street(address.road)
    neighborhood_type, neighborhood_reason = _classify_neighborhood(
        street_type,
        len(transit),
        len(anchors),
        address.district,
        address.city,
    )
    data_source = "live" if pois else "fallback"
    confidence_penalty = 0.0 if pois else 0.06
    return GeoContext(
        address=address,
        street_type=street_type,
        neighborhood_type=neighborhood_type,
        neighborhood_reason=f"{neighborhood_reason}; ключевые генераторы рядом: {_summarize_anchor_pois(anchors)}",
        transit_stops=transit,
        anchor_pois=anchors,
        nearby_pois=pois[:36],
        data_source=data_source,
        confidence_penalty=confidence_penalty,
    )

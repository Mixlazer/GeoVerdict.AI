# Retriever Spec

## Источники

- `Nominatim` — reverse geocoding
- `Overpass` — здания, POI, транспорт
- `DuckDuckGo HTML snippets` — улица и состояние здания

## Вход

- `lat`
- `lng`
- `city`
- `business_type`

## Выход

- `GeoContext`
- `BuildingCandidate[]`
- `BuildingInsight`
- `StreetInsight`

## Поиск и ранжирование

- Здания ранжируются по расстоянию, наличию адреса, коммерческих тегов и match score.
- При росте радиуса увеличивается лимит кандидатов.
- Значение `building=yes` нормализуется в русифицированную категорию и не попадает в UI как `yes`.

## Ограничения

- публичные rate limits;
- неполное покрытие OSM по отдельным точкам;
- web snippets не считаются authoritative source.

## Fallback

- synthetic building candidates;
- synthetic geo profile;
- пониженный confidence penalty.

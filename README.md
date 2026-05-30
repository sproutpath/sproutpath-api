# SproutPath Videos API

A small FastAPI service that serves the SproutPath video catalogue with optional `languages` and `age` filters. Wraps the upstream `videos.json` feed and exposes it through a single REST endpoint in the format the iOS client already expects.

## Project layout

```
sproutpath_api/
├── app/
│   ├── api/
│   │   └── videos.py          # /sproutpath/api/v1/getvideos route
│   ├── models/
│   │   └── video.py           # Pydantic response schemas
│   ├── services/
│   │   ├── upstream.py        # Loads + caches videos.json
│   │   └── filtering.py       # Language + age filters
│   ├── config.py              # Settings (env-driven)
│   └── main.py                # FastAPI factory
├── data/
│   └── videos.json            # Bundled source data (dev default)
├── tests/
│   └── test_videos.py
├── Dockerfile
├── requirements.txt
├── pytest.ini
└── README.md
```

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The API is then live at `http://localhost:8000`. OpenAPI docs at `http://localhost:8000/docs`.

## Endpoint

### `GET /sproutpath/api/v1/getvideos`

Returns the catalogue, optionally filtered.

#### Query parameters

| Param       | Type      | Required | Notes                                                                                                                                            |
| ----------- | --------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `languages` | string[]  | no       | One or more languages. Accepts ISO codes (`te`, `hi`, `en`) or full lowercase names (`telugu`, `hindi`). Repeat the param or comma-separate it. |
| `age`       | integer   | no       | Child's age in years (0–120). Keeps videos whose `age_range` covers the age. Videos with no `age_range` are always kept.                       |

Both filters compose with AND. With no filters, the full catalogue is returned.

#### Example requests

```bash
# Everything
curl 'http://localhost:8000/sproutpath/api/v1/getvideos'

# Telugu only (ISO code)
curl 'http://localhost:8000/sproutpath/api/v1/getvideos?languages=te'

# Telugu OR Hindi
curl 'http://localhost:8000/sproutpath/api/v1/getvideos?languages=telugu,hindi'

# Repeatable param form
curl 'http://localhost:8000/sproutpath/api/v1/getvideos?languages=telugu&languages=hindi'

# Age-appropriate for an 8-year-old
curl 'http://localhost:8000/sproutpath/api/v1/getvideos?age=8'

# Combined
curl 'http://localhost:8000/sproutpath/api/v1/getvideos?languages=te&age=8'
```

#### Response shape

```json
{
  "version": 9,
  "generated": "2026-05-17",
  "description": "Videos only accessible via the Videos tab category filter chips — no dedicated tile in Dashboard, Study or Activities tabs.",
  "total_videos": 308,
  "videos": [
    {
      "id": "lNjVc-xTWd0",
      "title": "Autism Help - How to Write Social Stories for Kids",
      "channel": "Autism Recovery Network",
      "category": "Autism Support",
      "duration": "3:14",
      "duration_seconds": 194,
      "description": "",
      "tags": ["calm", "communication", "socialSkills", "stories"],
      "age_range": "",
      "language": "english"
    }
  ]
}
```

`total_videos` reflects the **filtered** count (not the upstream feed total), so clients can use it directly for pagination / loading UI.

### `GET /healthz`

Liveness probe — returns `{"status": "ok", "version": "..."}`. Doesn't touch the upstream feed, safe to hit from load balancers.

## Configuration

All settings can be overridden via env vars with the `SPROUTPATH_` prefix:

| Variable                            | Default                     | Description                                            |
| ----------------------------------- | --------------------------- | ------------------------------------------------------ |
| `SPROUTPATH_DATA_PATH`              | `./data/videos.json`        | Local path to the bundled JSON feed.                   |
| `SPROUTPATH_DATA_URL`               | *(empty)*                   | If set, fetch from URL instead of the local file.      |
| `SPROUTPATH_REQUEST_TIMEOUT_SECONDS`| `10.0`                      | HTTP timeout for URL fetches.                          |
| `SPROUTPATH_CORS_ALLOW_ORIGINS`     | `["*"]`                     | CORS allowlist. JSON-encoded list when via env.        |

You can also drop a `.env` file in the project root with the same keys.

## Caching

The loader caches the parsed feed in process memory after the first request. Restart the app to pick up new data. (Adding a TTL or admin refresh endpoint is left for a follow-up if needed.)

## Tests

```bash
pytest
```

Tests run against the bundled `data/videos.json`, so they exercise the real loader, flattener, filters, and response schema end-to-end.

## Docker

```bash
docker build -t sproutpath-api .
docker run -p 8000:8000 sproutpath-api
```

For production, point at a live feed:

```bash
docker run -p 8000:8000 \
  -e SPROUTPATH_DATA_URL='https://example.com/videos.json' \
  sproutpath-api
```

## Notes on the data

The upstream `videos.json` is shaped as `{by_language: {language: {category: [videos]}}}`. The loader flattens this into a single list at startup, since the filters operate on a flat collection. Each video upstream already carries its `language` field, which is what the language filter checks — no inference from `category` or `tags` is needed.

`age_range` upstream is either a `"low-high"` string (e.g. `"3-12"`) or empty. Empty is treated as "all ages" by the filter — dropping those videos would hide a large chunk of the catalogue.

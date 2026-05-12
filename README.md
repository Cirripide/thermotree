# Thermotree

A web app that lets anyone pick **any city in the world** from a single search box and immediately see where it is hot and where it is green, summer by summer, computed on demand from satellite imagery.

## Why this project

Cities trap heat. Asphalt and concrete soak up the sun all day and release it slowly into the night, while trees and grass do the opposite. As the climate warms, urban heat becomes a growing health issue, especially for older residents and people who work outdoors.

The goal of this project is to **raise awareness of the need to plant more trees in cities**, and to give an initial indication of *which areas need them most*: the spots that come out both very hot **and** very low on vegetation. Those are the cells where new trees, parks, or green roofs would have the largest cooling impact, and where the conversation about urban greening should start.

This is a proof of concept and not a substitute for the work of local environmental authorities.

## What you get

* A single search input that finds any place worldwide (city, town, district) through OpenStreetMap.
* A full screen swipe map with two synchronized layers: **Heat** (Land Surface Temperature) on the left, **Vegetation** (NDVI) on the right. A draggable vertical slider sweeps between the two.
* A uniform **200 m × 200 m grid** clipped to the place's administrative boundary, with every cell carrying its own LST and NDVI value computed from the year's summer satellite passes.
* A year selector to compare summers. The earliest selectable year is 2022 (the first complete June, July and August with both Landsat 8 and Landsat 9 in nominal operation).
* Floating legends with fixed thermal bands and vegetation bands, so the same color always means the same value across cities and years.

There is **no per city configuration**. The city is a runtime input chosen by the user, the boundary is fetched on demand from OpenStreetMap, and the per cell satellite values are computed server side for that boundary. Switching cities is a single click in the UI.

## Quick start

```bash
cp .env.example .env       # optional provider overrides (defaults work out of the box)
make dev                   # builds and starts backend + frontend
```

When everything is up:

* Frontend: <http://localhost:4200>
* Backend: <http://localhost:8000>
* API docs: <http://localhost:8000/docs>

Pick a city in the search input, wait for the first composite to compute (boundary fetch + STAC search + median composite + zonal stats can take a couple of minutes the first time), then drag the slider. Every request is computed on demand against the live STAC catalog; there is no persistent cache at present.

## API

The backend is FastAPI, so it ships its own auto generated, always up to date OpenAPI documentation. Once `make dev` is running:

* Swagger UI: <http://localhost:8000/docs>
* ReDoc: <http://localhost:8000/redoc>
* Raw schema: <http://localhost:8000/openapi.json>

Endpoints are keyed by an **opaque OSM identifier** chosen by the user from the search results. The backend never knows what "Tokyo" or "São Paulo" means; it just receives an identifier and looks up the polygon.

## Data sources

| Layer | Source | Notes |
| --- | --- | --- |
| Heat (LST) | NASA and USGS **Landsat 8 + 9**, Collection 2 Level 2 Surface Temperature | 100 m thermal sensor, daytime passes only |
| Vegetation (NDVI) | ESA **Sentinel 2** Level 2A surface reflectance | 10 m red and near infrared bands; NDVI is the normalized difference of the two |
| Boundary | **OpenStreetMap** via Nominatim | One administrative polygon per query |
| Autocomplete | **Photon** (Komoot) over OSM | Fast prefix matching for the search box |

Imagery is read on demand as **Cloud Optimized GeoTIFFs** over STAC. Rasters are never persisted to disk. The STAC chain is **Planetary Computer first, Element84 as fallback** on transport errors. Landsat is served exclusively by Planetary Computer (Element84's Landsat assets live in a requester pays S3 bucket that needs AWS credentials); Sentinel 2 supports the full chain.

For each summer (June, July and August), the app combines every clear satellite pass over the city into a single **median composite**, which automatically filters out cloudy days, smoke, and short lived anomalies. The composite is then averaged inside each 200 m cell.

Cells appear as "no data" (diagonal stripes on the map) when the satellite could not get a clean read of that spot during the three summer months: too much cloud cover, too few clear passes, or no consistent signal across June, July and August. Water bodies are **not** masked: the surface temperature of a lake or a river is reported as is, alongside the rest of the cell.

## Configuration

Defaults work out of the box. Optional overrides in `.env`:

```env
# Self hosted Nominatim or Photon, or just a descriptive User Agent for
# compliance with the public instances' usage policy.
# NOMINATIM_BASE_URL=https://nominatim.openstreetmap.org
# NOMINATIM_USER_AGENT=thermotree/0.1 (contact: you@example.com)
# PHOTON_BASE_URL=https://photon.komoot.io
# PHOTON_USER_AGENT=thermotree/0.1 (contact: you@example.com)

# Reorder or drop STAC backends. Default: planetary_computer,element84
# STAC_PROVIDERS=planetary_computer,element84
```

The public Nominatim and Photon instances are fine for development but rate limited. Production deployments should run their own.

## Manual operations

| Action | Command |
| --- | --- |
| Start everything | `make dev` |
| Stop everything | `make down` |

## Project layout

```text
thermotree/
├── Makefile                     # reads frontend/.nvmrc, sets platform flag
├── docker-compose.yml           # backend + frontend services
├── .env / .env.example          # optional provider overrides
├── backend/                     # FastAPI, rioxarray, pystac-client
│   └── app/
│       ├── controllers/         # /geocode/search, /boundary/{osm_id}, /zones/{osm_id}/{year}
│       ├── services/            # boundary fetch, grid generation, zonal stats, imagery providers
│       └── core/                # config (target_resolution_meters, quality gates, ...)
└── frontend/                    # Angular 19, standalone components
    ├── .nvmrc                   # Node version, read by the Makefile
    └── src/app/                 # city picker, swipe map, about dialog
```

## Status and limitations

This is a proof of concept. Known limitations:

* The Northern Hemisphere summer window (June, July, August) is hard coded. Tropical and Southern Hemisphere cities will need a per latitude summer rule before this can credibly serve every place on Earth.
* AOIs above 3,000 km² are rejected with a 400. Very large metros (Tokyo, London, São Paulo) hit this cap and need either a smaller inner city polygon or internal tiling.
* The public Photon and Nominatim instances are not production grade dependencies at scale; respect their usage policies and self host before any real traffic.
* No auth, no rate limit, no observability beyond the FastAPI logs.

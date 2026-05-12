import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import type { FeatureCollection, Feature, MultiPolygon, Polygon } from 'geojson';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';

export interface GeocodeCandidate {
  osm_id: string;
  display_name: string;
  country: string | null;
  type: string;
  bbox: [number, number, number, number];
}

export type BoundaryFeature = Feature<Polygon | MultiPolygon> & {
  bbox: [number, number, number, number];
  properties: { osm_id: string; display_name: string };
};

export interface ZoneProperties {
  cell_id: string;
  row: number;
  col: number;
  lst_celsius: number | null;
  ndvi: number | null;
}

export type ZoneFeature = Feature<Polygon | MultiPolygon, ZoneProperties>;
export type ZoneCollection = FeatureCollection<Polygon | MultiPolygon, ZoneProperties>;

@Injectable({ providedIn: 'root' })
export class PlacesService {
  private readonly http = inject(HttpClient);
  private readonly base = environment.apiBaseUrl;

  search(q: string, limit = 10): Observable<GeocodeCandidate[]> {
    return this.http.get<GeocodeCandidate[]>(`${this.base}geocode/search`, {
      params: { q, limit: String(limit) },
    });
  }

  boundary(osmId: string): Observable<BoundaryFeature> {
    return this.http.get<BoundaryFeature>(
      `${this.base}boundary/${encodeURIComponent(osmId)}`,
    );
  }

  zones(osmId: string, year: number): Observable<ZoneCollection> {
    return this.http.get<ZoneCollection>(
      `${this.base}zones/${encodeURIComponent(osmId)}/${year}`,
    );
  }
}

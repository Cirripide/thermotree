import {
  AfterViewInit,
  Component,
  DestroyRef,
  ElementRef,
  OnInit,
  ViewChild,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { CommonModule } from '@angular/common';
import { Title, DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { FormControl, ReactiveFormsModule } from '@angular/forms';
import {
  MatAutocompleteModule,
  MatAutocompleteSelectedEvent,
} from '@angular/material/autocomplete';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatIconModule } from '@angular/material/icon';
import { MatChipsModule } from '@angular/material/chips';
import { MatSelectModule } from '@angular/material/select';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { AboutDialogComponent } from './components/about-dialog/about-dialog.component';
import {
  AttributionControl,
  GeoJSONSource,
  Map,
  NavigationControl,
  StyleSpecification,
} from 'maplibre-gl';
import Compare from '@maplibre/maplibre-gl-compare';
import { EMPTY, of } from 'rxjs';
import {
  catchError,
  debounceTime,
  distinctUntilChanged,
  filter,
  map,
  switchMap,
  tap,
} from 'rxjs/operators';
import { environment } from '../environments/environment';
import {
  BoundaryFeature,
  GeocodeCandidate,
  PlacesService,
  ZoneCollection,
} from './services/places.service';

const BOUNDARY_SOURCE = 'boundary';
const BOUNDARY_FILL_LAYER = 'boundary-fill';
const BOUNDARY_OUTLINE_LAYER = 'boundary-outline';

const ZONES_SOURCE = 'zones';
const ZONES_LST_VALUED_LAYER = 'zones-lst-valued';
const ZONES_LST_NODATA_LAYER = 'zones-lst-nodata';
const ZONES_LST_OUTLINE_LAYER = 'zones-lst-outline';
const ZONES_NDVI_VALUED_LAYER = 'zones-ndvi-valued';
const ZONES_NDVI_NODATA_LAYER = 'zones-ndvi-nodata';
const ZONES_NDVI_OUTLINE_LAYER = 'zones-ndvi-outline';
const HATCH_IMAGE_ID = 'hatch-nodata';

const ZONE_FILL_OPACITY = 0.7;

// Landsat 9 launched Sept 2021 and reached nominal operations in early 2022,
// so its first complete June–Aug summer is 2022. It is the most recent of the
// four reference satellites (Landsat 8/9 + Sentinel-2 A/B), so it sets the
// floor of the selectable year range.
const LANDSAT_9_FIRST_COMPLETE_SUMMER = 2022;

function computeAvailableYears(now: Date): number[] {
  // June–Aug summer is "complete" only once September has begun.
  const ceiling = now.getMonth() >= 8 ? now.getFullYear() : now.getFullYear() - 1;
  const years: number[] = [];
  for (let y = LANDSAT_9_FIRST_COMPLETE_SUMMER; y <= ceiling; y++) {
    years.push(y);
  }
  return years;
}

// matplotlib Inferno @ 5 evenly-spaced samples (perceptually uniform).
// Order matches the standard Inferno semantic: darkest = lowest value (cold),
// brightest = highest value (hot).
const INFERNO_5 = ['#000004', '#51127c', '#b73779', '#fc8961', '#fcfdbf'];
// Custom Urban-Greening 5-class scale: greys for built-up (NDVI < 0.6),
// greens for vegetated (NDVI ≥ 0.6). Built infrastructure recedes into the
// basemap; only vegetation carries chroma.
const URBAN_GREENING_5 = ['#4d4d4d', '#999999', '#e0e0e0', '#74c476', '#006d2c'];

// Fixed absolute breaks so the same value paints the same color across every
// city — the only way to support visual cross-city comparison.
const HEAT_BREAKS = [25, 30, 35, 40];        // °C
const VEG_BREAKS = [0.2, 0.4, 0.6, 0.8];     // NDVI

const DEFAULT_WORLD_BOUNDS: [number, number, number, number] = [-170, -55, 170, 70];

interface LegendBin {
  color: string;
  lower: number | null;   // null = open-ended below (rendered as "< upper")
  upper: number | null;   // null = open-ended above (rendered as "> lower")
  label?: string;         // semantic class name shown alongside the numeric range
}

const HEAT_LEGEND_FIXED: LegendBin[] = [
  { color: INFERNO_5[0], lower: null, upper: 25, label: 'Cool / Baseline' },
  { color: INFERNO_5[1], lower: 25,   upper: 30, label: 'Moderate' },
  { color: INFERNO_5[2], lower: 30,   upper: 35, label: 'High (Urban Standard)' },
  { color: INFERNO_5[3], lower: 35,   upper: 40, label: 'Warning / Hotspot' },
  { color: INFERNO_5[4], lower: 40,   upper: null, label: 'Critical Heat Zone' },
];

const VEG_LEGEND_FIXED: LegendBin[] = [
  { color: URBAN_GREENING_5[0], lower: null, upper: 0.2, label: 'Bare / Built-up' },
  { color: URBAN_GREENING_5[1], lower: 0.2,  upper: 0.4, label: 'Sparse vegetation' },
  { color: URBAN_GREENING_5[2], lower: 0.4,  upper: 0.6, label: 'Moderate vegetation' },
  { color: URBAN_GREENING_5[3], lower: 0.6,  upper: 0.8, label: 'Dense vegetation' },
  { color: URBAN_GREENING_5[4], lower: 0.8,  upper: null, label: 'Very dense vegetation' },
];

@Component({
  selector: 'app-root',
  imports: [
    CommonModule,
    ReactiveFormsModule,
    MatAutocompleteModule,
    MatFormFieldModule,
    MatInputModule,
    MatProgressSpinnerModule,
    MatIconModule,
    MatChipsModule,
    MatSelectModule,
    MatDialogModule,
  ],
  templateUrl: './app.component.html',
  styleUrl: './app.component.scss',
})
export class AppComponent implements OnInit, AfterViewInit {
  private readonly places = inject(PlacesService);
  private readonly titleService = inject(Title);
  private readonly http = inject(HttpClient);
  private readonly destroyRef = inject(DestroyRef);
  private readonly sanitizer = inject(DomSanitizer);
  private readonly dialog = inject(MatDialog);

  readonly queryControl = new FormControl<string | GeocodeCandidate>('', {
    nonNullable: true,
  });
  readonly results = signal<GeocodeCandidate[]>([]);
  readonly searching = signal(false);
  readonly mapLoading = signal(true);
  readonly mapError = signal(false);
  readonly boundaryLoading = signal(false);
  readonly boundaryError = signal<string | null>(null);
  readonly selectedName = signal<string | null>(null);
  readonly zonesLoading = signal(false);
  readonly zonesError = signal<string | null>(null);
  readonly hasZones = signal(false);
  readonly inputsLocked = computed(
    () => this.boundaryLoading() || this.zonesLoading(),
  );
  readonly heatLegend = signal<LegendBin[]>([]);
  readonly vegLegend = signal<LegendBin[]>([]);
  readonly heatHasNoData = signal(false);
  readonly vegHasNoData = signal(false);
  readonly colorsVisible = signal(true);
  readonly availableYears = signal<number[]>(computeAvailableYears(new Date()));
  readonly selectedYear = signal<number>(
    this.availableYears().at(-1) ?? LANDSAT_9_FIRST_COMPLETE_SUMMER,
  );
  // Desktop corner legends always render expanded; these signals exist so the
  // template bindings already in place stay valid. On compact viewports the
  // corner cards are hidden in CSS and the unified mobile card is shown.
  readonly heatExpanded = signal(true);
  readonly vegExpanded = signal(true);
  // Mirrors the legend-collapse CSS breakpoint, used by the template `@if`
  // for any compact-only chrome that needs JS coordination.
  readonly isCompact = signal(false);
  // Which scale is visible inside the mobile toggle card; flipped by the
  // segmented control. Persists across breakpoint resizes.
  readonly activeMobileLegend = signal<'heat' | 'veg'>('heat');
  // Mobile-only: the bottom-sheet open/closed state. Closed by default so
  // the map gets the full available height on small phones; user taps the
  // handle bar to slide it up.
  readonly legendOpen = signal(false);

  private currentOsmId: string | null = null;
  private mapLeft: Map | undefined;
  private mapRight: Map | undefined;
  private compare: Compare | undefined;

  @ViewChild('mapLeft') private mapLeftEl!: ElementRef<HTMLElement>;
  @ViewChild('mapRight') private mapRightEl!: ElementRef<HTMLElement>;
  @ViewChild('compareContainer') private compareContainerEl!: ElementRef<HTMLElement>;

  constructor() {
    effect(() => {
      if (this.inputsLocked()) {
        this.queryControl.disable({ emitEvent: false });
      } else {
        this.queryControl.enable({ emitEvent: false });
      }
    });
  }

  ngOnInit(): void {
    this.queryControl.valueChanges
      .pipe(
        map(v => (typeof v === 'string' ? v : '')),
        debounceTime(300),
        map(q => q.trim()),
        distinctUntilChanged(),
        filter(q => q.length >= 2),
        tap(() => this.searching.set(true)),
        switchMap(q =>
          this.places.search(q).pipe(
            catchError(err => {
              console.warn('[AppComponent] geocode/search failed', err);
              return of<GeocodeCandidate[]>([]);
            }),
          ),
        ),
        tap(() => this.searching.set(false)),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(results => this.results.set(results));

    // Mirror the legend-collapse breakpoint into a signal so the template
    // can switch between the desktop corner legends and the mobile toggle
    // card. Initial sync runs synchronously; subsequent changes via matchMedia.
    const mql = window.matchMedia('(max-width: 1055px)');
    this.isCompact.set(mql.matches);
    const onMqlChange = (e: MediaQueryListEvent): void => this.isCompact.set(e.matches);
    mql.addEventListener('change', onMqlChange);
    this.destroyRef.onDestroy(() => mql.removeEventListener('change', onMqlChange));
  }

  ngAfterViewInit(): void {
    this.http
      .get<StyleSpecification>(environment.basemapStyleUrl)
      .pipe(
        catchError(err => {
          console.error('[AppComponent] failed to load basemap style', err);
          this.mapLoading.set(false);
          this.mapError.set(true);
          return EMPTY;
        }),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(baseStyle => this.initMaps(baseStyle));
  }

  displayPlace = (place: GeocodeCandidate | string | null): string => {
    if (place == null) return '';
    return typeof place === 'string' ? place : place.display_name;
  };

  onSelect(event: MatAutocompleteSelectedEvent): void {
    const place = event.option.value as GeocodeCandidate;
    this.currentOsmId = place.osm_id;
    this.boundaryError.set(null);
    this.zonesError.set(null);
    this.hasZones.set(false);
    this.heatLegend.set([]);
    this.vegLegend.set([]);
    this.heatHasNoData.set(false);
    this.vegHasNoData.set(false);
    this.colorsVisible.set(true);
    this.clearZones();
    this.boundaryLoading.set(true);
    this.places
      .boundary(place.osm_id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: feature => {
          this.boundaryLoading.set(false);
          this.selectedName.set(feature.properties.display_name);
          this.titleService.setTitle(
            `${feature.properties.display_name} — Thermotree`,
          );
          this.renderBoundary(feature);
          this.loadZones(place.osm_id, this.selectedYear());
        },
        error: (err: HttpErrorResponse) => {
          this.boundaryLoading.set(false);
          this.boundaryError.set(this.extractErrorMessage(err));
        },
      });
  }

  toggleLegend(which: 'heat' | 'veg'): void {
    if (which === 'heat') this.heatExpanded.update(v => !v);
    else this.vegExpanded.update(v => !v);
  }

  setActiveMobileLegend(which: 'heat' | 'veg'): void {
    this.activeMobileLegend.set(which);
  }

  toggleLegendOpen(): void {
    this.legendOpen.update(v => !v);
  }

  onYearChange(year: number | null): void {
    if (year == null || year === this.selectedYear()) return;
    this.selectedYear.set(year);
    if (this.currentOsmId == null) return;
    this.zonesError.set(null);
    this.hasZones.set(false);
    this.heatLegend.set([]);
    this.vegLegend.set([]);
    this.heatHasNoData.set(false);
    this.vegHasNoData.set(false);
    this.clearZones();
    this.loadZones(this.currentOsmId, year);
  }

  highlight(text: string): SafeHtml {
    const raw = this.queryControl.value;
    const q = (typeof raw === 'string' ? raw : '').trim();
    if (!q) return this.sanitizer.bypassSecurityTrustHtml(text);
    const escaped = q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const html = text.replace(
      new RegExp(`(${escaped})`, 'ig'),
      '<strong>$1</strong>',
    );
    return this.sanitizer.bypassSecurityTrustHtml(html);
  }

  dismissBoundaryError(): void {
    this.boundaryError.set(null);
  }

  openAbout(): void {
    this.dialog.open(AboutDialogComponent, {
      panelClass: 'about-dialog-panel',
      maxWidth: '560px',
      width: 'calc(100% - 32px)',
      autoFocus: 'dialog',
      restoreFocus: true,
      ariaLabelledBy: 'about-dialog-title',
    });
  }

  private loadZones(osmId: string, year: number): void {
    this.zonesLoading.set(true);
    this.places
      .zones(osmId, year)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: fc => {
          this.zonesLoading.set(false);
          this.hasZones.set(true);
          this.renderZones(fc);
        },
        error: (err: HttpErrorResponse) => {
          this.zonesLoading.set(false);
          this.zonesError.set(this.extractErrorMessage(err));
        },
      });
  }

  private initMaps(baseStyle: StyleSpecification): void {
    if (this.mapLeft || !this.mapLeftEl) return;

    this.mapLeft = new Map({
      container: this.mapLeftEl.nativeElement,
      style: baseStyle,
      center: [0, 20],
      zoom: 1,
      minZoom: 1,
      maxZoom: 21,
      attributionControl: false,
    });
    this.mapLeft.addControl(new NavigationControl({}), 'top-left');
    this.mapLeft.addControl(
      new AttributionControl({ compact: true }),
      'bottom-left',
    );

    // Clone the parsed style so the two maps don't share mutable layer state.
    const styleClone = JSON.parse(JSON.stringify(baseStyle)) as StyleSpecification;
    this.mapRight = new Map({
      container: this.mapRightEl.nativeElement,
      style: styleClone,
      center: [0, 20],
      zoom: 1,
      minZoom: 1,
      maxZoom: 21,
      attributionControl: false,
    });

    let loaded = 0;
    const onLoad = (map: Map) => {
      if (!map.hasImage(HATCH_IMAGE_ID)) {
        map.addImage(HATCH_IMAGE_ID, buildHatchImage(), { pixelRatio: 1 });
      }
      // Defer the world fit to after the style has loaded and the canvas is
      // settled. Constructing the map with `bounds` on a small mobile canvas
      // (where `minZoom: 1` clamps the world fit) leaves MapLibre in a state
      // where the user's first fitBounds doesn't take effect.
      map.resize();
      map.fitBounds(DEFAULT_WORLD_BOUNDS, { padding: 20, animate: false });
      loaded += 1;
      if (loaded === 2) this.mapLoading.set(false);
    };
    this.mapLeft.on('load', () => onLoad(this.mapLeft!));
    this.mapRight.on('load', () => onLoad(this.mapRight!));

    this.compare = new Compare(
      this.mapLeft,
      this.mapRight,
      this.compareContainerEl.nativeElement,
      { orientation: 'vertical', mousemove: false },
    );

    this.destroyRef.onDestroy(() => {
      this.compare?.remove();
      this.mapLeft?.remove();
      this.mapRight?.remove();
    });
  }

  private renderBoundary(feature: BoundaryFeature): void {
    for (const map of this.maps()) {
      // MapLibre's default trackResize uses ResizeObserver, which fires after
      // paint. After a responsive layout change (sidebar flip, devtools mobile
      // toggle, orientation), the next synchronous fitBounds can use stale
      // canvas dimensions and the map appears not to move to the city. Force
      // a sync resize first.
      map.resize();
      const existing = map.getSource(BOUNDARY_SOURCE) as
        | GeoJSONSource
        | undefined;
      if (existing) {
        existing.setData(feature);
      } else {
        map.addSource(BOUNDARY_SOURCE, { type: 'geojson', data: feature });
        map.addLayer({
          id: BOUNDARY_FILL_LAYER,
          type: 'fill',
          source: BOUNDARY_SOURCE,
          paint: { 'fill-color': '#2D6E4E', 'fill-opacity': 0.06 },
        });
        map.addLayer({
          id: BOUNDARY_OUTLINE_LAYER,
          type: 'line',
          source: BOUNDARY_SOURCE,
          paint: {
            'line-color': '#2D6E4E',
            'line-width': 2,
            'line-opacity': 0.9,
          },
        });
      }
      map.fitBounds(feature.bbox, { padding: 40, maxZoom: 13 });
    }
  }

  private renderZones(fc: ZoneCollection): void {
    if (!this.mapLeft || !this.mapRight) return;

    // Only flag no-data presence; the color scales themselves are fixed.
    let heatHasNoData = false;
    let vegHasNoData = false;
    for (const ft of fc.features) {
      const { lst_celsius, ndvi } = ft.properties;
      if (lst_celsius == null || !Number.isFinite(lst_celsius)) heatHasNoData = true;
      if (ndvi == null || !Number.isFinite(ndvi)) vegHasNoData = true;
    }

    this.heatLegend.set(HEAT_LEGEND_FIXED);
    this.vegLegend.set(VEG_LEGEND_FIXED);
    this.heatHasNoData.set(heatHasNoData);
    this.vegHasNoData.set(vegHasNoData);

    this.renderIndicatorLayers(
      this.mapLeft,
      fc,
      'lst_celsius',
      ZONES_LST_VALUED_LAYER,
      ZONES_LST_NODATA_LAYER,
      ZONES_LST_OUTLINE_LAYER,
      HEAT_BREAKS,
      INFERNO_5,
    );
    this.renderIndicatorLayers(
      this.mapRight,
      fc,
      'ndvi',
      ZONES_NDVI_VALUED_LAYER,
      ZONES_NDVI_NODATA_LAYER,
      ZONES_NDVI_OUTLINE_LAYER,
      VEG_BREAKS,
      URBAN_GREENING_5,
    );
  }

  private renderIndicatorLayers(
    map: Map,
    fc: ZoneCollection,
    property: 'lst_celsius' | 'ndvi',
    valuedLayerId: string,
    noDataLayerId: string,
    outlineLayerId: string,
    breaks: number[],
    palette: string[],
  ): void {
    const stepExpr = buildStepExpression(property, breaks, palette);

    const existing = map.getSource(ZONES_SOURCE) as GeoJSONSource | undefined;
    if (existing) {
      existing.setData(fc);
      map.setPaintProperty(valuedLayerId, 'fill-color', stepExpr as never);
    } else {
      map.addSource(ZONES_SOURCE, { type: 'geojson', data: fc });
      map.addLayer(
        {
          id: valuedLayerId,
          type: 'fill',
          source: ZONES_SOURCE,
          filter: ['!=', ['get', property], null],
          paint: {
            'fill-color': stepExpr as never,
            'fill-opacity': ZONE_FILL_OPACITY,
          },
        },
        BOUNDARY_OUTLINE_LAYER,
      );
      map.addLayer(
        {
          id: noDataLayerId,
          type: 'fill',
          source: ZONES_SOURCE,
          filter: ['==', ['get', property], null],
          paint: {
            'fill-pattern': HATCH_IMAGE_ID,
            'fill-opacity': ZONE_FILL_OPACITY,
          },
        },
        BOUNDARY_OUTLINE_LAYER,
      );
      // Independent outline layer: draws every cell's edge regardless of fill
      // state. Stays visible when toggleColors() hides the fills above.
      map.addLayer(
        {
          id: outlineLayerId,
          type: 'line',
          source: ZONES_SOURCE,
          paint: {
            'line-color': 'rgba(26, 34, 24, 0.28)',
            'line-width': 0.5,
          },
        },
        BOUNDARY_OUTLINE_LAYER,
      );
    }
  }

  private clearZones(): void {
    const layerIds = [
      ZONES_LST_VALUED_LAYER,
      ZONES_LST_NODATA_LAYER,
      ZONES_LST_OUTLINE_LAYER,
      ZONES_NDVI_VALUED_LAYER,
      ZONES_NDVI_NODATA_LAYER,
      ZONES_NDVI_OUTLINE_LAYER,
    ];
    for (const map of this.maps()) {
      for (const id of layerIds) {
        if (map.getLayer(id)) map.removeLayer(id);
      }
      if (map.getSource(ZONES_SOURCE)) map.removeSource(ZONES_SOURCE);
    }
  }

  toggleColors(): void {
    const next = !this.colorsVisible();
    this.colorsVisible.set(next);
    const visibility = next ? 'visible' : 'none';
    // Only the fill layers toggle — the per-cell outline layers stay visible
    // in both states so the 300m grid frames the basemap when colors are off.
    const fillLayerIds = [
      ZONES_LST_VALUED_LAYER,
      ZONES_LST_NODATA_LAYER,
      ZONES_NDVI_VALUED_LAYER,
      ZONES_NDVI_NODATA_LAYER,
    ];
    for (const map of this.maps()) {
      for (const id of fillLayerIds) {
        if (map.getLayer(id)) {
          map.setLayoutProperty(id, 'visibility', visibility);
        }
      }
    }
  }

  private maps(): Map[] {
    const out: Map[] = [];
    if (this.mapLeft) out.push(this.mapLeft);
    if (this.mapRight) out.push(this.mapRight);
    return out;
  }

  private extractErrorMessage(err: HttpErrorResponse): string {
    const body = err?.error;
    if (typeof body === 'string') return body;
    if (body?.message) return body.message;
    if (body?.detail) {
      if (typeof body.detail === 'string') return body.detail;
      if (body.detail?.message) return body.detail.message;
    }
    return 'Unable to load this city.';
  }
}

/**
 * Build a MapLibre `step` expression from interior breaks:
 * values < breaks[0] → palette[0],
 * breaks[0] ≤ values < breaks[1] → palette[1], …,
 * values ≥ breaks[N-1] → palette[N].
 * Requires breaks.length === palette.length − 1.
 */
function buildStepExpression(
  property: string,
  breaks: number[],
  palette: string[],
): unknown[] {
  const expr: unknown[] = ['step', ['get', property], palette[0]];
  for (let i = 0; i < breaks.length; i++) {
    expr.push(breaks[i], palette[i + 1]);
  }
  return expr;
}

/**
 * 16×16 tile with diagonal stripes — registered with each map as the
 * `fill-pattern` for cells whose indicator value is null. Geometry of the
 * three line segments tessellates seamlessly across tile borders.
 */
function buildHatchImage(): ImageData {
  const size = 16;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('2D canvas context unavailable');
  ctx.fillStyle = '#E5E0D2';
  ctx.fillRect(0, 0, size, size);
  ctx.strokeStyle = '#9B9989';
  ctx.lineWidth = 3;
  ctx.lineCap = 'square';
  ctx.beginPath();
  ctx.moveTo(-4, 4);  ctx.lineTo(4, -4);
  ctx.moveTo(-4, 20); ctx.lineTo(20, -4);
  ctx.moveTo(12, 20); ctx.lineTo(20, 12);
  ctx.stroke();
  return ctx.getImageData(0, 0, size, size);
}

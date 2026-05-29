export const BOUNDARY_SOURCE = 'boundary' as const;
export const BOUNDARY_FILL_LAYER = 'boundary-fill' as const;
export const BOUNDARY_OUTLINE_LAYER = 'boundary-outline' as const;

export const ZONES_SOURCE = 'zones' as const;
export const ZONES_LST_VALUED_LAYER = 'zones-lst-valued' as const;
export const ZONES_LST_NODATA_LAYER = 'zones-lst-nodata' as const;
export const ZONES_LST_OUTLINE_LAYER = 'zones-lst-outline' as const;
export const ZONES_NDVI_VALUED_LAYER = 'zones-ndvi-valued' as const;
export const ZONES_NDVI_NODATA_LAYER = 'zones-ndvi-nodata' as const;
export const ZONES_NDVI_OUTLINE_LAYER = 'zones-ndvi-outline' as const;
export const HATCH_IMAGE_ID = 'hatch-nodata' as const;

export const ZONE_FILL_OPACITY = 0.7 as const;

// Landsat 9 launched Sept 2021 and reached nominal operations in early 2022,
// so its first complete June–Aug summer is 2022. It is the most recent of the
// four reference satellites (Landsat 8/9 + Sentinel-2 A/B), so it sets the
// floor of the selectable year range.
export const LANDSAT_9_FIRST_COMPLETE_SUMMER = 2022 as const;

// matplotlib Inferno @ 5 evenly-spaced samples (perceptually uniform).
// Order matches the standard Inferno semantic: darkest = lowest value (cold),
// brightest = highest value (hot).
export const INFERNO_5 = ['#000004', '#51127c', '#b73779', '#fc8961', '#fcfdbf'] as const;
// Custom Urban-Greening 5-class scale: greys for built-up (NDVI < 0.6),
// greens for vegetated (NDVI ≥ 0.6). Built infrastructure recedes into the
// basemap; only vegetation carries chroma.
export const URBAN_GREENING_5 = ['#4d4d4d', '#999999', '#e0e0e0', '#74c476', '#006d2c'] as const;

// Fixed absolute breaks so the same value paints the same color across every
// city — the only way to support visual cross-city comparison.
export const HEAT_BREAKS = [25, 30, 35, 40] as const;        // °C
export const VEG_BREAKS = [0.2, 0.4, 0.6, 0.8] as const;     // NDVI

export const DEFAULT_WORLD_BOUNDS: [number, number, number, number] = [-170, -55, 170, 70];

export interface LegendBin {
  readonly color: string;
  readonly lower: number | null;   // null = open-ended below (rendered as "< upper")
  readonly upper: number | null;   // null = open-ended above (rendered as "> lower")
  readonly label?: string;         // semantic class name shown alongside the numeric range
}

export const HEAT_LEGEND_FIXED: readonly LegendBin[] = [
  { color: INFERNO_5[0], lower: null, upper: 25, label: 'Cool / Baseline' },
  { color: INFERNO_5[1], lower: 25,   upper: 30, label: 'Moderate' },
  { color: INFERNO_5[2], lower: 30,   upper: 35, label: 'High (Urban Standard)' },
  { color: INFERNO_5[3], lower: 35,   upper: 40, label: 'Warning / Hotspot' },
  { color: INFERNO_5[4], lower: 40,   upper: null, label: 'Critical Heat Zone' },
] as const;

export const VEG_LEGEND_FIXED: readonly LegendBin[] = [
  { color: URBAN_GREENING_5[0], lower: null, upper: 0.2, label: 'Bare / Built-up' },
  { color: URBAN_GREENING_5[1], lower: 0.2,  upper: 0.4, label: 'Sparse vegetation' },
  { color: URBAN_GREENING_5[2], lower: 0.4,  upper: 0.6, label: 'Moderate vegetation' },
  { color: URBAN_GREENING_5[3], lower: 0.6,  upper: 0.8, label: 'Dense vegetation' },
  { color: URBAN_GREENING_5[4], lower: 0.8,  upper: null, label: 'Very dense vegetation' },
] as const;

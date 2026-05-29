import { LANDSAT_9_FIRST_COMPLETE_SUMMER } from './map-visualization.constants';

export function computeAvailableYears(now: Date): number[] {
  // June–Aug summer is "complete" only once September has begun.
  const ceiling = now.getMonth() >= 8 ? now.getFullYear() : now.getFullYear() - 1;
  const years: number[] = [];
  for (let y = LANDSAT_9_FIRST_COMPLETE_SUMMER; y <= ceiling; y++) {
    years.push(y);
  }
  return years;
}

/**
 * Build a MapLibre `step` expression from interior breaks:
 * values < breaks[0] → palette[0],
 * breaks[0] ≤ values < breaks[1] → palette[1], …,
 * values ≥ breaks[N-1] → palette[N].
 * Requires breaks.length === palette.length − 1.
 */
export function buildStepExpression(
  property: string,
  breaks: readonly number[],
  palette: readonly string[],
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
export function buildHatchImage(): ImageData {
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

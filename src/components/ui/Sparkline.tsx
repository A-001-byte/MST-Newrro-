import { useId } from 'react';

interface SparklineProps {
  values: number[];
  width?: number;
  height?: number;
  color?: string;
  /** Fixed domain; defaults to data min/max with padding */
  domain?: [number, number];
  label?: string;
  /** Dashed reference line (e.g. quarantine threshold) */
  threshold?: number;
}

export function Sparkline({ values, width = 120, height = 32, color = 'var(--color-accent)', domain, label, threshold }: SparklineProps) {
  const id = `spark${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`;
  const pts = values.length === 1 ? [values[0], values[0]] : values;
  const lo = domain?.[0] ?? Math.min(...pts) - 3;
  const hi = domain?.[1] ?? Math.max(...pts) + 3;
  const span = hi - lo || 1;
  const step = width / Math.max(1, pts.length - 1);
  const xy = pts.map((v, i) => [i * step, height - 2 - ((v - lo) / span) * (height - 4)] as const);
  const line = xy.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const area = `${line} L${width},${height} L0,${height} Z`;
  const [lx, ly] = xy[xy.length - 1];
  const ty = threshold == null || threshold < lo || threshold > hi ? null : height - 2 - ((threshold - lo) / span) * (height - 4);

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label ?? `Trend, latest ${values[values.length - 1]}`} className="overflow-visible">
      <defs>
        <linearGradient id={id} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor={color} stopOpacity="0.22" />
          <stop offset="1" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      {ty != null && <line x1="0" x2={width} y1={ty} y2={ty} stroke="white" strokeOpacity="0.18" strokeDasharray="3 4" />}
      <path d={area} fill={`url(#${id})`} />
      <path d={line} fill="none" stroke={color} strokeWidth="1.4" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={lx} cy={ly} r="2.4" fill={color} />
    </svg>
  );
}

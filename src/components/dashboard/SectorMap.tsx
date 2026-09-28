import { memo, useMemo } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { Radar } from 'lucide-react';
import type { NodeState, SwarmState } from '../../types';
import { useSwarm } from '../../hooks/useSwarm';
import { QUORUM_K, SECTOR_COUNT, sectorName } from '../../lib/config';
import { layoutNodes, MAP_C, MAP_R, MAP_SIZE, polar, sectorCenterDeg, wedgePath } from '../../lib/geometry';
import { STATUS } from '../../lib/status';
import { cx, nodeName } from '../../lib/format';
import { Panel, Skeleton } from '../ui/Panel';

interface SectorMapProps {
  onSelect: (address: string) => void;
  className?: string;
}

const SECTORS = Array.from({ length: SECTOR_COUNT }, (_, i) => i + 1);

export function SectorMap({ onSelect, className }: SectorMapProps) {
  const { state } = useSwarm();
  const counts = useMemo(() => {
    const c = { normal: 0, pending: 0, alert: 0, quarantined: 0 };
    state.nodes.forEach((n) => c[n.status]++);
    return c;
  }, [state.nodes]);

  return (
    <Panel
      id="sector-map"
      title="Sector map"
      icon={Radar}
      className={className}
      aside={<span className="text-xs text-gray-500">{SECTOR_COUNT} sectors · quorum k={QUORUM_K}</span>}
      bodyClassName="flex flex-col"
    >
      <div className="relative mx-auto w-full max-w-[640px] flex-1 p-3 sm:p-5">
        {state.ready ? <MapCanvas state={state} onSelect={onSelect} /> : <Skeleton className="aspect-square w-full rounded-full" />}
      </div>
      <ul className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-white/[0.05] px-4 py-3" aria-label="Legend">
        {(Object.keys(STATUS) as (keyof typeof STATUS)[]).map((k) => {
          const m = STATUS[k];
          return (
            <li key={k} className="flex items-center gap-1.5 text-xs text-gray-400">
              <m.icon className={cx('size-3.5', m.text)} aria-hidden />
              {m.label}
              <span className="font-mono text-[11px] text-gray-600 tabular">{counts[k]}</span>
            </li>
          );
        })}
        <li className="flex items-center gap-1.5 text-xs text-gray-400 sm:ml-auto">
          <span className="size-2.5 rounded-[3px] border border-accent bg-accent/20" aria-hidden />
          Neurick board (real)
        </li>
      </ul>
    </Panel>
  );
}

const MapCanvas = memo(function MapCanvas({ state, onSelect }: { state: SwarmState; onSelect: (a: string) => void }) {
  const reduced = useReducedMotion();
  const positions = useMemo(() => layoutNodes(state.nodes), [state.nodes]);

  const openWindows = state.windows.filter((w) => w.state === 'open');
  const pendingSectors = new Set(openWindows.map((w) => w.sector));
  const threat = state.threat;

  // Corroboration links: all pairs of claimants in each open window, plus the confirmed threat set
  const links: { a: string; b: string; kind: 'pending' | 'alert' }[] = [];
  const pairUp = (addrs: string[], kind: 'pending' | 'alert') => {
    for (let i = 0; i < addrs.length; i++) for (let j = i + 1; j < addrs.length; j++) links.push({ a: addrs[i], b: addrs[j], kind });
  };
  openWindows.forEach((w) => pairUp(w.claims.map((c) => c.nodeAddress), 'pending'));
  if (threat) pairUp(threat.claimants, 'alert');

  // Estimated acoustic contact: pushed outward from the claimants' centroid
  const contact = useMemo(() => {
    if (!threat) return null;
    const deg = sectorCenterDeg(threat.sector);
    return polar(deg + 6, 222);
  }, [threat]);

  return (
    <div className="relative aspect-square w-full select-none">
      {/* Radar sweep (HTML conic gradient under the SVG) */}
      {!reduced && (
        <div aria-hidden className="absolute inset-[6.3%] overflow-hidden rounded-full">
          <div
            className="absolute inset-0 animate-sweep rounded-full"
            style={{
              background:
                'conic-gradient(from 0deg, transparent 0deg, transparent 290deg, rgb(96 165 250 / 0.03) 310deg, rgb(96 165 250 / 0.16) 358deg, rgb(147 197 253 / 0.55) 359.4deg, transparent 360deg)',
            }}
          />
        </div>
      )}

      <svg viewBox={`0 0 ${MAP_SIZE} ${MAP_SIZE}`} className="absolute inset-0 h-full w-full overflow-visible" role="group" aria-label="Sector map of sensor nodes">
        <defs>
          <radialGradient id="map-floor" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="rgb(96 165 250)" stopOpacity="0.07" />
            <stop offset="100%" stopColor="rgb(96 165 250)" stopOpacity="0" />
          </radialGradient>
          <filter id="node-glow" x="-100%" y="-100%" width="300%" height="300%">
            <feGaussianBlur stdDeviation="5" />
          </filter>
        </defs>

        <circle cx={MAP_C} cy={MAP_C} r={MAP_R} fill="url(#map-floor)" />

        {/* sector wedges: tinted when a claim window is open or an alert is confirmed there */}
        {SECTORS.map((s) => {
          const isThreat = threat?.sector === s;
          const isPending = !isThreat && pendingSectors.has(s);
          return (
            <path
              key={s}
              d={wedgePath(s, 34, MAP_R)}
              className="transition-[fill,fill-opacity] duration-500"
              fill={isThreat ? 'var(--color-alert)' : isPending ? 'var(--color-pending)' : 'transparent'}
              fillOpacity={isThreat ? 0.1 : isPending ? 0.06 : 0}
            />
          );
        })}

        {/* rings + spokes */}
        {[90, 176, MAP_R].map((r) => (
          <circle key={r} cx={MAP_C} cy={MAP_C} r={r} fill="none" stroke="white" strokeOpacity={r === MAP_R ? 0.12 : 0.06} strokeDasharray={r === MAP_R ? undefined : '2 5'} />
        ))}
        {SECTORS.map((s) => {
          const a = sectorCenterDeg(s) - 180 / SECTOR_COUNT;
          const p0 = polar(a, 34);
          const p1 = polar(a, MAP_R);
          return <line key={s} x1={p0.x} y1={p0.y} x2={p1.x} y2={p1.y} stroke="white" strokeOpacity="0.07" />;
        })}
        {/* ticks on the outer ring */}
        {Array.from({ length: 72 }, (_, i) => {
          const p0 = polar(i * 5, MAP_R);
          const p1 = polar(i * 5, MAP_R + (i % 6 === 0 ? 7 : 3.5));
          return <line key={i} x1={p0.x} y1={p0.y} x2={p1.x} y2={p1.y} stroke="white" strokeOpacity={i % 6 === 0 ? 0.25 : 0.1} />;
        })}

        {/* sector labels */}
        {SECTORS.map((s) => {
          const p = polar(sectorCenterDeg(s), MAP_R + 22);
          const active = threat?.sector === s ? 'var(--color-alert)' : pendingSectors.has(s) ? 'var(--color-pending)' : undefined;
          return (
            <text key={s} x={p.x} y={p.y} textAnchor="middle" dominantBaseline="central" className="font-mono text-[12px] font-medium" fill={active ?? 'rgb(156 163 175)'}>
              {sectorName(s)}
            </text>
          );
        })}

        {/* protected site */}
        <circle cx={MAP_C} cy={MAP_C} r="30" fill="rgb(0 0 0 / 0.6)" stroke="white" strokeOpacity="0.14" />
        <circle cx={MAP_C} cy={MAP_C} r="3" fill="white" fillOpacity="0.8" />
        <text x={MAP_C} y={MAP_C + 15} textAnchor="middle" className="text-[8.5px] font-medium tracking-[0.12em] uppercase" fill="rgb(156 163 175)">
          Site
        </text>

        {/* corroboration links */}
        <g>
          {links.map(({ a, b, kind }) => {
            const pa = positions.get(a);
            const pb = positions.get(b);
            if (!pa || !pb) return null;
            return (
              <motion.line
                key={`${kind}-${a}-${b}`}
                x1={pa.x}
                y1={pa.y}
                x2={pb.x}
                y2={pb.y}
                stroke={kind === 'alert' ? 'var(--color-alert)' : 'var(--color-pending)'}
                strokeWidth={kind === 'alert' ? 1.6 : 1.2}
                strokeDasharray={kind === 'pending' ? '4 5' : undefined}
                initial={{ pathLength: 0, opacity: 0 }}
                animate={{ pathLength: 1, opacity: kind === 'alert' ? 0.85 : 0.6 }}
                transition={{ duration: 0.5, ease: 'easeOut' }}
              />
            );
          })}
        </g>

        {/* contact estimate + shockwave */}
        <AnimatePresence>
          {threat && contact && (
            <motion.g key={threat.confirmedAt} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.3 }}>
              {!reduced &&
                [0, 0.45, 0.9].map((delay) => (
                  <motion.circle
                    key={delay}
                    cx={contact.x}
                    cy={contact.y}
                    fill="none"
                    stroke="var(--color-alert)"
                    initial={{ r: 4, opacity: 0.8, strokeWidth: 2 }}
                    animate={{ r: 300, opacity: 0, strokeWidth: 0.5 }}
                    transition={{ duration: 2.2, delay, ease: 'easeOut' }}
                  />
                ))}
              {threat.claimants.map((addr) => {
                const p = positions.get(addr);
                return p ? <line key={addr} x1={p.x} y1={p.y} x2={contact.x} y2={contact.y} stroke="var(--color-alert)" strokeOpacity="0.3" strokeDasharray="1 4" /> : null;
              })}
              <g transform={`translate(${contact.x} ${contact.y})`}>
                <circle r="14" fill="var(--color-alert)" fillOpacity="0.12" />
                <rect x="-5" y="-5" width="10" height="10" transform="rotate(45)" fill="none" stroke="var(--color-alert)" strokeWidth="1.5" />
                <text y="-20" textAnchor="middle" className="text-[9.5px] font-medium tracking-[0.1em] uppercase" fill="var(--color-alert)">
                  Acoustic contact
                </text>
              </g>
            </motion.g>
          )}
        </AnimatePresence>

        {/* nodes */}
        {state.nodes.map((n) => {
          const p = positions.get(n.address)!;
          return <MapNode key={n.address} node={n} x={p.x} y={p.y} reduced={!!reduced} onSelect={onSelect} />;
        })}
      </svg>
    </div>
  );
});

function MapNode({ node, x, y, reduced, onSelect }: { node: NodeState; x: number; y: number; reduced: boolean; onSelect: (a: string) => void }) {
  const meta = STATUS[node.status];
  const color = meta.color;
  const q = node.quarantined;
  const r = node.isReal ? 8 : 6.5;
  const label = nodeName(node);

  return (
    <motion.g
      role="button"
      tabIndex={0}
      aria-label={`${label}, ${node.isReal ? 'Neurick board, ' : ''}sector ${sectorName(node.sector)}, reputation ${node.reputation}, ${meta.label}`}
      onClick={() => onSelect(node.address)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelect(node.address);
        }
      }}
      className="group cursor-pointer outline-none"
      initial={{ opacity: 0, scale: 0.4 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
    >
      <title>{`${label} · ${sectorName(node.sector)} · rep ${node.reputation} · ${meta.label}`}</title>
      {/* hit area */}
      <circle cx={x} cy={y} r="18" fill="transparent" />
      {/* focus + hover ring */}
      <circle cx={x} cy={y} r={r + 7} fill="none" stroke="var(--color-accent)" strokeWidth="1.5" className="opacity-0 transition-opacity duration-200 group-focus-visible:opacity-100" />
      <circle cx={x} cy={y} r={r + 5} fill="white" className="opacity-0 transition-opacity duration-200 group-hover:opacity-[0.06]" />

      {/* glow on active states */}
      {(node.status === 'alert' || node.status === 'pending') && <circle cx={x} cy={y} r={r + 2} fill={color} opacity="0.55" filter="url(#node-glow)" />}

      {/* pending pulse */}
      {node.status === 'pending' && !reduced && (
        <motion.circle
          cx={x}
          cy={y}
          fill="none"
          stroke={color}
          strokeWidth="1.5"
          initial={{ r, opacity: 0.8 }}
          animate={{ r: r + 16, opacity: 0 }}
          transition={{ duration: 1.4, repeat: Infinity, ease: 'easeOut' }}
        />
      )}
      {node.status === 'alert' && <circle cx={x} cy={y} r={r + 5} fill="none" stroke={color} strokeOpacity="0.7" strokeWidth="1.2" />}

      {node.isReal ? (
        <rect
          x={x - r}
          y={y - r}
          width={r * 2}
          height={r * 2}
          rx="3"
          fill={color}
          fillOpacity={q ? 0.35 : 0.95}
          stroke="var(--color-accent)"
          strokeWidth="1.8"
          className="transition-[fill] duration-300"
        />
      ) : (
        <circle cx={x} cy={y} r={r} fill={color} fillOpacity={q ? 0.35 : 0.95} stroke="black" strokeOpacity="0.5" strokeWidth="1" className="transition-[fill] duration-300" />
      )}

      {/* quarantined: strike-through */}
      {q && <line x1={x - r - 4} y1={y + r + 4} x2={x + r + 4} y2={y - r - 4} stroke={color} strokeWidth="1.6" strokeLinecap="round" />}

      <text
        x={x}
        y={y + r + 13}
        textAnchor="middle"
        className={cx('text-[10px] font-medium', q && 'line-through')}
        fill={q ? 'rgb(107 114 128)' : node.isReal ? 'rgb(191 219 254)' : 'rgb(209 213 219)'}
      >
        {label}
      </text>
    </motion.g>
  );
}

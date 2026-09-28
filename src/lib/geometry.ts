import type { NodeState } from '../types';
import { SECTOR_COUNT } from './config';
import { hashUnit } from './random';

export const MAP_SIZE = 600;
export const MAP_C = MAP_SIZE / 2;
export const MAP_R = 262;
const SECTOR_SPAN = 360 / SECTOR_COUNT;

/** Sector 1 is centred at 12 o'clock, then clockwise */
export const sectorCenterDeg = (sector: number) => -90 + (sector - 1) * SECTOR_SPAN;

export const polar = (deg: number, r: number) => {
  const rad = (deg * Math.PI) / 180;
  return { x: MAP_C + r * Math.cos(rad), y: MAP_C + r * Math.sin(rad) };
};

/** SVG path for a sector wedge between two radii */
export function wedgePath(sector: number, r0: number, r1: number) {
  const a0 = sectorCenterDeg(sector) - SECTOR_SPAN / 2;
  const a1 = a0 + SECTOR_SPAN;
  const p0 = polar(a0, r1);
  const p1 = polar(a1, r1);
  const q1 = polar(a1, r0);
  const q0 = polar(a0, r0);
  return `M${q0.x},${q0.y} L${p0.x},${p0.y} A${r1},${r1} 0 0 1 ${p1.x},${p1.y} L${q1.x},${q1.y} A${r0},${r0} 0 0 0 ${q0.x},${q0.y} Z`;
}

const RADII = [196, 128, 232, 160, 108];

/**
 * Stable positions for every node. Nodes sharing a sector are fanned out across it
 * (ordered by registration, then address) and staggered in radius so labels never collide.
 */
export function layoutNodes(nodes: NodeState[]) {
  const bySector = new Map<number, NodeState[]>();
  for (const n of nodes) bySector.set(n.sector, [...(bySector.get(n.sector) ?? []), n]);
  const out = new Map<string, { x: number; y: number }>();
  for (const [sector, group] of bySector) {
    const sorted = [...group].sort((a, b) => Number(b.isReal) - Number(a.isReal) || (a.registeredAt ?? 0) - (b.registeredAt ?? 0) || a.address.localeCompare(b.address));
    const m = sorted.length;
    const spread = SECTOR_SPAN * 0.62;
    sorted.forEach((n, i) => {
      const offset = m === 1 ? (hashUnit(n.address) - 0.5) * spread * 0.4 : -spread / 2 + (spread * i) / (m - 1);
      const r = n.isReal ? 150 : RADII[(i + (m > 1 ? 0 : Math.floor(hashUnit(n.address, 3) * 3))) % RADII.length];
      out.set(n.address, polar(sectorCenterDeg(sector) + offset, r));
    });
  }
  return out;
}

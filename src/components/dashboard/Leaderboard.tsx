import { useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, LayoutGroup, motion } from 'framer-motion';
import { ArrowDown, ArrowUp, Ban, Cpu, Trophy, Users } from 'lucide-react';
import type { NodeState } from '../../types';
import { useSwarm } from '../../hooks/useSwarm';
import { useAnimatedNumber } from '../../hooks/useAnimatedNumber';
import { QUARANTINE_THRESHOLD, sectorName } from '../../lib/config';
import { cx, nodeName, truncateMiddle } from '../../lib/format';
import { EmptyState, Panel, Skeleton } from '../ui/Panel';
import { StatusChip } from '../ui/StatusChip';

const sortNodes = (nodes: NodeState[]) =>
  [...nodes].sort((a, b) => Number(a.quarantined) - Number(b.quarantined) || b.reputation - a.reputation || a.address.localeCompare(b.address));

export function Leaderboard({ className, onSelect }: { className?: string; onSelect: (a: string) => void }) {
  const { state } = useSwarm();
  const nodes = useMemo(() => sortNodes(state.nodes), [state.nodes]);
  const quarantined = nodes.filter((n) => n.quarantined).length;

  return (
    <Panel
      id="leaderboard"
      title="Reputation leaderboard"
      icon={Trophy}
      className={className}
      aside={
        <span className="text-xs text-gray-500">
          {nodes.length} nodes{quarantined ? ` · ${quarantined} quarantined` : ''} · limit {QUARANTINE_THRESHOLD}
        </span>
      }
    >
      <div role="table" aria-label="Nodes ranked by reputation" className="text-sm">
        <div role="row" className="hidden grid-cols-[32px_minmax(0,1.6fr)_64px_minmax(0,1.3fr)_112px_104px] items-center gap-3 border-b border-white/[0.05] px-4 py-2 sm:grid">
          {['#', 'Node', 'Sector', 'Reputation', 'Status', 'Quarantine'].map((h) => (
            <span key={h} role="columnheader" className="eyebrow">
              {h}
            </span>
          ))}
        </div>
        {!state.ready ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 6 }, (_, i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        ) : nodes.length === 0 ? (
          <EmptyState icon={Users} title="No nodes registered" body="Register a node with the BridgeKey authority wallet to start the swarm." />
        ) : (
          <LayoutGroup>
            <div role="rowgroup">
              <AnimatePresence initial={false}>
                {nodes.map((n, i) => (
                  <Row key={n.address} node={n} rank={i + 1} onSelect={onSelect} />
                ))}
              </AnimatePresence>
            </div>
          </LayoutGroup>
        )}
      </div>
    </Panel>
  );
}

function Row({ node, rank, onSelect }: { node: NodeState; rank: number; onSelect: (a: string) => void }) {
  const rep = useAnimatedNumber(node.reputation, 600);
  const [delta, setDelta] = useState<number | null>(null);
  const prev = useRef(node.reputation);
  useEffect(() => {
    const d = node.reputation - prev.current;
    prev.current = node.reputation;
    if (!d) return;
    setDelta(d);
    const t = window.setTimeout(() => setDelta(null), 1800);
    return () => window.clearTimeout(t);
  }, [node.reputation]);

  const q = node.quarantined;
  const barColor = q ? 'var(--color-quarantine)' : node.reputation < QUARANTINE_THRESHOLD + 10 ? 'var(--color-pending)' : 'var(--color-ok)';

  return (
    <motion.div
      layout
      role="row"
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: 'auto' }}
      exit={{ opacity: 0, height: 0 }}
      transition={{ layout: { duration: 0.45, ease: [0.22, 1, 0.36, 1] }, duration: 0.3 }}
      className="border-b border-white/[0.035] last:border-b-0"
    >
      <button
        type="button"
        onClick={() => onSelect(node.address)}
        aria-label={`Open ${nodeName(node)} details`}
        className={cx(
          'grid w-full grid-cols-[24px_minmax(0,1fr)_auto] items-center gap-3 px-4 py-2.5 text-left transition-colors duration-200 hover:bg-white/[0.035] active:bg-white/[0.06]',
          'sm:grid-cols-[32px_minmax(0,1.6fr)_64px_minmax(0,1.3fr)_112px_104px]',
          q && 'opacity-70',
        )}
      >
        <span role="cell" className="font-mono text-[11px] text-gray-500 tabular">
          {String(rank).padStart(2, '0')}
        </span>

        <span role="cell" className="flex min-w-0 items-center gap-2">
          <span className={cx('truncate font-medium', q ? 'text-gray-500 line-through' : 'text-gray-100')}>{nodeName(node)}</span>
          {node.isReal ? (
            <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-accent/40 bg-accent-soft px-1.5 py-px text-2xs font-medium text-accent">
              <Cpu className="size-3" aria-hidden />
              Neurick board
            </span>
          ) : (
            <span className="hidden shrink-0 text-2xs text-gray-600 md:inline">Virtual</span>
          )}
          <span className="hidden truncate font-mono text-[10.5px] text-gray-600 xl:inline">{truncateMiddle(node.address, 6, 4)}</span>
        </span>

        <span role="cell" className="hidden font-mono text-[11.5px] text-gray-400 sm:block">
          {sectorName(node.sector)}
        </span>

        <span role="cell" className="hidden items-center gap-2.5 sm:flex">
          <span className="w-7 text-right font-medium text-white tabular">{Math.round(rep)}</span>
          <span className="relative h-1 flex-1 overflow-hidden rounded-full bg-white/[0.07]">
            <span className="absolute inset-y-0 left-0 rounded-full transition-[background-color] duration-300" style={{ width: `${rep}%`, background: barColor }} />
            <span className="absolute inset-y-0 w-px bg-white/40" style={{ left: `${QUARANTINE_THRESHOLD}%` }} aria-hidden />
          </span>
          <span className="w-9 text-2xs font-medium tabular">
            <AnimatePresence>
              {delta !== null && (
                <motion.span
                  initial={{ opacity: 0, y: delta > 0 ? 4 : -4 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                  className={cx('inline-flex items-center', delta > 0 ? 'text-ok' : 'text-quarantine')}
                >
                  {delta > 0 ? <ArrowUp className="size-3" aria-hidden /> : <ArrowDown className="size-3" aria-hidden />}
                  {Math.abs(delta)}
                </motion.span>
              )}
            </AnimatePresence>
          </span>
        </span>

        <span role="cell" className="flex items-center justify-end gap-2 sm:justify-start">
          <span className="font-medium text-white tabular sm:hidden">{Math.round(rep)}</span>
          <StatusChip status={node.status} compact />
        </span>

        <span role="cell" className="hidden text-xs sm:block">
          {q ? (
            <span className="inline-flex items-center gap-1 text-quarantine">
              <Ban className="size-3" aria-hidden />
              {node.quarantineReason === 'tamper' ? 'Tamper' : 'Low rep'}
            </span>
          ) : (
            <span className="text-gray-600">Active</span>
          )}
        </span>
      </button>
    </motion.div>
  );
}

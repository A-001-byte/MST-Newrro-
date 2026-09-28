import { memo, useMemo, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Activity, Inbox } from 'lucide-react';
import type { ChainEvent, ChainEventType, NodeState } from '../../types';
import { useSwarm } from '../../hooks/useSwarm';
import { useNow } from '../../hooks/useNow';
import { explorerTx } from '../../lib/config';
import { describeEvent, EVENT_META, TONE_CLASSES } from '../../lib/events';
import { cx, relativeTime } from '../../lib/format';
import { ExplorerLink, Mono } from '../ui/Hash';
import { EmptyState, Panel, Skeleton } from '../ui/Panel';

const FILTERS: { key: string; label: string; types: ChainEventType[] | null }[] = [
  { key: 'all', label: 'All', types: null },
  { key: 'alerts', label: 'Alerts', types: ['AlertConfirmed', 'ClaimSubmitted', 'ClaimRejected'] },
  { key: 'rep', label: 'Reputation', types: ['ReputationCredited', 'ReputationSlashed', 'TamperReported'] },
  { key: 'tasks', label: 'Tasks', types: ['TaskBid', 'TaskAwarded', 'NodeRegistered'] },
];

export function TxFeed({ className }: { className?: string }) {
  const { state } = useSwarm();
  const now = useNow(1000);
  const [filter, setFilter] = useState('all');
  const nodes = useMemo(() => new Map(state.nodes.map((n) => [n.address, n])), [state.nodes]);
  const types = FILTERS.find((f) => f.key === filter)?.types;
  const events = types ? state.events.filter((e) => types.includes(e.type)) : state.events;

  return (
    <Panel
      id="tx-feed"
      title="Live transactions"
      icon={Activity}
      className={className}
      aside={<span className="font-mono text-[11px] text-gray-500 tabular">{state.events.length}</span>}
      bodyClassName="flex flex-col"
    >
      <div className="flex gap-1 overflow-x-auto border-b border-white/[0.05] px-3 py-2 scroll-thin" role="tablist" aria-label="Filter events">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            role="tab"
            aria-selected={filter === f.key}
            onClick={() => setFilter(f.key)}
            className={cx(
              'h-7 shrink-0 rounded-full px-2.5 text-xs font-medium transition-colors duration-200',
              filter === f.key ? 'bg-white/10 text-white' : 'text-gray-500 hover:bg-white/[0.05] hover:text-gray-200',
            )}
          >
            {f.label}
          </button>
        ))}
      </div>
      <div className="relative min-h-[240px] flex-1">
        <div className="absolute inset-0 overflow-y-auto scroll-thin" aria-live="polite" aria-relevant="additions">
          {!state.ready ? (
            <div className="space-y-3 p-4">
              {Array.from({ length: 7 }, (_, i) => (
                <div key={i} className="flex gap-3">
                  <Skeleton className="size-7 rounded-full" />
                  <div className="flex-1 space-y-1.5">
                    <Skeleton className="h-3 w-3/4" />
                    <Skeleton className="h-2.5 w-1/2" />
                  </div>
                </div>
              ))}
            </div>
          ) : events.length === 0 ? (
            <EmptyState icon={Inbox} title="Waiting for on-chain events" body="Transactions appear here the moment the gateway relays them." />
          ) : (
            <ul className="divide-y divide-white/[0.04]">
              <AnimatePresence initial={false}>
                {events.map((e) => (
                  <FeedRow key={e.id} event={e} nodes={nodes} now={now} />
                ))}
              </AnimatePresence>
            </ul>
          )}
        </div>
      </div>
    </Panel>
  );
}

const FeedRow = memo(function FeedRow({ event, nodes, now }: { event: ChainEvent; nodes: Map<string, NodeState>; now: number }) {
  const meta = EVENT_META[event.type];
  const tone = TONE_CLASSES[meta.tone];
  const Icon = meta.icon;
  const highlight = event.type === 'AlertConfirmed';

  return (
    <motion.li
      layout="position"
      initial={{ opacity: 0, y: -10, backgroundColor: 'rgba(96,165,250,0.08)' }}
      animate={{ opacity: 1, y: 0, backgroundColor: highlight ? 'rgba(244,63,94,0.06)' : 'rgba(0,0,0,0)' }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1], backgroundColor: { duration: 1.4 } }}
      className="group flex gap-3 px-4 py-2.5"
    >
      <span className={cx('mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full border', tone.bg, tone.border)}>
        <Icon className={cx('size-3.5', tone.text)} aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <p className={cx('truncate text-xs font-medium', highlight ? 'text-alert' : 'text-gray-200')}>{meta.label}</p>
          <time className="shrink-0 text-2xs text-gray-500 tabular" dateTime={new Date(event.timestamp).toISOString()}>
            {relativeTime(event.timestamp, now)}
          </time>
        </div>
        <p className="mt-0.5 line-clamp-2 text-xs text-gray-400">{describeEvent(event, nodes)}</p>
        <div className="mt-1 flex items-center gap-1.5">
          <Mono value={event.txHash} head={8} tail={6} className="text-gray-500" />
          {event.blockNumber != null && <span className="font-mono text-[10.5px] text-gray-600 tabular">#{event.blockNumber.toLocaleString('en-US')}</span>}
          <ExplorerLink href={explorerTx(event.txHash)} label={`View ${meta.label} transaction on MST explorer`} className="ml-auto -my-1 size-5" />
        </div>
      </div>
    </motion.li>
  );
});

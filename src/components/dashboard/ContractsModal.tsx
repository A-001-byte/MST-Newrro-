import { useMemo } from 'react';
import { FileCode2 } from 'lucide-react';
import { useSwarm } from '../../hooks/useSwarm';
import { useNow } from '../../hooks/useNow';
import { CONTRACTS, EXPLORER_BASE, explorerAddress, NETWORK_NAME } from '../../lib/config';
import { EVENT_META } from '../../lib/events';
import { relativeTime, truncateMiddle } from '../../lib/format';
import { CopyButton, ExplorerLink } from '../ui/Hash';
import { Modal } from '../ui/Overlay';
import { StatCards } from './StatCards';

export function ContractsModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { state } = useSwarm();
  const now = useNow(1000, open);

  const activity = useMemo(() => {
    const byContract = new Map<string, { count: number; last: number | null }>();
    for (const c of CONTRACTS) byContract.set(c.name, { count: 0, last: null });
    for (const e of state.events) {
      const entry = byContract.get(EVENT_META[e.type].contract);
      if (!entry) continue;
      entry.count++;
      entry.last = Math.max(entry.last ?? 0, e.timestamp);
    }
    return byContract;
  }, [state.events]);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Contracts"
      subtitle={`${CONTRACTS.length} contracts on ${NETWORK_NAME}`}
      className="max-w-2xl"
    >
      <div className="space-y-5 p-5">
        <StatCards compact />
        <ul className="divide-y divide-white/[0.06] rounded-xl border border-white/[0.07]">
          {CONTRACTS.map((c) => {
            const a = activity.get(c.name)!;
            return (
              <li key={c.name} className="flex items-center gap-3 px-4 py-3">
                <span className="flex size-8 shrink-0 items-center justify-center rounded-lg border border-white/[0.08] bg-white/[0.03]">
                  <FileCode2 className="size-4 text-accent" aria-hidden />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="flex items-center gap-2 text-sm font-medium text-white">
                    {c.name}
                    {c.placeholder && (
                      <span className="rounded-full border border-white/10 px-1.5 py-px text-2xs font-normal text-gray-500" title="Set VITE_ADDR_* after deploying">
                        Placeholder
                      </span>
                    )}
                  </p>
                  <p className="truncate text-xs text-gray-500">{c.role}</p>
                  <p className="mt-1 font-mono text-[11px] text-gray-400 sm:hidden">{truncateMiddle(c.address, 8, 6)}</p>
                </div>
                <div className="hidden text-right sm:block">
                  <p className="font-mono text-[11px] text-gray-300">{truncateMiddle(c.address, 8, 6)}</p>
                  <p className="text-2xs text-gray-500 tabular">
                    {a.count} events{a.last ? ` · last ${relativeTime(a.last, now)}` : ''}
                  </p>
                </div>
                <div className="flex shrink-0 items-center">
                  <CopyButton value={c.address} label={`Copy ${c.name} address`} />
                  <ExplorerLink href={explorerAddress(c.address)} label={`View ${c.name} on explorer`} />
                </div>
              </li>
            );
          })}
        </ul>
        <p className="text-xs text-gray-500">
          Explorer: <span className="font-mono text-gray-400">{EXPLORER_BASE}</span>. Configure with <span className="font-mono text-gray-400">VITE_EXPLORER_URL</span>.
        </p>
      </div>
    </Modal>
  );
}

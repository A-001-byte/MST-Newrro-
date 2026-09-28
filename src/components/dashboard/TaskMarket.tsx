import { AnimatePresence, motion } from 'framer-motion';
import { Gavel, Lock, Trophy } from 'lucide-react';
import type { Auction, TaskBid } from '../../types';
import { useSwarm } from '../../hooks/useSwarm';
import { useNow } from '../../hooks/useNow';
import { explorerTx, sectorName } from '../../lib/config';
import { cx, truncateMiddle } from '../../lib/format';
import { ExplorerLink } from '../ui/Hash';
import { EmptyState, Panel } from '../ui/Panel';

const SLOTS = 4;

export function TaskMarket({ className }: { className?: string }) {
  const { state } = useSwarm();
  const auction = state.auction;
  const now = useNow(200, auction?.state === 'bidding');

  return (
    <Panel
      id="task-market"
      title="Task market · sealed-bid patrol auction"
      icon={Gavel}
      className={className}
      aside={auction && <AuctionStatus auction={auction} now={now} />}
    >
      {!auction ? (
        <EmptyState compact icon={Gavel} title="No open auctions" body="An inspection auction opens automatically when an alert confirms on-chain." />
      ) : (
        <div className="p-4">
          <p className="mb-3 text-xs text-gray-400">
            Patrol and inspection agents bid in MSTC to respond to {sectorName(auction.sector)}. Bids stay sealed until the window closes, then the lowest wins and is
            paid from the task fund.
          </p>
          <ul className="grid grid-cols-2 gap-2 lg:grid-cols-4">
            <AnimatePresence initial={false}>
              {auction.bids.map((b) => (
                <BidCard key={b.agent} bid={b} auction={auction} />
              ))}
            </AnimatePresence>
            {auction.state === 'bidding' &&
              Array.from({ length: Math.max(0, SLOTS - auction.bids.length) }, (_, i) => (
                <li key={`slot-${i}`} className="flex h-[86px] items-center justify-center rounded-xl border border-dashed border-white/10 text-xs text-gray-600">
                  Waiting for bid…
                </li>
              ))}
          </ul>
        </div>
      )}
    </Panel>
  );
}

function AuctionStatus({ auction, now }: { auction: Auction; now: number }) {
  if (auction.state === 'awarded')
    return (
      <span className="inline-flex items-center gap-1 text-xs font-medium text-accent">
        <Trophy className="size-3.5" aria-hidden />
        Awarded
        {auction.txHash && <ExplorerLink href={explorerTx(auction.txHash)} label="View TaskAwarded transaction" className="size-5" />}
      </span>
    );
  const left = Math.max(0, auction.closesAt - now);
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-gray-400">
      <span className="size-1.5 animate-live rounded-full bg-accent" aria-hidden />
      Bidding · closes in <span className="font-mono text-gray-200 tabular">{(left / 1000).toFixed(1)}s</span>
    </span>
  );
}

function BidCard({ bid, auction }: { bid: TaskBid; auction: Auction }) {
  const winner = auction.winner === bid.agent;
  const sealed = bid.amount == null;
  return (
    <motion.li
      layout
      initial={{ opacity: 0, y: 8, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
      className={cx(
        'relative h-[86px] overflow-hidden rounded-xl border px-3 py-2.5 transition-[border-color,background-color,box-shadow,opacity] duration-300',
        winner
          ? 'border-accent/60 bg-accent/[0.09] shadow-[0_0_30px_-10px_rgb(59_130_246/0.8)]'
          : auction.state === 'awarded'
            ? 'border-white/[0.06] bg-white/[0.02] opacity-60'
            : 'border-white/[0.08] bg-white/[0.03]',
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-sm font-medium text-gray-100">{bid.agentLabel}</span>
        {winner ? (
          <span className="inline-flex items-center gap-1 text-2xs font-medium text-accent">
            <Trophy className="size-3" aria-hidden />
            Winner
          </span>
        ) : sealed ? (
          <Lock className="size-3.5 text-gray-500" aria-label="Sealed" />
        ) : null}
      </div>
      <AnimatePresence mode="wait" initial={false}>
        {sealed ? (
          <motion.div key="sealed" exit={{ opacity: 0, y: -4 }} className="mt-2">
            <p className="text-2xs text-gray-500">Commitment</p>
            <p className="truncate font-mono text-[11px] text-gray-400">{truncateMiddle(bid.sealedHash, 10, 6)}</p>
          </motion.div>
        ) : (
          <motion.div key="open" initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} className="mt-2 flex items-end justify-between">
            <div>
              <p className="text-2xs text-gray-500">Bid</p>
              <p className={cx('text-md font-medium tabular', winner ? 'text-white' : 'text-gray-300')}>
                {bid.amount!.toFixed(2)} <span className="text-xs text-gray-500">MSTC</span>
              </p>
            </div>
            <p className="text-2xs text-gray-500 tabular">ETA {bid.etaSec}s</p>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.li>
  );
}

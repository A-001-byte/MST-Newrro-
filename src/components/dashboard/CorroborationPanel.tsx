import { AnimatePresence, motion } from 'framer-motion';
import { Ban, BadgeCheck, CircleDashed, Clock3, Siren, Users } from 'lucide-react';
import type { ClaimWindow } from '../../types';
import { useSwarm } from '../../hooks/useSwarm';
import { useNow } from '../../hooks/useNow';
import { QUORUM_K, sectorName } from '../../lib/config';
import { cx, formatMs, nodeName } from '../../lib/format';
import { Panel, Skeleton } from '../ui/Panel';

const STATE_META = {
  open: { label: 'Awaiting quorum', color: 'var(--color-pending)', text: 'text-pending', icon: Clock3 },
  confirmed: { label: 'Alert confirmed', color: 'var(--color-alert)', text: 'text-alert', icon: Siren },
  expired: { label: 'Expired, slashed', color: 'var(--color-quarantine)', text: 'text-quarantine', icon: Ban },
} as const;

/** Focus the newest open window; otherwise the newest window of any state */
function pickFocus(windows: ClaimWindow[]) {
  const open = windows.filter((w) => w.state === 'open').sort((a, b) => b.openedAt - a.openedAt);
  if (open.length) return open[0];
  return [...windows].sort((a, b) => b.openedAt - a.openedAt)[0] ?? null;
}

export function CorroborationPanel({ className }: { className?: string }) {
  const { state } = useSwarm();
  const focus = pickFocus(state.windows);
  const now = useNow(100, focus?.state === 'open');
  const others = state.windows.filter((w) => w.id !== focus?.id).sort((a, b) => b.openedAt - a.openedAt).slice(0, 3);

  return (
    <Panel
      id="corroboration"
      title="k-of-n corroboration"
      icon={Users}
      className={className}
      aside={focus && <WindowStateChip w={focus} />}
      bodyClassName="flex flex-col"
    >
      {!state.ready ? (
        <div className="space-y-3 p-4">
          <Skeleton className="h-8 w-24" />
          <Skeleton className="h-2 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      ) : !focus ? (
        <IdleView />
      ) : (
        <WindowView w={focus} now={now} others={others} />
      )}
    </Panel>
  );
}

/** No window yet: teach the mechanism with the empty quorum itself */
function IdleView() {
  return (
    <div className="flex flex-1 flex-col gap-4 p-4">
      <div>
        <p className="eyebrow">All sectors</p>
        <p className="mt-1 text-[26px] leading-none font-medium tracking-tight text-white tabular">
          0<span className="text-gray-500"> / {QUORUM_K}</span>
          <span className="ml-2 text-sm font-normal tracking-normal text-gray-400">no open windows</span>
        </p>
      </div>
      <div className="flex gap-1.5" aria-hidden>
        {Array.from({ length: QUORUM_K }, (_, i) => (
          <div key={i} className="h-1.5 flex-1 rounded-full bg-white/[0.07]" />
        ))}
      </div>
      <ul className="space-y-1.5">
        {Array.from({ length: QUORUM_K }, (_, i) => (
          <li key={i} className="flex items-center gap-2.5 rounded-lg border border-dashed border-white/10 px-2.5 py-2 text-sm text-gray-500">
            <CircleDashed className="size-3.5 shrink-0" aria-hidden />
            Independent node {i + 1}
          </li>
        ))}
      </ul>
      <p className="text-xs leading-relaxed text-gray-500">
        A single claim does nothing. CorroborationEngine only emits AlertConfirmed when {QUORUM_K} distinct wallets claim the same sector inside one window.
      </p>
    </div>
  );
}

function WindowStateChip({ w }: { w: ClaimWindow }) {
  const m = STATE_META[w.state];
  return (
    <span className={cx('inline-flex items-center gap-1 text-xs font-medium', m.text)}>
      <m.icon className="size-3.5" aria-hidden />
      {m.label}
    </span>
  );
}

function WindowView({ w, now, others }: { w: ClaimWindow; now: number; others: ClaimWindow[] }) {
  const { state } = useSwarm();
  const m = STATE_META[w.state];
  const total = w.expiresAt - w.openedAt;
  const remaining = Math.max(0, w.expiresAt - now);
  const pct = w.state === 'open' ? remaining / total : 0;
  const filled = Math.min(w.claims.length, w.k);
  const nodeByAddr = new Map(state.nodes.map((n) => [n.address, n]));

  return (
    <div className="flex flex-1 flex-col gap-4 p-4">
      <div className="flex items-end justify-between gap-3">
        <div>
          <p className="eyebrow">Sector {sectorName(w.sector)} · window</p>
          <p className="mt-1 text-[26px] leading-none font-medium tracking-tight text-white tabular" aria-live="polite">
            {filled}
            <span className="text-gray-500"> / {w.k}</span>
            <span className="ml-2 text-sm font-normal tracking-normal text-gray-400">claims · {w.n} eligible</span>
          </p>
        </div>
        <div className="text-right">
          <p className="eyebrow">{w.state === 'open' ? 'Closes in' : w.state === 'confirmed' ? 'Quorum in' : 'Result'}</p>
          <p className={cx('mt-1 font-mono text-md tabular', w.state === 'open' ? 'text-white' : m.text)}>
            {w.state === 'open'
              ? `${(remaining / 1000).toFixed(1)}s`
              : w.state === 'confirmed'
                ? formatMs((w.confirmedAt ?? w.openedAt) - (w.claims[0]?.timestamp ?? w.openedAt))
                : 'No quorum'}
          </p>
        </div>
      </div>

      {/* k segments + countdown */}
      <div className="space-y-2">
        <div className="flex gap-1.5" role="progressbar" aria-valuemin={0} aria-valuemax={w.k} aria-valuenow={filled} aria-label={`${filled} of ${w.k} corroborating claims`}>
          {Array.from({ length: w.k }, (_, i) => (
            <div key={i} className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/[0.07]">
              <motion.div
                className="h-full rounded-full"
                style={{ background: w.state === 'expired' ? 'var(--color-quarantine)' : w.state === 'confirmed' ? 'var(--color-alert)' : 'var(--color-pending)' }}
                initial={false}
                animate={{ width: i < filled ? '100%' : '0%' }}
                transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
              />
            </div>
          ))}
        </div>
        <div className="h-px w-full overflow-hidden bg-white/[0.06]" aria-hidden>
          <div className="h-full bg-white/40 transition-[width] duration-100 ease-linear" style={{ width: `${pct * 100}%` }} />
        </div>
      </div>

      {/* claimants and empty slots */}
      <ul className="space-y-1.5">
        <AnimatePresence initial={false}>
          {w.claims.map((c) => {
            const n = nodeByAddr.get(c.nodeAddress);
            return (
              <motion.li
                key={c.nodeAddress}
                layout
                initial={{ opacity: 0, x: -6 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.25 }}
                className="flex items-center gap-2.5 rounded-lg border border-white/[0.05] bg-white/[0.025] px-2.5 py-2"
              >
                <BadgeCheck className={cx('size-3.5 shrink-0', m.text)} aria-hidden />
                <span className="truncate text-sm text-gray-200">{n ? nodeName(n) : c.nodeAddress.slice(0, 10)}</span>
                {n?.isReal && <span className="rounded-full border border-accent/40 px-1.5 text-2xs font-medium text-accent">Real</span>}
                <span className="ml-auto font-mono text-[11px] text-gray-400 tabular">conf {c.confidence.toFixed(2)}</span>
              </motion.li>
            );
          })}
        </AnimatePresence>
        {w.state === 'open' &&
          Array.from({ length: Math.max(0, w.k - w.claims.length) }, (_, i) => (
            <li key={`slot-${i}`} className="flex items-center gap-2.5 rounded-lg border border-dashed border-white/10 px-2.5 py-2 text-sm text-gray-500">
              <CircleDashed className="size-3.5 shrink-0" aria-hidden />
              Awaiting an independent node
            </li>
          ))}
      </ul>

      <p className="text-xs leading-relaxed text-gray-500">
        {w.state === 'expired'
          ? 'No other node agreed before the window closed. The lone claimant was slashed by ReputationManager.'
          : w.state === 'confirmed'
            ? `${w.k} independent wallets agreed. CorroborationEngine emitted AlertConfirmed and credited each one.`
            : `One claim alone does nothing. ${w.k} distinct nodes must claim ${sectorName(w.sector)} before the window closes.`}
      </p>

      {others.length > 0 && (
        <div className="mt-auto flex flex-wrap gap-1.5 border-t border-white/[0.05] pt-3">
          <span className="mr-1 self-center text-2xs text-gray-600 uppercase">Recent</span>
          {others.map((o) => {
            const om = STATE_META[o.state];
            return (
              <span key={o.id} className="inline-flex items-center gap-1 rounded-full border border-white/[0.07] px-2 py-0.5 text-2xs text-gray-400">
                <om.icon className={cx('size-3', om.text)} aria-hidden />
                {sectorName(o.sector)} · {Math.min(o.claims.length, o.k)}/{o.k}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}

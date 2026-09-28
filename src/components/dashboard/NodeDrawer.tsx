import { useMemo, useState } from 'react';
import { Cpu, ShieldCheck, ShieldOff, Vibrate } from 'lucide-react';
import { useSwarm } from '../../hooks/useSwarm';
import { useAnimatedNumber } from '../../hooks/useAnimatedNumber';
import { useNow } from '../../hooks/useNow';
import { explorerAddress, explorerTx, QUARANTINE_THRESHOLD, sectorName } from '../../lib/config';
import { EVENT_META, TONE_CLASSES } from '../../lib/events';
import { cx, nodeName, relativeTime } from '../../lib/format';
import { Button } from '../ui/Button';
import { CopyButton, ExplorerLink, Mono } from '../ui/Hash';
import { Drawer } from '../ui/Overlay';
import { Sparkline } from '../ui/Sparkline';
import { StatusChip } from '../ui/StatusChip';

export function NodeDrawer({ address, onClose }: { address: string | null; onClose: () => void }) {
  const { state } = useSwarm();
  const node = state.nodes.find((n) => n.address === address) ?? null;
  return (
    <Drawer
      open={!!node}
      onClose={onClose}
      title={node ? nodeName(node) : 'Node'}
      subtitle={
        node && (
          <span className="flex flex-wrap items-center gap-2">
            <StatusChip status={node.status} compact />
            {node.isReal ? (
              <span className="inline-flex items-center gap-1 rounded-full border border-accent/40 bg-accent-soft px-1.5 py-px text-2xs font-medium text-accent">
                <Cpu className="size-3" aria-hidden />
                Neurick board
              </span>
            ) : (
              <span className="text-xs text-gray-500">Virtual node · real MST wallet</span>
            )}
          </span>
        )
      }
    >
      {node && <NodeDetail address={node.address} />}
    </Drawer>
  );
}

function NodeDetail({ address }: { address: string }) {
  const { state, actions } = useSwarm();
  const now = useNow(1000);
  const node = state.nodes.find((n) => n.address === address)!;
  const rep = useAnimatedNumber(node.reputation, 600);
  const [tampering, setTampering] = useState(false);
  const history = state.history[address] ?? [node.reputation];

  const { claims, tamper } = useMemo(() => {
    const mine = state.events.filter((e) => e.nodeAddress === address);
    return {
      claims: mine.filter((e) => e.type === 'ClaimSubmitted' || e.type === 'ClaimRejected' || e.type === 'ReputationSlashed' || e.type === 'ReputationCredited').slice(0, 6),
      tamper: mine.find((e) => e.type === 'TamperReported'),
    };
  }, [state.events, address]);

  return (
    <div className="space-y-6 p-5">
      <section>
        <p className="eyebrow mb-1.5">Wallet address</p>
        <div className="flex items-start gap-1 rounded-lg border border-white/[0.06] bg-white/[0.02] p-2.5">
          <p className="min-w-0 flex-1 font-mono text-[11.5px] leading-relaxed break-all text-gray-300">{node.address}</p>
          <CopyButton value={node.address} label="Copy address" />
          <ExplorerLink href={explorerAddress(node.address)} label="View address on explorer" />
        </div>
      </section>

      <dl className="grid grid-cols-2 gap-2">
        {[
          ['Sector', sectorName(node.sector)],
          ['Registered', node.registeredAt ? relativeTime(node.registeredAt, now) : '—'],
          ['Type', node.isReal ? 'Physical' : 'Virtual'],
          ['Recent claims', String(claims.filter((c) => c.type === 'ClaimSubmitted').length)],
        ].map(([k, v]) => (
          <div key={k} className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2.5">
            <dt className="eyebrow">{k}</dt>
            <dd className="mt-1 text-sm text-gray-100">{v}</dd>
          </div>
        ))}
      </dl>

      <section className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
        <div className="flex items-end justify-between">
          <div>
            <p className="eyebrow">Reputation</p>
            <p className="mt-1 text-[28px] leading-none font-medium tracking-tight text-white tabular">{Math.round(rep)}</p>
          </div>
          <p className="text-xs text-gray-500">Quarantine below {QUARANTINE_THRESHOLD}</p>
        </div>
        <div className="mt-4">
          <Sparkline
            values={history}
            width={340}
            height={56}
            domain={[Math.max(0, Math.min(...history, QUARANTINE_THRESHOLD + 6) - 6), Math.min(100, Math.max(...history) + 4)]}
            threshold={QUARANTINE_THRESHOLD}
            color={node.quarantined ? 'var(--color-quarantine)' : 'var(--color-accent)'}
            label={`Reputation history for ${nodeName(node)}`}
          />
        </div>
      </section>

      <section>
        <p className="eyebrow mb-2">Quarantine state</p>
        {node.quarantined ? (
          <div className="rounded-lg border border-quarantine/30 bg-quarantine-soft p-3">
            <p className="flex items-center gap-2 text-sm font-medium text-gray-200">
              <ShieldOff className="size-4 text-quarantine" aria-hidden />
              {node.quarantineReason === 'tamper' ? 'Self-quarantined after tamper' : 'Auto-quarantined, reputation below limit'}
            </p>
            <p className="mt-1 text-xs text-gray-400">CorroborationEngine now reverts every claim from this wallet.</p>
            {tamper?.meta.evidenceHash && (
              <div className="mt-2.5 flex items-center gap-1 border-t border-white/[0.06] pt-2.5">
                <span className="text-xs text-gray-500">Evidence</span>
                <Mono value={String(tamper.meta.evidenceHash)} head={10} tail={6} />
                <ExplorerLink href={explorerTx(tamper.txHash)} label="View tamper report transaction" className="ml-auto" />
              </div>
            )}
          </div>
        ) : (
          <div className="flex items-center justify-between gap-3 rounded-lg border border-white/[0.06] bg-white/[0.02] p-3">
            <p className="flex items-center gap-2 text-sm text-gray-300">
              <ShieldCheck className="size-4 text-ok" aria-hidden />
              Active, claims count toward quorum
            </p>
            <Button
              variant="danger"
              size="sm"
              icon={<Vibrate className="size-3.5" aria-hidden />}
              loading={tampering}
              disabled={state.connection !== 'live'}
              onClick={async () => {
                setTampering(true);
                try {
                  await actions.simulateTamper(address);
                } finally {
                  setTampering(false);
                }
              }}
            >
              Tamper
            </Button>
          </div>
        )}
      </section>

      <section>
        <p className="eyebrow mb-2">Recent claims and reputation</p>
        {claims.length === 0 ? (
          <p className="rounded-lg border border-dashed border-white/10 p-4 text-center text-xs text-gray-500">No claims from this node yet.</p>
        ) : (
          <ul className="divide-y divide-white/[0.05] rounded-lg border border-white/[0.06]">
            {claims.map((e) => {
              const meta = EVENT_META[e.type];
              const tone = TONE_CLASSES[meta.tone];
              return (
                <li key={e.id} className="flex items-center gap-2.5 px-3 py-2">
                  <meta.icon className={cx('size-3.5 shrink-0', tone.text)} aria-hidden />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-xs text-gray-200">
                      {meta.label}
                      {e.type === 'ClaimSubmitted' && typeof e.meta.confidence === 'number' && <span className="text-gray-500"> · conf {e.meta.confidence.toFixed(2)}</span>}
                      {typeof e.meta.delta === 'number' && (
                        <span className="text-gray-500">
                          {' '}
                          · {e.meta.delta > 0 ? '+' : '−'}
                          {Math.abs(e.meta.delta)}
                        </span>
                      )}
                    </p>
                    <p className="truncate text-2xs text-gray-500">{e.type === 'ClaimRejected' ? e.meta.reason : `${e.sector != null ? sectorName(e.sector) : ''} · ${relativeTime(e.timestamp, now)}`}</p>
                  </div>
                  <ExplorerLink href={explorerTx(e.txHash)} label={`View ${meta.label} transaction`} />
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}

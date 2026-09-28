import type { ReactNode } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { Cpu, Mic, MicOff, Move3d, Volume2, VolumeX, type LucideIcon } from 'lucide-react';
import { useSwarm } from '../../hooks/useSwarm';
import { sectorName } from '../../lib/config';
import { cx } from '../../lib/format';
import { EmptyState, Panel, Skeleton } from '../ui/Panel';
import { StatusChip } from '../ui/StatusChip';

/** Mirror of the physical Neurick board's outputs: speaker, OLED, servo flag */
export function BoardCard({ className, onSelect }: { className?: string; onSelect: (a: string) => void }) {
  const { state } = useSwarm();
  const reduced = useReducedMotion();
  const board = state.nodes.find((n) => n.isReal);
  const threat = state.threat;
  const alarm = !!threat;
  const tampered = board?.quarantineReason === 'tamper';
  const quarantined = !!board?.quarantined;

  let oled: [string, string];
  if (alarm) oled = ['THREAT CONFIRMED', `${sectorName(threat!.sector)} · ${threat!.claimants.length}/${threat!.n} NODES`];
  else if (quarantined) oled = ['QUARANTINED', tampered ? 'TAMPER LOGGED' : 'REP BELOW LIMIT'];
  else if (board?.status === 'pending') oled = ['CLAIM SIGNED', 'AWAITING PEERS'];
  else oled = ['SWARMSHIELD', board ? `${sectorName(board.sector)} · ALL CLEAR` : 'NO LINK'];

  return (
    <Panel
      id="board"
      title="Neurick board"
      icon={Cpu}
      className={className}
      aside={board && <StatusChip status={board.status} compact />}
    >
      {!state.ready ? (
        <div className="space-y-3 p-4">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
      ) : !board ? (
        <EmptyState icon={Cpu} title="Physical board not registered" body="Once flashed, the board posts to the same gateway endpoint as any virtual node." />
      ) : (
        <div className="space-y-3 p-4">
          <div className="flex items-stretch gap-3">
            {/* OLED */}
            <div
              className={cx(
                'relative flex min-w-0 flex-1 flex-col justify-center overflow-hidden rounded-lg border bg-black px-3 py-2.5 font-mono transition-colors duration-300',
                alarm ? 'border-alert/50' : 'border-white/10',
              )}
              aria-label={`OLED display: ${oled.join(', ')}`}
              role="img"
            >
              <div aria-hidden className="pointer-events-none absolute inset-0 bg-[repeating-linear-gradient(0deg,rgb(255_255_255/0.035)_0px,rgb(255_255_255/0.035)_1px,transparent_1px,transparent_3px)]" />
              <motion.p
                key={oled[0]}
                initial={{ opacity: 0 }}
                animate={alarm && !reduced ? { opacity: [1, 0.35, 1] } : { opacity: 1 }}
                transition={alarm && !reduced ? { duration: 1, repeat: Infinity } : { duration: 0.2 }}
                className={cx('truncate text-[12.5px] font-medium tracking-[0.06em]', alarm ? 'text-alert' : 'text-sky-200')}
              >
                {oled[0]}
              </motion.p>
              <p className={cx('mt-0.5 truncate text-[10.5px] tracking-[0.08em]', alarm ? 'text-alert/70' : 'text-sky-200/50')}>{oled[1]}</p>
            </div>

            {/* Servo flag */}
            <div className="flex w-[74px] shrink-0 flex-col items-center justify-center rounded-lg border border-white/[0.06] bg-white/[0.02] py-2">
              <svg width="40" height="38" viewBox="0 0 40 38" aria-hidden>
                <line x1="10" y1="4" x2="10" y2="36" stroke="rgb(156 163 175)" strokeWidth="2" strokeLinecap="round" />
                <motion.g style={{ originX: 0, originY: 0 }} initial={false} animate={{ rotate: alarm ? 0 : 90 }} transition={{ type: 'spring', stiffness: 160, damping: 14 }}>
                  <path d="M10 5 L34 10 L10 17 Z" fill={alarm ? 'var(--color-alert)' : 'rgb(107 114 128)'} className="transition-[fill] duration-300" />
                </motion.g>
              </svg>
              <span className={cx('mt-1 text-2xs font-medium', alarm ? 'text-alert' : 'text-gray-400')}>{alarm ? 'Flag up' : 'Flag down'}</span>
            </div>
          </div>

          <ul className="grid grid-cols-3 gap-2">
            <Peripheral
              icon={alarm ? Volume2 : VolumeX}
              label="Speaker"
              value={alarm ? 'Alarm on' : 'Silent'}
              active={alarm}
              tone="alert"
              extra={alarm && !reduced ? <SoundBars /> : null}
            />
            <Peripheral icon={quarantined ? MicOff : Mic} label="Mic / FFT" value={quarantined ? 'Ignored' : 'Listening'} active={!quarantined} tone="ok" />
            <Peripheral icon={Move3d} label="Motion" value={tampered ? 'Tamper' : 'Stable'} active={tampered} tone="quarantine" />
          </ul>

          <button
            type="button"
            onClick={() => onSelect(board.address)}
            className="group flex w-full items-center justify-between rounded-lg px-1 text-xs text-gray-400 transition-colors duration-200 hover:text-white"
          >
            <span>
              {board.label} · rep <span className="tabular text-gray-200">{board.reputation}</span>
            </span>
            <span className="inline-flex items-center gap-1 text-accent">
              Details
              <span aria-hidden className="inline-block transition-transform duration-200 group-hover:translate-x-0.5">
                →
              </span>
            </span>
          </button>
        </div>
      )}
    </Panel>
  );
}

function Peripheral({
  icon: Icon,
  label,
  value,
  active,
  tone,
  extra,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  active: boolean;
  tone: 'alert' | 'ok' | 'quarantine';
  extra?: ReactNode;
}) {
  const toneText = { alert: 'text-alert', ok: 'text-ok', quarantine: 'text-quarantine' }[tone];
  return (
    <li
      className={cx(
        'rounded-lg border px-2.5 py-2 transition-colors duration-300',
        active && tone === 'alert' ? 'border-alert/35 bg-alert/[0.07]' : 'border-white/[0.06] bg-white/[0.02]',
      )}
    >
      <div className="flex items-center justify-between">
        <Icon className={cx('size-3.5', active ? toneText : 'text-gray-500')} aria-hidden />
        {extra}
      </div>
      <p className="mt-1.5 text-2xs text-gray-500">{label}</p>
      <p className={cx('text-xs font-medium', active ? toneText : 'text-gray-300')}>{value}</p>
    </li>
  );
}

function SoundBars() {
  return (
    <span className="flex h-3 items-end gap-[2px]" aria-hidden>
      {[0, 0.15, 0.3].map((d) => (
        <motion.span
          key={d}
          className="w-[2px] rounded-full bg-alert"
          animate={{ height: ['30%', '100%', '30%'] }}
          transition={{ duration: 0.6, delay: d, repeat: Infinity, ease: 'easeInOut' }}
        />
      ))}
    </span>
  );
}

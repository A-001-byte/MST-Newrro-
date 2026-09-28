import { FileCode2, PlusCircle } from 'lucide-react';
import { useSwarm } from '../../hooks/useSwarm';
import { useAnimatedNumber } from '../../hooks/useAnimatedNumber';
import { cx } from '../../lib/format';
import { LogoPill } from '../ui/Logo';
import { WalletButton } from './WalletButton';

interface TopNavProps {
  onExit: () => void;
  onOpenContracts: () => void;
  onOpenRegister: () => void;
}

function LiveIndicator() {
  const { state, mode } = useSwarm();
  const live = state.connection === 'live';
  const block = Math.round(useAnimatedNumber(state.blockHeight, 400, 50));
  const threat = !!state.threat;

  return (
    <div className="glass flex h-10 items-center gap-3 rounded-full px-3.5 sm:h-11 sm:px-4" role="status" aria-live="off">
      <span className="flex items-center gap-2">
        <span className="relative flex size-2">
          {live && <span className={cx('absolute inset-0 animate-ping-slow rounded-full', threat ? 'bg-alert' : 'bg-ok')} />}
          <span className={cx('relative size-2 rounded-full', live ? (threat ? 'bg-alert' : 'bg-ok') : 'bg-quarantine')} />
        </span>
        <span className="text-sm font-medium text-white">{live ? 'Live' : state.connection === 'connecting' ? 'Connecting' : 'Offline'}</span>
      </span>
      <span className="hidden h-3.5 w-px bg-white/15 sm:block" aria-hidden />
      <span className="hidden text-sm text-gray-300 sm:inline">{state.network}</span>
      <span className="h-3.5 w-px bg-white/15" aria-hidden />
      <span className="font-mono text-[11px] text-gray-400 tabular" title="Latest block">
        #{state.blockHeight ? block.toLocaleString('en-US') : '—'}
      </span>
      {mode === 'mock' && (
        <span
          className="hidden rounded-full border border-white/10 px-1.5 py-px text-2xs font-medium text-gray-400 md:inline"
          title="Local simulator. Set VITE_USE_MOCK=false to stream from the gateway."
        >
          Simulator
        </span>
      )}
    </div>
  );
}

export function TopNav({ onExit, onOpenContracts, onOpenRegister }: TopNavProps) {
  const links = [
    { label: 'Contracts', icon: FileCode2, onClick: onOpenContracts },
    { label: 'Register node', icon: PlusCircle, onClick: onOpenRegister },
  ];

  return (
    <header className="sticky top-0 z-30 px-3 pt-3 sm:px-6 sm:pt-4">
      <div className="mx-auto flex max-w-[1600px] items-center gap-2 sm:gap-3">
        <button
          type="button"
          onClick={onExit}
          aria-label="Back to overview"
          className="flex items-center gap-2.5 rounded-full pr-1 transition-transform duration-200 active:scale-[0.97]"
        >
          <LogoPill />
          <span className="hidden text-md font-medium tracking-tight text-white lg:inline">SwarmShield</span>
        </button>

        <LiveIndicator />

        <nav aria-label="Dashboard" className="glass ml-auto hidden items-center gap-1 rounded-xl p-1 md:flex">
          {links.map((l) => (
            <button
              key={l.label}
              type="button"
              onClick={l.onClick}
              className="inline-flex h-8 items-center gap-1.5 rounded-lg px-3 text-sm font-medium text-gray-300 transition-colors duration-200 hover:bg-white/[0.08] hover:text-white active:bg-white/15"
            >
              <l.icon className="size-3.5" aria-hidden />
              {l.label}
            </button>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-1.5 md:ml-0">
          {links.map((l) => (
            <button
              key={l.label}
              type="button"
              onClick={l.onClick}
              aria-label={l.label}
              className="glass inline-flex size-10 items-center justify-center rounded-full text-gray-300 transition-colors duration-200 hover:text-white active:bg-white/20 md:hidden"
            >
              <l.icon className="size-4" aria-hidden />
            </button>
          ))}
          <WalletButton />
        </div>
      </div>
    </header>
  );
}

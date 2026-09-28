import { useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { AlertCircle, Bug, ChevronDown, Radar, RotateCcw, Vibrate, WifiOff } from 'lucide-react';
import { useSwarm } from '../../hooks/useSwarm';
import { sectorName } from '../../lib/config';
import { cx, nodeName } from '../../lib/format';
import { Button } from '../ui/Button';

type Act = 'drone' | 'rogue' | 'tamper' | 'reset';

/** Floating demo control dock: the three-act demo from the spec, plus reset */
export function DemoDock() {
  const { state, actions, mode } = useSwarm();
  const [running, setRunning] = useState<Partial<Record<Act, boolean>>>({});
  const [error, setError] = useState<string | null>(null);
  const candidates = state.nodes.filter((n) => !n.quarantined);
  const realNode = state.nodes.find((n) => n.isReal);
  const [target, setTarget] = useState<string>('');

  // default the tamper target to the physical board, fall back to the first healthy node
  useEffect(() => {
    if (target && candidates.some((n) => n.address === target)) return;
    const next = realNode && !realNode.quarantined ? realNode.address : (candidates[0]?.address ?? '');
    if (next !== target) setTarget(next);
  }, [candidates, realNode, target]);

  useEffect(() => {
    if (!error) return;
    const t = window.setTimeout(() => setError(null), 4000);
    return () => window.clearTimeout(t);
  }, [error]);

  const run = (act: Act, fn: () => Promise<void>) => async () => {
    setRunning((r) => ({ ...r, [act]: true }));
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Request failed');
    } finally {
      setRunning((r) => ({ ...r, [act]: false }));
    }
  };

  const disabled = !state.ready || state.connection !== 'live';
  const droneBusy = !!running.drone || !!state.threat;

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-0 z-40 flex flex-col items-center gap-2 px-3 pb-[max(12px,env(safe-area-inset-bottom))] sm:pb-5">
      <AnimatePresence>
        {error && (
          <motion.div
            role="alert"
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 6 }}
            className="pointer-events-auto inline-flex items-center gap-2 rounded-full border border-white/10 bg-black/80 px-3.5 py-2 text-sm text-gray-200 backdrop-blur-md"
          >
            <AlertCircle className="size-3.5 text-pending" aria-hidden />
            {error}
          </motion.div>
        )}
      </AnimatePresence>

      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.2, ease: [0.22, 1, 0.36, 1] }}
        role="toolbar"
        aria-label="Demo controls"
        className="pointer-events-auto flex w-full max-w-max flex-wrap items-center justify-center gap-1.5 rounded-2xl sm:flex-nowrap border border-white/10 bg-[#0b0e13]/75 p-1.5 shadow-2xl shadow-black/70 backdrop-blur-xl scroll-thin sm:gap-2"
      >
        <span className="hidden pr-1 pl-2.5 eyebrow lg:inline">Demo</span>

        <Button
          variant="primary"
          size="sm"
          icon={<Radar className="size-3.5" aria-hidden />}
          loading={running.drone}
          disabled={disabled || droneBusy}
          onClick={run('drone', actions.simulateDrone)}
          title={state.threat ? 'Alert in progress' : `Fire corroborating claims at ${sectorName(realNode?.sector ?? 3)}`}
        >
          <span className="hidden md:inline">Simulate drone detection</span>
          <span className="md:hidden">Drone</span>
        </Button>

        <Button
          variant="glass"
          size="sm"
          icon={<Bug className="size-3.5" aria-hidden />}
          loading={running.rogue}
          disabled={disabled || !!running.rogue}
          onClick={run('rogue', actions.injectRogue)}
        >
          <span className="hidden md:inline">Inject rogue node</span>
          <span className="md:hidden">Rogue</span>
        </Button>

        <div className="flex items-center rounded-full border border-white/10 bg-white/[0.06]">
          <label className="relative flex items-center">
            <span className="sr-only">Node to tamper</span>
            <select
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              disabled={disabled || candidates.length === 0}
              className="h-8 max-w-[120px] cursor-pointer appearance-none rounded-l-full bg-transparent pr-6 pl-3 text-sm text-gray-200 transition-colors duration-200 outline-none hover:text-white focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-40 sm:max-w-[150px]"
            >
              {candidates.length === 0 && <option value="">No healthy nodes</option>}
              {candidates.map((n) => (
                <option key={n.address} value={n.address} className="bg-[#0b0e13]">
                  {nodeName(n)}
                  {n.isReal ? ' (board)' : ''}
                </option>
              ))}
            </select>
            <ChevronDown className="pointer-events-none absolute right-1.5 size-3.5 text-gray-500" aria-hidden />
          </label>
          <span className="h-4 w-px bg-white/10" aria-hidden />
          <button
            type="button"
            onClick={run('tamper', () => actions.simulateTamper(target))}
            disabled={disabled || !target || !!running.tamper}
            className="inline-flex h-8 items-center gap-1.5 rounded-r-full pr-3 pl-2.5 text-sm font-medium text-gray-200 transition-colors duration-200 hover:bg-white/[0.08] hover:text-white active:bg-white/15 disabled:opacity-40 disabled:hover:bg-transparent"
          >
            <Vibrate className={cx('size-3.5', running.tamper && 'animate-pulse')} aria-hidden />
            <span className="hidden md:inline">Simulate tamper</span>
            <span className="md:hidden">Tamper</span>
          </button>
        </div>

        <span className="mx-0.5 hidden h-5 w-px shrink-0 bg-white/10 sm:block" aria-hidden />

        <Button
          variant="ghost"
          size="sm"
          icon={<RotateCcw className="size-3.5" aria-hidden />}
          loading={running.reset}
          disabled={!state.ready}
          onClick={run('reset', actions.reset)}
          aria-label="Reset demo"
        >
          <span className="hidden sm:inline">Reset</span>
        </Button>
        {mode === 'mock' && actions.dropConnection && (
          <Button
            variant="ghost"
            size="sm"
            className="w-8 px-0"
            icon={<WifiOff className="size-3.5" aria-hidden />}
            disabled={disabled}
            onClick={() => actions.dropConnection?.()}
            aria-label="Simulate socket drop"
            title="Simulate socket drop"
          />
        )}
      </motion.div>
    </div>
  );
}

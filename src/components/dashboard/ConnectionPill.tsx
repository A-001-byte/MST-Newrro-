import { AnimatePresence, motion } from 'framer-motion';
import { Loader2, WifiOff } from 'lucide-react';
import { useSwarm } from '../../hooks/useSwarm';

/** Floating pill shown when the stream drops after the first successful load */
export function ConnectionPill() {
  const { state } = useSwarm();
  const show = state.ready && state.connection !== 'live';
  const offline = state.connection === 'offline';

  return (
    <div className="pointer-events-none fixed inset-x-0 top-[72px] z-40 flex justify-center sm:top-[84px]" role="status" aria-live="polite">
      <AnimatePresence>
        {show && (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.22 }}
            className="glass inline-flex items-center gap-2 rounded-full bg-black/60 px-3.5 py-2 text-sm font-medium text-gray-200 shadow-lg shadow-black/40"
          >
            {offline ? <WifiOff className="size-3.5 text-quarantine" aria-hidden /> : <Loader2 className="size-3.5 animate-spin text-pending" aria-hidden />}
            {offline ? 'Gateway offline · retrying' : 'Reconnecting…'}
            <span className="text-xs text-gray-500">showing last known state</span>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

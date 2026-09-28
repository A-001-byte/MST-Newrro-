import { motion } from 'framer-motion';
import { Siren } from 'lucide-react';
import type { Threat } from '../../types';
import { useNow } from '../../hooks/useNow';
import { explorerTx, sectorName } from '../../lib/config';
import { relativeTime } from '../../lib/format';
import { ExplorerLink, Mono } from '../ui/Hash';

export function ThreatBanner({ threat }: { threat: Threat }) {
  const now = useNow(1000);
  return (
    <motion.div
      role="alert"
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: 'auto' }}
      exit={{ opacity: 0, height: 0 }}
      transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
      className="overflow-hidden"
    >
      <div className="relative flex flex-col gap-3 overflow-hidden rounded-2xl border border-alert/40 bg-alert/[0.09] px-4 py-3.5 shadow-[0_0_40px_-12px_rgb(244_63_94/0.6)] backdrop-blur-md sm:flex-row sm:items-center sm:gap-4 sm:px-5">
        <div className="absolute inset-y-0 left-0 w-0.5 bg-alert" aria-hidden />
        <div className="flex items-center gap-3">
          <span className="relative flex size-9 shrink-0 items-center justify-center rounded-full bg-alert/15">
            <span className="absolute inset-0 animate-ping-slow rounded-full bg-alert/30" aria-hidden />
            <Siren className="relative size-4 text-alert" aria-hidden />
          </span>
          <div className="min-w-0">
            <p className="text-sm font-semibold tracking-[0.08em] text-alert uppercase">
              Threat confirmed · Sector {sectorName(threat.sector)} · {threat.claimants.length} of {threat.n} nodes corroborated
            </p>
            <p className="mt-0.5 text-xs text-gray-300">
              Quorum k={threat.k} reached on-chain {relativeTime(threat.confirmedAt, now)}. Alarm relayed to the Neurick board and inspection auction opened. No engagement.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1 sm:ml-auto">
          <span className="text-xs text-gray-500">AlertConfirmed</span>
          <Mono value={threat.txHash} className="text-gray-300" />
          <ExplorerLink href={explorerTx(threat.txHash)} label="View AlertConfirmed transaction on explorer" />
        </div>
      </div>
    </motion.div>
  );
}

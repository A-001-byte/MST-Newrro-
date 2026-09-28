import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Copy, ExternalLink, KeyRound, Loader2, LogOut } from 'lucide-react';
import { useBridgeKey } from '../../hooks/useBridgeKey';
import { explorerAddress } from '../../lib/config';
import { truncateMiddle } from '../../lib/format';

export function WalletButton() {
  const wallet = useBridgeKey();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    document.addEventListener('pointerdown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  if (wallet.status !== 'connected' || !wallet.address) {
    const connecting = wallet.status === 'connecting';
    return (
      <button
        type="button"
        onClick={() => void wallet.connect()}
        disabled={connecting}
        title={wallet.error ?? undefined}
        className="group inline-flex h-10 items-center gap-2 rounded-full border border-white/30 px-3.5 text-sm font-medium text-white backdrop-blur-sm transition-all duration-200 hover:border-accent-strong hover:bg-accent-strong active:scale-[0.98] disabled:opacity-60 disabled:hover:bg-transparent disabled:hover:border-white/30 max-sm:w-10 max-sm:justify-center max-sm:px-0 sm:h-11 sm:px-4"
      >
        {connecting ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : <KeyRound className="size-3.5" aria-hidden />}
        <span className="hidden sm:inline">{connecting ? 'Connecting…' : 'Connect BridgeKey'}</span>
        <span className="sr-only sm:hidden">Connect BridgeKey</span>
      </button>
    );
  }

  const address = wallet.address;
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="glass inline-flex h-10 items-center gap-2 rounded-full px-3.5 text-sm font-medium text-white transition-colors duration-200 hover:bg-white/15 active:bg-white/20 sm:h-11 sm:px-4"
      >
        <span className="size-1.5 rounded-full bg-ok shadow-[0_0_8px_var(--color-ok)]" aria-hidden />
        <span className="sr-only">BridgeKey connected:</span>
        <span className="font-mono text-[11.5px]">{truncateMiddle(address, 6, 4)}</span>
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            role="menu"
            initial={{ opacity: 0, y: -4, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -4, scale: 0.98 }}
            transition={{ duration: 0.18 }}
            className="absolute right-0 mt-2 w-64 origin-top-right rounded-xl border border-white/10 bg-[#0b0e13]/95 p-1.5 shadow-2xl shadow-black/60 backdrop-blur-xl"
          >
            <div className="px-2.5 pt-1.5 pb-2">
              <p className="eyebrow">Authority wallet</p>
              <p className="mt-1 font-mono text-[11px] break-all text-gray-300">{address}</p>
            </div>
            {[
              { label: 'Copy address', icon: Copy, onClick: () => void navigator.clipboard?.writeText(address) },
              { label: 'View on explorer', icon: ExternalLink, onClick: () => window.open(explorerAddress(address), '_blank', 'noopener') },
              { label: 'Disconnect', icon: LogOut, onClick: wallet.disconnect },
            ].map((item) => (
              <button
                key={item.label}
                role="menuitem"
                type="button"
                onClick={() => {
                  item.onClick();
                  setOpen(false);
                }}
                className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm text-gray-300 transition-colors duration-150 hover:bg-white/[0.07] hover:text-white"
              >
                <item.icon className="size-3.5 text-gray-500" aria-hidden />
                {item.label}
              </button>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

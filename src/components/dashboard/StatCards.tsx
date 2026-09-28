import { useEffect, useRef, useState } from 'react';
import { BadgeCheck, Radio, Timer, TrendingDown, type LucideIcon } from 'lucide-react';
import { useSwarm } from '../../hooks/useSwarm';
import { useAnimatedNumber } from '../../hooks/useAnimatedNumber';
import { cx, formatMs } from '../../lib/format';
import { Skeleton } from '../ui/Panel';

interface StatProps {
  label: string;
  value: number | null;
  format?: (v: number) => string;
  icon: LucideIcon;
  hint: string;
  loading: boolean;
}

function Stat({ label, value, format = (v) => Math.round(v).toLocaleString('en-US'), icon: Icon, hint, loading }: StatProps) {
  const animated = useAnimatedNumber(value ?? 0, 600);
  const [flash, setFlash] = useState(false);
  const prev = useRef(value);
  useEffect(() => {
    if (prev.current !== null && value !== null && value !== prev.current) {
      setFlash(true);
      const t = window.setTimeout(() => setFlash(false), 700);
      prev.current = value;
      return () => window.clearTimeout(t);
    }
    prev.current = value;
  }, [value]);

  return (
    <div className={cx('panel px-4 py-3.5 transition-[border-color] duration-500', flash && 'border-accent/40')}>
      <div className="flex items-center justify-between">
        <p className="eyebrow">{label}</p>
        <Icon className="size-3.5 text-gray-600" aria-hidden />
      </div>
      {loading ? (
        <Skeleton className="mt-2.5 h-6 w-16" />
      ) : (
        <p className="mt-1.5 text-[22px] leading-7 font-medium tracking-tight text-white tabular">{value === null ? '—' : format(animated)}</p>
      )}
      <p className="mt-0.5 truncate text-xs text-gray-500">{hint}</p>
    </div>
  );
}

export function StatCards({ compact = false }: { compact?: boolean }) {
  const { state } = useSwarm();
  const { stats } = state;
  const loading = !state.ready;
  return (
    <div className={cx('grid gap-3', compact ? 'grid-cols-2' : 'grid-cols-2 lg:grid-cols-4')}>
      <Stat label="Total claims" value={stats.totalClaims} icon={Radio} hint="Signed DetectionClaims on-chain" loading={loading} />
      <Stat label="Alerts confirmed" value={stats.alertsConfirmed} icon={BadgeCheck} hint="k-of-n quorum reached" loading={loading} />
      <Stat label="Nodes slashed" value={stats.nodesSlashed} icon={TrendingDown} hint="Penalised for lone claims" loading={loading} />
      <Stat
        label="Avg time to confirm"
        value={stats.avgConfirmMs}
        format={(v) => formatMs(v)}
        icon={Timer}
        hint="First claim to AlertConfirmed"
        loading={loading}
      />
    </div>
  );
}

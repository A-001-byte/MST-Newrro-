import type { NodeStatus } from '../../types';
import { STATUS } from '../../lib/status';
import { cx } from '../../lib/format';

export function StatusChip({ status, compact = false }: { status: NodeStatus; compact?: boolean }) {
  const meta = STATUS[status];
  const Icon = meta.icon;
  return (
    <span
      className={cx(
        'inline-flex items-center gap-1 rounded-full border font-medium whitespace-nowrap transition-colors duration-300',
        compact ? 'h-5 px-1.5 text-2xs' : 'h-6 px-2 text-xs',
        meta.text,
        meta.bg,
        meta.border,
      )}
    >
      <Icon className={compact ? 'size-3' : 'size-3.5'} aria-hidden strokeWidth={2} />
      {meta.label}
    </span>
  );
}

export function StatusDot({ status, className }: { status: NodeStatus; className?: string }) {
  return <span aria-hidden className={cx('inline-block size-1.5 rounded-full', className)} style={{ background: STATUS[status].color }} />;
}

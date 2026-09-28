import type { ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import { cx } from '../../lib/format';

interface PanelProps {
  title: string;
  icon?: LucideIcon;
  /** Right side of the header (counts, actions) */
  aside?: ReactNode;
  className?: string;
  bodyClassName?: string;
  children: ReactNode;
  id?: string;
}

export function Panel({ title, icon: Icon, aside, className, bodyClassName, children, id }: PanelProps) {
  const headingId = id ? `${id}-title` : undefined;
  return (
    <section id={id} aria-labelledby={headingId} className={cx('panel flex min-w-0 flex-col', className)}>
      <header className="flex h-11 shrink-0 items-center justify-between gap-3 border-b border-white/[0.05] px-4">
        <h2 id={headingId} className="flex min-w-0 items-center gap-2 text-sm font-medium text-gray-200">
          {Icon && <Icon className="size-3.5 shrink-0 text-gray-500" aria-hidden />}
          <span className="truncate">{title}</span>
        </h2>
        {aside && <div className="flex shrink-0 items-center gap-2">{aside}</div>}
      </header>
      <div className={cx('min-h-0 flex-1', bodyClassName)}>{children}</div>
    </section>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cx('animate-pulse rounded-md bg-white/[0.05]', className)} />;
}

export function EmptyState({ icon: Icon, title, body, compact }: { icon: LucideIcon; title: string; body?: string; compact?: boolean }) {
  if (compact)
    return (
      <div className="flex items-center gap-3 px-4 py-4">
        <div className="flex size-8 shrink-0 items-center justify-center rounded-full border border-white/10 bg-white/[0.03]">
          <Icon className="size-3.5 text-gray-500" aria-hidden />
        </div>
        <p className="text-sm text-gray-300">
          {title}
          {body && <span className="text-gray-500"> · {body}</span>}
        </p>
      </div>
    );
  return (
    <div className="flex h-full min-h-[140px] flex-col items-center justify-center gap-2 px-6 py-8 text-center">
      <div className="flex size-9 items-center justify-center rounded-full border border-white/10 bg-white/[0.03]">
        <Icon className="size-4 text-gray-500" aria-hidden />
      </div>
      <p className="text-sm font-medium text-gray-300">{title}</p>
      {body && <p className="max-w-[260px] text-xs text-gray-500">{body}</p>}
    </div>
  );
}

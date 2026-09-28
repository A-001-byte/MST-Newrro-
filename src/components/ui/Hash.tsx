import { useEffect, useRef, useState } from 'react';
import { Check, Copy, ExternalLink } from 'lucide-react';
import { cx, truncateMiddle } from '../../lib/format';

export function CopyButton({ value, label = 'Copy', className }: { value: string; label?: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = value;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
    setCopied(true);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setCopied(false), 1400);
  };

  return (
    <button
      type="button"
      onClick={copy}
      aria-label={copied ? 'Copied' : `${label} ${value}`}
      title={copied ? 'Copied' : label}
      className={cx(
        'inline-flex size-6 items-center justify-center rounded-md text-gray-500 transition-colors duration-200 hover:bg-white/[0.08] hover:text-white active:bg-white/15',
        copied && 'text-ok hover:text-ok',
        className,
      )}
    >
      {copied ? <Check className="size-3.5" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
    </button>
  );
}

export function ExplorerLink({ href, label, className }: { href: string; label: string; className?: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      aria-label={label}
      title={label}
      className={cx(
        'inline-flex size-6 items-center justify-center rounded-md text-gray-500 transition-colors duration-200 hover:bg-white/[0.08] hover:text-accent active:bg-white/15',
        className,
      )}
    >
      <ExternalLink className="size-3.5" aria-hidden />
    </a>
  );
}

export function Mono({ value, head = 6, tail = 4, className }: { value: string; head?: number; tail?: number; className?: string }) {
  return (
    <span className={cx('font-mono text-[11px] tracking-tight text-gray-400', className)} title={value}>
      {truncateMiddle(value, head, tail)}
    </span>
  );
}

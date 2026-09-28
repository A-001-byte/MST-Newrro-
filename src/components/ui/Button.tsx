import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { Loader2 } from 'lucide-react';
import { cx } from '../../lib/format';

type Variant = 'primary' | 'outline' | 'glass' | 'ghost' | 'danger';
type Size = 'sm' | 'md';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  icon?: ReactNode;
  /** Trailing arrow that nudges right on hover (the reference micro-interaction) */
  arrow?: boolean;
  loading?: boolean;
}

const variants: Record<Variant, string> = {
  primary:
    'bg-accent-strong text-white border border-accent-strong hover:bg-blue-400 hover:border-blue-400 active:bg-blue-600 shadow-[0_0_0_1px_rgb(59_130_246/0.2),0_8px_24px_-8px_rgb(59_130_246/0.6)]',
  outline: 'text-white border border-white/30 backdrop-blur-sm hover:bg-accent-strong hover:border-accent-strong active:bg-blue-600',
  glass: 'glass text-gray-200 hover:text-white hover:bg-white/15 active:bg-white/20',
  ghost: 'text-gray-400 border border-transparent hover:text-white hover:bg-white/[0.06] active:bg-white/10',
  danger: 'text-alert border border-alert/35 bg-alert-soft hover:bg-alert/25 active:bg-alert/30',
};

const sizes: Record<Size, string> = {
  sm: 'h-8 px-3 text-sm gap-1.5',
  md: 'h-10 px-5 text-base gap-2',
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'glass', size = 'md', icon, arrow, loading, className, children, disabled, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cx(
        'group inline-flex shrink-0 items-center justify-center rounded-full font-medium whitespace-nowrap select-none',
        'transition-[background-color,border-color,color,box-shadow,opacity,transform] duration-200 active:scale-[0.98]',
        'disabled:opacity-40 disabled:active:scale-100 disabled:hover:bg-transparent',
        variants[variant],
        sizes[size],
        className,
      )}
      {...rest}
    >
      {loading ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : icon}
      {children}
      {arrow && (
        <span aria-hidden className="inline-block transition-transform duration-200 group-hover:translate-x-0.5 group-disabled:translate-x-0">
          →
        </span>
      )}
    </button>
  );
});

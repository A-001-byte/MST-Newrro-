/** SwarmShield mark: three peers around a protected core, no centre node */
export function Logo({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="8.2" stroke="currentColor" strokeOpacity=".35" strokeWidth="1.4" />
      <path d="M12 3.8 19.1 16.1H4.9Z" stroke="currentColor" strokeOpacity=".55" strokeWidth="1.2" strokeLinejoin="round" />
      <circle cx="12" cy="12" r="2.1" fill="currentColor" fillOpacity=".9" />
      <circle cx="12" cy="3.8" r="2" fill="currentColor" />
      <circle cx="19.1" cy="16.1" r="2" fill="currentColor" />
      <circle cx="4.9" cy="16.1" r="2" fill="currentColor" />
    </svg>
  );
}

export function LogoPill({ className = '' }: { className?: string }) {
  return (
    <div className={`glass flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-white sm:h-11 sm:w-11 ${className}`}>
      <Logo />
    </div>
  );
}

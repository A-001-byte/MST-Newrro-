import { useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { AudioLines, BadgeCheck, Ban, FileSignature, Gavel, ShieldCheck, Siren, Vibrate, type LucideIcon } from 'lucide-react';
import { LogoPill } from '../ui/Logo';
import { CONTRACTS, explorerAddress, NETWORK_NAME, QUORUM_K } from '../../lib/config';
import { truncateMiddle } from '../../lib/format';
import { CopyButton, ExplorerLink } from '../ui/Hash';

interface LandingProps {
  onLaunch: (extra?: string) => void;
}

const NAV = [
  { label: 'Overview', target: 'top' },
  { label: 'How it works', target: 'how' },
  { label: 'Live Network', target: 'live' },
  { label: 'Contracts', target: 'contracts' },
] as const;

const fade = (delay: number) => ({
  initial: { opacity: 0, y: 10 },
  animate: { opacity: 1, y: 0 },
  transition: { duration: 0.6, delay, ease: [0.22, 1, 0.36, 1] as const },
});

export function Landing({ onLaunch }: LandingProps) {
  const go = (target: (typeof NAV)[number]['target']) => {
    if (target === 'live') return onLaunch();
    if (target === 'top') return window.scrollTo({ top: 0, behavior: 'smooth' });
    document.getElementById(target)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  return (
    <div id="top" className="bg-black text-white">
      <Hero onLaunch={onLaunch} go={go} />
      <HowItWorks />
      <ContractsSection onLaunch={onLaunch} />
      <footer className="border-t border-white/[0.06] px-6 py-10 sm:px-12 md:px-20 lg:px-28">
        <div className="flex flex-col items-start justify-between gap-4 sm:flex-row sm:items-center">
          <p className="text-xs text-gray-500">SwarmShield · MST Blockchain Buildathon · Detect · Verify · Alert. Never engages.</p>
          <button
            type="button"
            onClick={() => onLaunch()}
            className="group inline-flex items-center gap-1.5 text-xs font-medium text-accent transition-colors duration-200 hover:text-blue-300"
          >
            Open the live dashboard
            <span aria-hidden className="inline-block transition-transform duration-200 group-hover:translate-x-0.5">
              →
            </span>
          </button>
        </div>
      </footer>
    </div>
  );
}

function HeroVideo() {
  const ref = useRef<HTMLVideoElement>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const apply = () => {
      const v = ref.current;
      if (!v) return;
      if (mq.matches) v.pause();
      else void v.play().catch(() => undefined);
    };
    apply();
    mq.addEventListener('change', apply);
    return () => mq.removeEventListener('change', apply);
  }, []);
  return (
    <video
      ref={ref}
      autoPlay
      muted
      loop
      playsInline
      preload="auto"
      aria-hidden
      onCanPlay={() => setReady(true)}
      className={`absolute inset-0 h-full w-full object-cover transition-opacity duration-700 ${ready ? 'opacity-100' : 'opacity-0'}`}
      src="./video.mp4"
    />
  );
}

function Hero({ onLaunch, go }: LandingProps & { go: (t: (typeof NAV)[number]['target']) => void }) {
  return (
    <div className="relative min-h-[100svh] overflow-hidden bg-black">
      <HeroVideo />
      {/* Dark overlay for contrast, plus a bottom-left vignette behind the copy */}
      <div className="pointer-events-none absolute inset-0 bg-black/30" />
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_70%_60%_at_0%_100%,rgb(0_0_0/0.55),transparent_70%)]" />
      <div className="pointer-events-none absolute inset-y-0 left-0 w-full bg-gradient-to-r from-black/75 via-black/30 to-transparent sm:w-[55%]" />
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-40 bg-gradient-to-t from-black/60 to-transparent" />

      <div className="relative z-10 flex min-h-[100svh] flex-col">
        <motion.nav {...fade(0.05)} aria-label="Primary" className="flex items-center justify-center gap-2 px-4 pt-4 sm:gap-3 sm:px-8 sm:pt-6">
          <button type="button" onClick={() => go('top')} aria-label="SwarmShield home" className="rounded-full transition-transform duration-200 active:scale-95">
            <LogoPill />
          </button>
          <div className="glass flex items-center gap-4 rounded-xl px-4 py-2.5 sm:gap-10 sm:px-8 sm:py-3">
            {NAV.map((l) => (
              <button
                key={l.label}
                type="button"
                onClick={() => go(l.target)}
                className="rounded text-[12px] font-medium whitespace-nowrap text-gray-300 transition-colors duration-200 hover:text-white sm:text-[14px]"
              >
                {l.label}
              </button>
            ))}
          </div>
        </motion.nav>

        <div className="flex flex-1 items-end justify-between gap-8 px-6 pb-10 sm:px-12 sm:pb-16 md:px-20 lg:px-28 lg:pb-20">
          <div className="max-w-[20rem] sm:max-w-sm">
            <motion.a
              {...fade(0.15)}
              href="#contracts"
              onClick={(e) => {
                e.preventDefault();
                go('contracts');
              }}
              className="group mb-3 inline-flex items-center gap-1.5 text-[11.5px] font-medium text-accent transition-colors duration-200 hover:text-blue-300"
            >
              Built on {NETWORK_NAME}
              <span aria-hidden className="inline-block transition-transform duration-200 group-hover:translate-x-0.5">
                →
              </span>
            </motion.a>

            <motion.h1 {...fade(0.22)} className="mb-3 text-[1.5rem] leading-[1.15] font-medium tracking-tight text-white drop-shadow-md sm:text-[1.75rem]">
              No single point of failure. A swarm that verifies itself.
            </motion.h1>

            <motion.p {...fade(0.3)} className="mb-4 text-[13px] leading-[1.5] font-normal text-gray-400">
              Independent sensor nodes corroborate every drone detection on-chain before anyone is alerted. One hacked node can't fake a threat or hide one.
            </motion.p>

            <motion.div {...fade(0.38)} className="flex flex-col items-start gap-4">
              <button
                type="button"
                onClick={() => onLaunch()}
                className="group inline-flex items-center gap-2 rounded-full border border-white/30 px-5 py-2.5 text-[13px] font-medium text-white backdrop-blur-sm transition-all duration-200 hover:border-accent-strong hover:bg-accent-strong active:scale-[0.98]"
              >
                Launch live dashboard
                <span aria-hidden className="inline-block transition-transform duration-200 group-hover:translate-x-0.5">
                  →
                </span>
              </button>
              <p className="inline-flex items-center gap-1.5 text-[11px] font-medium tracking-wide text-gray-400">
                <ShieldCheck className="size-3.5 text-gray-400" aria-hidden />
                Detect · Verify · Alert. Never engages.
              </p>
            </motion.div>
          </div>

          <motion.dl {...fade(0.5)} className="glass hidden items-center gap-6 rounded-xl px-5 py-3 lg:flex" aria-label="Network facts">
            {[
              [`${QUORUM_K}-of-n`, 'Quorum to alert'],
              ['5', 'Contracts'],
              ['0', 'Central servers'],
            ].map(([v, l]) => (
              <div key={l} className="flex flex-col">
                <dt className="order-2 text-[11px] text-gray-400">{l}</dt>
                <dd className="order-1 text-[14px] font-medium tracking-tight text-white tabular">{v}</dd>
              </div>
            ))}
          </motion.dl>
        </div>
      </div>
    </div>
  );
}

const STEPS: { icon: LucideIcon; title: string; body: string }[] = [
  { icon: AudioLines, title: 'Detect', body: 'Each node listens for a drone acoustic signature, on the Neurick board or a virtual node.' },
  { icon: FileSignature, title: 'Claim', body: 'The node signs a DetectionClaim with its own MST wallet. On its own, a claim does nothing.' },
  { icon: BadgeCheck, title: 'Corroborate', body: `CorroborationEngine fires AlertConfirmed only when ${QUORUM_K} independent nodes agree within the window.` },
  { icon: Gavel, title: 'Dispatch', body: 'Honest nodes earn reputation. Patrol agents win a sealed-bid inspection task, paid in MSTC.' },
];

const ACTS: { icon: LucideIcon; tone: string; title: string; body: string }[] = [
  { icon: Siren, tone: 'text-alert', title: 'Real threat', body: 'Multiple nodes corroborate, the alert confirms on-chain, and the board sounds its alarm.' },
  { icon: Ban, tone: 'text-quarantine', title: 'Hacked node', body: 'A rogue node spams claims alone. With no corroboration it is slashed and quarantined.' },
  { icon: Vibrate, tone: 'text-pending', title: 'Physical tamper', body: 'Shake the board. It reports tamper evidence, and the network ignores it from then on.' },
];

function SectionHeading({ eyebrow, title, body }: { eyebrow: string; title: string; body: string }) {
  return (
    <div className="mb-10 max-w-md">
      <p className="mb-2 text-[11.5px] font-medium text-accent">{eyebrow}</p>
      <h2 className="mb-3 text-[1.35rem] leading-[1.2] font-medium tracking-tight sm:text-[1.5rem]">{title}</h2>
      <p className="text-[13px] leading-[1.55] text-gray-400">{body}</p>
    </div>
  );
}

function HowItWorks() {
  return (
    <section id="how" className="relative scroll-mt-6 overflow-hidden px-6 py-20 sm:px-12 sm:py-28 md:px-20 lg:px-28">
      <div className="pointer-events-none absolute inset-0 bg-grid opacity-60" />
      <div className="relative">
        <SectionHeading
          eyebrow="How it works"
          title="Trust comes from agreement, not from a command post."
          body="The chain is a neutral place where mutually distrusting nodes agree. No server can be jammed, bribed or taken down to blind the system."
        />
        <ol className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {STEPS.map((s, i) => (
            <motion.li
              key={s.title}
              initial={{ opacity: 0, y: 12 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: '-60px' }}
              transition={{ duration: 0.5, delay: i * 0.07, ease: [0.22, 1, 0.36, 1] }}
              className="panel p-5"
            >
              <div className="mb-6 flex items-center justify-between">
                <s.icon className="size-4 text-accent" aria-hidden />
                <span className="font-mono text-[11px] text-gray-600">0{i + 1}</span>
              </div>
              <h3 className="mb-1.5 text-[14px] font-medium tracking-tight">{s.title}</h3>
              <p className="text-[12.5px] leading-[1.55] text-gray-400">{s.body}</p>
            </motion.li>
          ))}
        </ol>

        <div className="mt-16 grid gap-3 md:grid-cols-3">
          {ACTS.map((a, i) => (
            <motion.div
              key={a.title}
              initial={{ opacity: 0, y: 12 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: '-60px' }}
              transition={{ duration: 0.5, delay: i * 0.07, ease: [0.22, 1, 0.36, 1] }}
              className="flex gap-4 rounded-xl border border-white/[0.06] p-5"
            >
              <a.icon className={`mt-0.5 size-4 shrink-0 ${a.tone}`} aria-hidden />
              <div>
                <p className="mb-1 text-[11px] font-medium tracking-wide text-gray-500 uppercase">Act {i + 1}</p>
                <h3 className="mb-1 text-[14px] font-medium tracking-tight">{a.title}</h3>
                <p className="text-[12.5px] leading-[1.55] text-gray-400">{a.body}</p>
              </div>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}

function ContractsSection({ onLaunch }: LandingProps) {
  return (
    <section id="contracts" className="scroll-mt-6 border-t border-white/[0.06] px-6 py-20 sm:px-12 sm:py-28 md:px-20 lg:px-28">
      <div className="flex flex-col gap-10 lg:flex-row lg:gap-20">
        <SectionHeading
          eyebrow="Contracts"
          title="Five contracts. Every step settles on MST."
          body="Registration, claims, quorum alerts, reputation and task bids are all real transactions, not a single log line."
        />
        <ul className="flex-1 divide-y divide-white/[0.06] rounded-xl border border-white/[0.06]">
          {CONTRACTS.map((c) => (
            <li key={c.name} className="flex items-center gap-4 px-4 py-3.5 sm:px-5">
              <div className="min-w-0 flex-1">
                <p className="text-[13px] font-medium text-white">{c.name}</p>
                <p className="truncate text-[12px] text-gray-500">{c.role}</p>
              </div>
              <span className="hidden font-mono text-[11px] text-gray-400 sm:inline" title={c.address}>
                {truncateMiddle(c.address, 8, 6)}
              </span>
              <div className="flex items-center">
                <CopyButton value={c.address} label={`Copy ${c.name} address`} />
                <ExplorerLink href={explorerAddress(c.address)} label={`View ${c.name} on explorer`} />
              </div>
            </li>
          ))}
          <li className="px-4 py-3.5 sm:px-5">
            <button
              type="button"
              onClick={() => onLaunch('/contracts')}
              className="group inline-flex items-center gap-1.5 text-[12px] font-medium text-accent transition-colors duration-200 hover:text-blue-300"
            >
              See them live in the dashboard
              <span aria-hidden className="inline-block transition-transform duration-200 group-hover:translate-x-0.5">
                →
              </span>
            </button>
          </li>
        </ul>
      </div>
    </section>
  );
}

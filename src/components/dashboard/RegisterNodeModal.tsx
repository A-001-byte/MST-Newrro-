import { useEffect, useState, type FormEvent } from 'react';
import { CheckCircle2, KeyRound, ShieldCheck, Shuffle } from 'lucide-react';
import { useBridgeKey } from '../../hooks/useBridgeKey';
import { useSwarm } from '../../hooks/useSwarm';
import { SECTOR_COUNT, sectorName } from '../../lib/config';
import { cx, truncateMiddle } from '../../lib/format';
import { randomAddress } from '../../lib/random';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Overlay';

type Step = 'form' | 'signing' | 'submitting' | 'done';
const ADDRESS_RE = /^0x[0-9a-fA-F]{40}$/;

/** Authority action: registering a node must be signed by the BridgeKey authority wallet */
export function RegisterNodeModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const wallet = useBridgeKey();
  const { state, actions } = useSwarm();
  const [address, setAddress] = useState('');
  const [sector, setSector] = useState(1);
  const [label, setLabel] = useState('');
  const [step, setStep] = useState<Step>('form');
  const [error, setError] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);

  useEffect(() => {
    if (open) {
      setStep('form');
      setError(null);
      setTouched(false);
      setAddress('');
      setLabel('');
    }
  }, [open]);

  const valid = ADDRESS_RE.test(address);
  const duplicate = valid && state.nodes.some((n) => n.address.toLowerCase() === address.toLowerCase());
  const fieldError = touched && !valid ? 'Enter a 0x-prefixed 20-byte address.' : duplicate ? 'This wallet is already registered.' : null;
  const connected = wallet.status === 'connected' && !!wallet.address;
  const busy = step === 'signing' || step === 'submitting';

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (!valid || duplicate || !wallet.address) return;
    setError(null);
    try {
      setStep('signing');
      const signature = await wallet.signAuthorityAction('registerNode', { address, sector });
      setStep('submitting');
      await actions.registerNode({ address, sector, label: label || undefined }, wallet.address, signature);
      setStep('done');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Registration failed');
      setStep('form');
    }
  };

  return (
    <Modal open={open} onClose={busy ? () => undefined : onClose} title="Register node" subtitle="Authority action · SensorRegistry.registerNode">
      {!connected ? (
        <div className="space-y-4 p-5">
          <div className="flex gap-3 rounded-xl border border-white/[0.07] bg-white/[0.02] p-4">
            <KeyRound className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden />
            <div>
              <p className="text-sm font-medium text-white">Connect the authority wallet</p>
              <p className="mt-1 text-xs leading-relaxed text-gray-400">
                New nodes join the swarm only when the BridgeKey authority signs the registration. Anyone can run a node, but only the authority can admit one.
              </p>
            </div>
          </div>
          {wallet.error && <p className="text-xs text-pending">{wallet.error}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" size="md" onClick={onClose}>
              Cancel
            </Button>
            <Button variant="primary" size="md" data-autofocus loading={wallet.status === 'connecting'} onClick={() => void wallet.connect()} arrow>
              Connect BridgeKey
            </Button>
          </div>
        </div>
      ) : step === 'done' ? (
        <div className="space-y-4 p-5 text-center">
          <CheckCircle2 className="mx-auto size-8 text-ok" aria-hidden />
          <div>
            <p className="text-md font-medium text-white">Node registered</p>
            <p className="mt-1 text-sm text-gray-400">
              <span className="font-mono">{truncateMiddle(address, 8, 6)}</span> joined {sectorName(sector)}, signed by {truncateMiddle(wallet.address!, 6, 4)}.
            </p>
          </div>
          <Button variant="outline" size="md" data-autofocus onClick={onClose}>
            Done
          </Button>
        </div>
      ) : (
        <form onSubmit={submit} className="space-y-4 p-5" noValidate>
          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <label htmlFor="reg-address" className="eyebrow">
                Node wallet address
              </label>
              <button
                type="button"
                onClick={() => {
                  setAddress(randomAddress());
                  setTouched(true);
                }}
                className="inline-flex items-center gap-1 rounded text-xs text-accent transition-colors duration-200 hover:text-blue-300"
              >
                <Shuffle className="size-3" aria-hidden />
                Generate test address
              </button>
            </div>
            <input
              id="reg-address"
              data-autofocus
              value={address}
              onChange={(e) => setAddress(e.target.value.trim())}
              onBlur={() => setTouched(true)}
              placeholder="0x…"
              spellCheck={false}
              autoComplete="off"
              aria-invalid={!!fieldError}
              aria-describedby="reg-address-error"
              className={cx(
                'h-10 w-full rounded-lg border bg-white/[0.03] px-3 font-mono text-[12px] text-white transition-colors duration-200 outline-none placeholder:text-gray-600 focus:border-accent/60 focus:bg-white/[0.05]',
                fieldError ? 'border-pending/50' : 'border-white/10 hover:border-white/20',
              )}
            />
            <p id="reg-address-error" className="mt-1 min-h-4 text-xs text-pending">
              {fieldError}
            </p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor="reg-sector" className="eyebrow mb-1.5 block">
                Sector
              </label>
              <select
                id="reg-sector"
                value={sector}
                onChange={(e) => setSector(Number(e.target.value))}
                className="h-10 w-full cursor-pointer rounded-lg border border-white/10 bg-white/[0.03] px-3 text-sm text-white transition-colors duration-200 outline-none hover:border-white/20 focus:border-accent/60"
              >
                {Array.from({ length: SECTOR_COUNT }, (_, i) => i + 1).map((s) => (
                  <option key={s} value={s} className="bg-[#0b0e13]">
                    {sectorName(s)}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="reg-label" className="eyebrow mb-1.5 block">
                Label <span className="tracking-normal normal-case text-gray-600">(optional)</span>
              </label>
              <input
                id="reg-label"
                value={label}
                onChange={(e) => setLabel(e.target.value.slice(0, 16))}
                placeholder="VN-09"
                className="h-10 w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 text-sm text-white transition-colors duration-200 outline-none placeholder:text-gray-600 hover:border-white/20 focus:border-accent/60"
              />
            </div>
          </div>

          <div className="flex items-center gap-2 rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2.5 text-xs text-gray-400">
            <ShieldCheck className="size-3.5 shrink-0 text-ok" aria-hidden />
            Signing as authority <span className="font-mono text-gray-200">{truncateMiddle(wallet.address!, 6, 4)}</span>
          </div>

          {error && (
            <p role="alert" className="text-xs text-pending">
              {error}
            </p>
          )}

          <div className="flex justify-end gap-2 pt-1">
            <Button type="button" variant="ghost" size="md" onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" size="md" loading={busy} disabled={duplicate} arrow={!busy}>
              {step === 'signing' ? 'Awaiting signature…' : step === 'submitting' ? 'Submitting…' : 'Sign & register'}
            </Button>
          </div>
        </form>
      )}
    </Modal>
  );
}

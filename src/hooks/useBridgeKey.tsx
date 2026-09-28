import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';
import { randomHex } from '../lib/random';

export type WalletStatus = 'disconnected' | 'connecting' | 'connected' | 'error';

export interface BridgeKeyWallet {
  status: WalletStatus;
  address: string | null;
  error: string | null;
  connect: () => Promise<void>;
  disconnect: () => void;
  /** Signs an authority action (e.g. registerNode). Resolves with a signature hex. */
  signAuthorityAction: (action: string, payload: Record<string, unknown>) => Promise<string>;
}

const MOCK_AUTHORITY = '0xb71d9e4c2a0f3b6d8e1a5c7f9b2d4e6a8c0de4a1';
const delay = (ms: number) => new Promise((r) => window.setTimeout(r, ms));

const BridgeKeyContext = createContext<BridgeKeyWallet | null>(null);

export function BridgeKeyProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<WalletStatus>('disconnected');
  const [address, setAddress] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const connect = useCallback(async () => {
    setStatus('connecting');
    setError(null);
    try {
      // TODO(BridgeKey): replace this mock with the real extension handshake, e.g.
      //   const provider = (window as any).bridgekey;
      //   if (!provider) throw new Error('BridgeKey extension not found');
      //   const [account] = await provider.request({ method: 'eth_requestAccounts' });
      //   setAddress(account);
      await delay(700);
      setAddress(MOCK_AUTHORITY);
      setStatus('connected');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not connect');
      setStatus('error');
    }
  }, []);

  const disconnect = useCallback(() => {
    setAddress(null);
    setStatus('disconnected');
  }, []);

  const signAuthorityAction = useCallback(
    async (action: string, payload: Record<string, unknown>) => {
      if (!address) throw new Error('Connect BridgeKey first');
      // TODO(BridgeKey): request a real signature from the extension, e.g.
      //   return provider.request({ method: 'personal_sign', params: [JSON.stringify({ action, ...payload }), address] });
      void action;
      void payload;
      await delay(600);
      return randomHex(65);
    },
    [address],
  );

  const value = useMemo(
    () => ({ status, address, error, connect, disconnect, signAuthorityAction }),
    [status, address, error, connect, disconnect, signAuthorityAction],
  );
  return <BridgeKeyContext.Provider value={value}>{children}</BridgeKeyContext.Provider>;
}

export function useBridgeKey(): BridgeKeyWallet {
  const ctx = useContext(BridgeKeyContext);
  if (!ctx) throw new Error('useBridgeKey must be used inside <BridgeKeyProvider>');
  return ctx;
}

import { createContext, useContext, type ReactNode } from 'react';
import { useSwarmStream, type SwarmStream } from './useSwarmStream';

const SwarmContext = createContext<SwarmStream | null>(null);

/** Mounts the single useSwarmStream() instance and shares it with every panel */
export function SwarmProvider({ children }: { children: ReactNode }) {
  const stream = useSwarmStream();
  return <SwarmContext.Provider value={stream}>{children}</SwarmContext.Provider>;
}

export function useSwarm(): SwarmStream {
  const ctx = useContext(SwarmContext);
  if (!ctx) throw new Error('useSwarm must be used inside <SwarmProvider>');
  return ctx;
}

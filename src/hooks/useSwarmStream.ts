import { useEffect, useMemo, useReducer, useRef } from 'react';
import type { SwarmActions, SwarmState } from '../types';
import { USE_MOCK } from '../lib/config';
import { connectGateway, postGateway } from '../lib/gatewayClient';
import { createSimulator, type Simulator } from '../lib/simulator';
import { initialSwarmState, swarmReducer } from '../lib/swarmReducer';

export interface SwarmStream {
  state: SwarmState;
  actions: SwarmActions;
  mode: 'mock' | 'live';
}

/**
 * The only source of dashboard state.
 * Mock (default): local simulator. Live (VITE_USE_MOCK=false): gateway WebSocket + REST.
 * Both paths feed the same reducer with the same StreamMessage frames.
 */
export function useSwarmStream(): SwarmStream {
  const [state, dispatch] = useReducer(swarmReducer, initialSwarmState);
  const sim = useRef<Simulator | null>(null);

  useEffect(() => {
    if (!USE_MOCK) return connectGateway(dispatch);
    const s = createSimulator(dispatch);
    sim.current = s;
    s.start();
    return () => {
      s.stop();
      sim.current = null;
    };
  }, []);

  const actions = useMemo<SwarmActions>(() => {
    if (USE_MOCK) {
      const run = (fn: (s: Simulator) => Promise<void>) => (sim.current ? fn(sim.current) : Promise.resolve());
      return {
        simulateDrone: () => run((s) => s.drone()),
        injectRogue: () => run((s) => s.rogue()),
        simulateTamper: (address) => run((s) => s.tamper(address)),
        reset: () => run((s) => s.reset()),
        registerNode: (input, authority, signature) => run((s) => s.register(input, authority, signature)),
        dropConnection: () => sim.current?.drop(),
      };
    }
    return {
      simulateDrone: () => postGateway('/demo/drone'),
      injectRogue: () => postGateway('/demo/rogue'),
      simulateTamper: (nodeAddress) => postGateway('/demo/tamper', { nodeAddress }),
      reset: () => postGateway('/demo/reset'),
      registerNode: (input, authority, signature) => postGateway('/nodes/register', { ...input, authority, signature }),
    };
  }, []);

  return { state, actions, mode: USE_MOCK ? 'mock' : 'live' };
}

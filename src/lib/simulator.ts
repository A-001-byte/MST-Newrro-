/**
 * Local stand-in for the gateway + MST contracts.
 *
 * It keeps its own "chain" model and emits the exact StreamMessage frames the FastAPI
 * gateway sends, so the dashboard runs the same reducer path in mock and live mode.
 * Each demo act mirrors the three-act script in the spec (section 3 / 9).
 */
import type { Auction, ChainEvent, ChainEventMeta, ChainEventType, ClaimWindow, NodeState, RegisterNodeInput, SwarmSnapshot, Threat } from '../types';
import { NETWORK_NAME, QUARANTINE_THRESHOLD, QUORUM_K, SECTOR_COUNT } from './config';
import { between, pick, randomAddress, randomHex, randomTxHash } from './random';
import type { ReducerAction } from './swarmReducer';

export interface Simulator {
  start: () => void;
  stop: () => void;
  drone: () => Promise<void>;
  rogue: () => Promise<void>;
  tamper: (address: string) => Promise<void>;
  reset: () => Promise<void>;
  register: (input: RegisterNodeInput, authority: string, signature: string) => Promise<void>;
  drop: () => void;
}

const CANCELLED = Symbol('cancelled');
const MAX_LOG = 80;
const CLAIM_WINDOW_MS = 10_000;
const ROGUE_WINDOW_MS = 7_000;
const NOISE_WINDOW_MS = 7_000;
const THREAT_HOLD_MS = 9_000;
const BLOCK_MS = 2_400;

const PATROL_AGENTS = [
  { label: 'PA-Kestrel', address: '0x4b1e57a1c0ffee00000000000000000000a11ce1' },
  { label: 'PA-Osprey', address: '0x05b2e7a1c0ffee00000000000000000000a11ce2' },
  { label: 'PA-Harrier', address: '0x4a221e2c0ffee000000000000000000000a11ce3' },
  { label: 'PA-Merlin', address: '0x3e21170c0ffee000000000000000000000a11ce4' },
];

const SEED_NODES: { label: string; sector: number; reputation: number; isReal?: boolean }[] = [
  { label: 'Neurick-01', sector: 3, reputation: 91, isReal: true },
  { label: 'VN-02', sector: 1, reputation: 84 },
  { label: 'VN-03', sector: 2, reputation: 88 },
  { label: 'VN-04', sector: 2, reputation: 77 },
  { label: 'VN-05', sector: 3, reputation: 82 },
  { label: 'VN-06', sector: 4, reputation: 90 },
  { label: 'VN-07', sector: 5, reputation: 73 },
  { label: 'VN-08', sector: 6, reputation: 86 },
];

const sectorDistance = (a: number, b: number) => {
  const d = Math.abs(a - b) % SECTOR_COUNT;
  return Math.min(d, SECTOR_COUNT - d);
};
const clampRep = (v: number) => Math.max(0, Math.min(100, Math.round(v)));

export function createSimulator(emit: (action: ReducerAction) => void): Simulator {
  let nodes = new Map<string, NodeState>();
  let history: Record<string, number[]> = {};
  let log: ChainEvent[] = [];
  let windows = new Map<string, ClaimWindow>();
  let threat: Threat | null = null;
  let auction: Auction | null = null;
  let height = 0;
  let offline = false;
  let generation = 0;
  let activeActs = 0;
  let rogueCount = 0;
  let registeredCount = 0;
  let blockTimer: number | undefined;
  const waiters = new Set<() => void>();

  // ---------- plumbing ----------

  const send = (action: ReducerAction) => {
    if (!offline) emit(action);
  };

  /** Cancellable sleep: rejects with CANCELLED if reset() runs while waiting */
  const sleep = (ms: number) => {
    const gen = generation;
    return new Promise<void>((resolve, reject) => {
      const done = () => {
        waiters.delete(done);
        window.clearTimeout(id);
        if (gen === generation) resolve();
        else reject(CANCELLED);
      };
      const id = window.setTimeout(done, Math.max(0, ms));
      waiters.add(done);
    });
  };

  const act = async (fn: () => Promise<void>) => {
    activeActs++;
    try {
      await fn();
    } catch (err) {
      if (err !== CANCELLED) throw err;
    } finally {
      activeActs = Math.max(0, activeActs - 1);
    }
  };

  const snapshot = (): SwarmSnapshot => ({
    network: NETWORK_NAME,
    blockHeight: height,
    nodes: [...nodes.values()],
    events: log,
    windows: [...windows.values()],
    threat,
    auction,
    history,
  });

  const setNode = (address: string, patch: Partial<NodeState>) => {
    const current = nodes.get(address);
    if (!current) return;
    const next = { ...current, ...patch };
    nodes.set(address, next);
    if (patch.reputation !== undefined && patch.reputation !== current.reputation)
      history = { ...history, [address]: [...(history[address] ?? []), patch.reputation].slice(-40) };
    send({ type: 'node', node: next });
  };

  const addNode = (node: NodeState) => {
    nodes.set(node.address, node);
    history = { ...history, [node.address]: [node.reputation] };
    send({ type: 'node', node });
  };

  const chainEvent = (type: ChainEventType, nodeAddress: string, sector: number | null, meta: ChainEventMeta = {}, ts = Date.now()): ChainEvent => {
    const txHash = randomTxHash();
    const event: ChainEvent = { id: `${txHash}:0`, type, txHash, nodeAddress, sector, timestamp: ts, blockNumber: height, meta };
    log = [event, ...log].slice(0, MAX_LOG);
    send({ type: 'event', event });
    return event;
  };

  const setWindow = (w: ClaimWindow) => {
    windows.set(w.id, w);
    if (windows.size > 6) windows.delete(windows.keys().next().value!);
    send({ type: 'window', window: w });
  };

  const eligible = (sector: number) =>
    [...nodes.values()].filter((n) => !n.quarantined && sectorDistance(n.sector, sector) <= 1);

  const openWindow = (sector: number, durationMs: number): ClaimWindow => {
    const now = Date.now();
    const w: ClaimWindow = {
      id: randomHex(6),
      sector,
      openedAt: now,
      expiresAt: now + durationMs,
      k: QUORUM_K,
      n: eligible(sector).length,
      claims: [],
      state: 'open',
    };
    setWindow(w);
    return w;
  };

  /** Returns true if the claim was accepted by the CorroborationEngine */
  const submitClaim = (address: string, windowId: string, confidence: number): boolean => {
    const node = nodes.get(address);
    const w = windows.get(windowId);
    if (!node || !w) return false;
    if (node.quarantined) {
      chainEvent('ClaimRejected', address, w.sector, { reason: 'Node quarantined, claim reverted', windowId });
      return false;
    }
    if (w.claims.some((c) => c.nodeAddress === address)) {
      chainEvent('ClaimRejected', address, w.sector, { reason: 'Duplicate claimant in window, not counted', windowId });
      return false;
    }
    const conf = Math.round(confidence * 100) / 100;
    const ev = chainEvent('ClaimSubmitted', address, w.sector, { confidence: conf, windowId });
    setWindow({ ...w, claims: [...w.claims, { nodeAddress: address, confidence: conf, timestamp: ev.timestamp, txHash: ev.txHash }] });
    if (node.status === 'normal') setNode(address, { status: 'pending' });
    return true;
  };

  /** Window closed without quorum: slash each lone claimant */
  const expireWindow = (windowId: string, delta: number) => {
    const w = windows.get(windowId);
    if (!w || w.state !== 'open') return;
    setWindow({ ...w, state: 'expired' });
    for (const c of w.claims) {
      const node = nodes.get(c.nodeAddress);
      if (!node) continue;
      const reputation = clampRep(node.reputation + delta);
      const quarantine = reputation < QUARANTINE_THRESHOLD;
      chainEvent('ReputationSlashed', node.address, w.sector, {
        delta,
        reputation,
        reason: quarantine ? 'Uncorroborated claim; below threshold, auto-quarantined' : 'Uncorroborated claim, window expired',
      });
      setNode(node.address, {
        reputation,
        ...(quarantine
          ? { quarantined: true, status: 'quarantined', quarantineReason: 'reputation' }
          : node.status === 'pending'
            ? { status: 'normal' }
            : {}),
      });
    }
  };

  // ---------- seed ----------

  const seed = () => {
    const now = Date.now();
    nodes = new Map();
    history = {};
    windows = new Map();
    threat = null;
    auction = null;
    rogueCount = 0;
    registeredCount = SEED_NODES.length;
    height = 1_284_000 + Math.floor(Math.random() * 4000);

    const asc: ChainEvent[] = [];
    const past = (type: ChainEventType, nodeAddress: string, sector: number | null, minutesAgo: number, meta: ChainEventMeta = {}) => {
      const txHash = randomTxHash();
      const blocksAgo = Math.round((minutesAgo * 60_000) / BLOCK_MS);
      asc.push({ id: `${txHash}:0`, type, txHash, nodeAddress, sector, timestamp: now - minutesAgo * 60_000, blockNumber: height - blocksAgo, meta });
    };

    SEED_NODES.forEach((s, i) => {
      const address = randomAddress();
      const node: NodeState = {
        address,
        sector: s.sector,
        reputation: s.reputation,
        status: 'normal',
        isReal: !!s.isReal,
        quarantined: false,
        label: s.label,
        registeredAt: now - (46 - i * 2) * 60_000,
        quarantineReason: null,
      };
      nodes.set(address, node);
      // plausible recent drift ending at the current score
      const h: number[] = [];
      let v = s.reputation - 6 + Math.random() * 4;
      for (let j = 0; j < 11; j++) {
        h.push(clampRep(v));
        v += between(-1.5, 2);
      }
      h.push(s.reputation);
      history[address] = h;
      past('NodeRegistered', address, s.sector, 46 - i * 2, { authority: '0xb71d9e0000000000000000000000000000a0c0de' });
    });

    // A prior confirmed alert in sector 5, for a real-looking history
    const byLabel = (l: string) => [...nodes.values()].find((n) => n.label === l)!;
    const prior = [byLabel('VN-07'), byLabel('VN-06'), byLabel('VN-08')];
    prior.forEach((n, i) => past('ClaimSubmitted', n.address, 5, 12.2 - i * 0.02, { confidence: 0.84 + i * 0.04 }));
    past('AlertConfirmed', prior[2].address, 5, 12.14, { claimants: prior.map((n) => n.address), k: 3, n: 4, timeToConfirmMs: 2140 });
    prior.forEach((n) => past('ReputationCredited', n.address, 5, 12.1, { delta: 3, reputation: n.reputation, reason: 'Corroborated alert' }));
    // and one noisy lone claim that got slashed
    const noisy = byLabel('VN-04');
    past('ClaimSubmitted', noisy.address, 2, 6.4, { confidence: 0.41 });
    past('ReputationSlashed', noisy.address, 2, 6.28, { delta: -2, reputation: noisy.reputation, reason: 'Uncorroborated claim, window expired' });

    log = asc.reverse();
  };

  // ---------- ambient traffic ----------

  const ambientLoop = async () => {
    const gen = generation;
    try {
      while (gen === generation) {
        await sleep(between(18_000, 28_000));
        const busy = offline || activeActs > 0 || threat || [...windows.values()].some((w) => w.state === 'open');
        if (busy) continue;
        const candidates = [...nodes.values()].filter((n) => !n.quarantined && !n.isReal);
        if (!candidates.length) continue;
        if (Math.random() < 0.45) {
          const node = pick(candidates);
          const w = openWindow(node.sector, NOISE_WINDOW_MS);
          submitClaim(node.address, w.id, between(0.3, 0.48));
          await sleep(NOISE_WINDOW_MS);
          expireWindow(w.id, -2);
        } else {
          const healthy = candidates.filter((n) => n.reputation < 97);
          const node = pick(healthy.length ? healthy : candidates);
          const reputation = clampRep(node.reputation + 1);
          chainEvent('ReputationCredited', node.address, node.sector, { delta: 1, reputation, reason: 'Epoch uptime attestation' });
          setNode(node.address, { reputation });
        }
      }
    } catch (err) {
      if (err !== CANCELLED) throw err;
    }
  };

  const startClock = () => {
    window.clearInterval(blockTimer);
    blockTimer = window.setInterval(() => {
      height += 1;
      send({ type: 'block', height });
    }, BLOCK_MS);
  };

  const boot = async (initial: boolean) => {
    seed();
    if (initial) {
      emit({ type: 'connection', connection: 'connecting' });
      await sleep(650);
    }
    emit({ type: 'snapshot', state: snapshot() });
    emit({ type: 'connection', connection: 'live' });
    startClock();
    void ambientLoop();
  };

  const cancelAll = () => {
    generation++;
    [...waiters].forEach((w) => w());
    window.clearInterval(blockTimer);
    activeActs = 0;
  };

  // ---------- demo acts ----------

  /** Act 1: real threat. Several independent nodes corroborate, alert confirms, task auction runs. */
  const drone = () =>
    act(async () => {
      if (threat) return;
      const real = [...nodes.values()].find((n) => n.isReal);
      const sector = real?.sector ?? 3;
      // nearest first, the physical board as the second voice so the hardware is visibly in the loop
      const pool = eligible(sector).sort((a, b) => sectorDistance(a.sector, sector) - sectorDistance(b.sector, sector) || b.reputation - a.reputation);
      const realIdx = pool.findIndex((n) => n.isReal);
      if (realIdx > 1) pool.splice(1, 0, pool.splice(realIdx, 1)[0]);

      const w = openWindow(sector, CLAIM_WINDOW_MS);
      await sleep(600);
      const voices = pool.slice(0, QUORUM_K);
      for (let i = 0; i < voices.length; i++) {
        submitClaim(voices[i].address, w.id, between(0.78, 0.97));
        if (i < voices.length - 1) await sleep(between(800, 1100));
      }

      const current = windows.get(w.id)!;
      if (current.claims.length < QUORUM_K) {
        await sleep(current.expiresAt - Date.now());
        expireWindow(w.id, -5);
        return;
      }

      await sleep(380);
      const confirmedAt = Date.now();
      const claimants = current.claims.map((c) => c.nodeAddress);
      setWindow({ ...current, state: 'confirmed', confirmedAt });
      const alert = chainEvent('AlertConfirmed', claimants[claimants.length - 1], sector, {
        claimants,
        k: current.k,
        n: current.n,
        timeToConfirmMs: confirmedAt - current.claims[0].timestamp,
        windowId: w.id,
      });
      threat = { sector, claimants, k: current.k, n: current.n, confirmedAt, txHash: alert.txHash };
      send({ type: 'threat', threat });
      claimants.forEach((a) => setNode(a, { status: 'alert' }));

      await sleep(800);
      for (const a of claimants) {
        const node = nodes.get(a)!;
        const reputation = clampRep(node.reputation + 3);
        chainEvent('ReputationCredited', a, sector, { delta: 3, reputation, reason: 'Corroborated alert' });
        setNode(a, { reputation });
        await sleep(220);
      }

      // TaskMarket: sealed-bid patrol auction
      await sleep(500);
      const openedAt = Date.now();
      auction = { id: randomHex(4), sector, openedAt, closesAt: openedAt + 5_500, state: 'bidding', bids: [] };
      send({ type: 'auction', auction });
      for (const agent of PATROL_AGENTS) {
        await sleep(between(500, 900));
        const sealedHash = randomTxHash();
        chainEvent('TaskBid', agent.address, sector, { auctionId: auction.id, agentLabel: agent.label, sealedHash });
        auction = {
          ...auction,
          bids: [...auction.bids, { agent: agent.address, agentLabel: agent.label, sealedHash, amount: null, etaSec: null, timestamp: Date.now() }],
        };
        send({ type: 'auction', auction });
      }
      await sleep(auction.closesAt - Date.now());
      const revealed = auction.bids.map((b) => ({ ...b, amount: Math.round(between(1.2, 4.8) * 100) / 100, etaSec: Math.round(between(40, 180)) }));
      const winner = revealed.reduce((best, b) => (b.amount! < best.amount! ? b : best));
      const award = chainEvent('TaskAwarded', winner.agent, sector, { auctionId: auction.id, agentLabel: winner.agentLabel, amount: winner.amount! });
      auction = { ...auction, bids: revealed, state: 'awarded', winner: winner.agent, txHash: award.txHash };
      send({ type: 'auction', auction });

      await sleep(THREAT_HOLD_MS);
      threat = null;
      send({ type: 'threat', threat: null });
      claimants.forEach((a) => {
        const n = nodes.get(a);
        if (n && !n.quarantined) setNode(a, { status: 'normal' });
      });
    });

  /** Act 2: hacked node. Spams claims alone, gets no corroboration, is slashed and quarantined. */
  const rogue = () =>
    act(async () => {
      rogueCount++;
      const busySectors = new Set([threat?.sector, ...[...windows.values()].filter((w) => w.state === 'open').map((w) => w.sector)]);
      const options = Array.from({ length: SECTOR_COUNT }, (_, i) => i + 1).filter((s) => !busySectors.has(s));
      const sector = pick(options.length ? options : [6]);
      const address = randomAddress();
      const node: NodeState = {
        address,
        sector,
        reputation: 50,
        status: 'normal',
        isReal: false,
        quarantined: false,
        label: `Injected-${String(rogueCount).padStart(2, '0')}`,
        registeredAt: Date.now(),
        quarantineReason: null,
      };
      addNode(node);
      chainEvent('NodeRegistered', address, sector, { reason: 'Open registration (demo mode)' });

      await sleep(1000);
      const w = openWindow(sector, ROGUE_WINDOW_MS);
      submitClaim(address, w.id, 0.99);
      await sleep(800);
      submitClaim(address, w.id, 0.99);
      await sleep(800);
      submitClaim(address, w.id, 0.98);
      await sleep(windows.get(w.id)!.expiresAt - Date.now());
      expireWindow(w.id, -30);
      await sleep(1500);
      submitClaim(address, w.id, 0.99);
    });

  /** Act 3: physical tampering. Node reports tamper evidence and self-quarantines; later claims are ignored. */
  const tamper = (address: string) =>
    act(async () => {
      const node = nodes.get(address);
      if (!node || node.quarantined) return;
      chainEvent('TamperReported', address, node.sector, {
        evidenceHash: randomTxHash(),
        reason: node.isReal ? 'Motion sensor: sustained shake detected' : 'Enclosure integrity signal lost',
      });
      setNode(address, { quarantined: true, status: 'quarantined', quarantineReason: 'tamper' });
      await sleep(2200);
      chainEvent('ClaimRejected', address, node.sector, { reason: 'Node quarantined, claims ignored by CorroborationEngine' });
    });

  const register = async (input: RegisterNodeInput, authority: string, signature: string) => {
    const address = input.address.toLowerCase();
    if (nodes.has(address)) throw new Error('This wallet is already registered.');
    await new Promise((r) => window.setTimeout(r, 900)); // tx confirmation
    registeredCount++;
    addNode({
      address,
      sector: input.sector,
      reputation: 60,
      status: 'normal',
      isReal: false,
      quarantined: false,
      label: input.label?.trim() || `VN-${String(registeredCount).padStart(2, '0')}`,
      registeredAt: Date.now(),
      quarantineReason: null,
    });
    chainEvent('NodeRegistered', address, input.sector, { authority, signature });
  };

  return {
    start: () => void boot(true).catch((err) => {
      if (err !== CANCELLED) throw err;
    }),
    stop: cancelAll,
    drone,
    rogue,
    tamper,
    register,
    reset: async () => {
      cancelAll();
      offline = false;
      await boot(false);
    },
    drop: () => {
      if (offline) return;
      offline = true;
      emit({ type: 'connection', connection: 'reconnecting' });
      window.setTimeout(() => {
        offline = false;
        emit({ type: 'snapshot', state: snapshot() });
        emit({ type: 'connection', connection: 'live' });
      }, 3500);
    },
  };
}

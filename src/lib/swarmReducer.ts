import type { ChainEvent, ConnectionState, NodeState, StreamMessage, SwarmSnapshot, SwarmState, SwarmStats } from '../types';
import { NETWORK_NAME } from './config';

const MAX_EVENTS = 80;
const MAX_WINDOWS = 6;
const MAX_HISTORY = 40;

export type ReducerAction = StreamMessage | { type: 'connection'; connection: ConnectionState };

export const initialSwarmState: SwarmState = {
  network: NETWORK_NAME,
  blockHeight: 0,
  nodes: [],
  events: [],
  windows: [],
  threat: null,
  auction: null,
  connection: 'connecting',
  stats: { totalClaims: 0, alertsConfirmed: 0, nodesSlashed: 0, avgConfirmMs: null },
  history: {},
  slashed: [],
  confirmTotals: { sum: 0, count: 0 },
  ready: false,
};

function statsFrom(slashed: string[], totals: { sum: number; count: number }, claims: number, alerts: number): SwarmStats {
  return {
    totalClaims: claims,
    alertsConfirmed: alerts,
    nodesSlashed: slashed.length,
    avgConfirmMs: totals.count ? totals.sum / totals.count : null,
  };
}

function fromSnapshot(prev: SwarmState, snap: SwarmSnapshot): SwarmState {
  // Derive cumulative stats from the event log unless the gateway supplied them.
  const slashed = new Set<string>();
  const totals = { sum: 0, count: 0 };
  let claims = 0;
  let alerts = 0;
  for (const e of snap.events) {
    if (e.type === 'ClaimSubmitted') claims++;
    if (e.type === 'AlertConfirmed') {
      alerts++;
      if (typeof e.meta.timeToConfirmMs === 'number') {
        totals.sum += e.meta.timeToConfirmMs;
        totals.count++;
      }
    }
    if (e.type === 'ReputationSlashed') slashed.add(e.nodeAddress);
  }
  const slashedList = [...slashed];
  const history: Record<string, number[]> = {};
  for (const n of snap.nodes) {
    const seeded = snap.history?.[n.address];
    history[n.address] = seeded?.length ? seeded.slice(-MAX_HISTORY) : [n.reputation];
  }

  return {
    ...prev,
    ...snap,
    events: snap.events.slice(0, MAX_EVENTS),
    windows: snap.windows.slice(-MAX_WINDOWS),
    stats: snap.stats ?? statsFrom(slashedList, totals, claims, alerts),
    history,
    slashed: slashedList,
    confirmTotals: totals,
    ready: true,
  };
}

function applyEvent(state: SwarmState, event: ChainEvent): SwarmState {
  if (state.events.some((e) => e.id === event.id)) return state;
  let { slashed, confirmTotals } = state;
  let { totalClaims, alertsConfirmed } = state.stats;

  if (event.type === 'ClaimSubmitted') totalClaims++;
  if (event.type === 'AlertConfirmed') {
    alertsConfirmed++;
    if (typeof event.meta.timeToConfirmMs === 'number')
      confirmTotals = { sum: confirmTotals.sum + event.meta.timeToConfirmMs, count: confirmTotals.count + 1 };
  }
  if (event.type === 'ReputationSlashed' && !slashed.includes(event.nodeAddress)) slashed = [...slashed, event.nodeAddress];

  return {
    ...state,
    events: [event, ...state.events].slice(0, MAX_EVENTS),
    slashed,
    confirmTotals,
    stats: statsFrom(slashed, confirmTotals, totalClaims, alertsConfirmed),
  };
}

function upsertNode(state: SwarmState, node: NodeState): SwarmState {
  const idx = state.nodes.findIndex((n) => n.address === node.address);
  const nodes = idx === -1 ? [...state.nodes, node] : state.nodes.map((n, i) => (i === idx ? node : n));
  const prevHistory = state.history[node.address] ?? [];
  const last = prevHistory[prevHistory.length - 1];
  const history =
    last === node.reputation
      ? state.history
      : { ...state.history, [node.address]: [...prevHistory, node.reputation].slice(-MAX_HISTORY) };
  return { ...state, nodes, history };
}

export function swarmReducer(state: SwarmState, action: ReducerAction): SwarmState {
  switch (action.type) {
    case 'snapshot':
      return fromSnapshot(state, action.state);
    case 'event':
      return applyEvent(state, action.event);
    case 'node':
      return upsertNode(state, action.node);
    case 'window': {
      const others = state.windows.filter((w) => w.id !== action.window.id);
      return { ...state, windows: [...others, action.window].slice(-MAX_WINDOWS) };
    }
    case 'threat':
      return { ...state, threat: action.threat };
    case 'auction':
      return { ...state, auction: action.auction };
    case 'block':
      return action.height > state.blockHeight ? { ...state, blockHeight: action.height } : state;
    case 'connection':
      return { ...state, connection: action.connection };
    default:
      return state;
  }
}

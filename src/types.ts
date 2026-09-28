/**
 * SwarmShield event schema — the single contract between the dashboard and the gateway.
 *
 * The FastAPI gateway pushes `StreamMessage` JSON frames over the WebSocket (VITE_WS_URL).
 * The local simulator (src/lib/simulator.ts) emits exactly the same frames, so the UI
 * cannot tell the difference. All timestamps are Unix epoch milliseconds.
 * Addresses and hashes are 0x-prefixed lowercase hex.
 */

/** green = normal, yellow = claim pending corroboration, red = alert confirmed, gray = quarantined */
export type NodeStatus = 'normal' | 'pending' | 'alert' | 'quarantined';

export interface NodeState {
  /** Node wallet address (SensorRegistry key) */
  address: string;
  /** Sector index, 1..SECTOR_COUNT */
  sector: number;
  /** Reputation score, 0..100 (ReputationManager) */
  reputation: number;
  status: NodeStatus;
  /** true for the physical Neurick board, false for virtual nodes */
  isReal: boolean;
  quarantined: boolean;
  /** Optional display name, e.g. "Neurick-01" or "VN-04" */
  label?: string;
  /** Epoch ms of registration */
  registeredAt?: number;
  /** Why the node is quarantined, if it is */
  quarantineReason?: 'tamper' | 'reputation' | null;
}

export type ChainEventType =
  | 'NodeRegistered' // SensorRegistry.registerNode
  | 'ClaimSubmitted' // CorroborationEngine.submitClaim
  | 'ClaimRejected' // submitClaim reverted (quarantined, duplicate, unregistered)
  | 'AlertConfirmed' // CorroborationEngine k-of-n reached
  | 'ReputationCredited' // ReputationManager credit
  | 'ReputationSlashed' // ReputationManager slash
  | 'TamperReported' // EvidenceLog.reportTamper
  | 'TaskBid' // TaskMarket sealed bid
  | 'TaskAwarded'; // TaskMarket auction resolved

export interface ChainEventMeta {
  /** ClaimSubmitted: detector confidence 0..1 */
  confidence?: number;
  /** ClaimSubmitted / AlertConfirmed: claim-window id */
  windowId?: string;
  /** AlertConfirmed: addresses of corroborating nodes */
  claimants?: string[];
  /** AlertConfirmed: quorum required / eligible nodes */
  k?: number;
  n?: number;
  /** AlertConfirmed: ms between first claim and quorum */
  timeToConfirmMs?: number;
  /** Reputation*: signed delta and resulting score */
  delta?: number;
  reputation?: number;
  /** ClaimRejected / Reputation*: human-readable reason */
  reason?: string;
  /** TamperReported: keccak256 of the evidence blob */
  evidenceHash?: string;
  /** TaskBid / TaskAwarded */
  auctionId?: string;
  agentLabel?: string;
  /** TaskAwarded: winning bid in MSTC */
  amount?: number;
  /** NodeRegistered: authority (BridgeKey) address that signed the registration */
  authority?: string;
  /** Any extra fields the gateway wants to pass through */
  [key: string]: unknown;
}

export interface ChainEvent {
  /** Unique id (txHash + log index is fine) */
  id: string;
  type: ChainEventType;
  txHash: string;
  /** Node (or patrol agent) the event is about */
  nodeAddress: string;
  sector: number | null;
  timestamp: number;
  blockNumber?: number;
  meta: ChainEventMeta;
}

/** One CorroborationEngine claim window for a sector */
export interface ClaimWindow {
  id: string;
  sector: number;
  openedAt: number;
  expiresAt: number;
  /** Quorum required */
  k: number;
  /** Eligible (registered, non-quarantined, in range) nodes */
  n: number;
  /** Distinct accepted claims so far */
  claims: { nodeAddress: string; confidence: number; timestamp: number; txHash: string }[];
  state: 'open' | 'confirmed' | 'expired';
  confirmedAt?: number;
}

/** Present while an AlertConfirmed is active */
export interface Threat {
  sector: number;
  claimants: string[];
  k: number;
  n: number;
  confirmedAt: number;
  txHash: string;
}

export interface TaskBid {
  agent: string;
  agentLabel: string;
  /** Commitment hash, always present */
  sealedHash: string;
  /** Revealed after the bidding window closes; null while sealed */
  amount: number | null;
  etaSec: number | null;
  timestamp: number;
}

export interface Auction {
  id: string;
  sector: number;
  openedAt: number;
  closesAt: number;
  state: 'bidding' | 'awarded';
  bids: TaskBid[];
  winner?: string;
  txHash?: string;
}

export type ConnectionState = 'connecting' | 'live' | 'reconnecting' | 'offline';

export interface SwarmStats {
  totalClaims: number;
  alertsConfirmed: number;
  nodesSlashed: number;
  /** Mean ms from first claim to AlertConfirmed, null if no alerts yet */
  avgConfirmMs: number | null;
}

export interface SwarmSnapshot {
  network: string;
  blockHeight: number;
  nodes: NodeState[];
  /** Newest first */
  events: ChainEvent[];
  windows: ClaimWindow[];
  threat: Threat | null;
  auction: Auction | null;
  /** Optional: gateway may send cumulative stats; otherwise derived from events */
  stats?: SwarmStats;
  /** Optional: recent reputation values per address, oldest first (seeds sparklines) */
  history?: Record<string, number[]>;
}

/** Frames sent by the gateway over the WebSocket */
export type StreamMessage =
  | { type: 'snapshot'; state: SwarmSnapshot }
  | { type: 'event'; event: ChainEvent }
  | { type: 'node'; node: NodeState }
  | { type: 'window'; window: ClaimWindow }
  | { type: 'threat'; threat: Threat | null }
  | { type: 'auction'; auction: Auction | null }
  | { type: 'block'; height: number };

/** Full client-side state exposed by useSwarmStream() */
export interface SwarmState extends SwarmSnapshot {
  connection: ConnectionState;
  stats: SwarmStats;
  /** Reputation history per address (client-side, for sparklines) */
  history: Record<string, number[]>;
  /** Set of addresses that have ever been slashed (drives stats.nodesSlashed) */
  slashed: string[];
  /** Sum/count of confirm times (drives stats.avgConfirmMs) */
  confirmTotals: { sum: number; count: number };
  ready: boolean;
}

export interface RegisterNodeInput {
  address: string;
  sector: number;
  label?: string;
}

export interface SwarmActions {
  simulateDrone: () => Promise<void>;
  injectRogue: () => Promise<void>;
  simulateTamper: (nodeAddress: string) => Promise<void>;
  reset: () => Promise<void>;
  /** Authority action; `authority` is the connected BridgeKey address, `signature` its signature */
  registerNode: (input: RegisterNodeInput, authority: string, signature: string) => Promise<void>;
  /** Mock only: simulate a socket drop so the reconnect UI can be demoed */
  dropConnection?: () => void;
}

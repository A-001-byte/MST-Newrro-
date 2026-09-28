import { AudioLines, Gavel, Siren, Trophy, TrendingDown, TrendingUp, UserPlus, Vibrate, XCircle, type LucideIcon } from 'lucide-react';
import type { ChainEvent, ChainEventType, NodeState } from '../types';
import { sectorName } from './config';
import { nodeName, truncateMiddle } from './format';

export type EventTone = 'info' | 'pending' | 'alert' | 'ok' | 'muted';

export const EVENT_META: Record<ChainEventType, { label: string; icon: LucideIcon; tone: EventTone; contract: string }> = {
  NodeRegistered: { label: 'Node Registered', icon: UserPlus, tone: 'info', contract: 'SensorRegistry' },
  ClaimSubmitted: { label: 'Claim Submitted', icon: AudioLines, tone: 'pending', contract: 'CorroborationEngine' },
  ClaimRejected: { label: 'Claim Rejected', icon: XCircle, tone: 'muted', contract: 'CorroborationEngine' },
  AlertConfirmed: { label: 'Alert Confirmed', icon: Siren, tone: 'alert', contract: 'CorroborationEngine' },
  ReputationCredited: { label: 'Reputation Credited', icon: TrendingUp, tone: 'ok', contract: 'ReputationManager' },
  ReputationSlashed: { label: 'Reputation Slashed', icon: TrendingDown, tone: 'muted', contract: 'ReputationManager' },
  TamperReported: { label: 'Tamper Reported', icon: Vibrate, tone: 'muted', contract: 'EvidenceLog' },
  TaskBid: { label: 'Task Bid', icon: Gavel, tone: 'info', contract: 'TaskMarket' },
  TaskAwarded: { label: 'Task Awarded', icon: Trophy, tone: 'info', contract: 'TaskMarket' },
};

export const TONE_CLASSES: Record<EventTone, { text: string; bg: string; border: string }> = {
  info: { text: 'text-accent', bg: 'bg-accent-soft', border: 'border-accent/25' },
  pending: { text: 'text-pending', bg: 'bg-pending-soft', border: 'border-pending/25' },
  alert: { text: 'text-alert', bg: 'bg-alert-soft', border: 'border-alert/30' },
  ok: { text: 'text-ok', bg: 'bg-ok-soft', border: 'border-ok/25' },
  muted: { text: 'text-quarantine', bg: 'bg-quarantine-soft', border: 'border-quarantine/25' },
};

const signed = (n: number) => (n > 0 ? `+${n}` : `−${Math.abs(n)}`);

/** One-line human description of a chain event */
export function describeEvent(e: ChainEvent, nodes: Map<string, NodeState>): string {
  const node = nodes.get(e.nodeAddress);
  const who = node ? nodeName(node) : (e.meta.agentLabel ?? truncateMiddle(e.nodeAddress));
  const where = e.sector != null ? sectorName(e.sector) : '';
  const m = e.meta;
  switch (e.type) {
    case 'NodeRegistered':
      return `${who} joined ${where}${m.authority ? ', signed by authority' : ''}`;
    case 'ClaimSubmitted':
      return `${who} claimed ${where}${typeof m.confidence === 'number' ? ` · conf ${m.confidence.toFixed(2)}` : ''}`;
    case 'ClaimRejected':
      return `${who}: ${m.reason ?? 'claim reverted'}`;
    case 'AlertConfirmed':
      return `${where} confirmed · ${m.claimants?.length ?? m.k} of ${m.n} nodes agreed`;
    case 'ReputationCredited':
      return `${who} ${signed(m.delta ?? 0)} → ${m.reputation} · ${m.reason ?? 'credited'}`;
    case 'ReputationSlashed':
      return `${who} ${signed(m.delta ?? 0)} → ${m.reputation} · ${m.reason ?? 'slashed'}`;
    case 'TamperReported':
      return `${who} self-quarantined · ${m.reason ?? 'tamper evidence logged'}`;
    case 'TaskBid':
      return `${who} placed a sealed patrol bid for ${where}`;
    case 'TaskAwarded':
      return `${who} won ${where} inspection · ${m.amount} MSTC`;
    default:
      return who;
  }
}

import { Ban, CircleCheck, Clock3, Siren, type LucideIcon } from 'lucide-react';
import type { NodeStatus } from '../types';

export interface StatusMeta {
  label: string;
  /** CSS colour value (token) */
  color: string;
  text: string;
  bg: string;
  border: string;
  icon: LucideIcon;
}

/** Status colours carry meaning only, and always ship with an icon + label */
export const STATUS: Record<NodeStatus, StatusMeta> = {
  normal: { label: 'Normal', color: 'var(--color-ok)', text: 'text-ok', bg: 'bg-ok-soft', border: 'border-ok/30', icon: CircleCheck },
  pending: { label: 'Pending', color: 'var(--color-pending)', text: 'text-pending', bg: 'bg-pending-soft', border: 'border-pending/30', icon: Clock3 },
  alert: { label: 'Alert', color: 'var(--color-alert)', text: 'text-alert', bg: 'bg-alert-soft', border: 'border-alert/35', icon: Siren },
  quarantined: { label: 'Quarantined', color: 'var(--color-quarantine)', text: 'text-quarantine', bg: 'bg-quarantine-soft', border: 'border-quarantine/30', icon: Ban },
};

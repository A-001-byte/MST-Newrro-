const env = import.meta.env;

/** Mock simulator unless explicitly disabled */
export const USE_MOCK = env.VITE_USE_MOCK !== 'false';
export const WS_URL: string = env.VITE_WS_URL || 'ws://localhost:8000/ws';
export const API_URL: string = (env.VITE_API_URL || 'http://localhost:8000').replace(/\/$/, '');

/** MST block explorer base. TODO: set VITE_EXPLORER_URL to the official MST Testnet explorer. */
export const EXPLORER_BASE: string = (env.VITE_EXPLORER_URL || 'https://explorer.testnet.mst.example').replace(/\/$/, '');
export const explorerTx = (hash: string) => `${EXPLORER_BASE}/tx/${hash}`;
export const explorerAddress = (addr: string) => `${EXPLORER_BASE}/address/${addr}`;

export const NETWORK_NAME = 'MST Testnet';
export const SECTOR_COUNT = 6;
export const QUORUM_K = 3;
export const QUARANTINE_THRESHOLD = 40;

export const sectorName = (s: number) => `S${s}`;

export interface ContractInfo {
  name: string;
  role: string;
  address: string;
  placeholder: boolean;
}

const addr = (value: string | undefined, fallback: string): [string, boolean] =>
  value && /^0x[0-9a-fA-F]{40}$/.test(value) ? [value, false] : [fallback, true];

const contract = (name: string, role: string, value: string | undefined, fallback: string): ContractInfo => {
  const [address, placeholder] = addr(value, fallback);
  return { name, role, address, placeholder };
};

/** The five SwarmShield contracts. Placeholders until VITE_ADDR_* are set after deploy. */
export const CONTRACTS: ContractInfo[] = [
  contract('SensorRegistry', 'Node identity, wallet, sector, reputation', env.VITE_ADDR_SENSOR_REGISTRY, '0x5e75000000000000000000000000000000000001'),
  contract('CorroborationEngine', 'k-of-n claim windows, emits AlertConfirmed', env.VITE_ADDR_CORROBORATION_ENGINE, '0xc022000000000000000000000000000000000002'),
  contract('ReputationManager', 'Credits corroborators, slashes lone claimants', env.VITE_ADDR_REPUTATION_MANAGER, '0x2e90000000000000000000000000000000000003'),
  contract('TaskMarket', 'Sealed-bid patrol auctions paid in MSTC', env.VITE_ADDR_TASK_MARKET, '0x7a5c000000000000000000000000000000000004'),
  contract('EvidenceLog', 'Tamper evidence and self-quarantine', env.VITE_ADDR_EVIDENCE_LOG, '0xe71d000000000000000000000000000000000005'),
];

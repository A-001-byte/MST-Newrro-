# SwarmShield: dashboard

Live mission-control UI for **SwarmShield**, a commander-less drone early-warning mesh on the MST blockchain. Sensor nodes (one physical Neurick board plus virtual nodes) sign detection claims. `CorroborationEngine` only confirms an alert when **k of n** independent nodes agree, lone claimants are slashed, and tampered nodes self-quarantine.

Detect · Verify · Alert. Never engages.

- **Landing** (`#/`): video hero, how it works, contracts.
- **Live dashboard** (`#/live`): sector map, k-of-n corroboration, Neurick board mirror, live tx feed, reputation leaderboard, task market, demo dock, contracts modal, BridgeKey-gated node registration.

Stack: React 19 + Vite + Tailwind CSS v4 + Framer Motion + lucide-react. Hash routing (no server rewrites needed). The dashboard is code-split and lazy-loaded.

---

## Run

```bash
npm install
npm run dev        # http://localhost:5173
npm run build      # typecheck + production build to dist/
npm run preview    # serve dist/
```

By default the app runs against a **local simulator**, so no backend is needed.

### Demo script (maps to spec §9)

1. Open `#/live`. All nodes are green.
2. **Simulate drone detection**: three nodes around S3 claim in turn (yellow, pulsing). Quorum is reached, `AlertConfirmed` fires, the dashboard turns red, the Neurick board card sounds its alarm, raises its flag and shows `THREAT CONFIRMED`, claimants are credited, and a sealed-bid patrol auction runs and awards the lowest bid.
3. **Inject rogue node**: `Injected-01` registers and spams claims alone. Duplicates are rejected, the window expires, it is slashed −30, falls below 40 and is auto-quarantined (gray, struck through). A later claim is reverted.
4. **Simulate tamper** (defaults to the Neurick board): `TamperReported` fires, the node self-quarantines, and its next claim is ignored.
5. Click any feed row's external-link icon to open the explorer.
6. **Reset** restores the initial fleet. The Wi-Fi-off icon (mock only) simulates a socket drop, which shows the "Reconnecting…" pill.

---

## Environment variables

Copy `.env.example` to `.env.local`.

| Variable | Default | Purpose |
|---|---|---|
| `VITE_USE_MOCK` | `true` | `false` = connect to the FastAPI gateway instead of the simulator |
| `VITE_WS_URL` | `ws://localhost:8000/ws` | Gateway WebSocket streaming `StreamMessage` frames |
| `VITE_API_URL` | `http://localhost:8000` | Gateway REST base for demo triggers and registration |
| `VITE_EXPLORER_URL` | `https://explorer.testnet.mst.example` | **Placeholder, must be set.** Explorer base. Links are `${base}/tx/<hash>` and `${base}/address/<addr>` |
| `VITE_ADDR_SENSOR_REGISTRY` | placeholder | Deployed contract addresses. Shown in the Contracts modal and landing page. A "Placeholder" tag appears until they are set |
| `VITE_ADDR_CORROBORATION_ENGINE` | placeholder | |
| `VITE_ADDR_REPUTATION_MANAGER` | placeholder | |
| `VITE_ADDR_TASK_MARKET` | placeholder | |
| `VITE_ADDR_EVIDENCE_LOG` | placeholder | |

If the explorer uses different URL shapes, edit `explorerTx` / `explorerAddress` in `src/lib/config.ts`. Quorum `k`, sector count and the quarantine threshold also live there.

---

## Event schema (for the gateway)

The full definitions are in **`src/types.ts`**, which is the single source of truth. All timestamps are epoch **milliseconds**. Addresses and hashes are `0x` lowercase hex.

### WebSocket frames: `StreamMessage`

The gateway sends JSON frames of these shapes. Send a `snapshot` first on every (re)connect, then deltas.

```ts
{ type: 'snapshot', state: SwarmSnapshot }   // full state; also used after reconnects
{ type: 'event',    event: ChainEvent }      // one on-chain event (newest goes on top of the feed)
{ type: 'node',     node: NodeState }        // upsert one node (reputation/status/quarantine changed)
{ type: 'window',   window: ClaimWindow }    // upsert one claim window (open → confirmed | expired)
{ type: 'threat',   threat: Threat | null }  // set when AlertConfirmed, null when stood down
{ type: 'auction',  auction: Auction | null }// TaskMarket state
{ type: 'block',    height: number }         // latest block height
```

### Core types

```ts
NodeState {
  address: string            // node wallet (SensorRegistry key)
  sector: number             // 1..6
  reputation: number         // 0..100
  status: 'normal' | 'pending' | 'alert' | 'quarantined'
  isReal: boolean            // true = physical Neurick board
  quarantined: boolean
  label?: string             // "Neurick-01", "VN-04"
  registeredAt?: number
  quarantineReason?: 'tamper' | 'reputation' | null
}

ChainEvent {
  id: string                 // unique, e.g. `${txHash}:${logIndex}`
  type: 'NodeRegistered' | 'ClaimSubmitted' | 'ClaimRejected' | 'AlertConfirmed'
      | 'ReputationCredited' | 'ReputationSlashed' | 'TamperReported' | 'TaskBid' | 'TaskAwarded'
  txHash: string
  nodeAddress: string        // node, or patrol agent for Task* events
  sector: number | null
  timestamp: number
  blockNumber?: number
  meta: {                    // all optional; used where relevant
    confidence?, windowId?, claimants?, k?, n?, timeToConfirmMs?,
    delta?, reputation?, reason?, evidenceHash?, auctionId?, agentLabel?, amount?, authority?
  }
}

ClaimWindow { id, sector, openedAt, expiresAt, k, n,
              claims: { nodeAddress, confidence, timestamp, txHash }[],
              state: 'open' | 'confirmed' | 'expired', confirmedAt? }
Threat      { sector, claimants: string[], k, n, confirmedAt, txHash }
Auction     { id, sector, openedAt, closesAt, state: 'bidding' | 'awarded',
              bids: { agent, agentLabel, sealedHash, amount: number|null, etaSec: number|null, timestamp }[],
              winner?, txHash? }
SwarmSnapshot { network, blockHeight, nodes, events /* newest first */, windows, threat, auction,
                stats?, history? /* address -> recent reputation values, seeds sparklines */ }
```

Status meaning: **green** normal, **yellow** claim pending corroboration, **red** alert confirmed, **gray** quarantined. The UI always pairs each colour with an icon and label.

If `stats` is omitted, the dashboard derives total claims, alerts, slashed nodes and average confirm time from the events.

### REST endpoints the dashboard calls (live mode)

| Method | Path | Body |
|---|---|---|
| POST | `/demo/drone` | none |
| POST | `/demo/rogue` | none |
| POST | `/demo/tamper` | `{ "nodeAddress": "0x…" }` |
| POST | `/demo/reset` | none |
| POST | `/nodes/register` | `{ "address", "sector", "label"?, "authority", "signature" }` |

A non-2xx response shows its body text as a toast. Results arrive over the WebSocket, not in the HTTP response.

---

## Swapping mock → real

Everything flows through **`useSwarmStream()`** (`src/hooks/useSwarmStream.ts`), which is the only source of dashboard state:

```
simulator.ts ─┐
              ├─ StreamMessage ─> swarmReducer ─> useSwarmStream() ─> <SwarmProvider> ─> panels
gateway WS  ──┘
```

1. Implement the frames above in the gateway (Python: `await ws.send_json({...})`).
2. Set `VITE_USE_MOCK=false`, `VITE_WS_URL`, `VITE_API_URL`, and restart `npm run dev`.
3. The client reconnects with exponential backoff (0.8 s up to 10 s) and shows "Reconnecting…" while it waits. After about 7 failed attempts it shows "Gateway offline · retrying". Send a fresh `snapshot` on every connect.

`src/lib/simulator.ts` works as a reference implementation: each demo act shows the order in which the gateway should emit frames.

### BridgeKey

`src/hooks/useBridgeKey.tsx` exposes `{ status, address, connect, disconnect, signAuthorityAction }`. It is currently mocked (connects to a fixed authority address). The two `TODO(BridgeKey)` comments mark where the real extension calls go: account request and message signing. The authority action is **Register node** (top nav), which requires a connected wallet, requests a signature, and then calls `registerNode`.

---

## Project structure

```
src/
  types.ts                 event schema (the contract with the gateway)
  App.tsx                  hash routing + page transitions
  index.css                design tokens (@theme) + glass/panel utilities
  hooks/                   useSwarmStream, useSwarm (context), useBridgeKey, useNow, useAnimatedNumber
  lib/                     config, simulator, gatewayClient, swarmReducer, geometry, events, status, format
  components/
    ui/                    Button, StatusChip, Hash (copy/explorer), Panel, Overlay (Modal/Drawer), Sparkline, Logo
    landing/Landing.tsx
    dashboard/             Dashboard, TopNav, WalletButton, ConnectionPill, StatCards, ThreatBanner, SectorMap,
                           CorroborationPanel, BoardCard, TxFeed, Leaderboard, TaskMarket, DemoDock,
                           NodeDrawer, ContractsModal, RegisterNodeModal
public/video.mp4           hero background
```

Accessibility: every control has hover, focus-visible, active and disabled states. Dialogs trap focus and close on Esc. Map nodes are keyboard-focusable buttons. `prefers-reduced-motion` stops the radar sweep, shockwaves, pulses and the hero video.

---

## Deploy to Vercel

1. Push this folder to GitHub (it can be the `dashboard/` folder of the monorepo; set **Root Directory** accordingly).
2. In Vercel, click **New Project** and import the repo. The framework preset **Vite** is auto-detected (build `npm run build`, output `dist`).
3. Add the environment variables above under **Settings → Environment Variables**. For the live gateway, use `wss://` / `https://` URLs, since browsers block `ws://` from an HTTPS page.
4. Deploy. Routing is hash-based, so no rewrites are needed.

CLI alternative:

```bash
npm i -g vercel && vercel --prod
```

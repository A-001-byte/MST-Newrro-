/**
 * Live transport: FastAPI gateway WebSocket + REST demo endpoints.
 * Used when VITE_USE_MOCK=false. Frames must match StreamMessage in src/types.ts.
 */
import type { StreamMessage } from '../types';
import { API_URL, WS_URL } from './config';
import type { ReducerAction } from './swarmReducer';

const MESSAGE_TYPES = new Set(['snapshot', 'event', 'node', 'window', 'threat', 'auction', 'block']);

/** Opens the socket with exponential-backoff reconnects. Returns a cleanup function. */
export function connectGateway(dispatch: (action: ReducerAction) => void): () => void {
  let socket: WebSocket | null = null;
  let attempt = 0;
  let retryTimer: number | undefined;
  let closed = false;
  let everConnected = false;

  const scheduleRetry = () => {
    dispatch({ type: 'connection', connection: attempt > 6 ? 'offline' : everConnected ? 'reconnecting' : 'connecting' });
    const delay = Math.min(10_000, 800 * 2 ** attempt);
    attempt++;
    retryTimer = window.setTimeout(open, delay);
  };

  function open() {
    if (closed) return;
    try {
      socket = new WebSocket(WS_URL);
    } catch {
      scheduleRetry();
      return;
    }
    socket.onopen = () => {
      attempt = 0;
      everConnected = true;
      dispatch({ type: 'connection', connection: 'live' });
    };
    socket.onmessage = (msg) => {
      try {
        const data = JSON.parse(msg.data as string) as StreamMessage;
        if (data && MESSAGE_TYPES.has(data.type)) dispatch(data);
      } catch {
        // ignore malformed frames
      }
    };
    socket.onclose = () => {
      if (!closed) scheduleRetry();
    };
    socket.onerror = () => socket?.close();
  }

  dispatch({ type: 'connection', connection: 'connecting' });
  open();
  return () => {
    closed = true;
    window.clearTimeout(retryTimer);
    socket?.close();
  };
}

export async function postGateway(path: string, body?: unknown): Promise<void> {
  const res = await fetch(`${API_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(text || `Gateway returned ${res.status}`);
  }
}

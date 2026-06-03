import { useEffect, useRef } from "react";

const WS_BASE = (() => {
  const base = import.meta.env.VITE_API_URL || window.location.origin;
  return base.replace(/^https/, "wss").replace(/^http/, "ws");
})();

/**
 * Connects to /ws/results and calls onMessage(data) for every server push.
 * Auto-reconnects after 3 s on disconnect. Returns a ref to the live WebSocket.
 * Optional { onOpen, onClose } fire on connection state changes.
 */
export function useResultsSocket(onMessage, { onOpen, onClose } = {}) {
  const wsRef      = useRef(null);
  const msgRef     = useRef(onMessage);
  const openRef    = useRef(onOpen);
  const closeRef   = useRef(onClose);
  msgRef.current   = onMessage;
  openRef.current  = onOpen;
  closeRef.current = onClose;

  useEffect(() => {
    let ws;
    let reconnectTimer;
    let destroyed = false;

    function connect() {
      if (destroyed) return;
      ws = new WebSocket(`${WS_BASE}/ws/results`);
      wsRef.current = ws;

      ws.onopen = () => openRef.current?.();

      ws.onmessage = (e) => {
        try {
          msgRef.current(JSON.parse(e.data));
        } catch {
          // ignore malformed frames
        }
      };

      ws.onclose = () => {
        closeRef.current?.();
        if (!destroyed) reconnectTimer = setTimeout(connect, 3_000);
      };

      ws.onerror = () => ws.close();
    }

    connect();

    return () => {
      destroyed = true;
      clearTimeout(reconnectTimer);
      ws?.close();
    };
  }, []); // intentionally runs once — callbacks kept fresh via refs

  return wsRef;
}

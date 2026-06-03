import { create } from "zustand";

export const useWsStore = create((set) => ({
  connected: false,
  criticalCount: 0,
  lastUpdate: null,   // last results_update payload
  lastAlert: null,    // last anomaly_alert payload

  setConnected: (v) => set({ connected: v }),

  setCriticalCount: (n) => set({ criticalCount: n }),

  handleMessage: (msg) => {
    if (msg.type === "results_update") {
      set({ lastUpdate: msg });
    } else if (msg.type === "anomaly_alert") {
      set({
        lastAlert: msg,
        criticalCount: msg.unresolved_critical_total ?? 0,
      });
    }
  },
}));

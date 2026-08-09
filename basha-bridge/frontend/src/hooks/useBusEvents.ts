import { useCallback, useRef, useState } from "react";
import type { BusEvent } from "../types";

export function useBusEvents() {
  const [events, setEvents] = useState<BusEvent[]>([]);
  const t0 = useRef(Date.now());

  const push = useCallback((evt: BusEvent) => {
    setEvents((prev) => [...prev, { ...evt, ts: Date.now() - t0.current }]);
  }, []);

  const reset = useCallback(() => {
    t0.current = Date.now();
    setEvents([]);
  }, []);

  return { events, push, reset };
}

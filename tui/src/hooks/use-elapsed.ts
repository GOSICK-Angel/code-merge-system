import { useState, useEffect } from "react";

export function useElapsed(startIso: string | null): number {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!startIso) return;
    const startMs = new Date(startIso).getTime();

    const tick = () => setElapsed(Date.now() - startMs);
    tick();
    const interval = setInterval(tick, 1000);
    return () => clearInterval(interval);
  }, [startIso]);

  return elapsed;
}

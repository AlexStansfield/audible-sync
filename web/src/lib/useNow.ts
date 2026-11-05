import { useEffect, useState } from "react";

/** The time now in ms, ticking every second while `ticking` (a countdown, a live duration). */
export function useNow(ticking = true): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!ticking) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [ticking]);
  return now;
}

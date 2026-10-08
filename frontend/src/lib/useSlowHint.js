import { useEffect, useState } from "react";

// True once `active` has stayed true for `ms`. The hosted backend sleeps when idle and its
// first request can take most of a minute; a hint after a few seconds tells the user the
// app is waking up rather than broken.
export function useSlowHint(active, ms = 6000) {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    if (!active) {
      setSlow(false);
      return undefined;
    }
    const t = setTimeout(() => setSlow(true), ms);
    return () => clearTimeout(t);
  }, [active, ms]);
  return slow;
}

export const WAKING_MESSAGE =
  "Waking up the server… the free hosting plan sleeps when idle, so this first request can take up to a minute.";

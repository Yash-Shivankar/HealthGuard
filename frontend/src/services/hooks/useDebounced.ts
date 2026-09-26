/** A value that settles after the user stops typing. */

import { useEffect, useState } from 'react';

export function useDebounced<T>(value: T, delayMs = 300): T {
  const [settled, setSettled] = useState(value);

  useEffect(() => {
    const handle = window.setTimeout(() => setSettled(value), delayMs);
    return () => window.clearTimeout(handle);
  }, [value, delayMs]);

  return settled;
}

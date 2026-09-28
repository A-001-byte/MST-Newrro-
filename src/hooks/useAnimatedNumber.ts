import { useEffect, useRef, useState } from 'react';
import { useReducedMotion } from 'framer-motion';

/** Tweens a number toward `target` (ease-out). Snaps when reduced motion is on. */
export function useAnimatedNumber(target: number, duration = 500, maxJump = Infinity) {
  const reduced = useReducedMotion();
  const [value, setValue] = useState(target);
  const current = useRef(target);

  useEffect(() => {
    if (reduced || current.current === target || Math.abs(target - current.current) > maxJump) {
      current.current = target;
      setValue(target);
      return;
    }
    const from = current.current;
    const start = performance.now();
    let raf = 0;
    const tick = (t: number) => {
      const p = Math.min(1, (t - start) / duration);
      const v = from + (target - from) * (1 - Math.pow(1 - p, 3));
      current.current = v;
      setValue(v);
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, duration, reduced]);

  return value;
}

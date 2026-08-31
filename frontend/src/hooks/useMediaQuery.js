import { useEffect, useState } from "react";

/**
 * Subscribe to a CSS media query from React.
 *
 * Layout that only exists in CSS cannot change *behaviour* — on a phone we also
 * need to lower the render quality, widen the touch targets and swap the
 * floating panels for a dock, and those are JS decisions. This keeps the
 * breakpoint in one place so CSS and JS cannot drift apart: the value here must
 * match the `@media` breakpoints in styles.css.
 */
export function useMediaQuery(query) {
  const [matches, setMatches] = useState(() => {
    if (typeof window === "undefined" || !window.matchMedia) return false;
    return window.matchMedia(query).matches;
  });

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return undefined;

    const list = window.matchMedia(query);
    const onChange = (event) => setMatches(event.matches);

    // Re-read on subscribe: the query may have changed between the initial
    // render and this effect (e.g. an orientation change during hydration).
    setMatches(list.matches);

    // addEventListener is unavailable on Safari < 14, which still ships
    // addListener. Both are supported rather than assuming the modern one.
    if (list.addEventListener) {
      list.addEventListener("change", onChange);
      return () => list.removeEventListener("change", onChange);
    }
    list.addListener(onChange);
    return () => list.removeListener(onChange);
  }, [query]);

  return matches;
}

/** Phone / small tablet: panels dock instead of floating over the scene. */
export const COMPACT_QUERY = "(max-width: 900px)";

/** Coarse pointer: touch, so hover affordances never fire and taps need room. */
export const TOUCH_QUERY = "(hover: none) and (pointer: coarse)";

/** Honour the OS "reduce motion" setting for auto-rotate and pulse animation. */
export const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

export default useMediaQuery;

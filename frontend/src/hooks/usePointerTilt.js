import { useCallback, useEffect, useRef } from "react";

import useMediaQuery, { REDUCED_MOTION_QUERY, TOUCH_QUERY } from "./useMediaQuery";

/**
 * Pointer-driven 3D tilt for a card or plate.
 *
 * This is the CSS half of the "movement on every page" requirement — the pages
 * without a force graph still need depth, and a second WebGL context per page
 * would be an absurd way to buy it.
 *
 * Two rules it exists to enforce:
 *
 *   1. No React state. The transform is written straight onto the node as three
 *      custom properties (--tilt-x, --tilt-y, --tilt-lift) that styles.css
 *      composes into one transform. A pointer sweeping across a ten-card grid
 *      would otherwise fire a setState per mousemove per card.
 *   2. One write per frame. pointermove fires faster than the compositor on a
 *      120Hz trackpad, so the handler only records coordinates and a rAF does
 *      the writing — and there is never more than one frame queued.
 *
 * Returns props to spread onto the element. Disabled entirely on touch (there is
 * no hover state to tilt into, and firing on tap reads as a glitch) and under
 * prefers-reduced-motion.
 */
export default function usePointerTilt({ max = 7, lift = 10 } = {}) {
  const ref = useRef(null);
  const frameRef = useRef(0);
  const targetRef = useRef({ x: 0, y: 0 });

  const isTouch = useMediaQuery(TOUCH_QUERY);
  const prefersReduced = useMediaQuery(REDUCED_MOTION_QUERY);
  const enabled = !isTouch && !prefersReduced;

  const write = useCallback(() => {
    frameRef.current = 0;
    const node = ref.current;
    if (!node) return;
    const { x, y } = targetRef.current;
    node.style.setProperty("--tilt-x", `${(-y * max).toFixed(2)}deg`);
    node.style.setProperty("--tilt-y", `${(x * max).toFixed(2)}deg`);
    node.style.setProperty("--tilt-lift", `${lift}px`);
  }, [max, lift]);

  const schedule = useCallback(() => {
    if (frameRef.current) return;
    frameRef.current = requestAnimationFrame(write);
  }, [write]);

  const onPointerMove = useCallback(
    (event) => {
      if (!enabled) return;
      const node = ref.current;
      if (!node) return;
      const rect = node.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      // -1..1 from the centre of the card, so the tilt follows the pointer's
      // position within the card rather than its absolute screen position.
      targetRef.current = {
        x: ((event.clientX - rect.left) / rect.width - 0.5) * 2,
        y: ((event.clientY - rect.top) / rect.height - 0.5) * 2
      };
      schedule();
    },
    [enabled, schedule]
  );

  const onPointerEnter = useCallback(() => {
    if (!enabled) return;
    // The class drops the transform transition while the pointer drives the
    // element, otherwise the tilt eases along a frame or two behind the cursor.
    ref.current?.classList.add("is-tilting");
  }, [enabled]);

  const reset = useCallback(() => {
    const node = ref.current;
    if (!node) return;
    node.classList.remove("is-tilting");
    // Clearing the properties lets the transition in styles.css ease the card
    // back to flat, rather than snapping.
    node.style.setProperty("--tilt-x", "0deg");
    node.style.setProperty("--tilt-y", "0deg");
    node.style.setProperty("--tilt-lift", "0px");
  }, []);

  // A card can unmount mid-hover (filtering the roster, navigating away) and
  // leave a queued frame pointing at a detached node.
  useEffect(() => () => {
    if (frameRef.current) cancelAnimationFrame(frameRef.current);
  }, []);

  // Flipping the OS reduce-motion switch, or docking a mouse, should take effect
  // without a reload.
  useEffect(() => {
    if (!enabled) reset();
  }, [enabled, reset]);

  if (!enabled) return { ref };

  return {
    ref,
    onPointerMove,
    onPointerEnter,
    onPointerLeave: reset,
    // Losing focus while tilted (tab away from a focused card) should also flatten.
    onBlur: reset
  };
}

import { useEffect } from "react";
import { useThree } from "@react-three/fiber";

/**
 * Stop rendering when nobody is looking.
 *
 * Two conditions, both of which happen constantly in a four-page app:
 *
 *   - The tab is hidden. Browsers throttle requestAnimationFrame in background
 *     tabs but do not always stop it, and a WebGL context that keeps drawing
 *     costs battery for a scene nobody is looking at. Resuming also starts on a
 *     fresh frame instead of replaying a queued backlog.
 *   - The canvas is scrolled off screen. The landing page's hero is a full
 *     viewport tall, so by the time you are reading the section below it the
 *     orrery is entirely out of frame and still animating at 60fps. An
 *     IntersectionObserver on the canvas element costs nothing and this is the
 *     difference between a landing page that spins a phone's fan and one that
 *     does not.
 *
 * `mode` is the frameloop to return to: "always" for a continuously animating
 * scene, "demand" when the OS asks for reduced motion, where the scene is static
 * and only needs to be drawn when something actually changes.
 */
export default function RenderGovernor({ mode = "always" }) {
  const setFrameloop = useThree((state) => state.setFrameloop);
  const invalidate = useThree((state) => state.invalidate);
  const domElement = useThree((state) => state.gl.domElement);

  useEffect(() => {
    let onScreen = true;

    const apply = () => {
      if (document.hidden || !onScreen) {
        setFrameloop("never");
        return;
      }
      setFrameloop(mode);
      // Coming back from "never" under demand mode needs an explicit nudge:
      // nothing else is going to ask for the frame that redraws the scene.
      if (mode === "demand") invalidate();
    };

    apply();
    document.addEventListener("visibilitychange", apply);

    let observer = null;
    if (typeof IntersectionObserver !== "undefined" && domElement) {
      observer = new IntersectionObserver(
        (entries) => {
          onScreen = entries.some((entry) => entry.isIntersecting);
          apply();
        },
        // A small margin so the scene is already running by the time it scrolls
        // into view, rather than popping in mid-animation.
        { rootMargin: "120px" }
      );
      observer.observe(domElement);
    }

    return () => {
      document.removeEventListener("visibilitychange", apply);
      if (observer) observer.disconnect();
      setFrameloop(mode);
    };
  }, [setFrameloop, invalidate, domElement, mode]);

  return null;
}

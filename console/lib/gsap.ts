/**
 * GSAP, registered once.
 *
 * Every story component imports from here rather than from `gsap` directly, so the plugins are
 * always registered before the first ScrollTrigger is built. All of them ship free in the public
 * `gsap` package since 3.13.
 */

import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import { ScrollSmoother } from "gsap/ScrollSmoother";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { SplitText } from "gsap/SplitText";

if (typeof window !== "undefined") {
  gsap.registerPlugin(ScrollTrigger, ScrollSmoother, SplitText, useGSAP);
}

/**
 * The two breakpoints every section animates for. Desktop pins sections and scrubs them with
 * the scroll; narrow screens play the same beats once, as each section arrives. Reduced motion
 * gets neither — everything simply renders in its final state.
 */
export const MEDIA = {
  wide: "(min-width: 1024px) and (prefers-reduced-motion: no-preference)",
  narrow: "(max-width: 1023px) and (prefers-reduced-motion: no-preference)",
} as const;

export { gsap, ScrollSmoother, ScrollTrigger, SplitText, useGSAP };

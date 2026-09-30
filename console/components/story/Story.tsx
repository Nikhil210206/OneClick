"use client";

import Image from "next/image";
import { useRef } from "react";
import { Compile } from "@/components/story/Compile";
import { Complaint } from "@/components/story/Complaint";
import { Finale } from "@/components/story/Finale";
import { Grounding } from "@/components/story/Grounding";
import { Hero } from "@/components/story/Hero";
import { Live } from "@/components/story/Live";
import { Memory } from "@/components/story/Memory";
import { PhoneSection } from "@/components/story/PhoneSection";
import { Prompt } from "@/components/story/Prompt";
import { Proof } from "@/components/story/Proof";
import { Resolve } from "@/components/story/Resolve";
import { MEDIA, ScrollSmoother, ScrollTrigger, gsap, useGSAP } from "@/lib/gsap";
import type { StoryData } from "@/lib/story";

/**
 * The story page: one real request, told section by section as the reader scrolls.
 *
 * Order matters for GSAP. ScrollSmoother has to exist before any ScrollTrigger is measured, and
 * React runs layout effects child-first, siblings in order — so `Smoother` sits first inside the
 * content, every section after it, and the fixed chrome (whose triggers read the sections) last.
 */
export function Story({ data }: { data: StoryData }) {
  return (
    <div className="st">
      <div id="smooth-wrapper">
        <div id="smooth-content">
          <Smoother />
          <Hero data={data} />
          <Complaint data={data} />
          <Prompt data={data} />
          <Grounding data={data} />
          <Resolve data={data} />
          <PhoneSection data={data} />
          <Compile data={data} />
          <Memory data={data} />
          <Live data={data} />
          <Proof data={data} />
          <Finale />
        </div>
      </div>
      <FitSections />
      <SnapSections />
      <Nav />
    </div>
  );
}

function Smoother() {
  useGSAP(() => {
    const smoother = ScrollSmoother.create({
      wrapper: "#smooth-wrapper",
      content: "#smooth-content",
      smooth: 1.15,
      effects: true,
      smoothTouch: false,
    });
    // Outfit swaps in after first paint and changes every line height; re-measure once it has.
    document.fonts?.ready.then(() => ScrollTrigger.refresh());
    return () => smoother.kill();
  });
  return null;
}

/**
 * Keeps every `.fit` section to one screen on a desktop. The CSS sizes each one to the window;
 * when a short window still cannot hold a section, its content is zoomed down just enough to fit,
 * and ScrollTrigger re-measures. Narrow screens scroll naturally and are left alone.
 */
function FitSections() {
  useGSAP(() => {
    const wide = window.matchMedia("(min-width: 1024px)");
    const fit = () => {
      let changed = false;
      document.querySelectorAll<HTMLElement>(".fit").forEach((section) => {
        const inner = section.firstElementChild as HTMLElement | null;
        if (!inner) return;
        const before = inner.style.getPropertyValue("--fit-zoom");
        inner.style.removeProperty("--fit-zoom");
        let zoom = 1;
        if (wide.matches) {
          const css = getComputedStyle(section);
          const room = section.clientHeight - parseFloat(css.paddingTop) - parseFloat(css.paddingBottom);
          const need = inner.offsetHeight;
          if (need > room) zoom = Math.max(0.6, Math.floor((room / need) * 1000) / 1000);
        }
        if (zoom < 1) inner.style.setProperty("--fit-zoom", String(zoom));
        if ((before || "1") !== String(zoom)) changed = true;
      });
      if (changed) ScrollTrigger.refresh();
    };
    fit();
    document.fonts?.ready.then(fit);
    let frame = 0;
    const onResize = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(fit);
    };
    window.addEventListener("resize", onResize);
    wide.addEventListener("change", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      wide.removeEventListener("change", onResize);
      cancelAnimationFrame(frame);
    };
  });
  return null;
}

/**
 * A magnetic stop between sections on a desktop. When the reader stops scrolling between two
 * one-screen sections, the page settles on the nearer one. Inside a pinned walkthrough (prompt,
 * grounding, phone) the scroll drives the animation, so it only settles when the reader stops close to
 * where the walkthrough starts or ends, never in the middle of it.
 */
function SnapSections() {
  useGSAP(() => {
    const mm = gsap.matchMedia();
    mm.add(MEDIA.wide, () => {
      // Each top-level block of the page as a scroll range; a pin spacer is a pinned walkthrough. The
      // smoothed content starts at scroll 0, so a block's layout offset is the scroll that brings it to
      // the top. Measured lazily after each refresh: measuring inside one would start another.
      let ranges: { start: number; end: number; pinned: boolean }[] = [];
      let stale = true;
      const measure = () => {
        const blocks = [...document.querySelectorAll<HTMLElement>("#smooth-content > section, #smooth-content > .pin-spacer")];
        const max = ScrollTrigger.maxScroll(window);
        ranges = blocks.map((el, i) => {
          const start = Math.min(el.offsetTop, max);
          const end = Math.min(i + 1 < blocks.length ? blocks[i + 1].offsetTop : max, max);
          // Longer than a screen (a pinned walkthrough, or the caching section): read freely inside.
          return { start, end, pinned: el.classList.contains("pin-spacer") || end - start > window.innerHeight * 1.05 };
        });
        stale = false;
      };
      const markStale = () => {
        stale = true;
      };
      ScrollTrigger.addEventListener("refresh", markStale);

      // Where the page should come to rest from scroll position y: the nearer edge of a one-screen
      // section; inside a longer block, only an edge the reader has nearly reached.
      const rest = (y: number) => {
        if (stale) measure();
        const r = ranges.find((x) => y >= x.start && y < x.end) ?? ranges[ranges.length - 1];
        if (!r) return y;
        const pull = window.innerHeight * 0.22;
        if (!r.pinned) return y - r.start < r.end - y ? r.start : r.end;
        if (y - r.start < pull) return r.start;
        if (r.end - y < pull) return r.end;
        return y;
      };
      // When the reader stops scrolling, glide the rest of the way with the smoother's own easing.
      let settling = false;
      let release: ReturnType<typeof setTimeout> | undefined;
      const settle = () => {
        if (settling) return;
        const smoother = ScrollSmoother.get();
        // The native scroll is where the reader stopped; the smoothed view is still catching up to it.
        const y = window.scrollY;
        const to = rest(y);
        if (Math.abs(to - y) < 2) return;
        settling = true;
        if (smoother) smoother.scrollTo(to, true);
        else window.scrollTo({ top: to, behavior: "smooth" });
        release = setTimeout(() => {
          settling = false;
        }, 1200);
      };
      ScrollTrigger.addEventListener("scrollEnd", settle);

      return () => {
        ScrollTrigger.removeEventListener("refresh", markStale);
        ScrollTrigger.removeEventListener("scrollEnd", settle);
        clearTimeout(release);
      };
    });
  });
  return null;
}

const LINKS = [
  ["The prompt", "#prompt"],
  ["Grounding", "#grounding"],
  ["The phone", "#phone"],
  ["Checks", "#compile"],
  ["Proof", "#proof"],
] as const;

function Nav() {
  const bar = useRef<HTMLElement>(null);

  useGSAP(() => {
    const el = bar.current!;
    // Ink or white, whichever the section under the bar needs.
    gsap.utils.toArray<HTMLElement>("[data-nav]").forEach((section) => {
      ScrollTrigger.create({
        trigger: section,
        start: "top 48px",
        end: "bottom 48px",
        onToggle: (self) => {
          if (self.isActive) el.dataset.tone = section.dataset.nav;
        },
      });
    });
    // The bar steps out of the way while reading down and returns on the way back up — but never
    // over a pinned walkthrough, which uses the whole frame and would sit underneath it.
    const hide = gsap.to(el, { yPercent: -130, duration: 0.45, ease: "power3.out", paused: true });
    let down = false;
    let pinned = 0;
    const sync = () => (down || pinned > 0 ? hide.play() : hide.reverse());
    ScrollTrigger.create({
      start: 120,
      end: "max",
      onUpdate: (self) => {
        down = self.direction === 1;
        sync();
      },
      onLeaveBack: () => {
        down = false;
        sync();
      },
    });
    gsap.utils.toArray<HTMLElement>("#prompt, #grounding, #phone").forEach((section) => {
      ScrollTrigger.create({
        trigger: section,
        start: "top 100px",
        end: "bottom 100px",
        onToggle: (self) => {
          pinned += self.isActive ? 1 : -1;
          sync();
        },
      });
    });
    // Off the hero, the bar gets its own ground so it never sits bare on a section's content.
    ScrollTrigger.create({
      start: 80,
      end: "max",
      onToggle: (self) => {
        el.dataset.solid = String(self.isActive);
      },
    });
  });

  const jump = (hash: string) => (e: React.MouseEvent) => {
    const smoother = ScrollSmoother.get();
    if (!smoother) return;
    e.preventDefault();
    smoother.scrollTo(hash, true, "top top");
  };

  return (
    <header className="nav" ref={bar} data-tone="dark">
      <a className="nav-brand" href="#top" onClick={jump("#top")}>
        <Image className="nav-samsung" src="/samsung-logo.png" alt="Samsung" width={282} height={61} priority />
        <i className="nav-div" aria-hidden />
        <span>OneClick</span>
      </a>
      <nav className="nav-links" aria-label="Sections">
        {LINKS.map(([label, hash]) => (
          <a key={hash} href={hash} onClick={jump(hash)}>
            {label}
          </a>
        ))}
      </nav>
      <a className="nav-cta" href="#live" onClick={jump("#live")}>
        Try it live
      </a>
    </header>
  );
}

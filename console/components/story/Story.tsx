"use client";

import { useRef } from "react";
import { Compile } from "@/components/story/Compile";
import { Complaint } from "@/components/story/Complaint";
import { Finale } from "@/components/story/Finale";
import { Mark } from "@/components/story/Galaxy";
import { Grounding } from "@/components/story/Grounding";
import { Hero } from "@/components/story/Hero";
import { Live } from "@/components/story/Live";
import { Memory } from "@/components/story/Memory";
import { PhoneSection } from "@/components/story/PhoneSection";
import { Prompt } from "@/components/story/Prompt";
import { Proof } from "@/components/story/Proof";
import { Resolve } from "@/components/story/Resolve";
import { ScrollSmoother, ScrollTrigger, gsap, useGSAP } from "@/lib/gsap";
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
        <Mark className="nav-mark" />
        <span>OneClick</span>
        <em>for Galaxy</em>
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

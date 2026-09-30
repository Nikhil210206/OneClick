"use client";

import { useRef } from "react";
import { gsap, useGSAP } from "@/lib/gsap";
import type { StoryData } from "@/lib/story";

type Action = StoryData["resolve"]["actions"][number];

/** The plan's three kinds of action, in the order the phone shows them. */
const GROUPS: { category: Action["category"]; tag: string; what: string }[] = [
  { category: "auto", tag: "Auto · one tap", what: "One tap opens the exact Settings screen." },
  { category: "manual", tag: "Manual · by hand", what: "No screen to open: you do these by hand." },
  {
    category: "critical",
    tag: "Critical",
    what: "Restart, Safe mode, updates, reset: disruptive, never fired for you, always last.",
  },
];

/** What the resolver found for one action, in a few words. */
function note(x: Action): string {
  if (x.tier === "catalog") return `opens “${x.link}”${x.verifiable ? " · checks itself" : ""}`;
  if (x.tier === "dummy") return "screen known, no catalog link";
  return "no Settings screen to open";
}

export function Resolve({ data }: { data: StoryData }) {
  const root = useRef<HTMLElement>(null);
  const f = data.resolve.featured;

  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      mm.add("(prefers-reduced-motion: no-preference)", () => {
        const $ = gsap.utils.selector(root);
        gsap.from($(".rs-head > *"), {
          y: 60,
          autoAlpha: 0,
          stagger: 0.12,
          duration: 1.2,
          ease: "expo.out",
          scrollTrigger: { trigger: $(".rs-head")[0], start: "top 80%" },
        });

        const flow = gsap.timeline({
          defaults: { ease: "expo.out", duration: 1 },
          scrollTrigger: { trigger: $(".rs-flow")[0], start: "top 75%" },
        });
        flow
          .from($(".rs-card"), { y: 90, rotate: 2.5, autoAlpha: 0, stagger: 0.18 })
          .from($(".rs-arrow"), { scaleX: 0, transformOrigin: "left center", stagger: 0.18, duration: 0.6 }, 0.3)
          .from($(".rs-path > span"), { y: 16, autoAlpha: 0, stagger: 0.08, duration: 0.6 }, 0.4)
          .from($(".rs-bar i"), { scaleX: 0, transformOrigin: "left center", stagger: 0.12, duration: 1.2 }, 0.6)
          .fromTo($(".rs-cand-win"), { "--win": 0 }, { "--win": 1, duration: 0.5 }, 1.5)
          .from($(".rs-hit > *"), { y: 20, autoAlpha: 0, stagger: 0.07, duration: 0.7 }, 1.4)
          .fromTo(
            $(".rs-val svg path"),
            { strokeDasharray: 30, strokeDashoffset: 30 },
            { strokeDashoffset: 0, duration: 0.6 },
            2,
          );

        gsap.from($(".rs-group"), {
          y: 70,
          autoAlpha: 0,
          stagger: 0.12,
          duration: 1.1,
          ease: "expo.out",
          scrollTrigger: { trigger: $(".rs-board")[0], start: "top 85%" },
        });
        gsap.from($(".rs-items li"), {
          x: -16,
          autoAlpha: 0,
          stagger: 0.05,
          duration: 0.6,
          ease: "power2.out",
          delay: 0.3,
          scrollTrigger: { trigger: $(".rs-board")[0], start: "top 85%" },
        });
      });
    },
    { scope: root },
  );

  const path = f.path.split(" > ");

  return (
    <section className="rs fit" id="resolve" data-nav="light" ref={root}>
      <div className="rs-inner">
        <header className="rs-head">
          <div>
            <p className="st-eyebrow">04 · Resolve</p>
            <h2 className="st-h2">
              Straight to the
              <br />
              right screen.
            </h2>
          </div>
          <p className="st-lead rs-lead">
            Each step names a screen. OneClick finds it in a map of real Settings pages and copies the
            catalog&apos;s link exactly as written. <b>The model never writes a link.</b>
          </p>
        </header>

        <div className="rs-flow">
          <div className="rs-card rs-q">
            <span className="rs-label">The step</span>
            <p className="rs-step">&ldquo;{f.step}&rdquo;</p>
            <div className="rs-path">
              {path.map((seg) => (
                <span key={seg}>{seg}</span>
              ))}
            </div>
            <span className="rs-verb">
              intent · <b>{f.verb}</b>
            </span>
          </div>

          <span className="rs-arrow" aria-hidden />

          <div className="rs-card rs-rank">
            <span className="rs-label">Closest Settings screens</span>
            {f.candidates.map((c, i) => (
              <div className={`rs-cand${i === 0 ? " rs-cand-win" : ""}`} key={`${c.id}-${i}`}>
                <span className="rs-cand-name">
                  {c.path.split(" > ").pop()} <small className="rs-cand-id">{c.id}</small>
                </span>
                <span className="rs-bar">
                  <i style={{ width: `${(c.score / f.candidates[0].score) * 100}%` }} />
                </span>
                <b>{c.score.toFixed(2)}</b>
              </div>
            ))}
          </div>

          <span className="rs-arrow" aria-hidden />

          <div className="rs-card rs-hit">
            <span className="rs-label">Catalog entry</span>
            <b className="rs-id">{f.entryId}</b>
            <p className="rs-msg">{f.message}</p>
            <code className="rs-uri">{f.uri}</code>
            <div className="rs-val">
              <svg viewBox="0 0 24 24" aria-hidden>
                <path d="m6 12.5 4 4 8-9" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
              </svg>
              <span>
                Checks <b>{f.validation.key}</b>
                {f.validation.condition && ` ${f.validation.condition === "equal" ? "=" : f.validation.condition} `}
                {f.validation.value && <b>{f.validation.value}</b>}
              </span>
            </div>
          </div>
        </div>

        <div className="rs-board">
          {GROUPS.map((g) => {
            const items = data.resolve.actions.filter((x) => x.category === g.category);
            return (
              <div className={`rs-group rs-group-${g.category}`} key={g.category}>
                <div className="rs-group-head">
                  <span className="rs-tag">{g.tag}</span>
                  <b className="rs-count">{items.length}</b>
                </div>
                <p className="rs-group-what">{g.what}</p>
                <ul className="rs-items">
                  {items.map((x) => (
                    <li key={x.name}>
                      <b>{x.name}</b>
                      <small>{note(x)}</small>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
        <p className="st-fine rs-fine">
          The catalog has no entry for Safe mode, Software update or Factory data reset, so those steps never get
          a made-up link. Candidates, scores and categories from one recorded run.
        </p>
      </div>
    </section>
  );
}

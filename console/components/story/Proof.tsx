"use client";

import { useRef } from "react";
import { gsap, useGSAP } from "@/lib/gsap";
import type { StoryData } from "@/lib/story";

/**
 * Measured, not claimed. Only numbers the repo can reproduce today: the eval sets' design, the
 * BM25 baseline the resolver must beat, the catalog's own shape and the answer key's progress.
 * Everything that needs the live engine is listed as pending, never estimated.
 */

const R = 42;
const RING = 2 * Math.PI * R;

function Ring({ value, label, sub, tone }: { value: number; label: string; sub: string; tone: string }) {
  return (
    <div className="pf-ring">
      <svg viewBox="0 0 100 100" aria-hidden>
        <circle cx="50" cy="50" r={R} className="pf-ring-track" />
        <circle
          cx="50"
          cy="50"
          r={R}
          className={`pf-ring-fill ${tone}`}
          strokeDasharray={RING}
          strokeDashoffset={RING * (1 - value)}
          data-offset={RING * (1 - value)}
          transform="rotate(-90 50 50)"
        />
      </svg>
      <b>{Math.round(value * 100)}%</b>
      <span>{label}</span>
      <small>{sub}</small>
    </div>
  );
}

export function Proof({ data }: { data: StoryData }) {
  const root = useRef<HTMLElement>(null);
  const { proof } = data;
  const { sets, gold, catalog, overlap, resolver, cache } = proof;
  const pct = (x: number) => `${Math.round(x * 100)}%`;
  const owners = Object.entries(gold.owners)
    .map(([o, n]) => `${n} by ${o}`)
    .join(", ");
  const setRows = [
    ["Paraphrases", sets.paraphrases],
    ["Near misses", sets.nearMiss],
    ["Unseen domains", sets.unseen],
    ["Adversarial", sets.adversarial],
  ] as const;
  const setMax = Math.max(...setRows.map(([, n]) => n));
  const share = catalog.entries ? catalog.verifiable / catalog.entries : 0;

  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      mm.add("(prefers-reduced-motion: no-preference)", () => {
        const $ = gsap.utils.selector(root);
        gsap.from($(".pf-head > *"), {
          y: 60,
          autoAlpha: 0,
          stagger: 0.12,
          duration: 1.2,
          ease: "expo.out",
          scrollTrigger: { trigger: $(".pf-head")[0], start: "top 80%" },
        });

        $(".pf-card").forEach((card) => {
          const tl = gsap.timeline({
            defaults: { ease: "expo.out" },
            scrollTrigger: { trigger: card, start: "top 82%" },
          });
          tl.from(card, { y: 90, autoAlpha: 0, duration: 1.1 });
          // Each card holds one kind of chart; animate whichever it has.
          const parts: [string, gsap.TweenVars][] = [
            [".pf-hbar i", { scaleX: 0, transformOrigin: "left center", stagger: 0.15, duration: 1.4 }],
            [".pf-vbar i", { scaleY: 0, transformOrigin: "50% 100%", stagger: 0.1, duration: 1.2 }],
            [".pf-stack i", { scaleX: 0, transformOrigin: "left center", stagger: 0.12, duration: 1 }],
            [".pf-pending li", { x: -24, autoAlpha: 0, stagger: 0.08, duration: 0.8 }],
          ];
          parts.forEach(([sel, vars]) => {
            const els = card.querySelectorAll(sel);
            if (els.length) tl.from(els, vars, 0.3);
          });
          card.querySelectorAll<SVGCircleElement>(".pf-ring-fill, .pf-donut-fill").forEach((c) => {
            tl.fromTo(
              c,
              { strokeDashoffset: Number(c.getAttribute("stroke-dasharray")) },
              { strokeDashoffset: Number(c.dataset.offset), duration: 1.6, ease: "power3.out" },
              0.35,
            );
          });
        });
      });
    },
    { scope: root },
  );

  return (
    <section className="pf" id="proof" data-nav="light" ref={root}>
      <div className="pf-inner">
        <header className="pf-head">
          <p className="st-eyebrow">09 · Proof</p>
          <h2 className="st-h2">Measured, not claimed.</h2>
          <p className="st-lead">
            What the repo measures today. Each number says who measured it, and anything that needs the whole
            engine running says pending until it has a real value.
          </p>
        </header>

        <div className="pf-grid">
          <article className="pf-card pf-span-7">
            <div className="pf-top">
              <h3>Near misses look more alike than paraphrases</h3>
              <span className="tag tag-lime">Test design</span>
            </div>
            <div className="pf-chart">
              <div className="pf-grid-lines" aria-hidden>
                {[0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6].map((t) => (
                  <span key={t} style={{ left: `${(t / 0.6) * 100}%` }}>
                    {t.toFixed(1)}
                  </span>
                ))}
              </div>
              <div className="pf-hbar">
                <span>Paraphrases · {sets.paraphrases}</span>
                <i className="blue" style={{ width: `${(overlap.paraphrase / 0.6) * 100}%` }}>
                  <b>{overlap.paraphrase.toFixed(2)}</b>
                </i>
              </div>
              <div className="pf-hbar">
                <span>Near misses · {sets.nearMiss}</span>
                <i className="red" style={{ width: `${(overlap.nearMiss / 0.6) * 100}%` }}>
                  <b>{overlap.nearMiss.toFixed(2)}</b>
                </i>
              </div>
            </div>
            <p className="pf-note">
              Mean word overlap with the original complaint. The near misses were written to overlap more than
              the paraphrases, so similarity alone can&apos;t tell them apart. Only the slot guard can.
            </p>
          </article>

          <article className="pf-card pf-span-5">
            <div className="pf-top">
              <h3>Right screen, first try</h3>
              <span className="tag tag-blue">Resolver</span>
            </div>
            <div className="pf-rings">
              <Ring
                value={resolver.top1 / resolver.n}
                label="Screen Graph resolver"
                sub={`${resolver.top1} of ${resolver.n}`}
                tone="lime"
              />
              <Ring
                value={resolver.bm25Top1 / resolver.n}
                label="plain keyword search"
                sub={`${resolver.bm25Top1} of ${resolver.n}`}
                tone="blue"
              />
            </div>
            <p className="pf-note">
              Precision@1 on the same {resolver.n} labelled steps. A wrong link was attached {resolver.wrongLink} time
              in {resolver.n}. Measured by the mapping lane on labels it wrote; the check on the eval lane&apos;s
              independent labels is pending.
            </p>
          </article>

          <article className="pf-card pf-span-4">
            <div className="pf-top">
              <h3>The cache, measured</h3>
              <span className="tag tag-lime">Cache</span>
            </div>
            <ul className="pf-stats">
              <li>
                <b>{pct(cache.repeatHit)}</b>
                <span>same words answered from cache</span>
                <small>{cache.repeatMs} ms</small>
              </li>
              <li>
                <b>{pct(cache.paraphraseHit)}</b>
                <span>reworded questions recognised</span>
                <small>~{cache.paraphraseMs} ms</small>
              </li>
              <li>
                <b>{pct(cache.falseHits)}</b>
                <span>wrong plans reused</span>
                <small>false hits</small>
              </li>
            </ul>
            <p className="pf-note">Measured by the mapping lane on its held-out paraphrases.</p>
          </article>

          <article className="pf-card pf-span-4">
            <div className="pf-top">
              <h3>Held-out test sets</h3>
              <span className="tag tag-ink">Eval</span>
            </div>
            <div className="pf-vbars">
              {setRows.map(([label, n]) => (
                <div className="pf-vbar" key={label}>
                  <b>{n}</b>
                  <i style={{ height: `${Math.max(4, (n / setMax) * 100)}%` }} />
                  <span>{label}</span>
                </div>
              ))}
            </div>
            <p className="pf-note">
              {setRows.reduce((s, [, n]) => s + n, 0)} queries the engine is never tuned on.
            </p>
          </article>

          <article className="pf-card pf-span-4">
            <div className="pf-top">
              <h3>Links that can prove themselves</h3>
              <span className="tag tag-blue">Catalog</span>
            </div>
            <div className="pf-donut">
              <svg viewBox="0 0 100 100" aria-hidden>
                <circle cx="50" cy="50" r={R} className="pf-ring-track" />
                <circle
                  cx="50"
                  cy="50"
                  r={R}
                  className="pf-donut-fill"
                  strokeDasharray={RING}
                  strokeDashoffset={RING * (1 - share)}
                  data-offset={RING * (1 - share)}
                  transform="rotate(-90 50 50)"
                />
              </svg>
              <div>
                <b>{catalog.verifiable}</b>
                <span>of {catalog.entries}</span>
              </div>
            </div>
            <p className="pf-note">
              Only enable toggles carry a full check. Every other link can only show that a screen opened.
            </p>
          </article>

          <article className="pf-card pf-span-4">
            <div className="pf-top">
              <h3>Answer key</h3>
              <span className="tag tag-amber">Gold</span>
            </div>
            <div className="pf-gold">
              <b>{gold.labelled}</b>
              <span>{gold.labelled >= gold.target ? "steps labelled" : `/ ${gold.target} steps labelled`}</span>
            </div>
            <div className="pf-stack">
              <i className="lime" style={{ flexGrow: gold.catalog }} />
              <i className="blue" style={{ flexGrow: gold.dummy }} />
              <i className="grey" style={{ flexGrow: gold.manual }} />
              <i className="empty" style={{ flexGrow: Math.max(0, gold.target - gold.labelled) }} />
            </div>
            <div className="pf-stack-legend">
              <span>
                <i className="lime" /> {gold.catalog} catalog
              </span>
              <span>
                <i className="blue" /> {gold.dummy} placeholder
              </span>
              <span>
                <i className="grey" /> {gold.manual} manual
              </span>
            </div>
            <p className="pf-note">The answer key for deeplink precision: {owners}.</p>
          </article>

          <article className="pf-card pf-span-8 pf-card-pending">
            <div className="pf-top">
              <h3>Waiting for the live engine</h3>
              <span className="tag tag-ghost">Pending</span>
            </div>
            <ul className="pf-pending">
              <li>
                <span>Resolver on independent labels</span>
                <b>pending</b>
              </li>
              <li>
                <span>End-to-end latency p50 / p95</span>
                <b>pending</b>
              </li>
              <li>
                <span>Step accuracy, judged</span>
                <b>pending</b>
              </li>
              <li>
                <span>Cache on our 200 held-out paraphrases</span>
                <b>pending</b>
              </li>
            </ul>
          </article>
        </div>
      </div>
    </section>
  );
}

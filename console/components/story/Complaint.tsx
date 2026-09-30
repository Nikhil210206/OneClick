"use client";

import { useRef } from "react";
import { ScrollTrigger, gsap, useGSAP } from "@/lib/gsap";
import type { StoryData } from "@/lib/story";

/** Split the complaint into words, marking the first one a slot was read from. */
function words(query: string, slotValues: string[]) {
  const seen = new Set<string>();
  return query.split(/\s+/).map((w) => {
    const bare = w.toLowerCase().replace(/[^a-z0-9]/g, "");
    const slot = slotValues.includes(bare) && !seen.has(bare);
    if (slot) seen.add(bare);
    return { w, slot };
  });
}

export function Complaint({ data }: { data: StoryData }) {
  const root = useRef<HTMLElement>(null);
  const filled = Object.entries(data.slots).filter((e): e is [string, string] => Boolean(e[1]));
  const list = words(
    data.query,
    filled.map(([, v]) => v),
  );

  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      mm.add("(prefers-reduced-motion: no-preference)", () => {
        // Read along: each word lights as the scroll reaches it.
        gsap.fromTo(
          ".cmp-quote .w",
          { opacity: 0.13 },
          {
            opacity: 1,
            stagger: 0.12,
            ease: "none",
            scrollTrigger: { trigger: ".cmp-quote", start: "top 82%", end: "bottom 42%", scrub: true },
          },
        );
        gsap.fromTo(
          ".cmp-slot",
          { "--hl": 0 },
          {
            "--hl": 1,
            ease: "power2.out",
            scrollTrigger: { trigger: ".cmp-slot", start: "top 62%", end: "top 48%", scrub: true },
          },
        );
        gsap.from(".cmp-tag", {
          y: 14,
          autoAlpha: 0,
          scale: 0.8,
          duration: 0.6,
          ease: "back.out(2)",
          scrollTrigger: { trigger: ".cmp-slot", start: "top 50%", toggleActions: "play none none reverse" },
        });
        gsap.set(".cmp-step", { autoAlpha: 0 });
        ScrollTrigger.batch(".cmp-step", {
          start: "top 85%",
          onEnter: (els) =>
            gsap.fromTo(
              els,
              { y: 70, autoAlpha: 0, rotate: 2 },
              { y: 0, autoAlpha: 1, rotate: 0, stagger: 0.12, duration: 1.1, ease: "expo.out", overwrite: true },
            ),
        });
        gsap.from(".cmp-kicker > span", {
          y: 60,
          autoAlpha: 0,
          duration: 1.2,
          stagger: 0.12,
          ease: "expo.out",
          // Keyed to the cards: on a one-screen section the line sits low and would wait for a scroll.
          scrollTrigger: { trigger: ".cmp-steps", start: "top 70%" },
        });
      });
    },
    { scope: root },
  );

  return (
    <section className="cmp fit" data-nav="dark" ref={root}>
      <div className="cmp-inner">
        <p className="st-eyebrow">01 · The complaint</p>
        <p className="cmp-quote">
          {list.map(({ w, slot }, i) =>
            slot ? (
              <span className="w cmp-slot" key={i}>
                <span className="cmp-slot-word">{w}</span>
                <span className="cmp-tag">
                  {filled.find(([, v]) => w.toLowerCase().includes(v))?.[0]} · {w.replace(/[^\w]/g, "")}
                </span>{" "}
              </span>
            ) : (
              <span className="w" key={i}>
                {w}{" "}
              </span>
            ),
          )}
        </p>

        <div className="cmp-steps">
          <div className="cmp-step">
            <div className="cmp-top">
              <span className="cmp-n">1</span>
              <h3>Clean it</h3>
            </div>
            <p>Tidied before any model reads it, the article and the complaint alike.</p>
            <ul className="cmp-rules" aria-label="What cleaning does">
              <li>
                <s>links</s> removed
              </li>
              <li>
                <s>emails</s> removed
              </li>
              <li>glued words split</li>
              <li>numbering fixed</li>
            </ul>
            <p className="cmp-unique">
              <b>Only here:</b> the article gets a fingerprint, so memory never mixes up two articles.
            </p>
            <code>fingerprint · {data.cache.siisHash}</code>
          </div>
          <div className="cmp-step">
            <div className="cmp-top">
              <span className="cmp-n">2</span>
              <h3>Read the slots</h3>
            </div>
            <p>What broke and how, looked up in a fixed word list. Never guessed by the model.</p>
            <dl className="cmp-slots">
              {Object.entries(data.slots).map(([k, v]) => (
                <div key={k}>
                  <dt>{k}</dt>
                  <dd>{v ?? "none"}</dd>
                </div>
              ))}
            </dl>
            <p className="cmp-unique">
              <b>Only here:</b> same words, same slots, every time. Later they stop a near miss from getting
              someone else&apos;s answer.
            </p>
            <code>no model · word list</code>
          </div>
          <div className="cmp-step">
            <div className="cmp-top">
              <span className="cmp-n">3</span>
              <h3>Check memory</h3>
            </div>
            <p>Solved before, against this article, in these words or ones that mean the same?</p>
            <ol className="cmp-tiers" aria-label="Memory lookup in this run">
              <li>
                <span>Same words</span>
                <em>no</em>
              </li>
              <li>
                <span>Same meaning ≥ {data.cache.threshold.toFixed(2)}</span>
                <em>no</em>
              </li>
            </ol>
            <p className="cmp-unique">
              <b>Only here:</b> a match also needs the same slots and the same article, or it is not served.
            </p>
            <code>miss · {data.cache.ms} ms</code>
          </div>
        </div>

        <p className="cmp-kicker">
          <span>Nothing stored.</span> <span>So the model gets called.</span> <span className="hot">Twice.</span>
        </p>
      </div>
    </section>
  );
}

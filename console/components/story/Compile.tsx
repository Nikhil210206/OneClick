"use client";

import { Fragment, useRef, type ReactNode } from "react";
import { MEDIA, gsap, useGSAP } from "@/lib/gsap";
import type { StoryData } from "@/lib/story";

/**
 * Compile & check: the response itself, in the organisers' schema, beside the graded rules it
 * passes. The rule results are computed at build time by lib/checks.ts, a mirror of the gate
 * replica's checker, so the ticks on screen are the same verdicts the eval gates would give.
 */

const MORE = Symbol("more");
type Json = string | number | boolean | null | Json[] | { [k: string]: Json } | { [MORE]: string };

interface Line {
  depth: number;
  body: ReactNode;
}

const k = (s: string) => <span className="t-key">&quot;{s}&quot;</span>;
const p = (s: string) => <span className="t-pun">{s}</span>;
const v = (x: string | number | boolean | null) =>
  typeof x === "string" ? (
    <span className={x.startsWith("bixby://") ? "t-uri" : "t-str"}>&quot;{x}&quot;</span>
  ) : (
    <span className="t-num">{String(x)}</span>
  );

/** Pretty-print JSON as lines, so each line can arrive on its own as the reader scrolls. */
function lines(value: Json, depth = 0, key?: string, last = true, out: Line[] = []): Line[] {
  const head = key !== undefined ? (
    <>
        {k(key)}
        {p(": ")}
    </>
  ) : null;
  const tail = last ? "" : ",";
  if (value && typeof value === "object" && MORE in value) {
    out.push({ depth, body: <span className="t-more">{`// ${(value as { [MORE]: string })[MORE]}`}</span> });
    return out;
  }
  if (Array.isArray(value) || (value && typeof value === "object")) {
    const entries: [string | undefined, Json][] = Array.isArray(value)
      ? value.map((x) => [undefined, x])
      : Object.entries(value as Record<string, Json>);
    const [open, close] = Array.isArray(value) ? ["[", "]"] : ["{", "}"];
    if (entries.length === 0) {
      out.push({ depth, body: <>{head}{p(open + close + tail)}</> });
      return out;
    }
    out.push({ depth, body: <>{head}{p(open)}</> });
    entries.forEach(([ck, cv], i) => lines(cv, depth + 1, ck, i === entries.length - 1, out));
    out.push({ depth, body: p(close + tail) });
    return out;
  }
  out.push({ depth, body: <>{head}{v(value as string | number | boolean | null)}{p(tail)}</> });
  return out;
}

export function Compile({ data }: { data: StoryData }) {
  const root = useRef<HTMLElement>(null);
  const rules = useRef<HTMLElement>(null);
  const { compile, multiIntent } = data;
  const ctx = compile.body.contexts[0];
  const shown = 2;

  // The first two actions in full, then a count: enough to read every field, short enough to scroll.
  const excerpt: Json = {
    contexts: [
      {
        goal: ctx.goal,
        title: ctx.title,
        score: ctx.score,
        actions: [
          ...(ctx.actions.slice(0, shown) as unknown as Json[]),
          { [MORE]: `${ctx.actions.length - shown} more actions, built by the same rules` },
        ],
      },
    ],
  };
  const body = lines(excerpt);

  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      mm.add("(prefers-reduced-motion: no-preference)", () => {
        const $ = gsap.utils.selector(root);
        gsap.from($(".cp-head > *"), {
          y: 60,
          autoAlpha: 0,
          stagger: 0.12,
          duration: 1.2,
          ease: "expo.out",
          scrollTrigger: { trigger: $(".cp-head")[0], start: "top 80%" },
        });
        gsap.from($(".cp-check"), {
          x: 60,
          autoAlpha: 0,
          stagger: 0.12,
          duration: 0.9,
          ease: "expo.out",
          scrollTrigger: { trigger: $(".cp-checks")[0], start: "top 75%" },
        });
        gsap.fromTo(
          $(".cp-tick path"),
          { strokeDasharray: 24, strokeDashoffset: 24 },
          {
            strokeDashoffset: 0,
            stagger: 0.12,
            duration: 0.5,
            delay: 0.4,
            scrollTrigger: { trigger: $(".cp-checks")[0], start: "top 75%" },
          },
        );
      });

      // Desktop: the section holds still while the full response scrolls through its window with the
      // page, top to bottom. Every line is always shown, so the window is never part empty.
      mm.add(MEDIA.wide, () => {
        const $ = gsap.utils.selector(root);
        const code = $(".cp-code")[0] as HTMLElement;
        const roll = $(".cp-roll")[0] as HTMLElement;
        const room = () => code.clientHeight - parseFloat(getComputedStyle(code).paddingTop);
        gsap
          .timeline({
            defaults: { ease: "none" },
            scrollTrigger: {
              trigger: root.current,
              start: "top top",
              end: "+=110%",
              pin: true,
              scrub: 0.6,
              invalidateOnRefresh: true,
            },
          })
          .to({}, { duration: 0.15 })
          .fromTo(roll, { y: 0 }, { y: () => -Math.max(0, roll.offsetHeight - room() + 30), duration: 1 })
          .to({}, { duration: 0.2 });
      });
      // Narrow screens: the whole response is on the page; it types out as it scrolls past.
      mm.add(MEDIA.narrow, () => {
        const $ = gsap.utils.selector(root);
        gsap.fromTo(
          $(".cp-ln"),
          { opacity: 0, x: -8 },
          {
            opacity: 1,
            x: 0,
            stagger: 0.05,
            ease: "none",
            scrollTrigger: { trigger: $(".cp-json")[0], start: "top 75%", end: "bottom 55%", scrub: true },
          },
        );
      });
    },
    { scope: root },
  );

  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      mm.add("(prefers-reduced-motion: no-preference)", () => {
        const $ = gsap.utils.selector(rules);
        gsap.from($(".cp-head > *"), {
          y: 60,
          autoAlpha: 0,
          stagger: 0.12,
          duration: 1.2,
          ease: "expo.out",
          scrollTrigger: { trigger: $(".cp-head")[0], start: "top 80%" },
        });
        gsap.from($(".cp-card"), {
          y: 90,
          rotate: (i: number) => (i % 2 ? -2 : 2),
          autoAlpha: 0,
          stagger: 0.1,
          duration: 1.2,
          ease: "expo.out",
          scrollTrigger: { trigger: $(".cp-cards")[0], start: "top 80%" },
        });
      });
    },
    { scope: rules },
  );

  return (
    <>
    <section className="cp fit" id="compile" data-nav="dark" ref={root}>
      <div className="cp-inner">
        <header className="cp-head">
          <div>
            <p className="st-eyebrow">06 · Compile &amp; check</p>
            <h2 className="st-h2">
              Built by rule.
              <br />
              Checked before it leaves.
            </h2>
          </div>
          <p className="st-lead">
            The phone is one view of it. This is the answer itself, in the organisers&apos; official schema. The
            wording the judges grade is built by rule rather than written freely by the model, then checked,
            repaired once if it has to be, and never sent with a link in it.
          </p>
        </header>

        <div className="cp-grid">
          <div className="cp-json">
            <div className="cp-bar">
              <span className="win-label">Response</span>
              <code>ContextDeeplinkResponse</code>
              <span className="cp-flags">
                <span className={compile.schemaValid ? "ok" : "bad"}>
                  schema {compile.schemaValid ? "valid" : "invalid"}
                </span>
                <span>
                  {compile.repairs} repair{compile.repairs === 1 ? "" : "s"}
                </span>
                <span>{compile.droppedActions} dropped</span>
              </span>
            </div>
            <div className="cp-code">
              <div className="cp-roll">
                {body.map((l, i) => (
                  <div className="cp-ln" style={{ paddingLeft: `${l.depth * 1.4}em` }} key={i}>
                    {l.body}
                  </div>
                ))}
              </div>
            </div>
          </div>

          <ol className="cp-checks">
            {compile.checks.map((c) => {
              const ok = c.pass === c.total;
              return (
                <li className={`cp-check${ok ? "" : " cp-check-bad"}`} key={c.rule}>
                  <span className="cp-tick">
                    <svg viewBox="0 0 24 24" aria-hidden>
                      {ok ? (
                        <path d="m6 12.5 4 4 8-9" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
                      ) : (
                        <path d="M7 7l10 10M17 7 7 17" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
                      )}
                    </svg>
                  </span>
                  <span className="cp-rule">
                    <b>{c.rule}</b>
                    <small>{c.detail}</small>
                  </span>
                  <span className="cp-count">
                    {c.absence ? (ok ? "none found" : "found") : `${c.pass}/${c.total}`}
                  </span>
                </li>
              );
            })}
            <li className="cp-check cp-score">
              <span className="cp-rule">
                <b>Score {ctx.score.toFixed(2)}</b>
                <small>{compile.formula.replaceAll("*", " × ").replaceAll("_", " ")}</small>
              </span>
            </li>
          </ol>
        </div>
        <p className="st-fine cp-fine">
          Checked at build time with the same rules as the eval gate replica, on the recorded run.
        </p>
      </div>
    </section>

    <section className="cp cp-rules fit" data-nav="dark" ref={rules}>
      <div className="cp-inner">
        <header className="cp-head">
          <div>
            <p className="st-eyebrow">06 · Compile &amp; check</p>
            <h2 className="st-h2">Built not to break.</h2>
          </div>
          <p className="st-lead">
            Two problems in one message get two plans. A failure inside never becomes an error outside, and no
            link survives the trip.
          </p>
        </header>

        <div className="cp-cards">
          <article className="cp-card cp-card-multi">
            <span className="tag tag-blue">Multi-intent</span>
            <h3>Two problems, one message</h3>
            <blockquote>&ldquo;{multiIntent.query}&rdquo;</blockquote>
            <div className="cp-goals">
              {multiIntent.goals.map((g) => (
                <span className="cp-goal" key={g.title}>
                  <b>{g.title}</b>
                  <small>
                    {g.actions} {g.actions === 1 ? "action" : "actions"} · score {g.score.toFixed(2)}
                  </small>
                </span>
              ))}
            </div>
            <p>
              {multiIntent.deduped.map((d, i) => (
                <Fragment key={d.action}>
                  {i > 0 && " "}
                  <b>{d.action}</b> stays under {d.keptIn} only.
                </Fragment>
              ))}{" "}
              An action is never repeated across goals.
            </p>
          </article>
          <article className="cp-card cp-card-dark">
            <span className="cp-big">200</span>
            <h3>Always answers</h3>
            <p>
              Whatever fails inside, the endpoint returns 200 with a valid body. If nothing survives grounding, the
              answer has no steps and says <code>no_match</code>. An empty answer beats an invented one.
            </p>
          </article>
          <article className="cp-card cp-card-lime">
            <span className="cp-big">{data.llm.preferDeadline} s</span>
            <h3>Two models, one race</h3>
            <p>
              Open-weight Ministral 14B and 8B read the same prompt at once. The 14B answer wins if it is back
              within {data.llm.preferDeadline} seconds, otherwise the 8B answer is used, and the stage gives up at{" "}
              {data.llm.budget} s so a new question fits the 8 s limit
              {data.proof.metrics.latency.coldP95 !== null &&
                ` (measured: ${(data.proof.metrics.latency.coldP95 / 1000).toFixed(1)} s at p95)`}
              . With both models down, the article&apos;s own instructions still answer.
            </p>
          </article>
          <article className="cp-card cp-card-blue">
            <span className="cp-big">2×</span>
            <h3>Scrubbed twice</h3>
            <p>
              Links and email addresses leave the article before any model reads it, and the answer is scrubbed
              again on the way out, cache hits included.
            </p>
          </article>
        </div>
        <p className="st-fine cp-fine">
          The last three are rules in the engine&apos;s design. The eval gate replica already enforces always-200,
          the schema and zero URLs on every call it makes.
        </p>
      </div>
    </section>
    </>
  );
}

"use client";

import { useRef } from "react";
import { MEDIA, gsap, useGSAP } from "@/lib/gsap";
import type { StoryData } from "@/lib/story";

/**
 * Inside the model: what call B is sent, and what it sends back.
 *
 * Left, the prompt as a document: the complaint, the one rule that makes the model point instead of
 * write, and the article cut into numbered sentences. Right, the answer: the race between the two
 * Ministral models, then the actions the winner chose, each with the sentence ids it cited, which
 * light up in the article as they arrive. Last come the fixes the engine added after the answer,
 * in their own colour, because the model did not choose them.
 *
 * Three chapters on one pinned screen: it reads, it points, the gaps are filled. The rule is quoted
 * from api/app/llm/prompts/ at build time; models, times and tokens are the recorded run's own.
 */

/** "ministral-14b-latest" -> "Ministral 14B", "gemini-3.1-flash-lite" -> "Gemini 3.1 Flash-Lite". */
function modelName(id: string): string {
  const ministral = /^ministral-(\d+)b/i.exec(id);
  if (ministral) return `Ministral ${ministral[1]}B`;
  const gemini = /^gemini-([\d.]+)-(.+?)(?:-preview)?$/i.exec(id);
  if (gemini) {
    const kind = gemini[2]
      .split("-")
      .map((w) => w[0].toUpperCase() + w.slice(1))
      .join("-");
    return `Gemini ${gemini[1]} ${kind}`;
  }
  return id;
}

/** The prompt's markdown bold, as bold. */
const bold = (s: string) => s.split("**").map((part, i) => (i % 2 ? <b key={i}>{part}</b> : part));

/** Runs of three or more consecutive ids read as a range: S48–S54. */
function idChips(ids: string[]): string[] {
  const out: string[] = [];
  for (let i = 0; i < ids.length; ) {
    let j = i;
    const n = (k: number) => Number(ids[k].slice(1));
    while (j + 1 < ids.length && n(j + 1) === n(j) + 1) j++;
    if (j - i >= 2) out.push(`${ids[i]}–${ids[j]}`);
    else for (let k = i; k <= j; k++) out.push(ids[k]);
    i = j + 1;
  }
  return out;
}

const seconds = (ms: number) => `${(ms / 1000).toFixed(1)} s`;

export function Prompt({ data }: { data: StoryData }) {
  const root = useRef<HTMLElement>(null);
  const { article, llm, model, extract, enrich } = data;

  // Who cited each sentence: the model's picks win over the engine's additions.
  const by = new Map<string, "engine" | "model">();
  model.added.forEach((a) => a.ids.forEach((id) => by.set(id, "engine")));
  model.picked.forEach((a) => a.ids.forEach((id) => by.set(id, "model")));
  const pickedIds = new Set(model.picked.flatMap((a) => a.ids)).size;

  const sections = article.sections.map((s) => ({
    ...s,
    sentences: article.sentences.filter((x) => x.section === s.heading),
  }));

  const used = model.race.find((r) => r.used);
  const rival = model.race.find((r) => !r.used && r.ok);
  const raceNote = !used
    ? null
    : rival && rival.ms !== null && used.ms !== null && rival.ms < used.ms
      ? `${modelName(rival.model)} was back first. ${modelName(used.model)} made the ${llm.preferDeadline} s cut (dashed), so its answer is used.`
      : `${modelName(used.model)} was back first, so its answer is used.`;
  // The race's axis runs to the moment the stage gives up.
  const axis = llm.budget * 1000;

  const filled =
    model.addedBy === "coverage"
      ? `A second call on ${modelName(model.coverageModel ?? "")} marked the paragraphs that help. The engine added the ${model.added.length} the answer skipped, in the article's own words.`
      : model.addedBy === "procedure"
        ? `The article numbers its fixes, and the answer skipped ${model.added.length} of them. The engine adds them, in the article's own words.`
        : "The answer covered every fix the article numbers.";

  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      mm.add(MEDIA, (ctx) => {
        const wide = Boolean(ctx.conditions?.wide);
        const $ = gsap.utils.selector(root);
        const clip = $(".im-art-clip")[0] as HTMLElement;
        const list = $(".im-art-list")[0] as HTMLElement;
        const rowsClip = $(".im-rows-clip")[0] as HTMLElement;
        const rows = $(".im-rows")[0] as HTMLElement;

        // Centre a cited sentence in the article, and keep the newest answer row in view.
        const centre = (sent: HTMLElement) => () =>
          -gsap.utils.clamp(0, Math.max(0, list.scrollHeight - clip.clientHeight), sent.offsetTop - clip.clientHeight * 0.36);
        const follow = (row: HTMLElement) => () =>
          -Math.max(0, row.offsetTop + row.offsetHeight + 26 - rowsClip.clientHeight);
        // Counters start from zero while the animation owns them; the page's own text is the end value.
        const counters = [...$(".im-tok-in, .im-tok-out, .im-lane:not([data-ms=\"0\"]) .im-time")] as HTMLElement[];
        const finals = counters.map((el) => el.textContent);
        const count = (el: HTMLElement, to: number, format: (v: number) => string) => {
          const o = { v: 0 };
          return gsap.to(o, { v: to, ease: "none", onUpdate: () => void (el.textContent = format(o.v)) });
        };
        const chips = $(".im-ch") as HTMLElement[];
        const chapter = (n: number, at: number) => {
          chips.forEach((el, i) => {
            tl.set(el, { attr: { "data-state": i + 1 < n ? "done" : i + 1 === n ? "on" : "off" } }, at);
          });
          if (wide) tl.to($(".im-cap"), { autoAlpha: (i: number) => (i + 1 === n ? 1 : 0), duration: 0.4 }, at);
        };

        gsap.set($(".im-row, .im-row-sep"), { autoAlpha: 0 });
        gsap.set($(".im-s"), { "--cite": 0 });
        gsap.set($(".im-bar"), { scaleX: 0 });
        gsap.set($(".im-verdict, .im-race-note"), { autoAlpha: 0 });
        gsap.set($(".im-wait"), { autoAlpha: 1 });
        chips.forEach((el, i) => gsap.set(el, { attr: { "data-state": i === 0 ? "on" : "off" } }));
        if (wide) gsap.set($(".im-cap"), { autoAlpha: (i: number) => (i === 0 ? 1 : 0) });
        counters.forEach((el) => {
          el.textContent = el.classList.contains("im-time") ? seconds(0) : "0";
        });

        const tl = gsap.timeline({
          defaults: { ease: "power2.out", duration: 0.45 },
          scrollTrigger: wide
            ? {
                trigger: root.current,
                start: "top top",
                end: () => `+=${window.innerHeight * 3.4}`,
                pin: ".llm-pin",
                scrub: 0.8,
                invalidateOnRefresh: true,
              }
            : { trigger: root.current, start: "top 55%" },
        });
        if (!wide) tl.timeScale(2.4);

        // The cards arrive as the section scrolls in, so the pin never opens on an empty frame.
        gsap.from($(".im-doc, .im-model"), {
          y: 80,
          autoAlpha: 0,
          stagger: 0.14,
          duration: 1.1,
          ease: "expo.out",
          scrollTrigger: { trigger: root.current, start: "top 70%", toggleActions: "play none none reverse" },
        });

        // ── 1 · it reads ─────────────────────────────────────────────────────
        chapter(1, 0);
        tl.add(count($(".im-tok-in")[0], extract.tokensIn, (v) => Math.round(v).toLocaleString("en-US")).duration(1), 0.2)
          // It reads the whole article, top to bottom, before it answers.
          .fromTo(list, { y: 0 }, { y: () => -Math.max(0, list.scrollHeight - clip.clientHeight), duration: 2.6, ease: "power1.inOut" }, 0.3)
          .to(list, { y: 0, duration: 0.8, ease: "power2.inOut" }, ">");
        // Both models run at once; each bar grows at the same rate until its answer is back.
        $(".im-lane").forEach((lane) => {
          const ms = Number(lane.dataset.ms);
          if (!ms) return;
          const d = 3 * (ms / axis);
          tl.fromTo(
            lane.querySelector(".im-bar"),
            { scaleX: 0 },
            { scaleX: 1, duration: d, ease: "none", immediateRender: false },
            0.4,
          ).add(
            count(lane.querySelector(".im-time") as HTMLElement, ms, (v) => seconds(v)).duration(d),
            0.4,
          );
        });
        tl.to($(".im-verdict"), { autoAlpha: 1, stagger: 0.1 }, 3.5).to($(".im-race-note"), { autoAlpha: 1 }, 3.7);

        // ── 2 · it points ────────────────────────────────────────────────────
        const picks = $(".im-row-model");
        const b = 4.3;
        chapter(2, b);
        tl.to($(".im-wait"), { autoAlpha: 0, duration: 0.3 }, b);
        picks.forEach((row, i) => {
          const at = b + 0.3 + i * 0.45;
          tl.fromTo(row, { autoAlpha: 0, x: -14 }, { autoAlpha: 1, x: 0, duration: 0.35 }, at).to(rows, { y: follow(row), duration: 0.3 }, at);
          const sents = (row.dataset.ids ?? "")
            .split(" ")
            .map((id) => $(`.im-s[data-id="${id}"]`)[0] as HTMLElement | undefined)
            .filter((x): x is HTMLElement => Boolean(x));
          if (sents.length) {
            tl.to(list, { y: centre(sents[0]), duration: 0.4, ease: "power2.inOut" }, at).fromTo(
              sents,
              { "--cite": 0 },
              { "--cite": 1, duration: 0.3, stagger: 0.04, immediateRender: false },
              at + 0.15,
            );
          }
        });
        const bEnd = b + 0.3 + picks.length * 0.45;
        tl.add(count($(".im-tok-out")[0], extract.tokensOut, (v) => Math.round(v).toLocaleString("en-US")).duration(bEnd - b), b);

        // ── 3 · the gaps are filled ──────────────────────────────────────────
        const c = bEnd + 0.4;
        chapter(3, c);
        tl.fromTo($(".im-row-sep"), { autoAlpha: 0 }, { autoAlpha: 1 }, c + 0.2);
        $(".im-row-added").forEach((row, i) => {
          const at = c + 0.4 + i * 0.5;
          tl.fromTo(row, { autoAlpha: 0, x: -14 }, { autoAlpha: 1, x: 0, duration: 0.35 }, at).to(rows, { y: follow(row), duration: 0.3 }, at);
          const sents = (row.dataset.ids ?? "")
            .split(" ")
            .map((id) => $(`.im-s[data-id="${id}"]`)[0] as HTMLElement | undefined)
            .filter((x): x is HTMLElement => Boolean(x));
          if (sents.length) {
            tl.to(list, { y: centre(sents[0]), duration: 0.4, ease: "power2.inOut" }, at).fromTo(
              sents,
              { "--cite": 0 },
              { "--cite": 1, duration: 0.3, stagger: 0.03, immediateRender: false },
              at + 0.15,
            );
          }
        });
        tl.to({}, { duration: 1.2 });

        return () => {
          counters.forEach((el, i) => {
            el.textContent = finals[i];
          });
          chips.forEach((el) => el.setAttribute("data-state", "done"));
        };
      });
    },
    { scope: root },
  );

  return (
    <section className="llm" id="prompt" data-nav="light" ref={root}>
      <div className="llm-pin">
        <header className="im-head">
          <div>
            <p className="st-eyebrow">02 · Inside the model</p>
            <h2 className="st-h2 im-title">What the model sees.</h2>
          </div>
          <ol className="im-chapters" aria-label="Chapters">
            <li className="im-ch" data-state="done">
              <span>1</span>It reads
            </li>
            <li className="im-ch" data-state="done">
              <span>2</span>It points
            </li>
            <li className="im-ch" data-state="done">
              <span>3</span>Gaps filled
            </li>
          </ol>
        </header>

        <div className="im-stage">
          <article className="im-doc" aria-label="What the model is sent">
            <header className="im-card-head">
              <span className="im-label">What it is sent</span>
              <small>
                {data.prompts.extract.file} · <b className="im-tok-in">{extract.tokensIn.toLocaleString("en-US")}</b>{" "}
                tokens
              </small>
            </header>
            <div className="im-part">
              <span className="im-part-label">The complaint</span>
              <p className="im-complaint">&ldquo;{data.query}&rdquo;</p>
            </div>
            {model.rule && (
              <div className="im-part im-rule">
                <span className="im-part-label">The rule</span>
                <p>{bold(model.rule)}</p>
              </div>
            )}
            <div className="im-art">
              <div className="im-art-head">
                <span className="im-part-label">The article</span>
                <small>
                  <b>{article.sentences.length}</b> numbered sentences
                </small>
              </div>
              <div className="im-art-clip">
                <div className="im-art-list">
                  {sections.map((s) => (
                    <div className="im-sec" key={s.id}>
                      <h4>{s.heading}</h4>
                      {s.sentences.map((x) => (
                        <p className="im-s" data-id={x.id} data-by={by.get(x.id)} key={x.id}>
                          <span className="im-sid">{x.id}</span>
                          <span>{x.text}</span>
                        </p>
                      ))}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </article>

          <div className="im-model" aria-label="What the model answers">
            <header className="im-card-head">
              <span className="im-label">What it answers</span>
              <small>
                <b className="im-tok-out">{extract.tokensOut.toLocaleString("en-US")}</b> tokens
              </small>
            </header>

            {model.race.length > 0 && (
              <div className="im-race">
                {model.race.map((r) => (
                  <div className={`im-lane${r.used ? " im-lane-used" : ""}`} data-ms={r.ms ?? 0} key={r.model}>
                    <b title={r.model}>{modelName(r.model)}</b>
                    <span className="im-track">
                      {r.ms !== null && (
                        <i className="im-bar" style={{ width: `${Math.min(100, (r.ms / axis) * 100)}%` }} />
                      )}
                      <i
                        className="im-cut"
                        style={{ left: `${Math.min(100, ((llm.preferDeadline * 1000) / axis) * 100)}%` }}
                      />
                    </span>
                    <span className="im-time">{r.ms === null ? "failed" : seconds(r.ms)}</span>
                    <span className={`im-verdict${r.used ? " im-verdict-used" : ""}`}>
                      {r.used ? "used" : r.ok ? "not needed" : "failed"}
                    </span>
                  </div>
                ))}
                {raceNote && <p className="im-race-note">{raceNote}</p>}
              </div>
            )}

            <div className="im-rows-clip">
              <p className="im-wait" aria-hidden>
                <i />
                Both models are reading the article
              </p>
              <ol className="im-rows">
                {model.picked.map((a, i) => (
                  <li className="im-row im-row-model" data-ids={a.ids.join(" ")} key={a.name}>
                    <span className="im-row-n">{i + 1}</span>
                    <b>{a.name}</b>
                    <span className="im-ids">
                      {idChips(a.ids).map((id) => (
                        <i key={id}>{id}</i>
                      ))}
                    </span>
                  </li>
                ))}
                {model.added.length > 0 && (
                  <li className="im-row-sep">
                    {model.addedBy === "coverage" ? "Added from the coverage call" : "Added by the engine"}
                  </li>
                )}
                {model.added.map((a) => (
                  <li className="im-row im-row-added" data-ids={a.ids.join(" ")} key={a.name}>
                    <span className="im-row-n">+</span>
                    <b>{a.name}</b>
                    <span className="im-ids">
                      {idChips(a.ids).map((id) => (
                        <i key={id}>{id}</i>
                      ))}
                    </span>
                  </li>
                ))}
              </ol>
            </div>

            <footer className="im-card-foot">
              {data.recording.model} · temperature {llm.temperature} ·{" "}
              {llm.fallback ? `fallback ${llm.fallback}` : "no fallback model"}
            </footer>
          </div>
        </div>

        <div className="im-foot">
          <ol className="im-captions">
            <li className="im-cap">
              <span>1</span>
              <p>
                The model is sent the complaint, one rule, and the article cut into{" "}
                <em>{article.sentences.length} numbered sentences</em>.
              </p>
            </li>
            <li className="im-cap">
              <span>2</span>
              <p>
                It answers with sentence numbers, never its own words: <em>{model.picked.length} fixes</em> from{" "}
                {pickedIds} sentences. Every step you see later is one of them, word for word.
              </p>
            </li>
            <li className="im-cap">
              <span>3</span>
              <p>
                {filled} <em>Now we check the model&apos;s homework.</em>
              </p>
            </li>
          </ol>
          <p className="im-bg">
            In the background, never waited for: {modelName(enrich.model)} rewrote the complaint for the cache,{" "}
            {enrich.kept.length} rewordings kept. Times and token counts are this recorded run&apos;s own.
          </p>
        </div>
      </div>
    </section>
  );
}

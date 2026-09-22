"use client";

import { useRef } from "react";
import { Galaxy } from "@/components/story/Galaxy";
import { AskScreen } from "@/components/story/Screens";
import { ScrollSmoother, SplitText, gsap, useGSAP } from "@/lib/gsap";
import type { StoryData } from "@/lib/story";

export function stepCount(data: StoryData) {
  return data.plan.actions.reduce((n, a) => n + a.stepGroups.reduce((m, g) => m + g.steps.length, 0), 0);
}

export function Hero({ data }: { data: StoryData }) {
  const root = useRef<HTMLElement>(null);
  const steps = stepCount(data);
  const links = data.resolve.counts.catalog;

  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      mm.add("(prefers-reduced-motion: no-preference)", () => {
        const title = SplitText.create(".hero-title", { type: "lines,words", mask: "lines" });
        const typed = SplitText.create(".ask-typed", { type: "words,chars" });

        gsap.set("[data-intro]", { autoAlpha: 1 });
        gsap.set([".ask-bubble", ".ask-status", ".ask-result", ".ask-row"], { autoAlpha: 0 });
        gsap.set(typed.chars, { autoAlpha: 0 });

        const tl = gsap.timeline({ defaults: { ease: "expo.out" }, delay: 0.15 });
        tl.from(".hero-card", { clipPath: "inset(14% 6% 0% 6% round 64px)", y: 80, duration: 1.5 })
          .from(".hero-rings circle", { scale: 0.4, autoAlpha: 0, transformOrigin: "50% 50%", stagger: 0.08, duration: 2 }, 0.2)
          .from(".hero-eyebrow", { y: 24, autoAlpha: 0, duration: 1 }, 0.45)
          .from(title.words, { yPercent: 115, duration: 1.3, stagger: 0.07 }, 0.5)
          .from([".hero-lead", ".hero-cta"], { y: 30, autoAlpha: 0, duration: 1.1, stagger: 0.1 }, 0.9)
          .fromTo(
            ".hero-tilt",
            { y: 380, rotateX: 34, rotateY: -46, rotateZ: 16, autoAlpha: 0 },
            { y: 0, rotateX: 10, rotateY: -22, rotateZ: 6, autoAlpha: 1, duration: 2, ease: "expo.out" },
            0.35,
          )
          .from(".hero-shadow", { scale: 0.3, autoAlpha: 0, duration: 1.8 }, 0.6)
          .from(".gx-pen", { rotate: -38, y: -60, autoAlpha: 0, duration: 1.5, ease: "back.out(1.3)" }, 1.1)
          // Unlock: the lock screen slides away and the app is underneath.
          .to(".ask-lock", { yPercent: -100, duration: 0.8, ease: "power3.inOut" }, 1.7)
          .to(".hero .gx-status", { color: "#111216", duration: 0.4 }, 1.9)
          // On the phone: the complaint is typed, the article is read, the fix lands.
          .to(".ask-bubble", { autoAlpha: 1, duration: 0.4 }, 2.4)
          .to(typed.chars, { autoAlpha: 1, duration: 0.01, stagger: 0.011, ease: "none" }, 2.5)
          .to(".ask-status", { autoAlpha: 1, duration: 0.4 }, ">+0.15")
          .fromTo(".ask-spark", { scale: 0.6 }, { scale: 1.25, repeat: 3, yoyo: true, duration: 0.35, ease: "sine.inOut" }, "<")
          .to(".ask-status", { autoAlpha: 0.45, duration: 0.4 }, ">")
          .fromTo(".ask-result", { y: 40, autoAlpha: 0 }, { y: 0, autoAlpha: 1, duration: 0.9 }, "<")
          .fromTo(".ask-row", { y: 16, autoAlpha: 0 }, { y: 0, autoAlpha: 1, duration: 0.6, stagger: 0.12 }, "<+0.2");

        // Idle drift, so the device never looks pasted on.
        gsap.to(".hero-float", { y: -14, rotate: 0.6, duration: 3.2, ease: "sine.inOut", yoyo: true, repeat: -1 });

        // Leaving the hero: the phone turns to face the reader and the copy lifts away.
        gsap
          .timeline({ scrollTrigger: { trigger: root.current, start: "top top", end: "bottom top", scrub: true } })
          .to(".hero-phone", { rotateY: 12, rotateX: -6, y: 120, ease: "none" }, 0)
          .to(".hero-copy", { y: -120, opacity: 0.2, ease: "none" }, 0)
          .to(".hero-card", { scale: 0.94, borderRadius: "80px", ease: "none" }, 0);

        return () => {
          title.revert();
          typed.revert();
        };
      });
    },
    { scope: root },
  );

  const toPrompt = (e: React.MouseEvent) => {
    const smoother = ScrollSmoother.get();
    if (!smoother) return;
    e.preventDefault();
    smoother.scrollTo("#prompt", true, "top top");
  };

  return (
    <section className="hero" id="top" data-nav="dark" ref={root}>
      <div className="hero-card" data-intro>
        <svg className="hero-rings" viewBox="0 0 800 800" aria-hidden>
          {[120, 210, 300, 390].map((r) => (
            <circle key={r} cx="400" cy="400" r={r} />
          ))}
        </svg>
        <div className="hero-copy">
          <p className="st-eyebrow hero-eyebrow">Guided troubleshooting for Galaxy</p>
          <h1 className="st-h1 hero-title">
            Fixed in
            <br />
            one click.
          </h1>
          <p className="st-lead hero-lead">
            Describe the problem the way you would tell a friend. OneClick reads Samsung&apos;s own support
            article, keeps only the steps it can trace back to it, and opens the exact Settings screen for
            you.
          </p>
          <div className="hero-cta">
            <a className="pill pill-lime" href="#prompt" onClick={toPrompt}>
              See how it thinks
              <svg viewBox="0 0 24 24" aria-hidden>
                <path d="M12 5v14m0 0-6-6m6 6 6-6" fill="none" stroke="currentColor" strokeWidth="2.2" />
              </svg>
            </a>
            <span className="hero-facts">
              <b>{data.plan.actions.length}</b> actions <i /> <b>{steps}</b> grounded steps <i /> <b>{links}</b>{" "}
              one-tap links
            </span>
          </div>
        </div>
      </div>

      <div className="hero-phone" data-intro>
        <div className="hero-float">
          <div className="hero-tilt">
            <Galaxy pen>
              <AskScreen query={data.query} articleTitle={data.articleTitle} plan={data.plan} steps={steps} />
            </Galaxy>
          </div>
        </div>
        <span className="hero-shadow" />
      </div>
    </section>
  );
}

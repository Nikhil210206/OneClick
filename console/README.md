# OneClick Console

Next.js 16 (App Router, Turbopack) + React 19 + Tailwind v4 + TypeScript + GSAP 3.

```bash
cd console
npm install
npm run dev          # http://localhost:3000
npm run lint         # eslint
npm run typecheck    # tsc --noEmit
npm run build
```

One page, `/`. It tells one recorded request section by section as the reader scrolls (the
complaint, both LLM calls with prompt in and response out, grounding with the step that gets thrown
out, the resolver, the phone opening Settings, the compiled response and its checks, the cache), then
hands over to **Try it live**, which streams the real API, and ends on what is measured today.

The live section calls `POST /v1/troubleshoot/stream` at `NEXT_PUBLIC_API_URL` (default
`http://127.0.0.1:8000`, not `localhost`: uvicorn binds IPv4 only and browsers may try IPv6 first).
While the engine is a skeleton the API serves its mock replay; every frame then carries
`detail.mock` and the section shows a "Mock replay" badge instead of passing a replay off as live.
Its presets are real requests from `data/fixtures/` and `eval/sets/`.

```bash
cd api && uvicorn app.main:app      # in one terminal
cd console && npm run dev           # in another
```

## How it is wired today

The walkthrough sections replay a captured run rather than a live one. They import [data/fixtures/touch_lag/](../data/fixtures/README.md) directly through the
`@fixtures/*` alias rather than keeping a copy, so what renders here is the same file
[api/tests/test_fixtures.py](../api/tests/test_fixtures.py) guards. Change a fixture and the
console changes with it.

That import lives above the app folder, which Turbopack will not resolve by default, so
`next.config.ts` widens `turbopack.root` to the repo. Keep that if you move things around.

## The story page

`app/page.tsx` is a server component. At build time it reads the LLM prompt files from
`api/app/llm/prompts/` (the highest `*.vN.md` of each) and the eval sets, then hands plain JSON to
`components/story/Story.tsx`. So the prompt panel always shows exactly what the engine sends; while
a prompt file is still a `TODO` stub the panel says "not written yet" rather than inventing text.
Every other number comes from the fixtures (`lib/story.ts`) or is counted from the eval sets
(`lib/story.server.ts`). The fixture README marks timings, token counts, scores and the dropped step
as illustrative, and the page says so wherever they appear.

Motion is GSAP: ScrollSmoother for the scroll, ScrollTrigger pins for the model, grounding and phone
sections, SplitText for the hero. Three traps cost time here, so avoid them:

- **Never put a CSS `transition` on `transform` or `opacity` of anything GSAP animates.** React dev
  mode mounts effects twice; GSAP reverts between the two, the CSS transition starts easing back,
  and the second run reads the half-finished value as the element's resting state.
- **Use `opacity`, not `autoAlpha`, for `.to()` tweens inside a scrubbed timeline** whose element
  sits in a parent hidden by an entrance animation. `autoAlpha` records a start of 0 from any
  element whose parent is `visibility: hidden`, so the element never comes back.
- **A hidden browser tab or pane pauses `requestAnimationFrame`,** which stops GSAP's clock and
  scroll events. Frozen animations in a background tab are not a bug in the page.

## Layout

| Path | What it is |
| --- | --- |
| `lib/trace.ts` | Types for the SSE stage events, plus `stageDurations` / `totalMs`. |
| `lib/plan.ts` | The official response shape (`ContextDeeplinkResponse`), as far as the page reads it. |
| `lib/checks.ts` | The graded output rules, a TypeScript mirror of `eval/evalkit/checks.py`. Keep them in step. |
| `lib/stream.ts` | The SSE client for the live section. |
| `app/story.css` | The story page's palette, type and every section's layout. |
| `lib/story.ts`, `lib/story.server.ts` | Story data: derived from the fixtures, plus build-time reads of the prompt files and eval sets. |
| `lib/gsap.ts` | GSAP with its plugins registered once, and the two breakpoints every section animates for. |
| `components/story/` | One component per story section, plus `Galaxy` (a CSS handset) and its One UI `Screens`. |

## Rules this UI follows

- **One colour, one meaning.** Accent is "the engine decided", green is *proven* and only that
  (grounded, verified, gate passed), amber is disruptive, red is refused. The story page uses a
  Samsung palette: lime is the punch colour (proven things and calls to action), pink is only ever
  time inside an LLM call, red is only ever refused. If a colour
  ever means two things, the demo is lying.
- **Every number on screen is real.** The timings, scores, coverage and counts come from the
  fixture, never from a literal typed into a component. Judges see these same numbers in the video.
- **`ms` on a stage event is that stage's own duration**, not elapsed time since the request
  started; `done.ms` is the total. Differencing consecutive events is wrong.
- **The dropped step is not collapsible.** A step the model proposed and the engine refused is the
  strongest claim the product makes, so it is always on screen.
- **Every number says who measured it.** Resolver and cache figures from the mapping lane are
  labelled as such, and anything that needs the whole engine running says pending.

## Recording

Built for 1440×900 and checked at 1920×1080. `devIndicators` is off so the Next badge never
appears in a capture, and pane scrollbars are hidden for the same reason. Motion respects `prefers-reduced-motion`: the replay
skips its staging and every final value still renders.

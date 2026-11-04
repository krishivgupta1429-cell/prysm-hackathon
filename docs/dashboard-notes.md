# Dashboard (/dashboard): hackathon vs the main platform

Built during the Meta Global AI Developer Hackathon (new work). Written from the baseline code (`git show pre-hackathon-baseline:web/src/pages/Dashboard.tsx` and `backend/app/routers/prysm.py`), then checked in a browser with a seeded local account.

**Finding: nothing needed adding.** The hackathon dashboard is the baseline `Dashboard.tsx` plus a handful of demo-mode edits. The coach card, "Your best vs latest" and "Your score over time" are all in the baseline and all still there. They were missing in the earlier screenshots only because the test account had one video. No code on the dashboard changed in this task.

## Every difference between the baseline Dashboard and the hackathon Dashboard

The whole diff is 24 lines in `web/src/pages/Dashboard.tsx` (plus `lib/plans.ts`, `lib/config.ts`, `routers/prysm.py`). Each one is deliberate:

| # | Difference | Why it exists |
|---|---|---|
| 1 | The credits meter (and its "0 credits - Upgrade" pill and "Upgrade plan" link) is hidden when `VITE_STUDIO_MODE=1` | Demo has no credits, pricing or upgrade prompts |
| 2 | The "Paste a link" card, the "Reel Breakdowns" tab and the "or paste a link" wording are hidden unless `VITE_STUDIO_ALLOW_LINKS=1` (the grid goes from 3 cards to 2) | The links switch (see `docs/link-flow-notes.md`) |
| 3 | The mailto on the feedback button uses `VITE_SUPPORT_EMAIL` instead of a hard-coded address | No personal address in a public build |
| 4 | `hasPlan()` returns true in demo mode, so every plan-gated block is open | The demo has no paid tiers. This is why the full dashboard (coach, comparison, chart, strengths) is unlocked for every account |
| 5 | `GET /prysm/dashboard` reports plan and credits through `unlimited_view` (shows the top plan when `STUDIO_UNLIMITED` is on) | Same reason as 4; the rest of that endpoint is unchanged |

Everything else on the page (hero, trend line, chips, average-score ring with "up N from last", the three cards, the Prysm coach card, best vs latest, tabs, rows, "Your score over time", "Strengths & Weaknesses", "Keep working on") is the baseline code, untouched.

## Why each piece was "missing"

| Piece | Cause | Rule in the baseline code |
|---|---|---|
| Prysm coach card | (a) minimum count | needs at least **3 own videos** (and a plan with the full dashboard, which demo mode always grants) |
| Your best vs latest | (a) minimum count | shown only when the best video and the latest video are **different** videos, so it needs 2 or more and the best is not the newest |
| Your score over time | (a) minimum count | needs at least **2 own videos** |
| Strengths & Weaknesses, Keep working on | (a) minimum count | need own videos with parameters (Keep working on needs a parameter scoring under 60) |
| Third card "Paste a link", "Reel Breakdowns (n)" tab | (b) on purpose | the links switch; off means two cards |
| Credits meter | (b) on purpose | demo mode |
| Coach text | not (c) | it is **not AI**. It is three sentences assembled in the browser from the saved scores (strongest and weakest parameter, the most repeated problem). No Anthropic call, no Meta call, no cost, nothing to cache |
| Backend | not (c) | the page calls `GET /prysm/dashboard`, `GET /prysm/dashboard/{id}` and the lazy `.../thumbnail`. None of them calls a model |

Test account with 0 own videos and 1 studied reel: only the greeting, the cards, the tabs and the one row show (`samples/_local/dashboard-shots/few-*`). Test account with 10 own videos and 3 studied reels: the full page (`seeded-*`).

## "Report Issue" vs "Drop Feedback" (not changed)

The baseline button says **"Report Issue"** and opens a pre-filled email (`mailto:`). The words "Drop Feedback" do not exist anywhere in this repository, on any branch or tag. The production site is therefore newer than `pre-hackathon-baseline`, and its "Drop Feedback" code is not available here to reuse. I did not rename the button, as the instruction was to rename only if the baseline uses that wording and its feedback flow works. For reference, the baseline also has an unused in-page feedback form (`feedbackModal`, `POST /prysm/feedback/{id}/submit`, backend present) that nothing ever opens. If you want "Drop Feedback", the code from the production repository is needed, or say so and the existing button can simply be relabelled (a one-line change, still opening the email).

## Checked

`scripts/e2e_dashboard.mjs` (headless Chrome, 1280 and 390 wide) with a **local fixture** account (`dashboardtester@gmail.com`: 10 own videos scored 52 to 82, latest 76, 3 studied reels; fake rows in the local database only, titled "Fixture reel N"): greeting with the handle, trend line, the three chips, ring with "4 from last", three cards, coach card with all three sentences, best vs latest, both tabs, the chart, strengths and weaknesses, keep working on, feedback buttons on every row, no credits or pricing text, no horizontal scroll, no page errors. All pass at both widths. Screenshots (git-ignored): `samples/_local/dashboard-shots/`.

The thumbnails on the fixture rows are empty boxes because the fixture has no stored frame; real analyses show their still frame.

## Cost

$0.00 (no model call anywhere on this page).

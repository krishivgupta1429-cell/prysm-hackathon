# Prysm — AI Content Intelligence Platform

A complete, production-ready SaaS marketing website for Prysm, built with React + TypeScript + Tailwind CSS.

## Stack

- **React 18** + **TypeScript**
- **Tailwind CSS** (custom warm color system)
- **Radix UI** primitives via shadcn/ui
- **Framer Motion**-style scroll animations (pure CSS + IntersectionObserver)
- **Lucide React** icons
- **Vite 8** bundler

## Getting Started

```bash
# Install dependencies
npm install

# Start dev server
npm run dev

# Production build
npm run build
```

## Project Structure

```
src/
├── components/
│   ├── Navbar.tsx          # Floating glass pill nav, mobile drawer
│   ├── Hero.tsx            # Animated hero + live product preview
│   ├── FeatureDemos.tsx    # 3 alternating feature sections with mock UIs
│   ├── HorizontalGallery.tsx  # Sticky scroll-driven horizontal gallery
│   ├── FeatureCards.tsx    # 6 feature cards with color accents
│   ├── HowItWorks.tsx      # 3-step process with dashed connector
│   ├── CreatorNiches.tsx   # Dual-row infinite marquee
│   ├── Pricing.tsx         # Monthly/Annual toggle, 3 tiers
│   ├── FAQ.tsx             # Accordion FAQ with Radix UI
│   └── Footer.tsx          # Newsletter strip, social icons, columns
├── hooks/
│   └── useScrollAnimate.ts # IntersectionObserver scroll-in hook
├── pages/
│   └── Index.tsx           # Page assembly
└── index.css               # Full design system: animations, utilities
```

## Color System

| Token | Value | Usage |
|-------|-------|-------|
| `--background` | `#FFFAEC` | Page background |
| `--secondary` | `#FFF2CB` | Buttons, surfaces |
| `--primary` | warm amber | Accents, icons |
| `--foreground` | `#1F1F1F` | Headings |
| `--muted-foreground` | `#6B6B6B` | Body text |
| `--border` | `#EFE4B5` | Borders |

## Design Features

- **Warm minimal aesthetic** — cream/amber palette, no harsh blues
- **Glass navbar** with blur on scroll
- **Shimmer headline** animation
- **Horizontal scroll gallery** driven by page scroll progress
- **Dual-direction marquee** for creator niches
- **SVG virality score ring** with CSS draw animation
- **Dot-grid & mesh-gradient** backgrounds
- **Scroll-triggered** fade-up + slide-in animations (reversible)
- **Monthly/Annual billing toggle** in Pricing
- **Newsletter subscription strip** in Footer
- Fully **responsive** — mobile, tablet, desktop

## Fonts

- **Space Grotesk** — headings
- **Manrope** — body text

## Lenis Smooth Scroll Integration

Lenis is bundled **directly from source** — no npm package required. The full source lives at:

```
src/lib/lenis/
├── core/          # Lenis core engine (lenis.ts, animate.ts, dimensions.ts, …)
└── react/         # React bindings (provider.tsx, use-lenis.ts, store.ts, types.ts)
```

### How it's wired up

| File | Role |
|------|------|
| `src/providers/SmoothScroll.tsx` | Root `<ReactLenis root>` provider wrapping the whole app |
| `src/App.tsx` | Renders `<SmoothScroll>` around everything |
| `src/components/Navbar.tsx` | Uses `useLenis(cb)` to detect scroll position — synced to Lenis's smooth value, not `window.scrollY` |
| `src/components/HorizontalGallery.tsx` | Uses `useLenis(cb)` to drive the horizontal translate frame-by-frame — pixel-perfect with smooth scroll |
| `src/hooks/useLenisScroll.ts` | Convenience re-export of `useLenis` |
| `src/hooks/useScrollAnimate.ts` | Uses `IntersectionObserver` (works natively with Lenis) |

### Lenis config (SmoothScroll.tsx)

```ts
{
  lerp: 0.08,          // 0–1 — lower = smoother but slower
  smoothWheel: true,   // interpolate mouse wheel events
  syncTouch: false,    // native inertia on touch/mobile
  wheelMultiplier: 0.9,
  touchMultiplier: 1.5,
}
```

### Anchor scroll (Navbar)

Clicking a nav link dispatches a `lenis:scrollTo` custom event.
`SmoothScroll.tsx` listens for it and calls `lenis.scrollTo(target, { offset: -80, duration: 1.2 })`.
This gives animated, eased anchor navigation that respects the smooth-scroll feel.

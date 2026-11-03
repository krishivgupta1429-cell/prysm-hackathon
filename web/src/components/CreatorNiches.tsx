const row1 = ['Comedy Creators','Fitness Creators','Education Creators','Lifestyle Creators','Food Creators','Tech Creators','Fashion Creators','Travel Creators','Finance Creators','Business Creators']
const row2 = ['Gaming Creators','Beauty Creators','Music Creators','Sports Creators','Motivation Creators','Health & Wellness Creators','Dance Creators','DIY & Crafts Creators','Photography Creators','Entertainment Creators']

const Tag = ({ label }: { label: string }) => (
  <span className="inline-flex items-center gap-1.5 sm:gap-2 px-2.5 py-1.5 sm:px-4 sm:py-2 md:px-5 md:py-2.5 rounded-full border border-border text-[11px] sm:text-[12px] md:text-[13px] font-semibold font-body whitespace-nowrap text-foreground/60 bg-background/40 hover:text-foreground hover:border-foreground/25 transition-colors duration-150">
    <span className="w-1.5 h-1.5 rounded-full flex-shrink-0 bg-foreground/28" />
    {label}
  </span>
)

const CreatorNiches = () => {
  const d1 = [...row1, ...row1], d2 = [...row2, ...row2]
  return (
    <section className="border-t border-border py-16 sm:py-24 overflow-hidden relative">
      {/* Edge fade masks */}
      <div className="absolute inset-y-0 left-0 w-4 sm:w-16 md:w-24 bg-gradient-to-r from-background to-transparent z-10 pointer-events-none" />
      <div className="absolute inset-y-0 right-0 w-4 sm:w-16 md:w-24 bg-gradient-to-l from-background to-transparent z-10 pointer-events-none" />

      <div className="max-w-6xl mx-auto px-4 sm:px-6 mb-8 sm:mb-12">
        <span className="tag-chip mb-5 inline-flex">Niches</span>
        <h2 className="font-display text-2xl sm:text-4xl md:text-5xl font-bold text-foreground tracking-tight leading-[1.1]">
          Built for every creator
        </h2>
        <p className="mt-4 text-foreground/50 font-body prose-width leading-relaxed">
          Prysm adapts its analysis to your content style, platform, and audience.
        </p>
      </div>

      <div className="flex gap-2 sm:gap-3 mb-2 sm:mb-3 animate-marquee-left">
        {d1.map((n, i) => <Tag key={`r1-${i}`} label={n} />)}
      </div>
      <div className="flex gap-2 sm:gap-3 animate-marquee-right" style={{ animationDuration: '55s' }}>
        {d2.map((n, i) => <Tag key={`r2-${i}`} label={n} />)}
      </div>
    </section>
  )
}
export default CreatorNiches

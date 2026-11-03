import { TrendingUp, Users, MessageSquare, Hash, Sparkles, Send } from 'lucide-react'

const items = [
  { title: 'Competitor Analysis', sub: 'Learn from top creators', Icon: Users,
    preview: () => (
      <div className="space-y-2">
        <div className="flex items-center gap-2 p-2 rounded-lg border border-border/40 bg-secondary/30">
          <div className="w-6 h-6 rounded-full bg-foreground/10 flex items-center justify-center flex-shrink-0">
            <span className="text-[8px] font-bold font-display text-foreground/50">@</span>
          </div>
          <div className="flex-1 min-w-0">
            <span className="text-[10px] font-bold font-body text-foreground/70 block truncate">@competitor_handle</span>
            <span className="text-[8px] font-body text-foreground/35">1.2M followers · 4.8% eng. rate</span>
          </div>
        </div>
        <div className="space-y-1.5">
          {[
            { label: 'Hook Style', val: 'Question-based openers' },
            { label: 'Avg. Length', val: '12–18 seconds' },
            { label: 'Top Format', val: 'Before/After reveals' },
          ].map((r) => (
            <div key={r.label} className="flex justify-between text-[9px] font-body px-1">
              <span className="text-foreground/40">{r.label}</span>
              <span className="text-foreground/65 font-medium">{r.val}</span>
            </div>
          ))}
        </div>
      </div>
    )
  },
  { title: 'AI Chat', sub: 'Ask about your analysis', Icon: Sparkles,
    preview: () => (
      <div className="space-y-1.5">
        {[
          'How do I fix my hook strength?',
          'What would push this from good to viral?',
          'Write me a better hook script',
        ].map((q, i) => (
          <div key={i} className="p-2 rounded-lg border border-border/40 bg-secondary/30">
            <span className="text-[10px] font-body text-foreground/55">{q}</span>
          </div>
        ))}
        <div className="flex items-center gap-2 mt-1 rounded-lg border border-border/40 bg-background px-2.5 py-2">
          <span className="text-[9px] font-body text-foreground/25 flex-1">Ask about your video...</span>
          <Send className="w-3 h-3 text-foreground/25" />
        </div>
      </div>
    )
  },
  { title: 'Caption AI', sub: 'Ready-to-use captions', Icon: MessageSquare,
    preview: () => (
      <div className="space-y-2">
        <div className="p-2.5 rounded-lg border bg-foreground border-foreground space-y-1">
          <span className="text-[10px] font-bold uppercase tracking-widest font-body text-background/50">Suggested</span>
          <p className="text-xs font-body leading-relaxed text-background/80">"Wait for it... this changed everything 🔥 Save this."</p>
        </div>
        <div className="p-2.5 rounded-lg border bg-background border-border/40 space-y-1">
          <span className="text-[10px] font-bold uppercase tracking-widest font-body text-foreground/40">Alternative</span>
          <p className="text-xs font-body leading-relaxed text-foreground/65">"Nobody talks about this creator hack 🧠 Bookmark it."</p>
        </div>
      </div>
    )
  },
  { title: 'Trending Hashtags', sub: 'Niche-specific tags', Icon: Hash,
    preview: () => (
      <div className="flex flex-wrap gap-1.5">
        {['#reelsinstagram', '#viralreels', '#contentcreator', '#growthhacks', '#instagramtips', '#reelsviral', '#creatoreconomy', '#socialmedia'].map(tag => (
          <span key={tag} className="px-2 py-1 rounded-md bg-secondary/60 border border-border/40 text-[10px] font-body text-foreground/55">{tag}</span>
        ))}
      </div>
    )
  },
  { title: 'Virality Score', sub: '10-parameter breakdown', Icon: TrendingUp,
    preview: () => (
      <div className="flex flex-col items-center py-1">
        <div className="relative w-20 h-20">
          <svg viewBox="0 0 100 100" className="w-full h-full -rotate-90">
            <circle cx="50" cy="50" r="40" fill="none" stroke="hsl(44 25% 87%)" strokeWidth="9"/>
            <circle cx="50" cy="50" r="40" fill="none" stroke="hsl(0 0% 8%)" strokeWidth="9" strokeLinecap="round" strokeDasharray="251" strokeDashoffset="55"/>
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <span className="font-display text-2xl font-bold">78</span>
            <span className="text-[9px] text-foreground/40 font-body">/ 100</span>
          </div>
        </div>
        <div className="mt-2 text-center">
          <div className="text-xs font-semibold text-green-600 font-body">Good Potential</div>
          <div className="text-[10px] text-foreground/40 font-body">Across 10 parameters</div>
        </div>
      </div>
    )
  },
]

const HorizontalGallery = () => (
  <section className="relative border-t border-border py-8 sm:py-12 overflow-hidden">
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-10 mb-6">
      <span className="tag-chip mb-4 inline-flex">Examples</span>
      <h2 className="font-display text-2xl sm:text-5xl md:text-6xl font-bold text-foreground tracking-tight">Real insights, real results</h2>
      <p className="mt-2 text-foreground/50 font-body text-sm sm:text-base">See what Prysm surfaces from your content.</p>
    </div>
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-10">
      <div className="flex gap-3 sm:gap-4 overflow-x-auto pb-4 snap-x snap-mandatory scrollbar-hide" style={{ WebkitOverflowScrolling: 'touch' }}>
        {items.map((item, i) => (
          <div key={i} className="flex-shrink-0 snap-start rounded-2xl border border-border/60 bg-background overflow-hidden" style={{ width: 272 }}>
            <div className="flex items-center gap-3 px-4 py-3.5 border-b border-border/40 bg-muted/30">
              <div className="w-7 h-7 rounded-lg bg-foreground/8 flex items-center justify-center">
                <item.Icon className="w-3.5 h-3.5 text-foreground/70" />
              </div>
              <div>
                <div className="text-sm font-bold font-display text-foreground">{item.title}</div>
                <div className="text-[10px] font-body text-foreground/45">{item.sub}</div>
              </div>
            </div>
            <div className="p-4 h-44 overflow-hidden"><item.preview /></div>
          </div>
        ))}
      </div>
    </div>
  </section>
)
export default HorizontalGallery

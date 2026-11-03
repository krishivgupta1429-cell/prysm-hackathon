import { useScrollAnimate } from '@/hooks/useScrollAnimate'
import { Eye, Headphones, TrendingUp } from 'lucide-react'

const pillars = [
  {
    icon: Eye,
    tag: 'Vision',
    title: 'Watches every\nframe',
    description: 'Prysm\'s AI sees your video frame-by-frame — detecting text overlays, scene changes, and hook moments. It understands what viewers actually see.',
    details: ['Frames extracted at strategic timestamps', 'Detects hook, pacing, visual quality, and composition', 'Reads captions and text overlays in any language'],
  },
  {
    icon: Headphones,
    tag: 'Audio',
    title: 'Listens to\nyour audio',
    description: 'Prysm transcribes speech in Hindi, English, and 100+ languages. Analyzes voice quality, background music, and suggests trending audio.',
    details: ['Full transcription in Hindi & English', 'Detects music, voice clarity, and background noise', 'Suggests trending audio choices for your niche'],
  },
  {
    icon: TrendingUp,
    tag: 'Trends',
    title: 'Suggests what\nactually works',
    description: 'Generates captions, hashtags, and trending audio recommendations tailored to your niche. Every suggestion is specific and ready to use.',
    details: ['AI-generated captions tailored to your content', 'Trending hashtags specific to your niche', 'Audio recommendations based on what\'s performing'],
  },
]

const FeatureCards = () => {
  const titleRef = useScrollAnimate<HTMLDivElement>()
  return (
    <section id="features" className="border-t border-border">
      {/* Header */}
      <div ref={titleRef} className="scroll-animate max-w-6xl mx-auto px-4 sm:px-6 pt-14 sm:pt-24 pb-6 sm:pb-16 text-center">
        <span className="tag-chip mb-4 sm:mb-5 inline-flex">How it works under the hood</span>
        <h2 className="font-display text-2xl sm:text-4xl md:text-5xl font-bold text-foreground tracking-tight leading-[1.1]">
          Not just AI opinions
          <br />
          <span className="text-foreground/30">Real data, real analysis</span>
        </h2>
        <p className="mt-3 sm:mt-4 text-foreground/50 font-body text-[0.875rem] sm:text-base max-w-[54ch] mx-auto leading-relaxed px-2 sm:px-0">
          Prysm doesn't guess. It watches your video, listens to your audio, and generates specific captions, hashtags, and fixes before you hit publish.
        </p>
      </div>

      {/* 3-column cards */}
      <div className="border-t border-border grid grid-cols-1 md:grid-cols-3 divide-y md:divide-y-0 md:divide-x divide-border">
        {pillars.map((pillar, i) => {
          const ref = useScrollAnimate<HTMLDivElement>()
          return (
            <div key={i} ref={ref}
              className="scroll-animate group cursor-default flex flex-col"
              style={{ transitionDelay: `${i * 90}ms` }}>
              {/* Visual area */}
              <div className="min-h-[160px] py-6 sm:py-0 sm:h-48 md:h-56 bg-secondary/30 border-b border-border overflow-hidden flex items-center justify-center">
                <div className="flex flex-col items-center gap-4">
                  <div className="w-12 h-12 sm:w-14 sm:h-14 rounded-2xl bg-foreground/5 border border-border flex items-center justify-center group-hover:bg-foreground/10 transition-colors">
                    <pillar.icon className="w-5 h-5 sm:w-6 sm:h-6 text-foreground/40" />
                  </div>
                  <div className="flex flex-col gap-1.5 px-5 sm:px-8">
                    {pillar.details.map((detail, j) => (
                      <div key={j} className="flex items-start gap-2">
                        <span className="w-1 h-1 rounded-full bg-foreground/25 mt-1.5 flex-shrink-0" />
                        <span className="text-[11px] sm:text-[11px] font-body text-foreground/40 leading-relaxed">{detail}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
              {/* Text area */}
              <div className="p-5 sm:p-6 md:p-8 flex flex-col gap-2.5 sm:gap-3 flex-1">
                <span className="tag-chip w-fit">{pillar.tag}</span>
                <h3 className="font-display text-xl sm:text-2xl font-bold text-foreground leading-tight tracking-tight whitespace-pre-line">
                  {pillar.title}
                </h3>
                <p className="text-foreground/55 font-body text-[0.875rem] sm:text-[0.9375rem] leading-relaxed max-w-[38ch]">{pillar.description}</p>
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}
export default FeatureCards

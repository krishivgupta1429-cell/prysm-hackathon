import { useScrollAnimate } from '@/hooks/useScrollAnimate'
import { Upload, Cpu, Sparkles } from 'lucide-react'

const steps = [
  { icon: Upload,   number: '01', title: 'Paste a link or upload',   description: 'Paste any Instagram Reel URL or upload raw footage from your gallery. Prysm downloads the video and extracts frames and audio automatically.', badge: 'Instagram + Upload'  },
  { icon: Cpu,      number: '02', title: 'Prysm analyzes everything', description: 'Prysm watches every frame, transcribes speech in Hindi & English, analyzes audio quality, and scores your content across 10 parameters — all automatically.', badge: '10 parameters' },
  { icon: Sparkles, number: '03', title: 'Get fixes + captions', description: 'Specific suggestions for every weak parameter, plus AI-generated captions, trending hashtags, and audio recommendations tailored to your niche.',           badge: 'Ready to use'  },
]

const StepCard = ({ step, delay }: { step: typeof steps[0]; delay: number }) => {
  const ref = useScrollAnimate<HTMLDivElement>()
  const Icon = step.icon
  return (
    <div ref={ref} className="scroll-animate" style={{ transitionDelay: `${delay}ms` }}>
      <div className="text-3xl sm:text-5xl md:text-6xl font-display font-bold text-foreground/[0.07] mb-2 sm:mb-3 leading-none select-none">{step.number}</div>
      <div className="w-10 h-10 sm:w-11 sm:h-11 rounded-xl bg-foreground/90 flex items-center justify-center mb-4 sm:mb-5 shadow-sm">
        <Icon className="text-background" style={{ width: 16, height: 16 }} />
      </div>
      <span className="tag-chip mb-3 inline-flex">{step.badge}</span>
      <h3 className="font-display text-lg sm:text-xl font-bold text-foreground mb-2">{step.title}</h3>
      <p className="text-foreground/50 font-body text-[0.875rem] sm:text-[0.9375rem] leading-relaxed max-w-[34ch] sm:max-w-[30ch]">{step.description}</p>
    </div>
  )
}

const HowItWorks = () => {
  const titleRef = useScrollAnimate<HTMLDivElement>()
  return (
    <section id="how-it-works" className="border-t border-border">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-16 sm:py-24">
        <div ref={titleRef} className="scroll-animate mb-16">
          <span className="tag-chip mb-5 inline-flex">How it works</span>
          <h2 className="font-display text-2xl sm:text-4xl md:text-5xl font-bold text-foreground tracking-tight leading-[1.1]">
            From upload to insight
            <br />
            <span className="text-foreground/30">in minutes</span>
          </h2>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-8 sm:gap-10 md:gap-12 lg:gap-16">
          {steps.map((step, i) => <StepCard key={step.number} step={step} delay={i * 110} />)}
        </div>
      </div>
    </section>
  )
}
export default HowItWorks

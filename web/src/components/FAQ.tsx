import { useScrollAnimate } from '@/hooks/useScrollAnimate'
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion'

const faqs = [
  { q: 'How does Prysm analyze content?', a: 'Prysm watches your video frame-by-frame, transcribes the audio, and evaluates your content across 10 parameters — hook strength, retention, shareability, audio, on-screen text, save potential, trend alignment, replay value, visual quality, and CTA. Each parameter is scored based on what Instagram\'s algorithm has publicly confirmed matters for reach and virality.' },
  { q: 'What do I get in the analysis?', a: 'A virality score out of 100, detailed breakdowns for each of the 10 parameters with the problem, the fix, and a step-by-step checklist you can follow in your editing app. You also get AI-generated captions, trending hashtags, and audio suggestions — all ready to copy and paste.' },
  { q: 'Can I analyze other creators\' reels?', a: 'Yes — paste any public Instagram Reel link and Prysm will break down exactly what made it work. Same 10 parameters, but framed as techniques you can learn and apply to your own content.' },
  { q: 'What is ARIA?', a: 'ARIA is Prysm\'s built-in AI chat assistant. After every analysis, you can open ARIA to ask follow-up questions — "how do I fix my hook?", "rewrite this caption for my niche", "what trending audio would fit this?". It has full context of your video and scores, so every answer is specific to your content, not generic advice.' },
  { q: 'Do I need video editing skills?', a: 'No. Every fix comes with a step-by-step checklist — open your editing app, add text at this timestamp, use this font. You follow the steps, Prysm handles the thinking.' },
]

const FAQ = () => {
  const titleRef = useScrollAnimate<HTMLDivElement>()
  const bodyRef  = useScrollAnimate<HTMLDivElement>()

  return (
    <section id="faq" className="border-t border-border">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-16 sm:py-24">
        <div className="grid grid-cols-1 md:grid-cols-5 gap-6 sm:gap-8 md:gap-14 lg:gap-20">

          {/* Left: title + CTA */}
          <div ref={titleRef} className="scroll-animate md:col-span-2">
            <span className="tag-chip mb-5 inline-flex">FAQ</span>
            <h2 className="font-display text-2xl sm:text-4xl md:text-5xl font-bold text-foreground tracking-tight leading-[1.1]">
              Questions,
              <br />
              <span className="text-foreground/30">answered</span>
            </h2>
            <p className="mt-5 text-foreground/50 font-body leading-relaxed prose-width">
              Everything you need to know about Prysm.
            </p>
            <div className="mt-8 flex flex-col gap-3">
              <a href="mailto:theprysm77@gmail.com"
                className="btn-primary text-[13.5px] px-6 py-3 inline-flex items-center gap-2 w-fit">
                <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current"><path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 5 8-5v2z"/></svg>
                theprysm77@gmail.com
              </a>
              <a href="https://www.instagram.com/theprysm7/" target="_blank" rel="noopener noreferrer"
                className="btn-primary text-[13.5px] px-6 py-3 inline-flex items-center gap-2 w-fit">
                <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current"><path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zM12 0C8.741 0 8.333.014 7.053.072 2.695.272.273 2.69.073 7.052.014 8.333 0 8.741 0 12c0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98C8.333 23.986 8.741 24 12 24c3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98C15.668.014 15.259 0 12 0zm0 5.838a6.162 6.162 0 100 12.324 6.162 6.162 0 000-12.324zM12 16a4 4 0 110-8 4 4 0 010 8zm6.406-11.845a1.44 1.44 0 100 2.881 1.44 1.44 0 000-2.881z"/></svg>
                @theprysm7
              </a>
            </div>
          </div>

          {/* Right: accordion */}
          <div ref={bodyRef} className="scroll-animate md:col-span-3">
            <Accordion type="single" collapsible className="space-y-2">
              {faqs.map((faq, i) => (
                <AccordionItem key={i} value={`item-${i}`}
                  className="border border-border rounded-xl px-4 sm:px-5 bg-background overflow-hidden hover:border-foreground/15 transition-colors duration-200">
                  <AccordionTrigger className="font-display text-[13px] sm:text-[14.5px] font-bold text-foreground hover:no-underline py-3.5 sm:py-5 text-left leading-snug">
                    {faq.q}
                  </AccordionTrigger>
                  <AccordionContent className="text-foreground/55 font-body text-[13px] sm:text-[13.5px] leading-[1.75] pb-4 sm:pb-5">
                    {faq.a}
                  </AccordionContent>
                </AccordionItem>
              ))}
            </Accordion>
          </div>
        </div>
      </div>
    </section>
  )
}
export default FAQ

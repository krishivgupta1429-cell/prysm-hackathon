import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { isLoggedIn } from '@/lib/auth'
import Navbar           from '@/components/Navbar'
import Hero             from '@/components/Hero'
import FeatureDemos     from '@/components/FeatureDemos'
import FeatureCards     from '@/components/FeatureCards'
import HowItWorks       from '@/components/HowItWorks'
import CreatorNiches    from '@/components/CreatorNiches'
import Pricing          from '@/components/Pricing'
import FAQ              from '@/components/FAQ'
import Footer           from '@/components/Footer'

const Index = () => {
  const navigate = useNavigate()
  useEffect(() => {
    if (isLoggedIn()) navigate('/dashboard')
  }, [])

  return (
  <div className="min-h-screen bg-background overflow-x-hidden">
    {/* Top announcement bar */}
    <a href="/register" className="block w-full bg-foreground text-background text-center py-2 sm:py-2.5 px-6 hover:opacity-90 transition-opacity cursor-pointer">
      <span className="text-[11px] sm:text-[13px] font-body font-semibold tracking-wide">
        Sign up now and get 20 free credits
      </span>
    </a>
    <Navbar />
    <main>
      <Hero />
      <FeatureDemos />
      <FeatureCards />
      <HowItWorks />
      <CreatorNiches />
      <Pricing />
      <FAQ />
    </main>
    <Footer />
  </div>
  )
}
export default Index

import { useEffect, useState } from 'react'
import Pricing from '@/components/Pricing'
import { fetchUser } from '@/lib/auth'
import type { User } from '@/lib/auth'

export default function PricingPage() {
  const [user, setUser] = useState<User | null>(null)

  useEffect(() => {
    fetchUser().then(u => { if (u) setUser(u) })
  }, [])

  return (
    <div className="min-h-screen bg-background text-foreground">
      <nav className="border-b border-border px-4 sm:px-8 h-14 flex items-center justify-between sticky top-0 bg-background/90 backdrop-blur-md z-50">
        <a href="/" className="font-logo text-[21px] text-foreground">PRYSM</a>
        <a href="/dashboard" className="text-sm text-foreground/40 hover:text-foreground/60 transition-colors font-body">
          Back to dashboard
        </a>
      </nav>
      <Pricing verifiedUser={user} />
    </div>
  )
}

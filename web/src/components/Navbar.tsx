import { useState } from 'react'
import { Menu, X } from 'lucide-react'
import { isLoggedIn } from '@/lib/auth'
import { STUDIO_MODE } from '@/lib/config'

const allNavLinks = [
  { label: 'Product',      href: '#product'       },
  { label: 'Features',     href: '#features'      },
  { label: 'How It Works', href: '#how-it-works'  },
  { label: 'Pricing',      href: '#pricing'       },
  { label: 'Resources',    href: '#faq'           },
]

const navLinks = STUDIO_MODE ? allNavLinks.filter(l => l.label !== 'Pricing') : allNavLinks

const Navbar = () => {
  const [mobileOpen, setMobileOpen] = useState(false)

  const scrollTo = (href: string) => {
    const el = document.getElementById(href.replace('#', ''))
    if (el) window.dispatchEvent(new CustomEvent('lenis:scrollTo', { detail: { target: el, offset: -20 } }))
    setMobileOpen(false)
  }

  return (
    <nav className="w-full border-b border-border bg-background">
      <div className="max-w-6xl mx-auto flex items-center justify-between h-14 sm:h-16 px-4 sm:px-6">
        {/* Logo */}
        <a href="/" className="flex-shrink-0">
          <span className="font-logo text-[21px] text-foreground">Prysm</span>
        </a>

        {/* Desktop nav */}
        <div className="hidden md:flex items-center gap-1">
          {navLinks.map((link) => (
            <a key={link.label} href={link.href}
              onClick={(e) => { e.preventDefault(); scrollTo(link.href) }}
              className="px-3.5 py-2 text-[13.5px] font-medium text-foreground/55 hover:text-foreground transition-colors duration-150 rounded-lg hover:bg-foreground/[0.04]">
              {link.label}
            </a>
          ))}
        </div>

        {/* Desktop right */}
        <div className="hidden md:flex items-center gap-2 flex-shrink-0">
          {isLoggedIn() ? (
            <a href="/dashboard" className="btn-primary text-sm px-5 py-2.5">
              Dashboard →
            </a>
          ) : (
            <>
              <a href="/login" className="px-4 py-2 text-[13.5px] font-semibold text-foreground/60 hover:text-foreground transition-colors rounded-full">
                Login
              </a>
              <a href="/register" className="btn-primary text-sm px-5 py-2.5">
                Get Started →
              </a>
            </>
          )}
        </div>

        {/* Mobile */}
        <button className="md:hidden p-3 rounded-lg hover:bg-foreground/[0.06] transition-colors"
          onClick={() => setMobileOpen(!mobileOpen)} aria-label="Menu">
          {mobileOpen ? <X size={20} /> : <Menu size={20} />}
        </button>
      </div>

      <div className={`md:hidden overflow-hidden transition-all duration-300 ${mobileOpen ? 'max-h-[calc(100vh-4rem)]' : 'max-h-0'}`}>
        <div className="border-t border-border px-6 py-5 space-y-1 bg-background">
          {navLinks.map((link) => (
            <a key={link.label} href={link.href}
              className="block py-3 px-3 text-sm font-medium text-foreground/65 hover:text-foreground rounded-lg hover:bg-foreground/[0.05] transition-all"
              onClick={(e) => { e.preventDefault(); scrollTo(link.href) }}>
              {link.label}
            </a>
          ))}
          <div className="flex gap-2 pt-3 border-t border-border mt-2">
            {isLoggedIn() ? (
              <a href="/dashboard" className="flex-1 btn-primary text-sm py-3 justify-center text-center">Dashboard →</a>
            ) : (
              <>
                <a href="/login" className="flex-1 py-3 text-sm font-semibold border border-border rounded-full hover:bg-secondary/50 transition-colors text-center">Login</a>
                <a href="/register" className="flex-1 btn-primary text-sm py-3 justify-center text-center">Get Started</a>
              </>
            )}
          </div>
        </div>
      </div>
    </nav>
  )
}
export default Navbar

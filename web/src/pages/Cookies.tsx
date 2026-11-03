import { SUPPORT_EMAIL } from '@/lib/config'

export default function Cookies() {
  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Nav */}
      <nav className="border-b border-border px-4 sm:px-8 h-14 flex items-center justify-between sticky top-0 bg-background/90 backdrop-blur-md z-50">
        <a href="/" className="font-logo text-[21px] text-foreground">Prysm</a>
        <a href="/" className="text-sm text-foreground/40 hover:text-foreground/60 transition-colors font-body">
          Back to home
        </a>
      </nav>

      <div className="max-w-3xl mx-auto px-6 py-12">
        <h1 className="font-display text-3xl font-bold tracking-tight mb-2">Cookie Policy</h1>
        <p className="text-foreground/35 text-sm font-body mb-10">Last updated April 17, 2026</p>

        {/* 1. What Are Cookies */}
        <section className="mb-10">
          <h2 className="font-display text-xl font-semibold tracking-tight mb-4">1. What Are Cookies</h2>
          <p className="text-sm text-foreground/60 font-body leading-relaxed">
            Cookies are small text files that are placed on your device when you visit our website. They help us provide you with an improved experience by remembering your preferences and understanding how you use our platform.
          </p>
        </section>

        {/* 2. How We Use Cookies */}
        <section className="mb-10">
          <h2 className="font-display text-xl font-semibold tracking-tight mb-4">2. How We Use Cookies</h2>
          <div className="text-sm text-foreground/60 font-body leading-relaxed space-y-3">
            <p>Prysm primarily uses browser localStorage rather than traditional cookies. We use these technologies for the following purposes:</p>
            <ul className="space-y-2 list-disc list-inside">
              <li><strong className="text-foreground/80">Essential:</strong> Required for the website to function properly (login sessions, security tokens)</li>
              <li><strong className="text-foreground/80">Preference:</strong> Remembering your settings and preferences</li>
            </ul>
            <p>We do not use advertising or retargeting cookies.</p>
          </div>
        </section>

        {/* 3. Types of Cookies */}
        <section className="mb-10">
          <h2 className="font-display text-xl font-semibold tracking-tight mb-4">3. Specific Cookies We Use</h2>

          <p className="text-sm text-foreground/80 font-body font-medium mb-3">Essential Cookies</p>
          <div className="border border-border rounded-xl overflow-hidden mb-6">
            <table className="w-full text-sm font-body">
              <thead>
                <tr className="border-b border-border">
                  <th className="text-left px-4 py-3 text-foreground/40 font-medium text-xs uppercase tracking-wider">Name</th>
                  <th className="text-left px-4 py-3 text-foreground/40 font-medium text-xs uppercase tracking-wider">Purpose</th>
                  <th className="text-left px-4 py-3 text-foreground/40 font-medium text-xs uppercase tracking-wider">Duration</th>
                </tr>
              </thead>
              <tbody className="text-foreground/60">
                <tr className="border-b border-border">
                  <td className="px-4 py-3 text-foreground/80 font-mono text-xs">session_token</td>
                  <td className="px-4 py-3">Login session preservation</td>
                  <td className="px-4 py-3">Until logout</td>
                </tr>
                <tr>
                  <td className="px-4 py-3 text-foreground/80 font-mono text-xs">user_profile</td>
                  <td className="px-4 py-3">Cached user profile data</td>
                  <td className="px-4 py-3">Until logout</td>
                </tr>
              </tbody>
            </table>
          </div>

          <p className="text-sm text-foreground/80 font-body font-medium mb-3">Preference Cookies</p>
          <div className="border border-border rounded-xl overflow-hidden">
            <table className="w-full text-sm font-body">
              <thead>
                <tr className="border-b border-border">
                  <th className="text-left px-4 py-3 text-foreground/40 font-medium text-xs uppercase tracking-wider">Name</th>
                  <th className="text-left px-4 py-3 text-foreground/40 font-medium text-xs uppercase tracking-wider">Purpose</th>
                  <th className="text-left px-4 py-3 text-foreground/40 font-medium text-xs uppercase tracking-wider">Duration</th>
                </tr>
              </thead>
              <tbody className="text-foreground/60">
                <tr className="border-b border-border">
                  <td className="px-4 py-3 text-foreground/80 font-mono text-xs">analysis_result</td>
                  <td className="px-4 py-3">Latest analysis result cache</td>
                  <td className="px-4 py-3">Until new analysis</td>
                </tr>
                <tr>
                  <td className="px-4 py-3 text-foreground/80 font-mono text-xs">chat_context</td>
                  <td className="px-4 py-3">Chat session context</td>
                  <td className="px-4 py-3">Single use, auto-cleared</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        {/* 4. Third-Party Cookies */}
        <section className="mb-10">
          <h2 className="font-display text-xl font-semibold tracking-tight mb-4">4. Third-Party Cookies</h2>
          <div className="text-sm text-foreground/60 font-body leading-relaxed space-y-3">
            <p>Some third-party services may set cookies when you interact with our platform:</p>
            <ul className="space-y-2 list-disc list-inside">
              <li>Authentication providers may set cookies during sign-in</li>
              <li>Payment processors may set cookies during checkout</li>
            </ul>
            <p>We have no control over third-party cookies. You can manage these through your browser settings or the respective provider's opt-out tools.</p>
          </div>
        </section>

        {/* 5. Managing Your Preferences */}
        <section className="mb-10">
          <h2 className="font-display text-xl font-semibold tracking-tight mb-4">5. Managing Your Preferences</h2>
          <ul className="space-y-2 text-sm text-foreground/60 font-body leading-relaxed list-disc list-inside">
            <li>You can block or delete cookies through your browser settings</li>
            <li>Logging out clears all authentication data</li>
            <li>Clearing your browser data removes all locally stored information</li>
            <li>Deleting your account removes all server-side data</li>
          </ul>
        </section>

        {/* 6. Updates */}
        <section className="mb-10">
          <h2 className="font-display text-xl font-semibold tracking-tight mb-4">6. Updates to This Policy</h2>
          <p className="text-sm text-foreground/60 font-body leading-relaxed">
            We may update this Cookie Policy from time to time due to changes in our practices or for regulatory reasons. Changes will be posted on this page.
          </p>
        </section>

        {/* 7. Contact */}
        <section className="mb-10">
          <h2 className="font-display text-xl font-semibold tracking-tight mb-4">7. Contact Us</h2>
          <p className="text-sm text-foreground/60 font-body leading-relaxed">
            Questions about our cookie practices? Reach us at{' '}
            <a href={`mailto:${SUPPORT_EMAIL}`} className="text-foreground/80 underline underline-offset-2 hover:text-foreground transition-colors">
              {SUPPORT_EMAIL}
            </a>
          </p>
        </section>
      </div>
    </div>
  )
}

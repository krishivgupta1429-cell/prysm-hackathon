export default function Terms() {
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
        <h1 className="font-display text-3xl font-bold tracking-tight mb-2">Terms of Service</h1>
        <p className="text-foreground/35 text-sm font-body mb-10">Last updated April 17, 2026</p>

        {/* 1. Acceptance of Terms */}
        <section className="mb-10">
          <h2 className="font-display text-xl font-semibold tracking-tight mb-4">1. Acceptance of Terms</h2>
          <p className="text-sm text-foreground/60 font-body leading-relaxed">
            By accessing and using Prysm, you accept and agree to be bound by the terms and provisions of this agreement. If you do not agree to these terms, please do not use our service.
          </p>
        </section>

        {/* 2. Description of Service */}
        <section className="mb-10">
          <h2 className="font-display text-xl font-semibold tracking-tight mb-4">2. Description of Service</h2>
          <div className="text-sm text-foreground/60 font-body leading-relaxed space-y-2">
            <p>Prysm provides AI-powered video analysis for content creators. Our service includes video analysis, insights generation, and performance recommendations to help you improve your short-form content.</p>
            <p>All analysis and suggestions are <strong className="text-foreground/80">advisory in nature</strong> and do not constitute guarantees of performance or virality.</p>
          </div>
        </section>

        {/* 3. User Accounts */}
        <section className="mb-10">
          <h2 className="font-display text-xl font-semibold tracking-tight mb-4">3. User Accounts</h2>
          <ul className="space-y-2 text-sm text-foreground/60 font-body leading-relaxed list-disc list-inside">
            <li>You must provide accurate information when creating an account</li>
            <li>One account per person</li>
            <li>You agree to accept responsibility for all activities that occur under your account</li>
            <li>You are responsible for maintaining the confidentiality of your password</li>
            <li>We reserve the right to suspend accounts that violate these terms</li>
          </ul>
        </section>

        {/* 4. Credits & Billing */}
        <section className="mb-10">
          <h2 className="font-display text-xl font-semibold tracking-tight mb-4">4. Credits & Billing</h2>
          <ul className="space-y-2 text-sm text-foreground/60 font-body leading-relaxed list-disc list-inside">
            <li><strong className="text-foreground/80">Free tier:</strong> 20 one-time credits</li>
            <li><strong className="text-foreground/80">Paid plans (Pro, Creator):</strong> monthly credit allocation that refreshes each billing cycle</li>
            <li>Credits are deducted based on actual processing cost per analysis</li>
            <li>Existing credits carry over when upgrading plans</li>
            <li><strong className="text-foreground/80">Cancellation:</strong> you keep remaining credits, but they will not refresh</li>
            <li>No refunds on credits already used</li>
          </ul>
        </section>

        {/* 5. Your Content */}
        <section className="mb-10">
          <h2 className="font-display text-xl font-semibold tracking-tight mb-4">5. Your Content</h2>
          <ul className="space-y-2 text-sm text-foreground/60 font-body leading-relaxed list-disc list-inside">
            <li>You own your videos &mdash; we do not claim any ownership</li>
            <li>Uploaded videos are processed and deleted within minutes &mdash; we do not store them</li>
            <li>Analysis results are stored in your account and licensed to you for personal and commercial use</li>
            <li>You may share analysis results via share links</li>
          </ul>
        </section>

        {/* 6. Acceptable Use */}
        <section className="mb-10">
          <h2 className="font-display text-xl font-semibold tracking-tight mb-4">6. Acceptable Use</h2>
          <p className="text-sm text-foreground/60 font-body leading-relaxed mb-3">You agree not to:</p>
          <ul className="space-y-2 text-sm text-foreground/60 font-body leading-relaxed list-disc list-inside">
            <li>Upload illegal, harmful, or exploitative content</li>
            <li>Violate intellectual property rights</li>
            <li>Use automated scraping or abuse our API</li>
            <li>Share account credentials with others</li>
            <li>Attempt to reverse-engineer our systems or circumvent the credit system</li>
            <li>Access our systems without authorization</li>
          </ul>
        </section>

        {/* 7. Intellectual Property */}
        <section className="mb-10">
          <h2 className="font-display text-xl font-semibold tracking-tight mb-4">7. Intellectual Property</h2>
          <p className="text-sm text-foreground/60 font-body leading-relaxed">
            The service and its original content, features, and functionality are owned by Prysm and are protected by international copyright, trademark, patent, trade secret, and other intellectual property laws.
          </p>
        </section>

        {/* 8. Limitation of Liability */}
        <section className="mb-10">
          <h2 className="font-display text-xl font-semibold tracking-tight mb-4">8. Limitation of Liability</h2>
          <div className="text-sm text-foreground/60 font-body leading-relaxed space-y-2">
            <p>Prysm and its affiliates shall not be liable for any indirect, incidental, special, consequential, or punitive damages arising from your use of the service.</p>
            <p>Our total liability is limited to the amount you have paid us in the past 12 months.</p>
          </div>
        </section>

        {/* 9. Service Availability */}
        <section className="mb-10">
          <h2 className="font-display text-xl font-semibold tracking-tight mb-4">9. Service Availability</h2>
          <ul className="space-y-2 text-sm text-foreground/60 font-body leading-relaxed list-disc list-inside">
            <li>We aim for high uptime but do not guarantee uninterrupted service</li>
            <li>We may perform maintenance with or without prior notice</li>
            <li>We may modify features or pricing with reasonable notice</li>
          </ul>
        </section>

        {/* 10. Termination */}
        <section className="mb-10">
          <h2 className="font-display text-xl font-semibold tracking-tight mb-4">10. Termination</h2>
          <ul className="space-y-2 text-sm text-foreground/60 font-body leading-relaxed list-disc list-inside">
            <li>You can delete your account anytime &mdash; all data is permanently removed</li>
            <li>We may terminate or suspend your account and bar access to the service immediately, without prior notice or liability, under our sole discretion, for any reason</li>
          </ul>
        </section>

        {/* 11. Governing Law */}
        <section className="mb-10">
          <h2 className="font-display text-xl font-semibold tracking-tight mb-4">11. Governing Law</h2>
          <div className="text-sm text-foreground/60 font-body leading-relaxed space-y-2">
            <p>These terms are governed by the laws of India.</p>
            <p>Any disputes shall be resolved in the courts of Gurugram, Haryana, India.</p>
          </div>
        </section>

        {/* 12. Changes to Terms */}
        <section className="mb-10">
          <h2 className="font-display text-xl font-semibold tracking-tight mb-4">12. Changes to Terms</h2>
          <p className="text-sm text-foreground/60 font-body leading-relaxed">
            We reserve the right to modify or replace these Terms at any time. If a revision is material, we will provide at least 30 days notice prior to any new terms taking effect. Continued use of Prysm after changes constitutes acceptance of the updated terms.
          </p>
        </section>

        {/* 13. Contact */}
        <section className="mb-10">
          <h2 className="font-display text-xl font-semibold tracking-tight mb-4">13. Contact</h2>
          <p className="text-sm text-foreground/60 font-body leading-relaxed">
            Questions about these terms? Reach us at{' '}
            <a href="mailto:theprysm77@gmail.com" className="text-foreground/80 underline underline-offset-2 hover:text-foreground transition-colors">
              theprysm77@gmail.com
            </a>
          </p>
        </section>
      </div>
    </div>
  )
}

export default function Privacy() {
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
        <h1 className="font-display text-3xl font-bold tracking-tight mb-2">Privacy Policy</h1>
        <p className="text-foreground/35 text-sm font-body mb-10">Last updated April 17, 2026</p>

        {/* 1. Information We Collect */}
        <section className="mb-10">
          <h2 className="font-display text-xl font-semibold tracking-tight mb-4">1. Information We Collect</h2>
          <div className="text-sm text-foreground/60 font-body leading-relaxed space-y-3">
            <p>Prysm collects information you provide directly to us, such as when you create an account, upload videos for analysis, or contact us for support.</p>
            <p>We also automatically collect certain information when you use our service, including:</p>
            <ul className="space-y-2 list-disc list-inside">
              <li>Usage data and analytics</li>
              <li>Device information and IP addresses</li>
              <li>Cookies and similar tracking technologies</li>
            </ul>
          </div>
        </section>

        {/* 2. How We Use Your Information */}
        <section className="mb-10">
          <h2 className="font-display text-xl font-semibold tracking-tight mb-4">2. How We Use Your Information</h2>
          <div className="text-sm text-foreground/60 font-body leading-relaxed space-y-3">
            <p>We use the information we collect to:</p>
            <ul className="space-y-2 list-disc list-inside">
              <li>Provide and improve our AI analysis services</li>
              <li>Process your video uploads and generate insights</li>
              <li>Manage your subscription and credit balance</li>
              <li>Communicate with you about our services</li>
              <li>Ensure the security and integrity of our platform</li>
            </ul>
          </div>
        </section>

        {/* 3. Information Sharing */}
        <section className="mb-10">
          <h2 className="font-display text-xl font-semibold tracking-tight mb-4">3. Information Sharing</h2>
          <div className="text-sm text-foreground/60 font-body leading-relaxed space-y-3">
            <p>We do not sell, trade, or otherwise transfer your personal information to third parties without your consent, except as described in this policy.</p>
            <p>We may share data with trusted third-party service providers who assist us in operating our platform, including:</p>
            <ul className="space-y-2 list-disc list-inside">
              <li>AI processing services for video and audio analysis</li>
              <li>Payment processors for subscription billing</li>
              <li>Email delivery services for transactional notifications</li>
              <li>Cloud hosting providers for data storage</li>
              <li>Authentication services for secure sign-in</li>
            </ul>
            <p>These providers only receive the data necessary to perform their functions and are bound by confidentiality obligations.</p>
          </div>
        </section>

        {/* 4. Data Storage & Retention */}
        <section className="mb-10">
          <h2 className="font-display text-xl font-semibold tracking-tight mb-4">4. Data Storage & Retention</h2>
          <ul className="space-y-2 text-sm text-foreground/60 font-body leading-relaxed list-disc list-inside">
            <li><strong className="text-foreground/80">Account data</strong> — kept until you delete your account</li>
            <li><strong className="text-foreground/80">Analysis results</strong> — stored in your account history</li>
            <li><strong className="text-foreground/80">Uploaded videos</strong> — processed and permanently deleted within minutes</li>
            <li><strong className="text-foreground/80">Instagram videos (link analysis)</strong> — downloaded, processed, and deleted within minutes</li>
          </ul>
        </section>

        {/* 5. Data Security */}
        <section className="mb-10">
          <h2 className="font-display text-xl font-semibold tracking-tight mb-4">5. Data Security</h2>
          <p className="text-sm text-foreground/60 font-body leading-relaxed">
            We implement appropriate security measures to protect your personal information against unauthorized access, alteration, disclosure, or destruction. All connections use HTTPS encryption and sensitive data is never stored in plaintext.
          </p>
        </section>

        {/* 6. Your Rights */}
        <section className="mb-10">
          <h2 className="font-display text-xl font-semibold tracking-tight mb-4">6. Your Rights</h2>
          <div className="text-sm text-foreground/60 font-body leading-relaxed space-y-3">
            <p>You have the right to:</p>
            <ul className="space-y-2 list-disc list-inside">
              <li>Access your personal information</li>
              <li>Correct inaccurate data via your account settings</li>
              <li>Request deletion of your data by deleting your account</li>
              <li>Request a data export by emailing <a href="mailto:theprysm77@gmail.com" className="text-foreground/80 underline underline-offset-2 hover:text-foreground transition-colors">theprysm77@gmail.com</a></li>
              <li>Opt out of marketing communications</li>
            </ul>
          </div>
        </section>

        {/* 7. Children */}
        <section className="mb-10">
          <h2 className="font-display text-xl font-semibold tracking-tight mb-4">7. Children</h2>
          <p className="text-sm text-foreground/60 font-body leading-relaxed">
            Prysm is not intended for users under 13 years of age. We do not knowingly collect personal data from children.
          </p>
        </section>

        {/* 8. Changes */}
        <section className="mb-10">
          <h2 className="font-display text-xl font-semibold tracking-tight mb-4">8. Updates to This Policy</h2>
          <p className="text-sm text-foreground/60 font-body leading-relaxed">
            We may update this Privacy Policy from time to time. We will notify you of any changes by posting the new Privacy Policy on this page. Continued use of Prysm after changes constitutes acceptance of the updated policy.
          </p>
        </section>

        {/* 9. Contact */}
        <section className="mb-10">
          <h2 className="font-display text-xl font-semibold tracking-tight mb-4">9. Contact Us</h2>
          <p className="text-sm text-foreground/60 font-body leading-relaxed">
            If you have any questions about this Privacy Policy, please contact us at{' '}
            <a href="mailto:theprysm77@gmail.com" className="text-foreground/80 underline underline-offset-2 hover:text-foreground transition-colors">
              theprysm77@gmail.com
            </a>
          </p>
        </section>
      </div>
    </div>
  )
}

import { Toaster } from "@/components/ui/toaster";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, Suspense } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Index from "./pages/Index";
import Analyse from "./pages/Analyse";
import AnalyseUpload from "./pages/AnalyseUpload";
import AnalyseLink from "./pages/AnalyseLink";
import Results from "./pages/Results";
import Login from "./pages/Login";
import Register from "./pages/Register";
import Dashboard from "./pages/Dashboard";
import Chat from "./pages/Chat";
import GoogleCallback from "./pages/GoogleCallback";
import VerifyEmail from "./pages/VerifyEmail";
import ForgotPassword from "./pages/ForgotPassword";
import ResetPassword from "./pages/ResetPassword";
import Waitlist from "./pages/Waitlist";
import Settings from "./pages/Settings";
import SharedResults from "./pages/SharedResults";
import Onboarding from "./pages/Onboarding";
import PricingPage from "./pages/PricingPage";
import Privacy from "./pages/Privacy";
import Terms from "./pages/Terms";
import Cookies from "./pages/Cookies";
import NotFound from "./pages/NotFound";
import SmoothScroll from "./providers/SmoothScroll";
import { STUDIO_MODE, ALLOW_LINKS } from "./lib/config";
import CreatorDna from "./studio/CreatorDna"; // hackathon: Instagram connect + Creator DNA page

// Local-only review page for the blind bake-off: exists only on the dev server with VITE_DEV_BAKEOFF=1 (import.meta.env.DEV is false in
// production builds, so the page and its chunk are dropped from them).
const DEV_BAKEOFF = import.meta.env.DEV && import.meta.env.VITE_DEV_BAKEOFF === '1'
const DevBakeoff = DEV_BAKEOFF ? lazy(() => import('./studio/DevBakeoff')) : null

const queryClient = new QueryClient();

const gated = false

const App = () => (
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>
      <SmoothScroll>
        <Toaster />
        <Sonner />
        <BrowserRouter>
          <Routes>
            <Route path="/" element={<Index />} />
            <Route path="/analyse" element={gated ? <Waitlist /> : <Analyse />} />
            <Route path="/analyse/upload" element={gated ? <Waitlist /> : <AnalyseUpload />} />
            <Route path="/analyse/link" element={!ALLOW_LINKS ? <Navigate to="/analyse/upload" replace /> : gated ? <Waitlist /> : <AnalyseLink />} />
            <Route path="/results" element={gated ? <Waitlist /> : <Results />} />
            <Route path="/chat" element={gated ? <Waitlist /> : <Chat />} />
            <Route path="/login" element={<Login />} />
            <Route path="/register" element={<Register />} />
            <Route path="/dashboard" element={gated ? <Waitlist /> : <Dashboard />} />
            <Route path="/oauth/google" element={<GoogleCallback />} />
            <Route path="/onboarding" element={<Onboarding />} />
            <Route path="/verify-email" element={<VerifyEmail />} />
            <Route path="/forgot-password" element={<ForgotPassword />} />
            <Route path="/reset-password" element={<ResetPassword />} />
            <Route path="/settings" element={gated ? <Waitlist /> : <Settings />} />
            <Route path="/creator-dna" element={gated ? <Waitlist /> : <CreatorDna />} />
            <Route path="/pricing" element={STUDIO_MODE ? <Navigate to="/" replace /> : <PricingPage />} />
            <Route path="/share/:shareId" element={<SharedResults />} />
            {DevBakeoff && <Route path="/dev/bakeoff" element={<Suspense fallback={null}><DevBakeoff /></Suspense>} />}
            <Route path="/waitlist" element={<Waitlist />} />
            <Route path="/privacy" element={<Privacy />} />
            <Route path="/terms" element={<Terms />} />
            <Route path="/cookies" element={<Cookies />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </BrowserRouter>
      </SmoothScroll>
    </TooltipProvider>
  </QueryClientProvider>
);

export default App;

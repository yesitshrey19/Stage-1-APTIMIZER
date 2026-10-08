import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { AlertCircle, ArrowLeft, ArrowRight, Eye, EyeOff, Loader, Lock, Mail } from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import { Brand } from "../components/Brand";
import { useAuth } from "../context/AuthContext";
import { requestErrorMessage } from "../lib/api";
import { useSlowHint, WAKING_MESSAGE } from "../lib/useSlowHint";
import { Reveal, RevealItem, Stagger } from "../components/landing/Reveal";
import {
  AuthGlassStyles,
  BlurFade,
  GlassButton,
  GlassInput,
  GradientBackground,
} from "../components/ui/sign-up";

// Stage 1 (Site) — one card per part of the site workflow, in the order it is used.
const PILLS = [
  ["PLOT & BOUNDARY", "draw · survey points · road edges"],
  ["SETBACKS & CONTROLS", "NBC Part 3 · FAR · coverage"],
  ["SITE LAYOUT ENGINE", "envelope · roads · amenities · GA"],
  ["GIS INTELLIGENCE", "terrain · flood · seismic · wind · sun"],
];

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [step, setStep] = useState("email");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const slow = useSlowHint(busy);
  const passwordInputRef = useRef(null);

  const isEmailValid = /\S+@\S+\.\S+/.test(email);

  // The password field mounts with the step, so the focus has to wait out the
  // blur-fade before there is anything to focus.
  useEffect(() => {
    if (step !== "password") return undefined;
    const id = setTimeout(() => passwordInputRef.current?.focus(), 400);
    return () => clearTimeout(id);
  }, [step]);

  const goToPassword = () => {
    if (!isEmailValid) return;
    setError("");
    setStep("password");
  };

  const goBack = () => {
    setError("");
    setPassword("");
    setStep("email");
  };

  const handleEmailKeyDown = (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    goToPassword();
  };

  const submit = async (e) => {
    e.preventDefault();
    // Enter in the email field reaches the form too; there it means "advance".
    if (step !== "password" || busy) return;
    setBusy(true);
    setError("");
    try {
      await login(email, password);
      toast.success("Signed in");
      navigate("/projects");
    } catch (err) {
      setError(requestErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen grid lg:grid-cols-[1.1fr_0.9fr]">
      <div className="hidden lg:flex relative flex-col justify-between overflow-hidden bg-gradient-to-b from-slate-900 via-slate-900 to-slate-950 p-12">
        {/* Ambient ring, decorative only. Static so it reads as texture, not motion. */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -right-40 top-1/2 h-[36rem] w-[36rem] -translate-y-1/2 rounded-full border border-white/[0.08]"
        />
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -right-24 top-1/2 h-72 w-72 -translate-y-1/2 rounded-full border border-white/[0.06]"
        />

        <Reveal immediate>
          <Brand light markClass="h-10 w-auto" wordClass="text-lg" />
        </Reveal>

        <div className="relative max-w-lg">
          <Reveal immediate>
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-[11px] uppercase tracking-[0.2em] text-blue-300/80">
              <span>Boundary</span>
              <ArrowRight className="h-3 w-3" />
              <span>Setbacks</span>
              <ArrowRight className="h-3 w-3" />
              <span>Layout</span>
              <ArrowRight className="h-3 w-3" />
              <span>GIS</span>
              <ArrowRight className="h-3 w-3" />
              <span>Master plan</span>
            </div>
          </Reveal>

          <Reveal delay={0.06} immediate>
            <h1 className="mt-5 text-4xl sm:text-5xl font-semibold tracking-tight text-white leading-[1.05]">
              Start with the site.
              <br />
              <span className="text-blue-400">Stage 1 prototype.</span>
            </h1>
          </Reveal>

          <Reveal delay={0.12} immediate>
            <p className="mt-5 text-slate-300 text-base max-w-md">
              Draw the plot, apply NBC setbacks, generate an optimised site layout and
              read the land — terrain, flood, seismic, wind and sun — from one project
              model.
            </p>
          </Reveal>

          <Stagger delay={0.2} className="mt-10 grid grid-cols-2 gap-3">
            {PILLS.map(([title, caption]) => (
              <RevealItem
                key={title}
                className="rounded-md border border-white/15 bg-white/5 px-3 py-3 backdrop-blur-sm"
              >
                <div className="font-mono text-[11px] uppercase tracking-wide text-white">{title}</div>
                <div className="mt-1 text-[11px] leading-snug text-slate-400">{caption}</div>
              </RevealItem>
            ))}
          </Stagger>
        </div>

        <Reveal immediate className="relative">
          <p className="text-[11px] text-slate-500 font-mono tracking-wide">
            STAGE 1 · SITE
          </p>
        </Reveal>
      </div>

      {/* Sign-in side: the glass auth flow from components/ui/sign-up, wired to
          the real /auth/login call. Two steps, email then password, so the
          field the visitor is answering is the only one on screen. */}
      <div className="relative flex items-center justify-center overflow-hidden bg-card p-8">
        <AuthGlassStyles />
        <div aria-hidden="true" className="absolute inset-0 z-0 opacity-70">
          <GradientBackground />
        </div>

        <form
          onSubmit={submit}
          className="relative z-10 flex w-[300px] flex-col items-center gap-8"
          data-testid="login-form"
        >
          <fieldset disabled={busy} className="flex w-full flex-col items-center gap-8">
            <AnimatePresence mode="wait">
              {step === "email" ? (
                <motion.div
                  key="email-title"
                  initial={{ y: 6, opacity: 0 }}
                  animate={{ y: 0, opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.3, ease: "easeOut" }}
                  className="flex w-full flex-col items-center gap-3 text-center"
                >
                  <BlurFade delay={0.25} className="w-full">
                    <p className="font-serif font-light text-4xl sm:text-5xl tracking-tight text-foreground">
                      Sign in
                    </p>
                  </BlurFade>
                  <BlurFade delay={0.4}>
                    <p className="text-sm font-medium text-muted-foreground">
                      Access your projects and calculations.
                    </p>
                  </BlurFade>
                </motion.div>
              ) : (
                <motion.div
                  key="password-title"
                  initial={{ y: 6, opacity: 0 }}
                  animate={{ y: 0, opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.3, ease: "easeOut" }}
                  className="flex w-full flex-col items-center gap-3 text-center"
                >
                  <BlurFade delay={0} className="w-full">
                    <p className="font-serif font-light text-4xl sm:text-5xl tracking-tight text-foreground">
                      Welcome back
                    </p>
                  </BlurFade>
                  <BlurFade delay={0.25}>
                    <p className="text-sm font-medium text-muted-foreground">
                      Enter your password to continue.
                    </p>
                  </BlurFade>
                </motion.div>
              )}
            </AnimatePresence>

            <div className="w-full space-y-6">
              <BlurFade delay={step === "email" ? 0.55 : 0} className="w-full">
                <div className="relative w-full">
                  <AnimatePresence>
                    {step === "password" && (
                      <motion.div
                        initial={{ y: -10, opacity: 0 }}
                        animate={{ y: 0, opacity: 1 }}
                        transition={{ duration: 0.3, delay: 0.4 }}
                        className="absolute -top-6 left-4 z-10"
                      >
                        <label className="text-xs font-semibold text-muted-foreground">Email</label>
                      </motion.div>
                    )}
                  </AnimatePresence>
                  <GlassInput
                    data-testid="login-email-input"
                    type="email"
                    autoComplete="email"
                    placeholder="admin@aptimizer.com"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    onKeyDown={handleEmailKeyDown}
                    leading={
                      <div
                        className={`relative z-10 flex flex-shrink-0 items-center justify-center overflow-hidden transition-all duration-300 ease-in-out ${
                          email.length > 20 && step === "email" ? "w-0 px-0" : "w-10 pl-2"
                        }`}
                      >
                        <Mail className="h-5 w-5 flex-shrink-0 text-foreground/80" />
                      </div>
                    }
                    trailing={
                      <div
                        className={`relative z-10 flex-shrink-0 overflow-hidden transition-all duration-300 ease-in-out ${
                          isEmailValid && step === "email" ? "w-10 pr-1" : "w-0"
                        }`}
                      >
                        <GlassButton
                          type="button"
                          onClick={goToPassword}
                          size="icon"
                          aria-label="Continue with email"
                          data-testid="login-continue-button"
                          contentClassName="text-foreground/80 hover:text-foreground"
                        >
                          <ArrowRight className="h-5 w-5" />
                        </GlassButton>
                      </div>
                    }
                  />
                </div>
              </BlurFade>

              <AnimatePresence>
                {step === "password" && (
                  <BlurFade key="password-field" className="w-full">
                    <div className="relative w-full">
                      <AnimatePresence>
                        {password.length > 0 && (
                          <motion.div
                            initial={{ y: -10, opacity: 0 }}
                            animate={{ y: 0, opacity: 1 }}
                            transition={{ duration: 0.3 }}
                            className="absolute -top-6 left-4 z-10"
                          >
                            <label className="text-xs font-semibold text-muted-foreground">Password</label>
                          </motion.div>
                        )}
                      </AnimatePresence>
                      <GlassInput
                        ref={passwordInputRef}
                        data-testid="login-password-input"
                        type={showPassword ? "text" : "password"}
                        autoComplete="current-password"
                        placeholder="Password"
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        leading={
                          <div className="relative z-10 flex w-10 flex-shrink-0 items-center justify-center pl-2">
                            {password.length > 0 ? (
                              <button
                                type="button"
                                aria-label="Toggle password visibility"
                                onClick={() => setShowPassword(!showPassword)}
                                className="rounded-full p-2 text-foreground/80 transition-colors hover:text-foreground"
                              >
                                {showPassword ? <EyeOff className="h-5 w-5" /> : <Eye className="h-5 w-5" />}
                              </button>
                            ) : (
                              <Lock className="h-5 w-5 flex-shrink-0 text-foreground/80" />
                            )}
                          </div>
                        }
                        trailing={
                          <div className="relative z-10 w-10 flex-shrink-0 pr-1">
                            <GlassButton
                              type="submit"
                              size="icon"
                              disabled={busy || password.length === 0}
                              aria-label="Sign in"
                              data-testid="login-submit-button"
                              contentClassName="text-foreground/80 hover:text-foreground"
                            >
                              {busy ? (
                                <Loader className="h-5 w-5 animate-spin" />
                              ) : (
                                <ArrowRight className="h-5 w-5" />
                              )}
                            </GlassButton>
                          </div>
                        }
                      />
                    </div>
                    <BlurFade inView delay={0.2}>
                      <button
                        type="button"
                        onClick={goBack}
                        className="mt-4 flex items-center gap-2 text-sm text-foreground/70 transition-colors hover:text-foreground"
                      >
                        <ArrowLeft className="h-4 w-4" /> Go back
                      </button>
                    </BlurFade>
                  </BlurFade>
                )}
              </AnimatePresence>
            </div>
          </fieldset>

          {slow && (
            <p className="w-full text-center text-xs text-foreground/60" data-testid="login-waking">
              {WAKING_MESSAGE}
            </p>
          )}

          <AnimatePresence>
            {error && (
              <motion.div
                initial={{ opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                className="flex w-full items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive"
                data-testid="login-error"
              >
                <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" />
                <span>{error}</span>
              </motion.div>
            )}
          </AnimatePresence>

          <p className="text-sm text-muted-foreground">
            No account?{" "}
            <Link to="/register" className="text-primary hover:underline" data-testid="go-register-link">
              Create one
            </Link>
          </p>
        </form>
      </div>
    </div>
  );
}

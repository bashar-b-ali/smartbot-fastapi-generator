import React from 'react';
import { Link } from 'react-router-dom';
import { Sparkles, ShieldCheck, Zap, Code2 } from 'lucide-react';
import { motion } from 'framer-motion';
import ThemeToggle from '../layout/ThemeToggle';

const highlights = [
  { icon: Zap,         title: 'Build faster',     body: 'A context-aware assistant that reads your project.' },
  { icon: Code2,       title: 'Own every file',   body: 'Browse, search, and download your workspace.' },
  { icon: ShieldCheck, title: 'Secure by default', body: 'JWT auth, email verification, recovery flows.' },
];

const AuthShell = ({ title, subtitle, children }) => (
  <div className="min-h-screen w-full overflow-x-hidden grid lg:grid-cols-[1fr_1fr] bg-surface-muted text-ink">
    {/* ──────── LEFT — branded panel ──────── */}
    <aside className="relative hidden lg:flex flex-col overflow-hidden text-white p-10 xl:p-14">
      {/* Background */}
      <div className="absolute inset-0 bg-gradient-to-br from-primary-600 via-primary-700 to-accent-700" />
      <div className="absolute inset-0 bg-grid-dark opacity-20" />
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute -top-32 -left-32 h-[28rem] w-[28rem] rounded-full bg-white/15 blur-3xl animate-blob" />
        <div className="absolute bottom-[-10rem] right-[-6rem] h-80 w-80 rounded-full bg-accent-300/30 blur-3xl animate-blob [animation-delay:3s]" />
      </div>

      {/* Brand */}
      <Link to="/" className="relative z-10 inline-flex items-center gap-2.5 self-start group">
        <span className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-white/15 backdrop-blur ring-1 ring-white/30 group-hover:bg-white/25 transition-colors">
          <Sparkles className="h-5 w-5" />
        </span>
        <span className="text-xl font-bold tracking-tight">AI Bot</span>
      </Link>

      {/* Centered message */}
      <div className="relative z-10 flex-1 flex flex-col justify-center max-w-md">
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.55, ease: [0.16, 1, 0.3, 1] }}
        >
          <span className="inline-flex items-center gap-1.5 rounded-full border border-white/20 bg-white/10 backdrop-blur px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wider">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-300" />
            FastAPI workspace
          </span>
          <h2 className="mt-5 text-3xl xl:text-4xl font-bold leading-[1.1] tracking-tight">
            The AI workspace built for shipping{' '}
            <span className="bg-gradient-to-r from-white to-sky-200 bg-clip-text text-transparent">
              FastAPI services
            </span>
            .
          </h2>
          <p className="mt-4 text-white/80 leading-relaxed">
            A focused environment that gets out of your way and helps you go from idea to deploy.
          </p>
        </motion.div>

        <ul className="mt-10 space-y-4">
          {highlights.map(({ icon: Icon, title, body }, i) => (
            <motion.li
              key={title}
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.4, delay: 0.25 + i * 0.07, ease: [0.16, 1, 0.3, 1] }}
              className="flex items-start gap-3"
            >
              <span className="inline-flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg bg-white/15 ring-1 ring-white/20 backdrop-blur">
                <Icon className="h-4 w-4" />
              </span>
              <div>
                <p className="font-semibold text-[14px]">{title}</p>
                <p className="text-[13px] text-white/70 leading-snug">{body}</p>
              </div>
            </motion.li>
          ))}
        </ul>
      </div>

      <p className="relative z-10 text-xs text-white/55">
        © {new Date().getFullYear()} AI Bot — crafted for developers.
      </p>
    </aside>

    {/* ──────── RIGHT — form panel ──────── */}
    <main className="relative flex flex-col overflow-hidden">
      {/* Subtle ambient blobs (contained) */}
      <div className="pointer-events-none absolute inset-0 -z-10 opacity-50">
        <div className="absolute top-[-8rem] right-[-6rem] h-72 w-72 rounded-full bg-primary-500/10 blur-3xl" />
        <div className="absolute bottom-[-6rem] left-[-4rem] h-64 w-64 rounded-full bg-accent-500/10 blur-3xl" />
      </div>

      {/* Top bar */}
      <div className="flex items-center justify-between p-4 sm:p-6 lg:hidden">
        <Link to="/" className="flex items-center gap-2">
          <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-primary-500 to-accent-500 text-white shadow-soft">
            <Sparkles className="h-4 w-4" />
          </span>
          <span className="font-semibold">AI Bot</span>
        </Link>
        <ThemeToggle />
      </div>
      <div className="hidden lg:flex justify-end p-6">
        <ThemeToggle />
      </div>

      {/* Form */}
      <div className="flex-1 flex items-center justify-center px-4 sm:px-6 lg:px-12 pb-10 pt-2">
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
          className="w-full max-w-md"
        >
          <div className="mb-7">
            <h1 className="text-2xl sm:text-[28px] font-bold tracking-tight leading-tight">
              {title}
            </h1>
            {subtitle && (
              <p className="mt-2 text-sm text-ink-muted leading-relaxed">{subtitle}</p>
            )}
          </div>
          {children}
        </motion.div>
      </div>
    </main>
  </div>
);

export default AuthShell;

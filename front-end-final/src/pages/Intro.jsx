import React from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  Sparkles,
  LayoutDashboard,
  LogOut,
  ArrowRight,
} from 'lucide-react';
import { motion } from 'framer-motion';
import { useAuth } from '../contexts/AuthContext';
import Button from '../components/common/Button';
import ThemeToggle from '../components/layout/ThemeToggle';

/* ────────────────────────────────────────────────────────────
 * Trust / tech stack strip
 * ──────────────────────────────────────────────────────────── */
const stackItems = [
  { label: 'FastAPI',     emoji: '⚡' },
  { label: 'React 18',    emoji: '⚛' },
  { label: 'JWT Auth',    emoji: '🔐' },
  { label: 'WebSockets',  emoji: '🔌' },
  { label: 'PostgreSQL',  emoji: '🐘' },
  { label: 'Tailwind',    emoji: '🎨' },
];

/* ────────────────────────────────────────────────────────────
 * Page
 * ──────────────────────────────────────────────────────────── */
const Intro = () => {
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const isLoggedIn = !!user;

  const handleLogout = async () => {
    try {
      await logout();
    } finally {
      navigate('/');
    }
  };

  return (
    <div className="relative min-h-screen overflow-hidden bg-surface-muted text-ink">
      {/* ────── Decorative background layer ────── */}
      <div className="pointer-events-none absolute inset-0 -z-10">
        <div className="absolute inset-0 bg-mesh opacity-60 dark:opacity-25" />
        <div className="absolute top-[-12%] left-[-10%] h-[36rem] w-[36rem] rounded-full bg-primary-500/15 blur-3xl animate-blob" />
        <div className="absolute bottom-[-18%] right-[-12%] h-[32rem] w-[32rem] rounded-full bg-accent-500/15 blur-3xl animate-blob [animation-delay:2s]" />
        <div className="absolute inset-x-0 top-0 h-[60vh] bg-gradient-to-b from-transparent via-transparent to-surface-muted" />
        <div className="absolute inset-0 bg-grid-light dark:bg-grid-dark [mask-image:radial-gradient(ellipse_at_center,black_25%,transparent_70%)] opacity-60" />
      </div>

      {/* ────── Top nav ────── */}
      <header className="relative z-10 px-4 sm:px-6 lg:px-8">
        <nav className="max-w-7xl mx-auto flex items-center justify-between py-5">
          <Link to="/" className="flex items-center gap-2.5 group">
            <span className="relative inline-flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-primary-500 to-accent-500 text-white shadow-soft group-hover:shadow-glow-primary transition-shadow duration-300">
              <Sparkles className="h-5 w-5" />
            </span>
            <span className="text-lg font-bold tracking-tight">AI Bot</span>
          </Link>
          <div className="flex items-center gap-2 sm:gap-3">
            <ThemeToggle />
            {isLoggedIn ? (
              <>
                <Button
                  as={Link}
                  to="/dashboard"
                  variant="secondary"
                  size="sm"
                  leftIcon={<LayoutDashboard className="h-4 w-4" />}
                >
                  <span className="hidden sm:inline">Dashboard</span>
                </Button>
                <Button
                  variant="primary"
                  size="sm"
                  leftIcon={<LogOut className="h-4 w-4" />}
                  onClick={handleLogout}
                >
                  <span className="hidden sm:inline">Sign out</span>
                </Button>
              </>
            ) : (
              <>
                <Button as={Link} to="/login" variant="ghost" size="sm">
                  Sign in
                </Button>
                <Button
                  as={Link}
                  to="/register"
                  variant="primary"
                  size="sm"
                  rightIcon={<ArrowRight className="h-4 w-4" />}
                >
                  Get started
                </Button>
              </>
            )}
          </div>
        </nav>
      </header>

      {/* ────── Hero ────── */}
      <section className="relative px-4 sm:px-6 lg:px-8 pt-8 sm:pt-14">
        <div className="max-w-5xl mx-auto text-center">
          {/* Eyebrow pill */}
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
            className="inline-flex items-center gap-2 rounded-full border border-line bg-surface-raised/80 backdrop-blur px-3.5 py-1.5 text-xs font-medium text-ink-muted shadow-card"
          >
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
            </span>
            <span>v0.1 · Live preview</span>
            <span className="text-ink-subtle">·</span>
            <span className="text-primary-600 dark:text-primary-300 font-semibold">FastAPI native</span>
          </motion.div>

          <motion.h1
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.65, ease: [0.16, 1, 0.3, 1], delay: 0.05 }}
            className="mt-7 text-4xl sm:text-6xl lg:text-7xl font-bold tracking-tight leading-[1.02]"
          >
            Ship FastAPI services
            <span className="block mt-1.5 bg-gradient-to-r from-primary-500 via-accent-500 to-sky-400 bg-clip-text text-transparent pb-2">
              at the speed of thought.
            </span>
          </motion.h1>

          <motion.p
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1], delay: 0.15 }}
            className="mt-6 max-w-2xl mx-auto text-base sm:text-lg text-ink-muted leading-relaxed"
          >
            A focused workspace for designing, browsing, and reasoning about FastAPI
            services — paired with a context-aware coding assistant that actually reads
            your project.
          </motion.p>

          <motion.div
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1], delay: 0.22 }}
            className="mt-10 flex flex-col sm:flex-row items-center justify-center gap-3"
          >
            {isLoggedIn ? (
              <Button
                as={Link}
                to="/dashboard"
                variant="primary"
                size="xl"
                rightIcon={<ArrowRight className="h-5 w-5" />}
              >
                Open dashboard
              </Button>
            ) : (
              <>
                <Button
                  as={Link}
                  to="/register"
                  variant="primary"
                  size="xl"
                  rightIcon={<ArrowRight className="h-5 w-5" />}
                >
                  Get started
                </Button>
                <Button as={Link} to="/login" variant="secondary" size="xl">
                  Sign in
                </Button>
              </>
            )}
          </motion.div>

          {/* Trust strip */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.6, delay: 0.32 }}
            className="mt-10 flex flex-wrap items-center justify-center gap-2"
          >
            {stackItems.map((s, i) => (
              <span
                key={s.label}
                className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface-raised/70 backdrop-blur px-3 py-1 text-[11px] font-medium text-ink-muted hover:text-ink hover:border-line-strong transition-colors"
                style={{ animationDelay: `${i * 60}ms` }}
              >
                <span aria-hidden>{s.emoji}</span>
                {s.label}
              </span>
            ))}
          </motion.div>

        </div>
      </section>
    </div>
  );
};

export default Intro;

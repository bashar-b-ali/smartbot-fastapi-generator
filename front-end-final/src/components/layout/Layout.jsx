import React, { useEffect, useRef, useState } from 'react';
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom';
import {
  LayoutDashboard,
  LogOut,
  Sparkles,
  Menu,
  X,
  ChevronDown,
  UserCircle2,
  Settings,
} from 'lucide-react';
import { AnimatePresence, motion } from 'framer-motion';
import { useAuth } from '../../contexts/AuthContext';
import Avatar from '../common/Avatar';
import IconButton from '../common/IconButton';
import { cn } from '../common/cn';
import ThemeToggle from './ThemeToggle';

const dropdownMotion = {
  initial: { opacity: 0, y: -6, scale: 0.97 },
  animate: { opacity: 1, y: 0, scale: 1, transition: { duration: 0.18, ease: [0.16, 1, 0.3, 1] } },
  exit:    { opacity: 0, y: -4, scale: 0.98, transition: { duration: 0.12, ease: [0.4, 0, 1, 1] } },
};

const drawerMotion = {
  initial: { x: '-100%' },
  animate: { x: 0, transition: { type: 'spring', stiffness: 320, damping: 36, mass: 0.8 } },
  exit:    { x: '-100%', transition: { duration: 0.22, ease: [0.4, 0, 1, 1] } },
};

const navItems = [
  { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/settings', label: 'Settings', icon: Settings },
];

const Layout = ({ children }) => {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const userMenuRef = useRef(null);

  useEffect(() => {
    const onClick = (e) => {
      if (userMenuRef.current && !userMenuRef.current.contains(e.target)) {
        setUserMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, []);

  useEffect(() => {
    setMenuOpen(false);
    setUserMenuOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') {
        setMenuOpen(false);
        setUserMenuOpen(false);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => {
    if (!menuOpen) return undefined;
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previous;
    };
  }, [menuOpen]);

  const handleLogout = async () => {
    await logout();
    navigate('/');
  };

  return (
    <div className="min-h-screen bg-surface-muted text-ink">
      <header className="sticky top-0 z-40 border-b border-line glass">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex h-14 items-center justify-between">
            <div className="flex items-center gap-3">
              <button
                className="md:hidden -ml-2 inline-flex h-9 w-9 items-center justify-center rounded-lg text-ink-muted transition-smooth hover:bg-surface-muted hover:text-ink"
                onClick={() => setMenuOpen(true)}
                aria-label="Open menu"
              >
                <Menu className="h-5 w-5" />
              </button>
              <Link
                to="/"
                className="flex min-w-0 items-center gap-2 group"
                aria-label="Go to homepage"
              >
                <span className="relative inline-flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-primary-500 to-accent-500 text-white shadow-soft group-hover:shadow-pop transition-shadow">
                  <Sparkles className="h-4 w-4" />
                </span>
                <span className="text-base font-bold tracking-tight">AI Bot</span>
              </Link>

              <nav className="hidden md:flex items-center gap-1 ml-4">
                {navItems.map(({ to, label, icon: Icon }) => (
                  <NavLink
                    key={to}
                    to={to}
                    className={({ isActive }) =>
                      cn(
                    'inline-flex items-center gap-1.5 px-2.5 h-8 rounded-lg text-xs font-medium transition-smooth',
                        isActive
                          ? 'bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300'
                          : 'text-ink-muted hover:text-ink hover:bg-surface-muted'
                      )
                    }
                  >
                    <Icon className="h-4 w-4" />
                    {label}
                  </NavLink>
                ))}
              </nav>
            </div>

            <div className="flex items-center gap-2">
              <ThemeToggle />
              <div className="relative" ref={userMenuRef}>
                <button
                  onClick={() => setUserMenuOpen((o) => !o)}
                  aria-haspopup="menu"
                  aria-expanded={userMenuOpen}
                  className={cn(
                    'flex min-w-0 items-center gap-2 pl-1 pr-2 sm:pr-3 h-9 rounded-full border border-line bg-surface-raised',
                    'hover:border-line-strong transition-smooth shadow-card'
                  )}
                >
                  <Avatar user={user} size="sm" />
                  <div className="hidden sm:flex flex-col items-start leading-none">
                    <span className="text-xs font-semibold text-ink truncate max-w-[10rem]">
                      {user?.first_name || user?.email?.split('@')[0] || 'Account'}
                    </span>
                    <span className="text-[11px] text-ink-subtle truncate max-w-[10rem]">
                      {user?.email}
                    </span>
                  </div>
                  <ChevronDown
                    className={cn(
                      'h-4 w-4 text-ink-subtle transition-transform',
                      userMenuOpen && 'rotate-180'
                    )}
                  />
                </button>

                <AnimatePresence>
                  {userMenuOpen && (
                    <motion.div
                      role="menu"
                      variants={dropdownMotion}
                      initial="initial"
                      animate="animate"
                      exit="exit"
                      className="absolute right-0 mt-2 w-64 origin-top-right rounded-xl border border-line bg-surface-raised shadow-pop p-1"
                    >
                      <div className="px-3 py-2.5 border-line">
                        <p className="text-sm font-semibold text-ink truncate">
                          {user?.first_name} {user?.last_name}
                        </p>
                        <p className="text-xs text-ink-subtle truncate">{user?.email}</p>
                      </div>
                      <button
                        role="menuitem"
                        onClick={() => {
                          setUserMenuOpen(false);
                          navigate('/profile');
                        }}
                        className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm text-ink-muted hover:bg-surface-muted hover:text-ink transition-colors"
                      >
                        <UserCircle2 className="h-4 w-4" />
                        Profile
                      </button>
                      <button
                        role="menuitem"
                        onClick={() => {
                          setUserMenuOpen(false);
                          navigate('/settings');
                        }}
                        className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm text-ink-muted hover:bg-surface-muted hover:text-ink transition-colors"
                      >
                        <Settings className="h-4 w-4" />
                        Settings
                      </button>
                      <div className="my-1 h-px bg-line" />
                      <button
                        role="menuitem"
                        onClick={handleLogout}
                        className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-500/10 transition-colors"
                      >
                        <LogOut className="h-4 w-4" />
                        Sign out
                      </button>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            </div>
          </div>
        </div>
      </header>

      <AnimatePresence>
        {menuOpen && (
          <div className="fixed inset-0 z-50 md:hidden">
            <motion.div
              className="absolute inset-0 bg-slate-900/60 backdrop-blur-sm"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1, transition: { duration: 0.18 } }}
              exit={{ opacity: 0, transition: { duration: 0.14 } }}
              onClick={() => setMenuOpen(false)}
            />
            <motion.aside
              variants={drawerMotion}
              initial="initial"
              animate="animate"
              exit="exit"
              className="absolute left-0 top-0 flex h-full w-72 max-w-[85vw] flex-col overflow-y-auto bg-surface-raised border-r border-line shadow-pop p-4 safe-bottom-3"
            >
              <div className="flex items-center justify-between mb-6">
              <Link to="/" className="flex items-center gap-2">
                <span className="inline-flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-primary-500 to-accent-500 text-white">
                  <Sparkles className="h-4 w-4" />
                </span>
                <span className="text-base font-bold">AI Bot</span>
              </Link>
              <IconButton
                onClick={() => setMenuOpen(false)}
                label="Close menu"
                tone="ghost"
                size="sm"
              >
                <X className="h-4 w-4" />
              </IconButton>
            </div>

            <div className="flex items-center gap-3 p-3 rounded-xl bg-surface-muted mb-4">
              <Avatar user={user} size="md" />
              <div className="min-w-0">
                <p className="text-sm font-semibold truncate">
                  {user?.first_name} {user?.last_name}
                </p>
                <p className="text-xs text-ink-subtle truncate">{user?.email}</p>
              </div>
            </div>

            <nav className="space-y-1">
              {navItems.map(({ to, label, icon: Icon }) => (
                <NavLink
                  key={to}
                  to={to}
                  className={({ isActive }) =>
                    cn(
                      'flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-smooth',
                      isActive
                        ? 'bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300'
                        : 'text-ink-muted hover:bg-surface-muted hover:text-ink'
                    )
                  }
                >
                  <Icon className="h-4 w-4" />
                  {label}
                </NavLink>
              ))}
              <button
                onClick={handleLogout}
                className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-500/10 transition-smooth"
              >
                <LogOut className="h-4 w-4" />
                Sign out
              </button>
            </nav>
            </motion.aside>
          </div>
        )}
      </AnimatePresence>

      <main>{children}</main>
    </div>
  );
};

export default Layout;


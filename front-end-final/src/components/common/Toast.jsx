import React, { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';
import { CheckCircle2, AlertTriangle, AlertCircle, Info, X } from 'lucide-react';
import { AnimatePresence, motion } from 'framer-motion';
import { cn } from './cn';

const ToastContext = createContext(null);

export const useToast = () => {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used inside ToastProvider');
  return ctx;
};

const tones = {
  success: { icon: CheckCircle2, ring: 'ring-emerald-500/30', iconColor: 'text-emerald-500' },
  error:   { icon: AlertCircle,  ring: 'ring-red-500/30',     iconColor: 'text-red-500' },
  warning: { icon: AlertTriangle,ring: 'ring-amber-500/30',   iconColor: 'text-amber-500' },
  info:    { icon: Info,         ring: 'ring-primary-500/30', iconColor: 'text-primary-500' },
};

const toastMotion = {
  initial: { opacity: 0, y: -16, scale: 0.96 },
  animate: { opacity: 1, y: 0, scale: 1, transition: { duration: 0.24, ease: [0.2, 0.8, 0.2, 1] } },
  exit:    { opacity: 0, y: -8, scale: 0.97, transition: { duration: 0.16, ease: [0.4, 0, 1, 1] } },
};

export const ToastProvider = ({ children }) => {
  const [toasts, setToasts] = useState([]);
  const idRef = useRef(0);

  const dismiss = useCallback((id) => {
    setToasts((list) => list.filter((t) => t.id !== id));
  }, []);

  const push = useCallback(
    (toast) => {
      const id = ++idRef.current;
      const t = {
        id,
        type: 'info',
        duration: 4500,
        ...toast,
      };
      setToasts((list) => [...list, t]);
      if (t.duration > 0) {
        setTimeout(() => dismiss(id), t.duration);
      }
      return id;
    },
    [dismiss]
  );

  const api = useMemo(
    () => ({
      toast: push,
      success: (text, opts) => push({ type: 'success', text, ...opts }),
      error:   (text, opts) => push({ type: 'error',   text, ...opts }),
      warning: (text, opts) => push({ type: 'warning', text, ...opts }),
      info:    (text, opts) => push({ type: 'info',    text, ...opts }),
      dismiss,
    }),
    [push, dismiss]
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        className="pointer-events-none fixed top-4 right-4 z-[80] flex flex-col gap-2 max-w-[calc(100vw-2rem)] w-full sm:w-96"
        role="region"
        aria-label="Notifications"
      >
        <AnimatePresence initial={false}>
          {toasts.map((t) => {
            const tone = tones[t.type] || tones.info;
            const Icon = tone.icon;
            return (
              <motion.div
                key={t.id}
                layout
                initial={toastMotion.initial}
                animate={toastMotion.animate}
                exit={toastMotion.exit}
                role="status"
                className={cn(
                  'pointer-events-auto flex items-start gap-3 rounded-xl border border-line bg-surface-raised',
                  'shadow-pop p-3 ring-2',
                  tone.ring
                )}
              >
                <Icon className={cn('h-5 w-5 mt-0.5 flex-shrink-0', tone.iconColor)} />
                <div className="flex-1 min-w-0 text-sm">
                  {t.title && <p className="font-semibold text-ink">{t.title}</p>}
                  {t.text && <p className="text-ink-muted leading-snug whitespace-pre-line">{t.text}</p>}
                </div>
                <button
                  onClick={() => dismiss(t.id)}
                  aria-label="Dismiss notification"
                  className="-m-1 p-1 rounded-md text-ink-subtle hover:text-ink hover:bg-surface-muted transition-colors"
                >
                  <X className="h-4 w-4" />
                </button>
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  );
};

export default ToastProvider;

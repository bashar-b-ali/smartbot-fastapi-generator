import React from 'react';
import { motion } from 'framer-motion';
import { cn } from './cn';

const EmptyState = ({ icon: Icon, title, description, action, secondaryAction, className }) => (
  <div
    className={cn(
      'relative overflow-hidden rounded-3xl border border-line bg-surface-raised',
      'text-center px-6 py-16 sm:py-20',
      className
    )}
  >
    {/* Decorative dotted grid + soft gradient halo */}
    <div className="pointer-events-none absolute inset-0 bg-grid-light dark:bg-grid-dark opacity-50 [mask-image:radial-gradient(ellipse_at_center,black_20%,transparent_70%)]" />
    <div className="pointer-events-none absolute -top-24 left-1/2 -translate-x-1/2 h-72 w-[28rem] rounded-full bg-gradient-to-br from-primary-500/15 via-accent-500/12 to-sky-500/15 blur-3xl" />

    <div className="relative">
      {Icon && (
        <motion.div
          initial={{ scale: 0.8, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ type: 'spring', stiffness: 320, damping: 22 }}
          className="mx-auto mb-6 inline-flex"
        >
          {/* Stacked rings: ambient ring + soft gradient ring + icon chip */}
          <span className="relative inline-flex">
            <span className="absolute inset-[-14px] rounded-3xl border border-line/70 animate-breathe" />
            <span className="absolute inset-[-6px] rounded-2xl bg-gradient-to-br from-primary-500/15 to-accent-500/15" />
            <span className="relative inline-flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-primary-500 to-accent-500 text-white shadow-glow-primary">
              <Icon className="h-7 w-7" />
            </span>
          </span>
        </motion.div>
      )}
      {title && (
        <motion.h3
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0.1, ease: [0.16, 1, 0.3, 1] }}
          className="text-2xl font-bold tracking-tight text-ink"
        >
          {title}
        </motion.h3>
      )}
      {description && (
        <motion.p
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0.18, ease: [0.16, 1, 0.3, 1] }}
          className="mt-2 max-w-md mx-auto text-sm text-ink-muted leading-relaxed"
        >
          {description}
        </motion.p>
      )}
      {(action || secondaryAction) && (
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0.26, ease: [0.16, 1, 0.3, 1] }}
          className="mt-7 flex flex-col sm:flex-row items-center justify-center gap-2.5"
        >
          {action}
          {secondaryAction}
        </motion.div>
      )}
    </div>
  </div>
);

export default EmptyState;

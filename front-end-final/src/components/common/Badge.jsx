import React from 'react';
import { cn } from './cn';

const tones = {
  neutral: 'bg-surface-muted text-ink-muted border-line',
  primary: 'bg-primary-50 text-primary-700 border-primary-100 dark:bg-primary-500/10 dark:text-primary-300 dark:border-primary-500/20',
  success: 'bg-emerald-50 text-emerald-700 border-emerald-100 dark:bg-emerald-500/10 dark:text-emerald-300 dark:border-emerald-500/20',
  warning: 'bg-amber-50 text-amber-700 border-amber-100 dark:bg-amber-500/10 dark:text-amber-300 dark:border-amber-500/20',
  danger:  'bg-red-50 text-red-700 border-red-100 dark:bg-red-500/10 dark:text-red-300 dark:border-red-500/20',
  accent:  'bg-accent-50 text-accent-700 border-accent-100 dark:bg-accent-500/10 dark:text-accent-300 dark:border-accent-500/20',
};

const Badge = ({ tone = 'neutral', className, children, leftIcon, ...rest }) => (
  <span
    className={cn(
      'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium',
      tones[tone] || tones.neutral,
      className
    )}
    {...rest}
  >
    {leftIcon && <span className="inline-flex">{leftIcon}</span>}
    {children}
  </span>
);

export default Badge;

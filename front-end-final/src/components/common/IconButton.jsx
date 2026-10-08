import React, { forwardRef } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { cn } from './cn';

const sizes = {
  sm: 'h-8 w-8 rounded-md',
  md: 'h-9 w-9 rounded-lg',
  lg: 'h-10 w-10 rounded-lg',
};

const tones = {
  ghost:   'text-ink-muted hover:text-ink hover:bg-surface-muted',
  subtle:  'bg-surface-muted text-ink hover:bg-surface-raised border border-line',
  danger:  'text-red-500 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-500/10',
  primary: 'text-primary-600 hover:text-primary-700 hover:bg-primary-50 dark:hover:bg-primary-500/10',
};

const IconButton = forwardRef(function IconButton(
  { className, children, label, size = 'md', tone = 'ghost', disabled, ...rest },
  ref
) {
  const reduceMotion = useReducedMotion();
  const Tag = reduceMotion ? 'button' : motion.button;
  const isMotion = Tag !== 'button';

  const motionProps = isMotion
    ? {
        whileHover: disabled ? undefined : { scale: 1.06 },
        whileTap:   disabled ? undefined : { scale: 0.92 },
        transition: { type: 'spring', stiffness: 420, damping: 22, mass: 0.5 },
      }
    : {};

  return (
    <Tag
      ref={ref}
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      className={cn(
        'inline-flex items-center justify-center transition-smooth gpu',
        'focus-visible:ring-2 focus-visible:ring-primary-500/40',
        'disabled:opacity-50 disabled:cursor-not-allowed',
        sizes[size],
        tones[tone],
        className
      )}
      {...motionProps}
      {...rest}
    >
      {children}
    </Tag>
  );
});

export default IconButton;

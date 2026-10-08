import React, { forwardRef } from 'react';
import { Loader2 } from 'lucide-react';
import { motion, useReducedMotion } from 'framer-motion';
import { cn } from './cn';

const variants = {
  primary:
    'bg-gradient-to-r from-primary-600 to-accent-600 text-white shadow-soft hover:shadow-pop hover:brightness-110 active:brightness-95',
  solid:
    'bg-primary-600 text-white hover:bg-primary-700 active:bg-primary-800 shadow-card hover:shadow-soft',
  secondary:
    'bg-surface-raised text-ink border border-line hover:border-line-strong hover:bg-surface-muted shadow-card',
  ghost:
    'bg-transparent text-ink-muted hover:bg-surface-muted hover:text-ink',
  danger:
    'bg-red-600 text-white hover:bg-red-700 active:bg-red-800 shadow-card hover:shadow-soft',
  subtle:
    'bg-primary-50 text-primary-700 hover:bg-primary-100 dark:bg-primary-500/10 dark:text-primary-300 dark:hover:bg-primary-500/20',
};

const sizes = {
  xs: 'h-7 px-2.5 text-xs gap-1 rounded-md',
  sm: 'h-8 px-2.5 text-xs gap-1.5 rounded-md',
  md: 'h-9 px-3.5 text-sm gap-1.5 rounded-lg',
  lg: 'h-10 px-4 text-sm gap-2 rounded-lg',
  xl: 'h-11 px-5 text-sm gap-2 rounded-xl font-semibold',
  icon: 'h-9 w-9 rounded-lg',
  'icon-sm': 'h-7 w-7 rounded-md',
};

const MotionButton = motion.button;

const Button = forwardRef(function Button(
  {
    as,
    variant = 'solid',
    size = 'md',
    loading = false,
    leftIcon,
    rightIcon,
    fullWidth = false,
    className,
    children,
    disabled,
    type = 'button',
    motionProps,
    ...rest
  },
  ref
) {
  const reduceMotion = useReducedMotion();
  const isDisabled = disabled || loading;

  // For non-button render targets we fall back to a plain element (no motion)
  // so that anchor/Link semantics aren't disrupted by motion-component wrapping.
  const Component = as || (reduceMotion ? 'button' : MotionButton);
  const isMotion = Component === MotionButton;

  const motionDefaults = isMotion
    ? {
        whileHover: isDisabled ? undefined : { y: -1, scale: 1.015 },
        whileTap:   isDisabled ? undefined : { scale: 0.97 },
        transition: { type: 'spring', stiffness: 380, damping: 26, mass: 0.6 },
        ...motionProps,
      }
    : {};

  return (
    <Component
      ref={ref}
      type={typeof Component === 'string' && Component === 'button' ? type : (isMotion ? type : undefined)}
      aria-busy={loading || undefined}
      disabled={isMotion || Component === 'button' ? isDisabled : undefined}
      className={cn(
        'relative inline-flex min-w-0 items-center justify-center font-medium select-none gpu',
        'transition-smooth',
        'disabled:opacity-50 disabled:cursor-not-allowed disabled:pointer-events-none',
        'focus-visible:ring-2 focus-visible:ring-primary-500/40 focus-visible:ring-offset-2 focus-visible:ring-offset-surface',
        variants[variant] || variants.solid,
        sizes[size] || sizes.md,
        fullWidth && 'w-full',
        className
      )}
      {...motionDefaults}
      {...rest}
    >
      {loading ? (
        <Loader2 className="h-4 w-4 animate-spin" />
      ) : (
        leftIcon && <span className="-ml-0.5 inline-flex">{leftIcon}</span>
      )}
      {children && <span className="min-w-0 truncate whitespace-nowrap">{children}</span>}
      {!loading && rightIcon && <span className="-mr-0.5 inline-flex">{rightIcon}</span>}
    </Component>
  );
});

export default Button;

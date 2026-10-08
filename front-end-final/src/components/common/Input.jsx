import React, { forwardRef, useId } from 'react';
import { AlertCircle, CheckCircle2 } from 'lucide-react';
import { cn } from './cn';

/**
 * Input - clean, elevated form field.
 * Icons are absolutely positioned (no flex shifting), the field has a subtle
 * inset + outer shadow for depth, and focus produces a soft 4px halo.
 */
const Input = forwardRef(function Input(
  {
    label,
    hint,
    error,
    success,
    leftIcon,
    rightSlot,
    className,
    inputClassName,
    id,
    type = 'text',
    size = 'md',
    placeholder,
    disabled,
    ...rest
  },
  ref
) {
  const autoId = useId();
  const inputId = id || autoId;

  const tone = error ? 'error' : success ? 'success' : 'idle';

  // Heights chosen for a compact type rhythm with text-sm.
  const heights = { sm: 'h-8', md: 'h-10', lg: 'h-11' };
  const h = heights[size] || heights.md;

  const fieldTone = {
    idle:    'border-line hover:border-line-strong focus-within:border-primary-500 focus-within:ring-4 focus-within:ring-primary-500/18',
    error:   'border-red-400 focus-within:border-red-500 focus-within:ring-4 focus-within:ring-red-500/18',
    success: 'border-emerald-400 focus-within:border-emerald-500 focus-within:ring-4 focus-within:ring-emerald-500/18',
  };

  // Padding accounts for absolutely-positioned icons / right slot.
  const padLeft  = leftIcon ? 'pl-9' : 'pl-3';
  const padRight = rightSlot ? 'pr-10' : (tone !== 'idle' ? 'pr-9' : 'pr-3');

  return (
    <div className={cn('w-full', className)}>
      {label && (
        <label
          htmlFor={inputId}
          className={cn(
            'mb-1 block text-xs font-semibold transition-colors duration-200',
            tone === 'error' ? 'text-red-500'
              : tone === 'success' ? 'text-emerald-600'
              : 'text-ink'
          )}
        >
          {label}
        </label>
      )}

      <div
        className={cn(
          'group relative w-full rounded-lg border bg-surface-raised',
          // Subtle depth: inset top highlight + soft outer shadow.
          'shadow-[inset_0_1px_0_rgba(255,255,255,0.6),0_1px_2px_rgba(15,23,42,0.04)]',
          'dark:shadow-[inset_0_1px_0_rgba(255,255,255,0.04),0_1px_2px_rgba(0,0,0,0.2)]',
          'transition-[border-color,box-shadow,transform] duration-200 ease-out-soft',
          'focus-within:shadow-[0_2px_4px_rgba(58,99,245,0.06),0_8px_24px_-12px_rgba(58,99,245,0.18)]',
          disabled && 'opacity-60 cursor-not-allowed bg-surface-muted',
          fieldTone[tone]
        )}
      >
        {/* Left icon (absolute, doesn't shift layout) */}
        {leftIcon && (
          <span
            className={cn(
              'pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 transition-colors duration-200',
              tone === 'error' ? 'text-red-400 group-focus-within:text-red-500'
                : tone === 'success' ? 'text-emerald-500'
                : 'text-ink-subtle group-focus-within:text-primary-500'
            )}
          >
            {leftIcon}
          </span>
        )}

        <input
          ref={ref}
          id={inputId}
          type={type}
          disabled={disabled}
          placeholder={placeholder}
          aria-invalid={!!error || undefined}
          aria-describedby={
            error ? `${inputId}-error` : hint ? `${inputId}-hint` : undefined
          }
          className={cn(
            'block w-full bg-transparent text-sm text-ink placeholder:text-ink-subtle',
            'rounded-lg outline-none focus:outline-none disabled:cursor-not-allowed',
            h,
            padLeft,
            padRight,
            inputClassName
          )}
          {...rest}
        />

        {/* Tone indicator - only when there's no right slot to avoid stacking */}
        {!rightSlot && tone === 'success' && (
          <CheckCircle2 className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 h-4 w-4 text-emerald-500" />
        )}
        {!rightSlot && tone === 'error' && (
          <AlertCircle className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 h-4 w-4 text-red-500" />
        )}

        {/* Right slot (absolute) */}
        {rightSlot && (
          <span className="absolute right-1.5 top-1/2 -translate-y-1/2 flex items-center">
            {rightSlot}
          </span>
        )}
      </div>

      {error ? (
        <p
          id={`${inputId}-error`}
          className="mt-1.5 inline-flex items-center gap-1.5 text-xs font-medium text-red-500 animate-fade-in"
        >
          <AlertCircle className="h-3.5 w-3.5 flex-shrink-0" />
          {error}
        </p>
      ) : typeof success === 'string' ? (
        <p className="mt-1.5 inline-flex items-center gap-1.5 text-xs font-medium text-emerald-600 animate-fade-in">
          <CheckCircle2 className="h-3.5 w-3.5 flex-shrink-0" />
          {success}
        </p>
      ) : hint ? (
        <p id={`${inputId}-hint`} className="mt-1.5 text-xs text-ink-subtle">
          {hint}
        </p>
      ) : null}
    </div>
  );
});

export default Input;

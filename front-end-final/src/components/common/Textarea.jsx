import React, { forwardRef, useId } from 'react';
import { CheckCircle2, AlertCircle } from 'lucide-react';
import { cn } from './cn';

const Textarea = forwardRef(function Textarea(
  {
    label,
    hint,
    error,
    success,
    className,
    textareaClassName,
    id,
    rows = 4,
    showCounter = true,
    maxLength,
    disabled,
    ...rest
  },
  ref
) {
  const autoId = useId();
  const fieldId = id || autoId;
  const value = rest.value ?? rest.defaultValue ?? '';

  const tone = error ? 'error' : success ? 'success' : 'idle';

  const fieldTone = {
    idle:    'border-line hover:border-line-strong focus-within:border-primary-500 focus-within:ring-4 focus-within:ring-primary-500/18',
    error:   'border-red-400 focus-within:border-red-500 focus-within:ring-4 focus-within:ring-red-500/18',
    success: 'border-emerald-400 focus-within:border-emerald-500 focus-within:ring-4 focus-within:ring-emerald-500/18',
  };

  const charCount = typeof value === 'string' ? value.length : 0;
  const showCount = showCounter && maxLength != null;

  return (
    <div className={cn('w-full', className)}>
      {label && (
        <label
          htmlFor={fieldId}
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
          'shadow-[inset_0_1px_0_rgba(255,255,255,0.6),0_1px_2px_rgba(15,23,42,0.04)]',
          'dark:shadow-[inset_0_1px_0_rgba(255,255,255,0.04),0_1px_2px_rgba(0,0,0,0.2)]',
          'transition-[border-color,box-shadow] duration-200 ease-out-soft',
          'focus-within:shadow-[0_2px_4px_rgba(58,99,245,0.06),0_8px_24px_-12px_rgba(58,99,245,0.18)]',
          disabled && 'opacity-60 cursor-not-allowed bg-surface-muted',
          fieldTone[tone]
        )}
      >
        <textarea
          ref={ref}
          id={fieldId}
          rows={rows}
          maxLength={maxLength}
          disabled={disabled}
          aria-invalid={!!error || undefined}
          aria-describedby={
            error ? `${fieldId}-error` : hint ? `${fieldId}-hint` : undefined
          }
          className={cn(
            'block w-full bg-transparent px-3 py-2.5 text-sm text-ink placeholder:text-ink-subtle',
            'rounded-lg outline-none focus:outline-none disabled:cursor-not-allowed resize-none',
            'leading-6',
            textareaClassName
          )}
          {...rest}
        />

        {tone !== 'idle' && (
          <div className="pointer-events-none absolute top-3 right-3">
            {tone === 'success' ? (
              <CheckCircle2 className="h-4 w-4 text-emerald-500" />
            ) : (
              <AlertCircle className="h-4 w-4 text-red-500" />
            )}
          </div>
        )}
      </div>

      <div className="mt-1 flex items-start justify-between gap-3 text-xs">
        <div className="min-w-0">
          {error ? (
            <p
              id={`${fieldId}-error`}
              className="inline-flex items-center gap-1.5 font-medium text-red-500 animate-fade-in"
            >
              <AlertCircle className="h-3.5 w-3.5 flex-shrink-0" />
              {error}
            </p>
          ) : typeof success === 'string' ? (
            <p className="inline-flex items-center gap-1.5 font-medium text-emerald-600 animate-fade-in">
              <CheckCircle2 className="h-3.5 w-3.5 flex-shrink-0" />
              {success}
            </p>
          ) : hint ? (
            <p id={`${fieldId}-hint`} className="text-ink-subtle">
              {hint}
            </p>
          ) : null}
        </div>
        {showCount && (
          <p
            className={cn(
              'tabular-nums flex-shrink-0',
              charCount >= maxLength
                ? 'text-red-500 font-semibold'
                : charCount > maxLength * 0.85
                  ? 'text-amber-500'
                  : 'text-ink-subtle'
            )}
          >
            {charCount}/{maxLength}
          </p>
        )}
      </div>
    </div>
  );
});

export default Textarea;

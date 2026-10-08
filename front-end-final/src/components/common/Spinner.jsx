import React from 'react';
import { cn } from './cn';

const sizes = {
  xs: 'h-3 w-3 border-[1.5px]',
  sm: 'h-4 w-4 border-2',
  md: 'h-6 w-6 border-2',
  lg: 'h-8 w-8 border-[3px]',
  xl: 'h-12 w-12 border-[3px]',
};

export const Spinner = ({ size = 'md', className, label }) => (
  <span
    role="status"
    aria-live="polite"
    aria-label={label || 'Loading'}
    className={cn('inline-flex items-center justify-center', className)}
  >
    <span
      className={cn(
        'inline-block rounded-full animate-spin border-current border-t-transparent text-primary-500',
        sizes[size] || sizes.md
      )}
    />
    {label && <span className="sr-only">{label}</span>}
  </span>
);

export const FullPageSpinner = ({ label = 'Loading…' }) => (
  <div className="fixed inset-0 z-[60] flex items-center justify-center bg-surface/80 backdrop-blur-sm">
    <div className="flex flex-col items-center gap-3">
      <Spinner size="xl" />
      <p className="text-sm font-medium text-ink-muted">{label}</p>
    </div>
  </div>
);

export default Spinner;

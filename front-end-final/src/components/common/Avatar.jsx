import React from 'react';
import { cn } from './cn';
import { initialsOf } from '../../utils/formatters';

const sizes = {
  xs: 'h-6 w-6 text-[10px]',
  sm: 'h-8 w-8 text-xs',
  md: 'h-10 w-10 text-sm',
  lg: 'h-12 w-12 text-base',
  xl: 'h-16 w-16 text-lg',
};

const Avatar = ({ user, size = 'md', className, name }) => {
  const label = name || `${user?.first_name || ''} ${user?.last_name || ''}`.trim() || user?.email;
  return (
    <div
      className={cn(
        'inline-flex items-center justify-center rounded-full font-semibold text-white',
        'bg-gradient-to-br from-primary-500 to-accent-500 ring-2 ring-surface-raised select-none',
        sizes[size] || sizes.md,
        className
      )}
      aria-label={label}
      title={label}
    >
      {initialsOf(user)}
    </div>
  );
};

export default Avatar;

import React from 'react';
import { cn } from './cn';

export const Skeleton = ({ className, ...rest }) => (
  <div className={cn('skeleton', className)} {...rest} />
);

export const SkeletonText = ({ lines = 3, className }) => (
  <div className={cn('space-y-2', className)}>
    {Array.from({ length: lines }).map((_, i) => (
      <Skeleton
        key={i}
        className={cn('h-3 w-full', i === lines - 1 && 'w-2/3')}
      />
    ))}
  </div>
);

export const SkeletonCard = ({ className }) => (
  <div className={cn('rounded-2xl border border-line bg-surface-raised p-5', className)}>
    <div className="flex items-center gap-3 mb-4">
      <Skeleton className="h-10 w-10 rounded-lg" />
      <div className="flex-1 space-y-2">
        <Skeleton className="h-3 w-1/2" />
        <Skeleton className="h-3 w-1/3" />
      </div>
    </div>
    <Skeleton className="h-3 w-full mb-2" />
    <Skeleton className="h-3 w-4/5 mb-2" />
    <Skeleton className="h-3 w-3/5" />
  </div>
);

export default Skeleton;

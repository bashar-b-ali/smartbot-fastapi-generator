import React from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { cn } from './cn';

export const Card = ({ className, interactive = false, children, style, ...rest }) => {
  const reduceMotion = useReducedMotion();
  const baseClass = cn(
    'bg-surface-raised border border-line rounded-2xl shadow-card gpu',
    interactive && 'cursor-pointer',
    className
  );

  if (interactive && !reduceMotion) {
    return (
      <motion.div
        className={baseClass}
        style={style}
        whileHover={{
          y: -3,
          boxShadow:
            '0 1px 2px rgba(15,23,42,0.04), 0 16px 32px -8px rgba(15,23,42,0.16), 0 4px 8px -4px rgba(15,23,42,0.06)',
        }}
        transition={{ type: 'spring', stiffness: 320, damping: 26, mass: 0.7 }}
        {...rest}
      >
        {children}
      </motion.div>
    );
  }

  return (
    <div className={baseClass} style={style} {...rest}>
      {children}
    </div>
  );
};

export const CardHeader = ({ className, children, ...rest }) => (
  <div className={cn('p-5 border-line', className)} {...rest}>
    {children}
  </div>
);

export const CardBody = ({ className, children, ...rest }) => (
  <div className={cn('p-5', className)} {...rest}>
    {children}
  </div>
);

export const CardFooter = ({ className, children, ...rest }) => (
  <div className={cn('p-5 border-t border-line bg-surface-muted/40 rounded-b-2xl', className)} {...rest}>
    {children}
  </div>
);

export default Card;

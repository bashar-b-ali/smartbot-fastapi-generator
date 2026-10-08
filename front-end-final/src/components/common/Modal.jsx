import React, { useEffect } from 'react';
import { X } from 'lucide-react';
import { AnimatePresence, motion } from 'framer-motion';
import { cn } from './cn';

const sizes = {
  sm: 'max-w-sm',
  md: 'max-w-md',
  lg: 'max-w-lg',
  xl: 'max-w-2xl',
  '2xl': 'max-w-3xl',
};

const backdrop = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { duration: 0.14, ease: [0.16, 1, 0.3, 1] } },
  exit: { opacity: 0, transition: { duration: 0.1, ease: [0.4, 0, 1, 1] } },
};

const panel = {
  hidden: { opacity: 0, scale: 0.985, y: 8 },
  visible: {
    opacity: 1,
    scale: 1,
    y: 0,
    transition: { duration: 0.18, ease: [0.16, 1, 0.3, 1] },
  },
  exit: {
    opacity: 0,
    scale: 0.99,
    y: 5,
    transition: { duration: 0.1, ease: [0.4, 0, 1, 1] },
  },
};

const Modal = ({
  open,
  onClose,
  title,
  description,
  size = 'md',
  hideClose = false,
  className,
  bodyClassName,
  footer,
  children,
}) => {
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => e.key === 'Escape' && onClose?.();
    document.addEventListener('keydown', onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prev;
    };
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && (
        <div
          className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby={title ? 'modal-title' : undefined}
        >
          <motion.div
            variants={backdrop}
            initial="hidden"
            animate="visible"
            exit="exit"
            className="absolute inset-0 bg-slate-900/55 backdrop-blur-sm"
            onClick={onClose}
          />
          <motion.div
            variants={panel}
            initial="hidden"
            animate="visible"
            exit="exit"
            className={cn(
              'relative flex max-h-[92vh] w-full flex-col overflow-hidden bg-surface-raised border border-line shadow-pop will-change-transform',
              'rounded-t-xl sm:rounded-xl',
              sizes[size] || sizes.md,
              className
            )}
          >
            {(title || !hideClose) && (
              <div className="flex items-start justify-between gap-3 border-line p-4">
                <div className="min-w-0">
                  {title && (
                    <h2 id="modal-title" className="truncate text-base font-semibold text-ink">
                      {title}
                    </h2>
                  )}
                  {description && (
                    <p className="mt-1 text-xs leading-5 text-ink-muted">{description}</p>
                  )}
                </div>
                {!hideClose && (
                  <button
                    onClick={onClose}
                    aria-label="Close dialog"
                    className="-mr-1 -mt-1 rounded-lg p-1.5 text-ink-subtle transition-colors hover:bg-surface-muted hover:text-ink"
                  >
                    <X className="h-4 w-4" />
                  </button>
                )}
              </div>
            )}
            <div className={cn('overflow-y-auto p-4 scrollbar-fancy', bodyClassName)}>{children}</div>
            {footer && (
              <div className="flex flex-wrap items-center justify-end gap-2 rounded-b-xl border-t border-line bg-surface-muted/40 p-4">
                {footer}
              </div>
            )}
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
};

export default Modal;

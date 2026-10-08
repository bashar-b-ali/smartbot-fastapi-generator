import React from 'react';
import { CheckCircle2, AlertCircle, AlertTriangle, Info, X } from 'lucide-react';
import { cn } from './cn';

const tones = {
  success: { icon: CheckCircle2,  color: 'text-emerald-600 dark:text-emerald-400', bg: 'bg-emerald-50 dark:bg-emerald-500/10', border: 'border-emerald-200 dark:border-emerald-500/20' },
  error:   { icon: AlertCircle,   color: 'text-red-600 dark:text-red-400',         bg: 'bg-red-50 dark:bg-red-500/10',         border: 'border-red-200 dark:border-red-500/20' },
  warning: { icon: AlertTriangle, color: 'text-amber-600 dark:text-amber-400',     bg: 'bg-amber-50 dark:bg-amber-500/10',     border: 'border-amber-200 dark:border-amber-500/20' },
  info:    { icon: Info,          color: 'text-primary-600 dark:text-primary-400', bg: 'bg-primary-50 dark:bg-primary-500/10', border: 'border-primary-200 dark:border-primary-500/20' },
};

const Message = ({ type = 'info', text, onClose, inline = false, className }) => {
  if (!text) return null;
  const tone = tones[type] || tones.info;
  const Icon = tone.icon;

  const content = (
    <div
      role={type === 'error' ? 'alert' : 'status'}
      className={cn(
        'flex items-start gap-3 rounded-xl border p-3.5 text-sm shadow-card',
        tone.bg,
        tone.border,
        className
      )}
    >
      <Icon className={cn('h-5 w-5 mt-0.5 flex-shrink-0', tone.color)} />
      <span className={cn('flex-1 font-medium', tone.color)}>{text}</span>
      {onClose && (
        <button
          onClick={onClose}
          aria-label="Dismiss"
          className={cn('p-0.5 rounded hover:bg-black/5 dark:hover:bg-white/10 transition-colors', tone.color)}
        >
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
  );

  if (inline) return content;

  return (
    <div className="fixed top-4 right-4 z-[70] max-w-md w-[calc(100%-2rem)] sm:w-96 animate-slide-down">
      {content}
    </div>
  );
};

export default Message;

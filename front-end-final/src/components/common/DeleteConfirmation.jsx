import React, { useEffect, useState } from 'react';
import { AlertTriangle, Trash2, Shield, Lock } from 'lucide-react';
import { motion } from 'framer-motion';
import Modal from './Modal';
import Button from './Button';
import Input from './Input';

const DeleteConfirmation = ({
  isOpen,
  onClose,
  onConfirm,
  loading = false,
  title = 'Delete confirmation',
  message = 'Are you sure you want to delete this item?',
  confirmText = 'Delete',
  cancelText = 'Cancel',
  itemName = '',
  consequences = [],
  requireTypeMatch = true,
}) => {
  const [typed, setTyped] = useState('');

  useEffect(() => {
    if (isOpen) setTyped('');
  }, [isOpen]);

  const matchTarget = (itemName || '').trim();
  const canConfirm =
    !requireTypeMatch || !matchTarget || typed.trim() === matchTarget;

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      size="lg"
      hideClose
      bodyClassName="p-0"
      className="overflow-hidden"
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={loading}>
            {cancelText}
          </Button>
          <Button
            variant="danger"
            onClick={onConfirm}
            loading={loading}
            leftIcon={<Trash2 className="h-4 w-4" />}
            disabled={!canConfirm}
          >
            {confirmText}
          </Button>
        </>
      }
    >
      {/* Dramatic red gradient header */}
      <div className="relative overflow-hidden bg-gradient-to-br from-red-500 via-red-600 to-rose-600 text-white p-6 sm:p-8">
        <div className="pointer-events-none absolute inset-0">
          <div className="absolute -top-20 -right-20 h-56 w-56 rounded-full bg-white/15 blur-3xl" />
          <div className="absolute bottom-[-6rem] left-[-2rem] h-44 w-44 rounded-full bg-rose-300/25 blur-3xl" />
          <div className="absolute inset-0 bg-grid-dark opacity-20" />
        </div>
        <div className="relative flex items-start gap-4">
          <motion.span
            initial={{ scale: 0.6, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ type: 'spring', stiffness: 380, damping: 22, delay: 0.05 }}
            className="inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-white/15 backdrop-blur ring-1 ring-white/30 flex-shrink-0"
          >
            <AlertTriangle className="h-6 w-6" />
          </motion.span>
          <div className="min-w-0">
            <p className="text-[11px] font-bold uppercase tracking-[0.16em] text-white/80">
              Destructive action
            </p>
            <h2 className="mt-1 text-2xl font-bold tracking-tight leading-tight">
              {title}
            </h2>
            <p className="mt-2 text-white/85 text-sm leading-relaxed">{message}</p>
          </div>
        </div>
      </div>

      {/* Body */}
      <div className="p-6 sm:p-8 space-y-5">
        {itemName && (
          <div className="rounded-xl border border-red-200 dark:border-red-500/30 bg-red-50/70 dark:bg-red-500/10 p-4">
            <p className="text-[10px] font-bold uppercase tracking-wider text-red-700 dark:text-red-300">
              Will be permanently deleted
            </p>
            <p className="mt-1 text-base font-semibold text-red-700 dark:text-red-200 break-all">
              {itemName}
            </p>
          </div>
        )}

        {consequences.length > 0 && (
          <div>
            <p className="text-xs font-semibold text-ink uppercase tracking-wider mb-2">
              This will:
            </p>
            <ul className="space-y-1.5">
              {consequences.map((c, i) => (
                <motion.li
                  key={i}
                  initial={{ opacity: 0, x: -8 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ duration: 0.3, delay: 0.05 + i * 0.05, ease: [0.16, 1, 0.3, 1] }}
                  className="flex items-start gap-2 text-sm text-ink-muted"
                >
                  <span className="mt-1.5 inline-block h-1 w-1 flex-shrink-0 rounded-full bg-red-500" />
                  <span>{c}</span>
                </motion.li>
              ))}
            </ul>
          </div>
        )}

        {requireTypeMatch && matchTarget && (
          <div>
            <Input
              label={
                <span className="inline-flex items-center gap-1.5">
                  Type{' '}
                  <code className="rounded bg-surface-muted border border-line px-1.5 py-0.5 text-[11px] font-mono text-ink">
                    {matchTarget}
                  </code>{' '}
                  to confirm
                </span>
              }
              leftIcon={<Lock className="h-4 w-4" />}
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={matchTarget}
              autoFocus
              autoComplete="off"
              spellCheck={false}
              error={typed.length > 0 && typed.trim() !== matchTarget ? 'Names do not match' : undefined}
            />
          </div>
        )}

        <div className="flex items-center gap-2 text-xs text-ink-subtle">
          <Shield className="h-3.5 w-3.5" />
          <span>This action cannot be undone.</span>
        </div>
      </div>
    </Modal>
  );
};

export default DeleteConfirmation;

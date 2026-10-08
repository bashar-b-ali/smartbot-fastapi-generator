import React, { useEffect, useRef, useState } from 'react';
import { useForm } from 'react-hook-form';
import { Link, useNavigate } from 'react-router-dom';
import { Eye, EyeOff, Mail, Lock, User as UserIcon, ArrowRight, ArrowLeft, MailCheck } from 'lucide-react';
import { useAuth } from '../../contexts/AuthContext';
import AuthShell from './AuthShell';
import Input from '../common/Input';
import Button from '../common/Button';
import { cn } from '../common/cn';

const VerificationStep = ({ email, onBack }) => {
  const { verifyEmail, resendVerification, submitting } = useAuth();
  const [code, setCode] = useState(['', '', '', '', '', '']);
  const [cooldown, setCooldown] = useState(60);
  const inputs = useRef([]);
  const navigate = useNavigate();
  const allFilled = code.every((d) => d !== '');

  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  const setDigit = (i, v) => {
    if (!/^\d?$/.test(v)) return;
    const next = [...code];
    next[i] = v;
    setCode(next);
    if (v && i < 5) inputs.current[i + 1]?.focus();
  };

  const onKeyDown = (i, e) => {
    if (e.key === 'Backspace' && !code[i] && i > 0) inputs.current[i - 1]?.focus();
    if (e.key === 'ArrowLeft' && i > 0) inputs.current[i - 1]?.focus();
    if (e.key === 'ArrowRight' && i < 5) inputs.current[i + 1]?.focus();
  };

  const onPaste = (e) => {
    e.preventDefault();
    const digits = e.clipboardData.getData('text').replace(/\D/g, '').slice(0, 6).split('');
    if (!digits.length) return;
    const next = ['', '', '', '', '', ''];
    digits.forEach((d, i) => (next[i] = d));
    setCode(next);
    inputs.current[Math.min(digits.length, 5)]?.focus();
  };

  const onSubmit = async () => {
    const verificationCode = code.join('');
    if (verificationCode.length !== 6) return;
    const result = await verifyEmail({ email, code: verificationCode });
    if (result.success) {
      setTimeout(() => navigate('/login'), 800);
    }
  };

  useEffect(() => {
    if (allFilled) onSubmit();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allFilled]);

  const onResend = async () => {
    if (cooldown > 0 || submitting) return;
    const result = await resendVerification(email);
    if (result.success) {
      setCooldown(60);
      setCode(['', '', '', '', '', '']);
      inputs.current[0]?.focus();
    }
  };

  return (
    <AuthShell
      title="Check your inbox"
      subtitle={
        <>
          We sent a 6-digit code to <span className="font-semibold text-ink">{email}</span>
        </>
      }
    >
      <button
        type="button"
        onClick={onBack}
        className="inline-flex items-center gap-1.5 text-sm text-ink-muted hover:text-ink transition-colors mb-6"
      >
        <ArrowLeft className="h-4 w-4" />
        Use a different email
      </button>

      <div className="rounded-2xl border border-line bg-surface-raised p-6 shadow-card">
        <div className="flex items-center gap-3 mb-6">
          <span className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-primary-50 text-primary-600 dark:bg-primary-500/10 dark:text-primary-300">
            <MailCheck className="h-5 w-5" />
          </span>
          <p className="text-sm text-ink-muted">
            Enter the verification code below — check spam if you don&apos;t see it.
          </p>
        </div>

        <div className="flex justify-between gap-2 sm:gap-3" onPaste={onPaste}>
          {code.map((digit, i) => (
            <input
              key={i}
              ref={(el) => (inputs.current[i] = el)}
              type="text"
              inputMode="numeric"
              maxLength={1}
              value={digit}
              onChange={(e) => setDigit(i, e.target.value)}
              onKeyDown={(e) => onKeyDown(i, e)}
              autoFocus={i === 0}
              className={cn(
                'w-11 h-12 sm:w-12 sm:h-14 text-2xl text-center font-semibold tabular-nums',
                'rounded-xl border-2 bg-surface-raised text-ink',
                'border-line focus:border-primary-500 focus:ring-2 focus:ring-primary-500/20 outline-none',
                'transition-colors'
              )}
            />
          ))}
        </div>

        <Button
          variant="primary"
          size="lg"
          fullWidth
          className="mt-6"
          loading={submitting}
          disabled={!allFilled || submitting}
          onClick={onSubmit}
        >
          Verify email
        </Button>

        <button
          type="button"
          onClick={onResend}
          disabled={cooldown > 0 || submitting}
          className="mt-4 w-full text-center text-sm font-medium text-primary-600 hover:text-primary-700 dark:text-primary-400 disabled:text-ink-subtle disabled:cursor-not-allowed transition-colors"
        >
          {cooldown > 0 ? `Resend code in ${cooldown}s` : 'Resend verification code'}
        </button>
      </div>
    </AuthShell>
  );
};

const RegisterForm = () => {
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [step, setStep] = useState('register');
  const [registeredEmail, setRegisteredEmail] = useState('');
  const { register: registerUser, submitting } = useAuth();
  const { register, handleSubmit, formState: { errors }, watch } = useForm();
  const password = watch('password');

  const onSubmit = async (data) => {
    const result = await registerUser(data);
    if (result.success !== false) {
      setRegisteredEmail(data.email);
      setStep('verify');
    }
  };

  if (step === 'verify') {
    return <VerificationStep email={registeredEmail} onBack={() => setStep('register')} />;
  }

  return (
    <AuthShell
      title="Create your account"
      subtitle="Start your AI-powered development journey — free."
    >
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
        <div className="grid grid-cols-2 gap-3">
          <Input
            label="First name"
            placeholder="Jane"
            leftIcon={<UserIcon className="h-4 w-4" />}
            autoComplete="given-name"
            error={errors.first_name?.message}
            {...register('first_name', { required: 'Required' })}
          />
          <Input
            label="Last name"
            placeholder="Doe"
            autoComplete="family-name"
            error={errors.last_name?.message}
            {...register('last_name', { required: 'Required' })}
          />
        </div>

        <Input
          label="Email"
          type="email"
          autoComplete="email"
          placeholder="you@example.com"
          leftIcon={<Mail className="h-4 w-4" />}
          error={errors.email?.message}
          {...register('email', {
            required: 'Email is required',
            pattern: { value: /^\S+@\S+\.\S+$/, message: 'Enter a valid email' },
          })}
        />

        <Input
          label="Password"
          type={showPassword ? 'text' : 'password'}
          autoComplete="new-password"
          placeholder="At least 6 characters"
          leftIcon={<Lock className="h-4 w-4" />}
          hint="Mix letters, numbers, and a symbol for a stronger password."
          rightSlot={
            <button
              type="button"
              onClick={() => setShowPassword((v) => !v)}
              aria-label={showPassword ? 'Hide password' : 'Show password'}
              className="p-1.5 text-ink-subtle hover:text-ink transition-colors"
            >
              {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          }
          error={errors.password?.message}
          {...register('password', {
            required: 'Password is required',
            minLength: { value: 6, message: 'At least 6 characters' },
          })}
        />

        <Input
          label="Confirm password"
          type={showConfirm ? 'text' : 'password'}
          autoComplete="new-password"
          placeholder="Repeat your password"
          leftIcon={<Lock className="h-4 w-4" />}
          rightSlot={
            <button
              type="button"
              onClick={() => setShowConfirm((v) => !v)}
              aria-label={showConfirm ? 'Hide password' : 'Show password'}
              className="p-1.5 text-ink-subtle hover:text-ink transition-colors"
            >
              {showConfirm ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          }
          error={errors.password_confirm?.message}
          {...register('password_confirm', {
            required: 'Please confirm your password',
            validate: (v) => v === password || 'Passwords do not match',
          })}
        />

        <Button
          type="submit"
          variant="primary"
          size="lg"
          fullWidth
          loading={submitting}
          rightIcon={!submitting && <ArrowRight className="h-4 w-4" />}
        >
          {submitting ? 'Creating account…' : 'Create account'}
        </Button>
      </form>

      <p className="mt-6 text-center text-sm text-ink-muted">
        Already have an account?{' '}
        <Link
          to="/login"
          className="font-semibold text-primary-600 hover:text-primary-700 dark:text-primary-400"
        >
          Sign in
        </Link>
      </p>
    </AuthShell>
  );
};

export default RegisterForm;

import React, { useState } from 'react';
import { useForm } from 'react-hook-form';
import { Link, useNavigate } from 'react-router-dom';
import { Mail, Lock, ArrowLeft, KeyRound, ArrowRight } from 'lucide-react';
import { useAuth } from '../../contexts/AuthContext';
import AuthShell from './AuthShell';
import Input from '../common/Input';
import Button from '../common/Button';

const ForgotPasswordForm = () => {
  const [step, setStep] = useState('request');
  const [resetEmail, setResetEmail] = useState('');
  const { requestPasswordReset, confirmPasswordReset, submitting } = useAuth();
  const { register, handleSubmit, formState: { errors }, watch, reset } = useForm();
  const navigate = useNavigate();
  const newPassword = watch('new_password');

  const onRequest = async (data) => {
    const result = await requestPasswordReset(data.email);
    if (result.success) {
      setResetEmail(data.email);
      reset();
      setStep('reset');
    }
  };

  const onReset = async (data) => {
    const result = await confirmPasswordReset(
      resetEmail,
      data.code,
      data.new_password,
      data.new_password_confirm
    );
    if (result.success) setTimeout(() => navigate('/login'), 800);
  };

  if (step === 'reset') {
    return (
      <AuthShell
        title="Set a new password"
        subtitle={
          <>
            Enter the code we sent to <span className="font-semibold text-ink">{resetEmail}</span>
          </>
        }
      >
        <button
          type="button"
          onClick={() => setStep('request')}
          className="inline-flex items-center gap-1.5 text-sm text-ink-muted hover:text-ink transition-colors mb-6"
        >
          <ArrowLeft className="h-4 w-4" />
          Use a different email
        </button>

        <form onSubmit={handleSubmit(onReset)} className="space-y-5" noValidate>
          <Input
            label="Verification code"
            placeholder="000000"
            inputMode="numeric"
            maxLength={6}
            inputClassName="text-center text-2xl font-mono tracking-[0.5em] font-semibold"
            leftIcon={<KeyRound className="h-4 w-4" />}
            error={errors.code?.message}
            {...register('code', {
              required: 'Verification code is required',
              pattern: { value: /^\d{6}$/, message: 'Code must be 6 digits' },
            })}
          />

          <Input
            label="New password"
            type="password"
            autoComplete="new-password"
            placeholder="At least 6 characters"
            leftIcon={<Lock className="h-4 w-4" />}
            error={errors.new_password?.message}
            {...register('new_password', {
              required: 'New password is required',
              minLength: { value: 6, message: 'At least 6 characters' },
            })}
          />

          <Input
            label="Confirm new password"
            type="password"
            autoComplete="new-password"
            placeholder="Repeat your password"
            leftIcon={<Lock className="h-4 w-4" />}
            error={errors.new_password_confirm?.message}
            {...register('new_password_confirm', {
              required: 'Please confirm your password',
              validate: (v) => v === newPassword || 'Passwords do not match',
            })}
          />

          <Button type="submit" variant="primary" size="lg" fullWidth loading={submitting}>
            {submitting ? 'Resetting password…' : 'Reset password'}
          </Button>
        </form>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      title="Forgot your password?"
      subtitle="Enter your email and we'll send you a reset code."
    >
      <form onSubmit={handleSubmit(onRequest)} className="space-y-5" noValidate>
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

        <Button
          type="submit"
          variant="primary"
          size="lg"
          fullWidth
          loading={submitting}
          rightIcon={!submitting && <ArrowRight className="h-4 w-4" />}
        >
          {submitting ? 'Sending code…' : 'Send reset code'}
        </Button>
      </form>

      <p className="mt-8 text-center text-sm text-ink-muted">
        Remembered it?{' '}
        <Link
          to="/login"
          className="font-semibold text-primary-600 hover:text-primary-700 dark:text-primary-400"
        >
          Back to sign in
        </Link>
      </p>
    </AuthShell>
  );
};

export default ForgotPasswordForm;

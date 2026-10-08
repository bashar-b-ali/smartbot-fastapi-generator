import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { authService } from '../services/api';
import { useToast } from '../components/common/Toast';

const AuthContext = createContext(null);
const SESSION_KEY = 'session_token';
const REFRESH_KEY = 'session_refresh_token';

export const useAuth = () => {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider');
  return ctx;
};

const errorMessage = (err, fallback) =>
  err?.message || err?.response?.data?.error || fallback;

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const toast = useToast();

  const checkAuthStatus = useCallback(async () => {
    setLoading(true);
    try {
      const token = localStorage.getItem(SESSION_KEY);
      if (!token) {
        setUser(null);
        return;
      }
      const userData = await authService.getProfile();
      setUser(userData);
    } catch {
      localStorage.removeItem(SESSION_KEY);
      localStorage.removeItem(REFRESH_KEY);
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    checkAuthStatus();
  }, [checkAuthStatus]);

  const wrap = async (fn, { successMessage, fallback } = {}) => {
    setSubmitting(true);
    try {
      const data = await fn();
      if (successMessage) toast.success(successMessage);
      return { success: true, data };
    } catch (err) {
      const msg = errorMessage(err, fallback);
      toast.error(msg);
      return { success: false, error: msg };
    } finally {
      setSubmitting(false);
    }
  };

  const register = (userData) =>
    wrap(() => authService.register(userData), {
      successMessage: 'Account created — check your email for the verification code.',
      fallback: 'Registration failed',
    });

  const verifyEmail = ({ email, code }) =>
    wrap(
      () => authService.verifyEmail({ email, code: String(code).padStart(6, '0') }),
      {
        successMessage: 'Email verified — you can now sign in.',
        fallback: 'Verification failed',
      }
    );

  const resendVerification = (email) =>
    wrap(() => authService.resendVerification({ email }), {
      successMessage: 'Verification code sent.',
      fallback: 'Failed to send verification code',
    });

  const login = async (email, password) => {
    setSubmitting(true);
    try {
      const response = await authService.login({ email, password });
      localStorage.setItem(SESSION_KEY, response.access_token);
      if (response.refresh_token) {
        localStorage.setItem(REFRESH_KEY, response.refresh_token);
      } else {
        localStorage.removeItem(REFRESH_KEY);
      }
      setUser(response.user);
      toast.success(`Welcome back, ${response.user.first_name || 'friend'}!`);
      return { success: true, data: response };
    } catch (err) {
      const msg = errorMessage(err, 'Login failed');
      toast.error(msg);
      return { success: false, error: msg };
    } finally {
      setSubmitting(false);
    }
  };

  const logout = async () => {
    setSubmitting(true);
    localStorage.removeItem(SESSION_KEY);
    localStorage.removeItem(REFRESH_KEY);
    setUser(null);
    setSubmitting(false);
    toast.success('Signed out.');
  };

  const requestPasswordReset = (email) =>
    wrap(() => authService.requestPasswordReset({ email }), {
      successMessage: 'Password reset code sent to your email.',
      fallback: 'Failed to send reset code',
    });

  const confirmPasswordReset = (email, code, newPassword, newPasswordConfirm) =>
    wrap(
      () =>
        authService.confirmPasswordReset({
          email,
          code,
          new_password: newPassword,
          new_password_confirm: newPasswordConfirm ?? newPassword,
        }),
      {
        successMessage: 'Password reset — please sign in.',
        fallback: 'Password reset failed',
      }
    );

  const changePassword = (oldPassword, newPassword, newPasswordConfirm) =>
    wrap(
      () =>
        authService.changePassword({
          old_password: oldPassword,
          new_password: newPassword,
          new_password_confirm: newPasswordConfirm,
        }),
      { successMessage: 'Password changed.', fallback: 'Password change failed' }
    );

  const updateProfile = async (profileData) => {
    setSubmitting(true);
    try {
      const response = await authService.updateProfile(profileData);
      setUser(response?.user || response);
      toast.success('Profile updated.');
      return { success: true, data: response };
    } catch (err) {
      const msg = errorMessage(err, 'Profile update failed');
      toast.error(msg);
      return { success: false, error: msg };
    } finally {
      setSubmitting(false);
    }
  };

  const value = {
    user,
    loading,
    submitting,
    register,
    verifyEmail,
    resendVerification,
    login,
    logout,
    requestPasswordReset,
    confirmPasswordReset,
    changePassword,
    updateProfile,
    checkAuthStatus,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

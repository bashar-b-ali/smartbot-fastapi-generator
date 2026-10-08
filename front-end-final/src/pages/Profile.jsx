import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { KeyRound, Mail, Save, ShieldCheck, UserCircle2 } from 'lucide-react';
import Button from '../components/common/Button';
import { Card, CardBody, CardHeader } from '../components/common/Card';
import Input from '../components/common/Input';
import Avatar from '../components/common/Avatar';
import { useAuth } from '../contexts/AuthContext';

const Profile = () => {
  const { user, submitting, updateProfile, changePassword } = useAuth();
  const [profile, setProfile] = useState({ first_name: '', last_name: '' });
  const [passwords, setPasswords] = useState({
    old_password: '',
    new_password: '',
    new_password_confirm: '',
  });

  useEffect(() => {
    setProfile({
      first_name: user?.first_name || '',
      last_name: user?.last_name || '',
    });
  }, [user]);

  const submitProfile = async (event) => {
    event.preventDefault();
    await updateProfile(profile);
  };

  const submitPassword = async (event) => {
    event.preventDefault();
    const result = await changePassword(
      passwords.old_password,
      passwords.new_password,
      passwords.new_password_confirm
    );
    if (result.success) {
      setPasswords({ old_password: '', new_password: '', new_password_confirm: '' });
    }
  };

  return (
    <div className="min-h-[calc(100vh-3.5rem)] bg-surface-muted">
      <div className="mx-auto max-w-6xl px-3 py-5 sm:px-5 lg:px-6">
        <section className="mb-4 flex flex-col gap-3 border-b border-line pb-4 sm:flex-row sm:items-end sm:justify-between">
            <div className="flex min-w-0 items-center gap-3">
              <Avatar user={user} size="lg" />
              <div className="min-w-0">
                <p className="text-xs font-semibold uppercase tracking-[0.14em] text-ink-subtle">
                  Account
                </p>
                <h1 className="mt-1 truncate text-xl font-semibold tracking-tight text-ink">
                  {user?.first_name || user?.email?.split('@')[0] || 'Profile'}
                </h1>
                <p className="mt-1 truncate text-sm text-ink-muted">{user?.email}</p>
              </div>
            </div>
            <Button as={Link} to="/forgot-password" variant="secondary" size="sm" leftIcon={<KeyRound className="h-4 w-4" />}>
              Forgot password
            </Button>
        </section>

        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
          <Card className="rounded-xl">
            <CardHeader className="p-4">
              <h2 className="flex items-center gap-2 text-base font-semibold text-ink">
                <UserCircle2 className="h-4 w-4 text-primary-600 dark:text-primary-300" />
                Personal details
              </h2>
              <p className="mt-1 text-sm text-ink-subtle">
                Update the name shown in the app and chat workspace.
              </p>
            </CardHeader>
            <CardBody className="p-4 pt-0">
              <form onSubmit={submitProfile} className="grid gap-4 sm:grid-cols-2">
                <Input
                  label="First name"
                  value={profile.first_name}
                  onChange={(event) => setProfile((current) => ({ ...current, first_name: event.target.value }))}
                  placeholder="First name"
                />
                <Input
                  label="Last name"
                  value={profile.last_name}
                  onChange={(event) => setProfile((current) => ({ ...current, last_name: event.target.value }))}
                  placeholder="Last name"
                />
                <div className="sm:col-span-2">
                  <Button type="submit" loading={submitting} leftIcon={<Save className="h-4 w-4" />}>
                    Save profile
                  </Button>
                </div>
              </form>
            </CardBody>
          </Card>

          <Card className="rounded-xl lg:row-span-2">
            <CardHeader className="p-4">
              <h2 className="flex items-center gap-2 text-base font-semibold text-ink">
                <Mail className="h-4 w-4 text-primary-600 dark:text-primary-300" />
                Account status
              </h2>
            </CardHeader>
            <CardBody className="space-y-3 p-4 pt-0">
              <div className="rounded-xl border border-line bg-surface-muted px-3 py-3">
                <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-subtle">Sign-in email</p>
                <p className="text-sm font-semibold text-ink truncate">{user?.email}</p>
                <p className="mt-1 text-xs leading-5 text-ink-subtle">
                  Email changes are not enabled in this build. Use password recovery if you lose access.
                </p>
              </div>
              <div className="rounded-xl border border-line bg-surface-muted px-3 py-3">
                <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-subtle">Security</p>
                <p className="mt-1 text-sm font-semibold text-ink">Password protected</p>
                <p className="mt-1 text-xs leading-5 text-ink-subtle">
                  Use the password form for known-password changes, or recovery for reset links.
                </p>
              </div>
            </CardBody>
          </Card>

          <Card className="rounded-xl">
            <CardHeader className="p-4">
              <h2 className="flex items-center gap-2 text-base font-semibold text-ink">
                <ShieldCheck className="h-4 w-4 text-primary-600 dark:text-primary-300" />
                Password
              </h2>
              <p className="mt-1 text-sm text-ink-subtle">
                Change your password if you know the current one, or use forgot password for email recovery.
              </p>
            </CardHeader>
            <CardBody className="p-4 pt-0">
              <form onSubmit={submitPassword} className="grid gap-4 md:grid-cols-3">
                <Input
                  label="Current password"
                  type="password"
                  value={passwords.old_password}
                  onChange={(event) => setPasswords((current) => ({ ...current, old_password: event.target.value }))}
                  required
                />
                <Input
                  label="New password"
                  type="password"
                  value={passwords.new_password}
                  onChange={(event) => setPasswords((current) => ({ ...current, new_password: event.target.value }))}
                  required
                />
                <Input
                  label="Confirm password"
                  type="password"
                  value={passwords.new_password_confirm}
                  onChange={(event) => setPasswords((current) => ({ ...current, new_password_confirm: event.target.value }))}
                  required
                />
                <div className="md:col-span-3">
                  <Button type="submit" loading={submitting} leftIcon={<KeyRound className="h-4 w-4" />}>
                    Change password
                  </Button>
                </div>
              </form>
            </CardBody>
          </Card>
        </div>
      </div>
    </div>
  );
};

export default Profile;

/**
 * Staff sign-in for the clinic app.
 *
 * This is **not** the admin login. The admin requires a verified second factor
 * and lives at its own path; a session started here grants the clinic app only.
 * Different surfaces, different risk.
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Navigate, useNavigate } from 'react-router-dom';

import { Alert, Card, Field } from '../../components/common';
import { useSession } from '../../services/store/SessionContext';

export function SignInPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { session, signIn } = useSession();

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  if (session) return <Navigate to="/clinic/queue" replace />;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setFailed(false);
    setBusy(true);
    try {
      await signIn(username, password);
      navigate('/clinic/queue');
    } catch {
      // One message whatever went wrong: the server deliberately does not say
      // whether the username or the password was incorrect, and neither does
      // this.
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="hg-narrow">
      <h1>{t('clinic.signInTitle')}</h1>
      {failed ? <Alert>{t('clinic.signInFailed')}</Alert> : null}

      <Card>
        <form onSubmit={submit} noValidate>
          <Field
            id="username"
            label={t('clinic.username')}
            value={username}
            autoComplete="username"
            required
            onChange={(event) => setUsername(event.target.value)}
          />
          <Field
            id="password"
            label={t('clinic.password')}
            type="password"
            value={password}
            autoComplete="current-password"
            required
            onChange={(event) => setPassword(event.target.value)}
          />
          <button type="submit" className="hg-btn hg-btn--primary" disabled={busy}>
            {busy ? t('clinic.signingIn') : t('clinic.signIn')}
          </button>
        </form>
      </Card>
    </div>
  );
}

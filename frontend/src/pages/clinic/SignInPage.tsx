/**
 * Staff sign-in for the clinic app.
 *
 * This is **not** the admin login. The admin requires a verified second factor
 * and lives at its own path; a session started here grants the clinic app only.
 * Different surfaces, different risk.
 */

import { CircleCheck, Lock, LogIn } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Navigate, useNavigate } from 'react-router-dom';

import { Alert, Card, Field, LogoMark } from '../../components/common';
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
    <div className="hg-auth">
      <section className="hg-auth__panel" aria-hidden="true">
        <LogoMark size={44} />
        <p className="hg-eyebrow hg-eyebrow--light">{t('home.clinicEyebrow')}</p>
        <p className="hg-auth__title">{t('clinic.signInPanelTitle')}</p>
        <ul>
          {[t('home.clinicPoint1'), t('home.clinicPoint2'), t('home.clinicPoint3')].map((point) => (
            <li key={point}>
              <CircleCheck size={18} />
              {point}
            </li>
          ))}
        </ul>
      </section>

      <Card className="hg-auth__card">
        <h1>{t('clinic.signInTitle')}</h1>
        <p className="hg-muted">{t('clinic.signInLead')}</p>
        {failed ? <Alert>{t('clinic.signInFailed')}</Alert> : null}

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
          <button
            type="submit"
            className="hg-btn hg-btn--primary hg-btn--lg hg-btn--block"
            disabled={busy}
          >
            <LogIn size={18} aria-hidden="true" />
            {busy ? t('clinic.signingIn') : t('clinic.signIn')}
          </button>
        </form>
        <p className="hg-auth__note">
          <Lock size={14} aria-hidden="true" />
          {t('clinic.sessionNote')}
        </p>
      </Card>
    </div>
  );
}

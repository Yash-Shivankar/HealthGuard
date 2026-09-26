/**
 * Routes.
 *
 * The patient flow is public; the clinic flow needs a session. `RequireSession`
 * only decides what to render - the server refuses every clinic endpoint
 * independently, so a hand-edited bundle gains nothing.
 */

import type { ReactNode } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';

import { AppShell } from './components/layout/AppShell';
import { Spinner } from './components/common';
import { HomePage } from './pages/patient/HomePage';
import { IntakePage } from './pages/patient/IntakePage';
import { PatientsPage } from './pages/clinic/PatientsPage';
import { ResultPage } from './pages/patient/ResultPage';
import { ReviewQueuePage } from './pages/doctor/ReviewQueuePage';
import { SignInPage } from './pages/clinic/SignInPage';
import { SymptomsPage } from './pages/patient/SymptomsPage';
import { useSession } from './services/store/SessionContext';

function RequireSession({ children }: { children: ReactNode }) {
  const { session, loading } = useSession();
  // Waiting matters: redirecting before the session lookup settles would bounce
  // a signed-in user to the login page on every refresh.
  if (loading) return <Spinner />;
  if (!session) return <Navigate to="/clinic/sign-in" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/intake" element={<IntakePage />} />
        <Route path="/symptoms" element={<SymptomsPage />} />
        <Route path="/result" element={<ResultPage />} />

        <Route path="/clinic/sign-in" element={<SignInPage />} />
        <Route
          path="/clinic/patients"
          element={
            <RequireSession>
              <PatientsPage />
            </RequireSession>
          }
        />
        <Route
          path="/clinic/queue"
          element={
            <RequireSession>
              <ReviewQueuePage />
            </RequireSession>
          }
        />

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}

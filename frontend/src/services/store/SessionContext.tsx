/**
 * Who is signed in, and what they may see.
 *
 * A context rather than a state library: this holds one object that changes on
 * sign-in, sign-out and clinic switch. Reaching for Redux or Zustand for that
 * would be machinery without a job.
 *
 * `permissions` is used only to hide controls a caller cannot use. It is **not**
 * authorisation - the server checks every request regardless, and a SPA that
 * believes its own permission list is a SPA that can be edited in devtools.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import { ApiRequestError, authApi } from '../api';
import type { Clinic, Session } from '../../types';

interface SessionState {
  session: Session | null;
  /** True until the first session lookup settles, so routes can wait. */
  loading: boolean;
  signIn: (username: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  switchClinic: (clinic: Clinic) => Promise<void>;
  can: (permission: string) => boolean;
}

const SessionContext = createContext<SessionState | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    authApi
      .session()
      .then((value) => {
        if (!cancelled) setSession(value);
      })
      .catch((error: unknown) => {
        // 401/403 on load is the normal anonymous case, not a failure: the
        // patient-facing pages are public and must render without a session.
        if (!(error instanceof ApiRequestError && error.isUnauthenticated)) {
          console.error('Could not read session', error);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const signIn = useCallback(async (username: string, password: string) => {
    setSession(await authApi.signIn(username, password));
  }, []);

  const signOut = useCallback(async () => {
    await authApi.signOut();
    setSession(null);
  }, []);

  const switchClinic = useCallback(async (clinic: Clinic) => {
    setSession(await authApi.switchClinic(clinic));
  }, []);

  const can = useCallback(
    (permission: string) => session?.permissions.includes(permission) ?? false,
    [session],
  );

  const value = useMemo<SessionState>(
    () => ({ session, loading, signIn, signOut, switchClinic, can }),
    [session, loading, signIn, signOut, switchClinic, can],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionState {
  const context = useContext(SessionContext);
  if (context === null) {
    throw new Error('useSession must be used inside a SessionProvider');
  }
  return context;
}

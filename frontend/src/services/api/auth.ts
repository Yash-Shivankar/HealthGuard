/** Auth and tenancy calls. */

import type { Clinic, Session } from '../../types';
import { request } from './client';

export const authApi = {
  session: () => request<Session>('/auth/session/'),

  signIn: (username: string, password: string) =>
    request<Session>('/auth/login/', { method: 'POST', body: { username, password } }),

  signOut: () => request<void>('/auth/logout/', { method: 'POST' }),

  switchClinic: (clinic: Clinic) =>
    request<Session>('/auth/clinic/', { method: 'POST', body: { clinic_id: clinic.id } }),
};

/** Patient intake and the clinic-facing patient list. */

import type { Paginated, Patient } from '../../types';
import { request } from './client';

export interface IntakePayload {
  name: string;
  contact: string;
  age: number;
  height_cm: number;
  weight_kg: number;
  consent: boolean;
}

export const patientsApi = {
  intake: (payload: IntakePayload) =>
    request<Patient>('/patients/intake/', { method: 'POST', body: payload }),

  /** The record held in the caller's own session, if any. */
  me: () => request<{ patient: Patient | null; consent: unknown[] }>('/patients/me/'),

  /** Withdraw consent and erase the session's record. */
  forgetMe: () => request<void>('/patients/me/', { method: 'DELETE' }),

  list: (opts: { search?: string; signal?: AbortSignal } = {}) =>
    request<Paginated<Patient>>('/patients/', {
      params: { search: opts.search },
      signal: opts.signal,
    }),

  detail: (id: number) => request<Patient>(`/patients/${id}/`),
};

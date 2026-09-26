/** Symptom checking and the clinician review queue. */

import type { Assessment, Paginated, Symptom, TriageResult } from '../../types';
import { request } from './client';

export const triageApi = {
  /**
   * The symptom vocabulary the model accepts.
   *
   * Fetched rather than hardcoded: the list comes from the trained artefact, so
   * a retrained model with a different vocabulary cannot leave the form
   * offering symptoms the server will reject.
   */
  symptoms: (signal?: AbortSignal) => request<Symptom[]>('/triage/symptoms/', { signal }),

  check: (symptoms: string[], days: number) =>
    request<TriageResult>('/triage/check/', { method: 'POST', body: { symptoms, days } }),

  queue: (opts: { unreviewedOnly?: boolean; signal?: AbortSignal } = {}) =>
    request<Paginated<Assessment>>('/triage/assessments/', {
      params: { unreviewed: opts.unreviewedOnly ? 'true' : undefined },
      signal: opts.signal,
    }),

  markReviewed: (id: number) =>
    request<Assessment>(`/triage/assessments/${id}/review/`, { method: 'PATCH' }),
};

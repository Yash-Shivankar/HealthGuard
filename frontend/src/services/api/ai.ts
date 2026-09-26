/**
 * AI assistance: describe the problem in your own words, in your own language.
 *
 * Note what is absent: there is no call here that turns a description into a
 * condition. `describe` returns proposed symptoms and nothing else, and the
 * symptom check still runs from `triageApi.check` or, for a clinician, from
 * `confirmDraft`. That mirrors the server's own separation - the language layer
 * normalises, the deterministic model classifies - and keeping the client honest
 * about it means a future screen cannot accidentally present an extraction as a
 * result.
 */

import type { ConsultationNote, IntakeDraft, Paginated, TriageResult } from '../../types';
import { request } from './client';

export const aiApi = {
  /** Free text in English, Hindi or Marathi to proposed canonical symptoms. */
  describe: (text: string, language: string) =>
    request<IntakeDraft>('/ai/intake/describe/', {
      method: 'POST',
      body: { text, language },
    }),

  /**
   * A recording to proposed canonical symptoms.
   *
   * Sent as multipart because it is a file. With no speech provider configured
   * the server returns a draft marked `simulated`, which cannot be confirmed -
   * so the caller must check `can_be_confirmed` rather than assume.
   */
  voice: (audio: Blob, language: string) => {
    const form = new FormData();
    form.append('audio', audio, 'intake.webm');
    form.append('language', language);
    return request<IntakeDraft>('/ai/intake/voice/', { method: 'POST', body: form });
  },

  drafts: (opts: { pendingOnly?: boolean; signal?: AbortSignal } = {}) =>
    request<Paginated<IntakeDraft>>('/ai/drafts/', {
      params: { pending: opts.pendingOnly ? 'true' : undefined },
      signal: opts.signal,
    }),

  /**
   * Clinician sign-off, which runs the confirmed symptoms.
   *
   * Returns the assessment, still `unreviewed`: confirming the symptoms is not
   * the same judgement as agreeing with the differential, and each needs its own
   * sign-off.
   */
  confirmDraft: (id: number, body: { symptoms?: string[]; days?: number } = {}) =>
    request<TriageResult>(`/ai/drafts/${id}/confirm/`, { method: 'POST', body }),

  discardDraft: (id: number, reason?: string) =>
    request<IntakeDraft>(`/ai/drafts/${id}/discard/`, { method: 'POST', body: { reason } }),

  noteForAssessment: (assessmentId: number) =>
    request<ConsultationNote>(`/ai/notes/for-assessment/${assessmentId}/`, {
      method: 'POST',
    }),

  signOffNote: (id: number, clinicianText?: string) =>
    request<ConsultationNote>(`/ai/notes/${id}/sign-off/`, {
      method: 'POST',
      body: { clinician_text: clinicianText },
    }),
};

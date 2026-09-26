/**
 * Shapes returned by /api/v1/. Kept in one file so a server change that breaks
 * the contract breaks the build here rather than at runtime in a clinic.
 *
 * These mirror the DRF serializers. When one changes, change both - there is no
 * codegen step, and pretending otherwise would be worse than saying so.
 */

export interface Clinic {
  id: number;
  name: string;
}

export type Role = 'owner' | 'doctor' | 'front_desk' | 'platform_ops';

export interface Session {
  username: string;
  is_staff: boolean;
  role: Role | null;
  /** True for a superuser or platform_ops: sees across clinics. */
  unrestricted: boolean;
  clinic: Clinic | null;
  available_clinics: Clinic[];
  /** Used only to hide controls. The server enforces every one of these. */
  permissions: string[];
}

export interface Symptom {
  value: string;
  label: string;
}

/**
 * One entry in the ranked differential.
 *
 * `confidence` is the share of the model that voted for this condition - how
 * completely the reported symptoms matched a stored pattern. It is **not** the
 * probability that the person has the disease, and the UI must never word it
 * that way. See ml/artifacts/MODEL_CARD.md.
 */
export interface Candidate {
  disease: string;
  confidence: number;
  confidence_percent: number;
  match_band: 'Strong match' | 'Possible match' | 'Weak match';
}

export type UrgencyLevel = 'unknown' | 'routine' | 'consult' | 'urgent';

export interface Urgency {
  level: UrgencyLevel;
  label: string;
  advice: string;
}

export interface TriageResult {
  assessment_id: number | null;
  /** True when confidence fell below the threshold and nothing was named. */
  abstained: boolean;
  candidates: Candidate[];
  urgency: Urgency;
  description: string;
  precautions: string[];
  remedies: string[];
  symptoms: string[];
  days: number;
  review_state: string;
  model_fingerprint: string;
  /** Part of the contract, not decoration. Render it. */
  disclaimer: string;
}

export interface Patient {
  id: number;
  name: string;
  /** Masked by the server. The full number is never sent to a browser. */
  contact: string;
  age: number;
  height_cm: number;
  weight_kg: number;
  bmi: number | null;
  created_at: string;
}

export interface Assessment {
  id: number;
  patient: number | null;
  patient_name: string;
  symptoms: string[];
  days: number;
  abstained: boolean;
  top_disease: string | null;
  top_confidence: number | null;
  urgency_level: UrgencyLevel;
  urgency_label: string;
  review_state: 'unreviewed' | 'reviewed' | 'corrected';
  needs_review: boolean;
  created_at: string;
}

export interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

/** The envelope utils/exceptions/handlers.py produces for every error. */
export interface ApiError {
  error: {
    code: string;
    message: string;
    detail?: Record<string, string[]>;
  };
}

/**
 * One symptom an extractor proposed, with the words that produced it.
 *
 * `confidence` here is **not** the same quantity as on `Candidate`. It is how
 * specific the matched phrase was - a hand-set weight, not a learned value and
 * not a probability of anything. So the UI leads with `evidence`: the phrase is
 * the evidence a reviewer can actually check, and the number is not.
 */
export interface ProposedSymptom {
  symptom: string;
  label: string;
  evidence: string;
  confidence: number;
}

export type DraftState = 'draft' | 'confirmed' | 'discarded';

/**
 * What a patient said, and the canonical symptoms it maps to.
 *
 * A draft, not a record. `can_be_confirmed` is false for a draft produced by a
 * simulated provider - which is every voice draft until a speech vendor is
 * configured - and the UI must disable sign-off on it rather than offering an
 * action the server will refuse.
 */
export interface IntakeDraft {
  id: number;
  patient: number | null;
  language: string;
  language_label: string;
  source_text: string;
  from_audio: boolean;
  proposed_symptoms: ProposedSymptom[];
  /** Phrases nothing matched, including questions like "fever - which one?". */
  unmapped_phrases: string[];
  confirmed_symptoms: string[];
  /** Duration read out of the description, so nobody is asked twice. */
  suggested_days: number | null;
  state: DraftState;
  state_label: string;
  needs_review: boolean;
  can_be_confirmed: boolean;
  /** True when nothing matched. Not the same as "no symptoms". */
  understood_nothing: boolean;
  simulated: boolean;
  externally_processed: boolean;
  provider: string;
  model_version: string;
  assessment: number | null;
  created_at: string;
}

export interface NoteSection {
  label: string;
  text: string;
}

/** An AI-drafted consultation note. Carries no treatment or medication advice. */
export interface ConsultationNote {
  id: number;
  patient: number | null;
  assessment: number | null;
  sections: NoteSection[];
  clinician_text: string;
  state: DraftState;
  state_label: string;
  needs_review: boolean;
  can_be_confirmed: boolean;
  simulated: boolean;
  externally_processed: boolean;
  provider: string;
  model_version: string;
  created_at: string;
}

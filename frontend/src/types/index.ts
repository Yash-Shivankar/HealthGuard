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

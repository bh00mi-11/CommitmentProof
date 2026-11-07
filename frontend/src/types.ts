export type EvidenceValue = boolean | null;

export interface EvidenceItem {
  value: EvidenceValue;
  source_url?: string;
  detail?: string;
}

export interface EvidenceBundle {
  pr_merged?: EvidenceItem;
  tests_passed?: EvidenceItem;
  deployment_verified?: EvidenceItem;
  qa_confirmed?: EvidenceItem;
  collection_errors?: string[];
}

export interface Assessment {
  decision: 'BLOCKED' | 'NEEDS_EVIDENCE' | 'REVIEW_READY';
  reasons: string[];
  can_claim_complete: false;
  permitted_action: 'PREPARE_FOLLOWUP_DRAFT' | 'REQUEST_EVIDENCE' | 'REQUEST_HUMAN_APPROVAL';
}

export interface Investigation {
  repository: string;
  pull_request_number: number;
  evidence: EvidenceBundle;
  assessment: Assessment;
  collected_at: string;
}

export type ViewMode = 'example' | 'live';

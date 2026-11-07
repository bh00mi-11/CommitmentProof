import type { Assessment, EvidenceBundle, EvidenceItem, Investigation } from './types';

export type EvidenceState = 'verified' | 'failed' | 'missing' | 'disputed' | 'context' | 'review-ready';

export interface EvidenceStep {
  id: string;
  title: string;
  eyebrow: string;
  state: EvidenceState;
  label: string;
  explanation: string;
  sourceUrl?: string;
  value?: boolean | null;
  detail?: string;
}

const evidenceDefinitions = [
  { key: 'pr_merged', id: 'pr', title: 'Pull request', eyebrow: 'Code change', explanation: 'A merge shows the code change entered the repository. It does not show that customers are running it.' },
  { key: 'tests_passed', id: 'checks', title: 'CI checks', eyebrow: 'Automated tests', explanation: 'Required checks must finish successfully on the pull request commit.' },
  { key: 'deployment_verified', id: 'deploy', title: 'Deployment', eyebrow: 'Customer environment', explanation: 'A deployment record must connect this fix to the affected customer environment.' },
  { key: 'qa_confirmed', id: 'qa', title: 'QA verification', eyebrow: 'Original scenario', explanation: 'QA must reproduce the reported scenario against the deployed version and record the result.' },
] as const;

export function evidenceState(item: EvidenceItem | undefined): EvidenceState {
  if (!item || item.value === null || item.value === undefined) return 'missing';
  if (item.value === false) return 'failed';
  if (!item.source_url || !item.source_url.startsWith('https://')) return 'missing';
  return 'verified';
}

export function decisionLabel(assessment: Assessment): { label: string; state: EvidenceState; message: string } {
  if (assessment.decision === 'BLOCKED') {
    return { label: 'BLOCKED', state: 'failed', message: 'Negative evidence is present. Keep the customer promise open and prepare a follow-up.' };
  }
  if (assessment.decision === 'REVIEW_READY') {
    return { label: 'REVIEW READY', state: 'review-ready', message: 'Evidence is complete. A human must still review it; completion cannot be claimed automatically.' };
  }
  return { label: 'NEEDS EVIDENCE', state: 'missing', message: 'Some proof is unresolved. Do not tell the customer the fix is delivered.' };
}

export function buildEvidenceSteps(bundle: EvidenceBundle, assessment: Assessment): EvidenceStep[] {
  const data = bundle as Record<string, EvidenceItem | undefined>;
  const evidence = evidenceDefinitions.map((definition): EvidenceStep => {
    const item = data[definition.key];
    const state = evidenceState(item);
    const label = state === 'verified' ? 'VERIFIED' : state === 'failed' ? 'FAILED' : state === 'disputed' ? 'DISPUTED' : 'MISSING';
    let explanation: string = definition.explanation;
    if (state === 'failed') explanation = 'The collector returned negative evidence for this requirement. Review the linked source before proceeding.';
    else if (state === 'missing') explanation = 'The available collector did not verify this requirement. Unknown stays unknown.';
    else if (item?.detail) explanation = item.detail;
    return {
      id: definition.id,
      title: definition.title,
      eyebrow: definition.eyebrow,
      state,
      label,
      explanation,
      sourceUrl: item?.source_url,
      value: item?.value,
      detail: item?.detail,
    };
  });
  const final = decisionLabel(assessment);
  evidence.unshift({
    id: 'promise', title: 'Customer promise', eyebrow: 'The question', state: 'context', label: 'UNDER REVIEW',
    explanation: 'Can we tell the customer their promised fix is delivered? CommitmentProof checks the proof before anyone answers.',
  });
  evidence.push({
    id: 'decision', title: final.label, eyebrow: 'Policy decision', state: final.state,
    label: assessment.can_claim_complete ? 'COMPLETE' : 'NOT COMPLETE', explanation: final.message,
    detail: assessment.reasons.join(' '), value: assessment.can_claim_complete,
  });
  return evidence;
}

export function makeExampleInvestigation(): Investigation {
  const assessment: Assessment = {
    decision: 'NEEDS_EVIDENCE',
    reasons: ['Deployment verification is unresolved.', 'QA confirmation is unresolved.'],
    can_claim_complete: false,
    permitted_action: 'REQUEST_EVIDENCE',
  };
  return {
    investigation_id: 'recorded-microsoft-vscode-pr-331010',
    repository: 'microsoft/vscode',
    pull_request_number: 331010,
    collected_at: 'Recorded read-only collection · not refreshed in this session',
    evidence: {
      pr_merged: { value: true, source_url: 'https://github.com/microsoft/vscode/pull/331010', detail: 'The pull request is merged.' },
      tests_passed: { value: true, source_url: 'https://github.com/microsoft/vscode/commit/026fe5316ffc0998aab553b3ca3d4f36d8e8792f/checks', detail: '27 completed check runs were reported successful.' },
      deployment_verified: { value: null },
      qa_confirmed: { value: null },
    },
    assessment,
  };
}

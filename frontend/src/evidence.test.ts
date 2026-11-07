import { describe, expect, it } from 'vitest';
import { buildEvidenceSteps, decisionLabel, evidenceState, makeExampleInvestigation } from './evidence';

describe('CommitmentProof evidence presentation', () => {
  it('keeps absent deployment and QA unknown in the recorded example', () => {
    const example = makeExampleInvestigation();
    const steps = buildEvidenceSteps(example.evidence, example.assessment);
    expect(steps.find((step) => step.id === 'pr')?.state).toBe('verified');
    expect(steps.find((step) => step.id === 'checks')?.state).toBe('verified');
    expect(steps.find((step) => step.id === 'deploy')?.state).toBe('missing');
    expect(steps.find((step) => step.id === 'qa')?.state).toBe('missing');
    expect(steps.find((step) => step.id === 'decision')?.label).toBe('NOT COMPLETE');
  });

  it('does not treat unsupported source links as verified', () => {
    expect(evidenceState({ value: true, source_url: 'http://example.test/proof' })).toBe('missing');
    expect(evidenceState({ value: false, source_url: 'https://example.test/proof' })).toBe('failed');
    expect(evidenceState({ value: null })).toBe('missing');
  });

  it('renders review-ready as a human review boundary, never completion', () => {
    const label = decisionLabel({
      decision: 'REVIEW_READY', reasons: ['Human review required'], can_claim_complete: false,
      permitted_action: 'REQUEST_HUMAN_APPROVAL',
    });
    expect(label.label).toBe('REVIEW READY');
    expect(label.message).toContain('cannot be claimed automatically');
  });
});

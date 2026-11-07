import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';

vi.mock('@xyflow/react', async () => {
  const React = await import('react');
  const passthrough = ({ children }: { children?: React.ReactNode }) => React.createElement('div', null, children);
  return {
    Background: passthrough,
    Controls: passthrough,
    Handle: () => null,
    MarkerType: { ArrowClosed: 'arrowclosed' },
    MiniMap: passthrough,
    Position: { Left: 'left', Right: 'right' },
    ReactFlow: passthrough,
    ReactFlowProvider: ({ children }: { children?: React.ReactNode }) => React.createElement(React.Fragment, null, children),
    useReactFlow: () => ({ fitView: vi.fn() }),
  };
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('investigation walkthrough', () => {
  it('opens with an explicitly recorded example and unresolved proof', () => {
    render(<App />);
    expect(screen.getByText(/A merged PR doesn’t mean a customer promise is/i)).toBeInTheDocument();
    expect(screen.getByText(/Recorded evidence · collected previously · not refreshed/)).toBeInTheDocument();
    expect(screen.getByText(/POLICY DECISION.*NEEDS EVIDENCE/)).toBeInTheDocument();
    expect(screen.getByText(/no approval or GitHub action has occurred/i)).toBeInTheDocument();
  });

  it('submits live investigations to the local adapter and renders policy output', async () => {
    const payload = {
      repository: 'acme/app', pull_request_number: 7, collected_at: '2026-11-07T00:00:00Z',
      evidence: {
        pr_merged: { value: true, source_url: 'https://github.com/acme/app/pull/7' },
        tests_passed: { value: false, source_url: 'https://github.com/acme/app/commit/abc/checks' },
        deployment_verified: { value: null }, qa_confirmed: { value: null }, collection_errors: [],
      },
      assessment: { decision: 'BLOCKED', reasons: ['Negative evidence: required tests passed'], can_claim_complete: false, permitted_action: 'PREPARE_FOLLOWUP_DRAFT' },
    };
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    render(<App />);
    fireEvent.click(screen.getByRole('tab', { name: /Live investigation/i }));
    fireEvent.change(screen.getByPlaceholderText('owner/repository'), { target: { value: 'acme/app' } });
    fireEvent.change(screen.getByPlaceholderText('123'), { target: { value: '7' } });
    fireEvent.click(screen.getByRole('button', { name: /Check evidence/i }));
    await waitFor(() => expect(screen.getByText(/POLICY DECISION.*BLOCKED/)).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledWith('/api/investigate', expect.objectContaining({ method: 'POST' }));
    expect(screen.getByText(/Read-only GitHub evidence/)).toBeInTheDocument();
  });
});

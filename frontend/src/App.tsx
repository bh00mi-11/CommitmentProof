import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity, ArrowDownRight, ArrowRight, ArrowUpRight, Check, CheckCheck, CircleHelp,
  ClipboardCheck, ExternalLink, FileWarning, GitBranch, GitPullRequest, LoaderCircle,
  MessageSquareText, Rocket, ShieldAlert, ShieldCheck, Sparkles, X,
} from 'lucide-react';
import {
  Background, Controls, Handle, MarkerType, MiniMap, Position, ReactFlow,
  ReactFlowProvider, type Edge, type Node, type NodeProps, useReactFlow,
} from '@xyflow/react';
import { buildEvidenceSteps, decisionLabel, makeExampleInvestigation, type EvidenceStep } from './evidence';
import type { Investigation, SwitchEvent, SwitchEventFeed, ViewMode } from './types';

type GraphNodeData = { step: EvidenceStep; order: number };
type GraphNode = Node<GraphNodeData, 'evidence'>;

const icons = {
  promise: MessageSquareText,
  pr: GitPullRequest,
  checks: CheckCheck,
  deploy: Rocket,
  qa: ClipboardCheck,
  decision: ShieldCheck,
};

function statusIcon(state: EvidenceStep['state']) {
  if (state === 'verified') return Check;
  if (state === 'failed') return X;
  if (state === 'review-ready') return ShieldCheck;
  if (state === 'context') return MessageSquareText;
  return CircleHelp;
}

function EvidenceNodeView({ data, selected }: NodeProps<GraphNode>) {
  const Icon = icons[data.step.id as keyof typeof icons] ?? FileWarning;
  const StateIcon = statusIcon(data.step.state);
  return (
    <article className={`evidence-node state-${data.step.state}${selected ? ' is-selected' : ''}`} aria-label={`${data.step.title}: ${data.step.label}`}>
      {data.order > 0 && <Handle type="target" position={Position.Left} className="flow-handle" />}
      <div className="node-topline"><span className="node-icon"><Icon size={18} strokeWidth={1.8} /></span><span className="node-order">0{data.order + 1}</span></div>
      <div className="node-eyebrow">{data.step.eyebrow}</div>
      <div className="node-title">{data.step.title}</div>
      <div className="node-status"><StateIcon size={13} strokeWidth={2.6} /><span>{data.step.label}</span></div>
      {data.order < 5 && <Handle type="source" position={Position.Right} className="flow-handle" />}
    </article>
  );
}

const nodeTypes = { evidence: EvidenceNodeView };
const edgeLabels = ['customer report', 'merged code', 'CI is not deployment', 'deployed build', 'policy result'];

function EvidenceGraph({ steps, onSelect }: { steps: EvidenceStep[]; onSelect: (step: EvidenceStep) => void }) {
  const { fitView } = useReactFlow();
  const nodes = useMemo<GraphNode[]>(() => steps.map((step, order) => ({
    id: step.id,
    type: 'evidence',
    position: { x: order * 255, y: 102 },
    data: { step, order },
  })), [steps]);
  const edges = useMemo<Edge[]>(() => steps.slice(0, -1).map((step, index) => {
    const next = steps[index + 1];
    const uncertainty = ['missing', 'failed', 'disputed'].includes(step.state) || ['missing', 'failed', 'disputed'].includes(next.state);
    return {
      id: `${step.id}-${next.id}`,
      source: step.id,
      target: next.id,
      type: 'smoothstep',
      label: edgeLabels[index],
      markerEnd: { type: MarkerType.ArrowClosed, color: uncertainty ? '#ba7652' : '#57766c' },
      style: { stroke: uncertainty ? '#aa765f' : '#66827a', strokeWidth: 1.7, strokeDasharray: uncertainty ? '6 5' : undefined },
      labelStyle: { fill: '#9ba9a8', fontSize: 10, fontWeight: 600 },
      labelBgStyle: { fill: '#10212a', fillOpacity: 0.92 },
      labelBgPadding: [7, 4] as [number, number],
      labelBgBorderRadius: 4,
    };
  }), [steps]);

  return (
    <div className="graph-shell">
      <div className="graph-caption"><span><span className="live-dot" />Evidence chain</span><span>Drag to pan · scroll to zoom · select a node for proof</span></div>
      <div className="graph-canvas" role="region" aria-label="Interactive commitment evidence graph">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodeClick={(_event, node) => onSelect(node.data.step)}
          fitView
          fitViewOptions={{ padding: 0.18, minZoom: 0.48, maxZoom: 1.1 }}
          minZoom={0.38}
          maxZoom={1.45}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable
          zoomOnDoubleClick={false}
          proOptions={{ hideAttribution: true }}
          ariaLabelConfig={{
            'node.a11yDescription.default': 'Evidence step. Press Enter to inspect details.',
            'controls.zoomIn.ariaLabel': 'Zoom in',
            'controls.zoomOut.ariaLabel': 'Zoom out',
            'controls.fitView.ariaLabel': 'Fit graph to view',
          }}
        >
          <Background color="#253943" gap={24} size={1} />
          <Controls showInteractive={false} position="bottom-left" />
          <MiniMap pannable zoomable position="bottom-right" ariaLabel="Evidence graph overview" nodeColor={(node) => {
            const state = (node.data as GraphNodeData).step.state;
            return state === 'verified' ? '#83b6a2' : state === 'failed' ? '#d4846d' : state === 'context' ? '#8c9ea0' : state === 'review-ready' ? '#b7a878' : '#d2a16a';
          }} />
        </ReactFlow>
        <button className="fit-view" onClick={() => void fitView({ padding: 0.18, duration: 450 })} aria-label="Fit evidence graph to view">Fit graph <ArrowUpRight size={14} /></button>
      </div>
    </div>
  );
}

function DetailPanel({ step, onClose }: { step: EvidenceStep; onClose: () => void }) {
  const Icon = icons[step.id as keyof typeof icons] ?? Activity;
  const StateIcon = statusIcon(step.state);
  return (
    <aside className="detail-panel" aria-label={`${step.title} evidence details`}>
      <div className="detail-head"><span className={`detail-icon state-${step.state}`}><Icon size={19} /></span><button className="icon-button" onClick={onClose} aria-label="Close details"><X size={18} /></button></div>
      <div className="detail-eyebrow">{step.eyebrow}</div>
      <h3>{step.title}</h3>
      <div className={`detail-state state-${step.state}`}><StateIcon size={14} />{step.label}</div>
      <p>{step.explanation}</p>
      {step.detail && <div className="detail-note">{step.detail}</div>}
      {step.sourceUrl ? <a className="source-link" href={step.sourceUrl} target="_blank" rel="noreferrer">Open original evidence <ExternalLink size={14} /></a> : <div className="no-source"><CircleHelp size={15} />No source record was returned</div>}
      {step.id === 'decision' && <div className="policy-note"><ShieldAlert size={16} /><span>The policy never permits an automatic completion claim. Human review remains a separate step.</span></div>}
    </aside>
  );
}

function WorkflowStrip({ mode, events, connected }: { mode: ViewMode; events: SwitchEvent[]; connected: boolean }) {
  const example = mode === 'example';
  const evidenceEvent = events.find((event) => event.agent_id === 'commitmentproof-evidence-agent');
  const coordinatorEvent = events.find((event) => event.agent_id === 'commitmentproof-coordinator');
  const items = [
    { label: 'Human Request', purpose: 'Names the customer promise and the pull request to check.', output: example ? 'Request recorded in this room' : 'Investigation submitted from this dashboard', icon: MessageSquareText, state: 'done', badge: example ? 'RECORDED' : 'LOCAL REQUEST' },
    { label: 'Evidence Agent', purpose: 'Collects source-linked PR and CI evidence; calls out proof still missing.', output: example ? 'Merged PR + passing CI; deployment and QA unknown' : evidenceEvent?.summary || 'No signed agent event; read-only GitHub collector returned evidence', icon: GitBranch, state: example ? 'done' : evidenceEvent ? 'done' : 'waiting', badge: example ? 'RECORDED HANDOFF' : evidenceEvent ? 'SIGNED EVENT' : 'DISCONNECTED' },
    { label: 'Coordinator Agent', purpose: 'Applies the completion policy and turns evidence gaps into a next step.', output: example ? 'Requested deployment proof and QA confirmation' : coordinatorEvent?.summary || 'No signed coordinator event for this investigation', icon: Activity, state: example ? 'done' : coordinatorEvent ? 'done' : 'waiting', badge: example ? 'RECORDED ASSESSMENT' : coordinatorEvent ? 'SIGNED EVENT' : 'DISCONNECTED' },
    { label: 'Human Approval', purpose: 'A person reviews the exact follow-up before any external write.', output: example ? 'Owner approved the exact follow-up in Switch' : 'No approval requested', icon: ShieldCheck, state: example ? 'done' : 'waiting', badge: example ? 'HUMAN APPROVED' : 'PENDING' },
    { label: 'GitHub Action', purpose: example ? 'Human created the issue manually after approval; no automated write ran.' : 'Creates an issue only after verified human approval.', output: example ? 'Issue #2 created manually' : 'No issue created', icon: ArrowUpRight, state: example ? 'done' : 'locked', badge: example ? 'MANUAL ACTION' : 'NOT EXECUTED' },
  ];
  return (
    <section className="workflow-section" aria-labelledby="workflow-title">
      <div className="switch-workflow-heading"><div><div className="section-kicker">THE COLLABORATION PATH</div><h2 id="workflow-title">Powered by Switch: Humans + AI Agents Working Together.</h2><p>Evidence moves from a person, through two distinct agent roles, and returns to a human before GitHub can change.</p></div><div className={`workflow-status ${example || connected ? 'is-recorded' : 'is-unconnected'}`}>{example ? 'RECORDED ROOM HANDOFF' : connected ? 'LIVE AGENT EVENTS CONNECTED' : 'LIVE AGENT EVENTS DISCONNECTED'}<span>{example ? 'Actual handoff - not a live event feed' : connected ? 'Fresh signed events from both configured agents' : 'Waiting for fresh signed agent publications'}</span></div></div>
      <div className="workflow-track" aria-label="Human request to GitHub action workflow">
        {items.map((item, index) => {
          const Icon = item.icon;
          return <div className="workflow-item-wrap" key={item.label}>
            <article className={`workflow-item ${item.state}`}>
              <div className="workflow-step-top"><div className="workflow-icon"><Icon size={21} /></div><small>{item.badge}</small></div>
              <strong>{item.label}</strong><span className="workflow-purpose">{item.purpose}</span>
              <div className="workflow-output"><b>OUTPUT</b><span>{item.output}</span></div>
            </article>
            {index < items.length - 1 && <ArrowRight className="workflow-arrow" size={17} aria-hidden="true" />}
          </div>;
        })}
      </div>
      <div className="agent-rationale"><div className="handoff-beam"><ArrowRight size={19} /></div><p><strong>Why two agents?</strong> The Evidence Agent gathers and cites the proof. The Coordinator independently applies the policy and identifies what is missing. That handoff keeps raw evidence separate from the decision about whether a customer promise is safe to close.</p></div>
      {example && <section className="human-completion" aria-labelledby="human-completion-title">
        <div className="human-completion-heading"><div><span>VERIFIED HUMAN-IN-THE-LOOP OUTCOME</span><h3 id="human-completion-title">HUMAN APPROVED · GITHUB ISSUE CREATED</h3></div><span className="manual-action-badge">MANUALLY EXECUTED AFTER APPROVAL</span></div>
        <ol className="completion-sequence">
          <li><b>Evidence Agent investigated</b><span>PR merged; CI passed. Deployment and QA remain unverified.</span><a href="https://github.com/microsoft/vscode/pull/331010" target="_blank" rel="noreferrer">Open PR evidence <ExternalLink size={12} /></a></li>
          <li><b>Coordinator drafted follow-up</b><span>Requested deployment proof and QA reproduction.</span><small>Switch draft sw_62aebc7a04614e88bcd9d06065b6ec04</small></li>
          <li><b>Human approved exact draft</b><span>Approval recorded in the Switch conversation.</span><small>Switch approval sw_353065a05331d2295d29b05961ea6c17</small></li>
          <li><b>Human manually created issue</b><span>No automated GitHub write was performed.</span><a href="https://github.com/bh00mi-11/CommitmentProof/issues/2" target="_blank" rel="noreferrer">Open issue #2 <ExternalLink size={12} /></a></li>
          <li><b>Coordinator verified the issue</b><span>Title, open status, body and evidence links match the approved draft.</span><a href="https://github.com/bh00mi-11/CommitmentProof/issues/2" target="_blank" rel="noreferrer">Verified issue #2 · OPEN <ExternalLink size={12} /></a></li>
        </ol>
        <div className="verified-issue-title"><span>GITHUB ISSUE #2</span><strong>Verify deployment and QA for merged VS Code fix #331010</strong><a href="https://github.com/bh00mi-11/CommitmentProof/issues/2" target="_blank" rel="noreferrer">View the manually created issue <ExternalLink size={13} /></a>
          <div className="issue-evidence-links"><a href="https://github.com/microsoft/vscode/pull/331010" target="_blank" rel="noreferrer">Merged PR #331010 <ExternalLink size={12} /></a><a href="https://github.com/microsoft/vscode/commit/026fe5316ffc0998aab553b3ca3d4f36d8e8792f/checks" target="_blank" rel="noreferrer">27/27 successful checks <ExternalLink size={12} /></a></div>
        </div>
        <p className="manual-action-note">This issue was created manually by the human owner after approving the exact proposal. The automated approval backend is not deployed.</p>
      </section>}
      {example ? <div className="room-note"><div><span>VERIFIED SWITCH-CONNECTED DISCORD ROOM</span><strong>#commitment-proof</strong><small>Channel ID - 1568584704301473985</small></div><a href="https://discord.com/channels" target="_blank" rel="noreferrer">Open Discord <ExternalLink size={14} /></a><p>Recorded handoff: Evidence Agent message <code>sw_f3edfcaadde246219504809024217a72</code> → Coordinator follow-up draft <code>sw_62aebc7a04614e88bcd9d06065b6ec04</code>. This verified room history is not a live event feed.</p></div> : <div className="room-note room-note-unconnected"><span>{connected ? 'SIGNED AGENT EVENT FEED' : 'SWITCH AGENT EVENT FEED DISCONNECTED'}</span><p>Per-agent Ed25519 signatures authenticate these publications, not Switch or Discord. Events expire from this view after 15 minutes; approvals and GitHub writes remain disabled.</p>{events.map((event) => <article className="agent-event" key={event.event_id}><strong>{event.agent_id}</strong><span>{event.execution_status} · {new Date(event.received_at).toLocaleString()}</span><p>{event.summary}</p>{event.evidence_links.map((url) => <a key={url} href={url} target="_blank" rel="noreferrer">Open evidence <ExternalLink size={12} /></a>)}</article>)}</div>}
    </section>
  );
}

export default function App() {
  const [mode, setMode] = useState<ViewMode>('example');
  const [investigation, setInvestigation] = useState<Investigation>(() => makeExampleInvestigation());
  const [selected, setSelected] = useState<EvidenceStep | null>(null);
  const [showGraph, setShowGraph] = useState(false);
  const [repository, setRepository] = useState('');
  const [pullRequest, setPullRequest] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [switchFeed, setSwitchFeed] = useState<SwitchEventFeed>({ status: 'DISCONNECTED', authenticated: false, events: [] });
  const steps = useMemo(() => buildEvidenceSteps(investigation.evidence, investigation.assessment), [investigation]);
  const decision = decisionLabel(investigation.assessment);
  const verifiedCount = steps.filter((step) => step.state === 'verified').length;
  const sourceCount = steps.filter((step) => step.sourceUrl).length;
  const unresolvedCount = steps.filter((step) => step.id !== 'decision' && step.state === 'missing').length;
  const checklist = steps.filter((step) => ['pr', 'checks', 'deploy', 'qa'].includes(step.id));

  useEffect(() => {
    const hasLiveInvestigationId = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(investigation.investigation_id);
    if (mode !== 'live' || !hasLiveInvestigationId) {
      setSwitchFeed({ status: 'DISCONNECTED', authenticated: false, events: [] });
      return;
    }
    let active = true;
    const refresh = async () => {
      try {
        const response = await fetch(`/api/switch/events?investigation_id=${encodeURIComponent(investigation.investigation_id)}`);
        const value: unknown = await response.json();
        if (!active || !response.ok || !value || typeof value !== 'object' || !('events' in value) || !Array.isArray(value.events)) {
          if (active) setSwitchFeed({ status: 'DISCONNECTED', authenticated: false, events: [] });
          return;
        }
        const feed = value as SwitchEventFeed;
        setSwitchFeed({ status: feed.status === 'CONNECTED' && feed.authenticated ? 'CONNECTED' : 'DISCONNECTED', authenticated: feed.authenticated === true, events: feed.events });
      } catch {
        if (active) setSwitchFeed({ status: 'DISCONNECTED', authenticated: false, events: [] });
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 15_000);
    return () => { active = false; window.clearInterval(timer); };
  }, [investigation.investigation_id, mode]);

  const runInvestigation = useCallback(async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError('');
    const parsedNumber = Number(pullRequest);
    if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository.trim()) || !Number.isSafeInteger(parsedNumber) || parsedNumber < 1) {
      setError('Enter a repository as owner/name and a positive pull request number.');
      return;
    }
    setLoading(true);
    try {
      const response = await fetch('/api/investigate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repository: repository.trim(), pull_request_number: parsedNumber, investigation_id: crypto.randomUUID() }),
      });
      const rawResponse = await response.text();
      let payload: unknown;
      try {
        payload = JSON.parse(rawResponse);
      } catch {
        const message = response.ok
          ? 'The investigation API returned a non-JSON response. Confirm the Vercel /api/investigate function is deployed.'
          : `The investigation API returned HTTP ${response.status} without a JSON error. Check Vercel /api routing.`;
        throw new Error(message);
      }
      if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
        throw new Error('The investigation API returned an unexpected response.');
      }
      if (!response.ok) {
        const apiError = 'error' in payload && typeof payload.error === 'string' ? payload.error : '';
        throw new Error(apiError || `The evidence service could not complete the investigation (HTTP ${response.status}).`);
      }
      setInvestigation(payload as Investigation);
      setMode('live');
      setSelected(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not reach the local evidence service.');
    } finally {
      setLoading(false);
    }
  }, [pullRequest, repository]);

  const switchMode = (next: ViewMode) => {
    setMode(next);
    setError('');
    setSelected(null);
    if (next === 'example') setInvestigation(makeExampleInvestigation());
  };

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="CommitmentProof home"><span className="brand-mark"><ShieldCheck size={19} /></span><span>commitment<span>proof</span></span></a>
        <div className="topbar-right"><span className="product-tag">EVIDENCE REVIEW</span><span className="topbar-divider" /><span className="switch-mark"><span /> Switch workflow</span></div>
      </header>

      <section className="hero" id="top">
        <div className="hero-copy">
          <div className="eyebrow"><span className="eyebrow-line" />THE PROMISE ISN'T DONE YET</div>
          <h1>Can we tell the customer their fix is <em>delivered?</em></h1>
          <p>A merged PR is one clue. CommitmentProof checks the evidence before anyone says the promise is fulfilled.</p>
        </div>
        <div className="hero-side"><div className="hero-side-icon"><FileWarning size={20} /></div><div><span>THE QUESTION</span><strong>Can we tell the customer their promised fix is live?</strong></div></div>
      </section>

      <section className="investigation-bar" aria-label="Investigation controls">
        <div className="mode-switch" role="tablist" aria-label="Investigation mode">
          <button className={mode === 'example' ? 'active' : ''} onClick={() => switchMode('example')} role="tab" aria-selected={mode === 'example'}><Sparkles size={15} /> Recorded Switch session</button>
          <button className={mode === 'live' ? 'active' : ''} onClick={() => switchMode('live')} role="tab" aria-selected={mode === 'live'}><Activity size={15} /> Live investigation</button>
        </div>
        {mode === 'example' ? <div className="mode-disclaimer"><span className="disclaimer-dot" />Verified Switch transcript · GitHub evidence collected previously · not a live event feed</div> : (
          <form className="investigate-form" onSubmit={runInvestigation}>
            <label><span>REPOSITORY</span><input value={repository} onChange={(event) => setRepository(event.target.value)} placeholder="owner/repository" autoComplete="off" /></label>
            <label className="pr-input"><span>PULL REQUEST</span><input value={pullRequest} onChange={(event) => setPullRequest(event.target.value)} placeholder="123" inputMode="numeric" /></label>
            <button className="investigate-button" type="submit" disabled={loading}>{loading ? <LoaderCircle className="spin" size={16} /> : <Activity size={16} />}{loading ? 'Checking…' : 'Check evidence'}<ArrowUpRight size={15} /></button>
          </form>
        )}
      </section>
      {error && <div className="error-banner" role="alert"><ShieldAlert size={16} />{error}<button onClick={() => setError('')} aria-label="Dismiss error"><X size={15} /></button></div>}

      <section className="dashboard-evidence" aria-labelledby="checklist-title">
        <div className="section-heading checklist-heading"><div><div className="section-kicker">THE EVIDENCE</div><h2 id="checklist-title">Four checks. One honest answer.</h2></div><div className="evidence-tally"><div><b>{verifiedCount}</b><span>verified</span></div><div className="tally-divider" /><div><b>{unresolvedCount}</b><span>unresolved</span></div><div className="tally-divider" /><div><b>{sourceCount}</b><span>source links</span></div></div></div>
        <div className="checklist-grid">
          {checklist.map((step, index) => {
            const Icon = icons[step.id as keyof typeof icons] ?? FileWarning;
            const StateIcon = statusIcon(step.state);
            return <article className={`check-card state-${step.state}`} key={step.id}>
              <div className="check-card-top"><div className="check-icon"><Icon size={18} /></div><span className="check-step">0{index + 1} / 04</span></div>
              <div className="check-card-title">{step.title}</div>
              <div className="check-card-status"><StateIcon size={13} />{step.label}</div>
              <p>{step.detail || step.explanation}</p>
              {step.sourceUrl ? <a href={step.sourceUrl} target="_blank" rel="noreferrer">Open source evidence <ExternalLink size={13} /></a> : <span className="check-no-source"><CircleHelp size={13} />No source record</span>}
            </article>;
          })}
        </div>
      </section>

      <section className={`verdict-card state-${decision.state}`} aria-live="polite">
        <div className="verdict-icon">{decision.state === 'failed' ? <X size={24} /> : decision.state === 'review-ready' ? <ShieldCheck size={24} /> : <CircleHelp size={24} />}</div>
        <div className="verdict-copy"><div className="verdict-kicker">POLICY VERDICT</div><h2>{decision.label}</h2><p>{decision.message}</p><div className="verdict-reasons">{investigation.assessment.reasons.map((reason) => <span key={reason}><i />{reason}</span>)}</div></div>
        <div className="verdict-guardrail"><ShieldAlert size={17} /><span>Completion claim<br /><strong>NOT PERMITTED</strong></span></div>
      </section>

      {investigation.evidence.collection_errors && investigation.evidence.collection_errors.length > 0 && <div className="collector-notes" role="status"><FileWarning size={15} /><div><strong>Collector notes</strong>{investigation.evidence.collection_errors.map((note) => <span key={note}>{note}</span>)}</div></div>}

      <WorkflowStrip mode={mode} events={switchFeed.events} connected={switchFeed.status === 'CONNECTED'} />

      <section className="next-step"><div className="next-step-icon"><CircleHelp size={19} /></div><div><span>NEXT ACTION</span><strong>{investigation.assessment.permitted_action === 'REQUEST_EVIDENCE' ? 'Request deployment proof and QA confirmation.' : investigation.assessment.permitted_action === 'PREPARE_FOLLOWUP_DRAFT' ? 'Prepare a follow-up and keep the customer promise open.' : 'Ask a human to review the complete source-linked evidence.'}</strong><p>Unknown evidence stays unknown. Issue #2 was manually created after approval; this screen does not create issues or send customer messages.</p></div><div className="next-step-lock"><ShieldAlert size={15} /> Human decision required</div></section>

      <section className="map-disclosure" aria-labelledby="graph-title">
        <button className="map-toggle" onClick={() => setShowGraph((visible) => !visible)} aria-expanded={showGraph} aria-controls="evidence-map"><span><GitBranch size={16} />Explore the evidence relationship map</span><span>{showGraph ? 'Hide map' : 'Optional detail'} <ArrowDownRight size={15} /></span></button>
        {showGraph && <div id="evidence-map" className="graph-section">
          <div className="section-heading graph-heading"><div><div className="section-kicker">OPTIONAL EXPLORATION</div><h2 id="graph-title">How each piece connects</h2></div><div className="graph-key"><span><i className="key-dot verified" />Verified</span><span><i className="key-dot failed" />Failed</span><span><i className="key-dot missing" />Missing</span></div></div>
          <div className="graph-layout"><ReactFlowProvider><EvidenceGraph steps={steps} onSelect={setSelected} /></ReactFlowProvider>{selected ? <DetailPanel step={selected} onClose={() => setSelected(null)} /> : <aside className="inspect-prompt"><span className="prompt-ring"><ArrowDownRight size={19} /></span><strong>Follow the proof</strong><p>Select a step to inspect its source, status and reason.</p><span className="prompt-meta">{investigation.repository} · PR #{investigation.pull_request_number}</span></aside>}</div>
        </div>}
      </section>

      <footer className="footer"><span>COMMITMENTPROOF <i>·</i> PROVE IT BEFORE YOU CLOSE IT</span><span>{mode === 'example' ? 'Recorded Switch session · no live GitHub request made' : `Read-only GitHub evidence · ${investigation.collected_at}`}</span></footer>
    </main>
  );
}

# CommitmentProof — Prove it before you close it

**Flip the Switch Hackathon · Track 01: Best Business Workflow**

> A merged pull request is not the same as a customer promise kept.

Support asks whether a promised checkout fix is complete. An evidence agent checks linked PR, tests, deployment and QA evidence; a coordination agent prepares the safe next step; a human reviews it. **Switch should be the shared workspace that connects these participants.**

## Current build status

| Component | Status |
| --- | --- |
| Python evidence gate | Implemented, 7 local tests passing |
| Negative / missing evidence blocks completion | Implemented |
| Human review requirement | Implemented as gate; no external approval integration |
| Switch configuration | Starter template, **not verified against Switch Console** |
| Live Switch messages / agent handoffs | **Not implemented** |
| Authenticated GitHub PR, checks, deployment reads | **Not implemented** |
| Approved follow-up issue creation | **Not implemented** |

**Do not present this starter as a functioning Switch integration or live proof.**

## Files

- `commitment_proof.py` — standard-library deterministic evidence checker; not an LLM, GitHub connector or Switch agent.
- `tests/test_commitment_proof.py` — seven policy tests.
- `examples/merged_failed.json` — fictional case showing an unsafe completion claim.
- `switch/commitment-proof.template.yaml` — proposed two-agent room template with placeholders, not validated against the installed Switch version.

## Run

Python 3.10+; no third-party dependencies.

```bash
python -m unittest discover -s tests -v
python commitment_proof.py examples/merged_failed.json
```

The sample must return `BLOCKED` and `can_claim_complete: false`.

## Minimum actual Switch acceptance

1. Install and configure Switch Console using the [official resources](https://www.wemakedevs.org/hackathons/switch/resources).
2. Start one real room with human support manager and two functioning agents; adapt the template to the installed version.
3. Evidence agent retrieves **real, current, linked** GitHub PR/check/deployment records with read-only access; never fabricate sources.
4. Evidence agent hands findings to coordinator inside Switch and coordinator requests human review.
5. Failed tests, unknown deployment and contradictory QA must keep a promise open.
6. On **explicit human authorization**, create an actual follow-up task once; verify the GitHub result and record it in the room.
7. Record a reproducible live Switch demo and preserve anonymized logs. No automatic customer communication.

**Safety:** The checker always leaves `can_claim_complete` false. It does not itself fetch evidence, publish messages or make any external write.

Official challenge: https://www.wemakedevs.org/hackathons/switch

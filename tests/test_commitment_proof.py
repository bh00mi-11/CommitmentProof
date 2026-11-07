import unittest
from commitment_proof import assess

def sample():
    return {k: {"value": True, "source_url": "https://github.com/example/repo/actions/runs/123"} for k in (
        "pr_merged", "tests_passed", "deployment_verified", "qa_confirmed")}

class EvidenceGateTests(unittest.TestCase):
    def test_complete_evidence_requires_human_review(self):
        result = assess(sample())
        self.assertEqual(result.decision, "REVIEW_READY")
        self.assertFalse(result.can_claim_complete)
        self.assertEqual(result.permitted_action, "REQUEST_HUMAN_APPROVAL")
    def test_merged_pr_failed_tests_blocks(self):
        evidence = sample()
        evidence["tests_passed"]["value"] = False
        self.assertEqual(assess(evidence).decision, "BLOCKED")
    def test_missing_deploy_needs_evidence(self):
        evidence = sample()
        del evidence["deployment_verified"]
        self.assertEqual(assess(evidence).decision, "NEEDS_EVIDENCE")
    def test_no_source_does_not_pass(self):
        evidence = sample()
        del evidence["pr_merged"]["source_url"]
        self.assertEqual(assess(evidence).decision, "NEEDS_EVIDENCE")
    def test_qa_disagreement_blocks(self):
        evidence = sample()
        evidence["qa_confirmed"]["value"] = False
        self.assertEqual(assess(evidence).decision, "BLOCKED")
    def test_unknown_values_cannot_pass(self):
        evidence = sample()
        evidence["deployment_verified"]["value"] = None
        self.assertEqual(assess(evidence).decision, "NEEDS_EVIDENCE")
    def test_empty_evidence_fails_closed(self):
        self.assertEqual(assess({}).decision, "NEEDS_EVIDENCE")

if __name__ == "__main__":
    unittest.main()

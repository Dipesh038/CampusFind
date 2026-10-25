"""Temporary CF-11 negative CI demonstration; never merge into main."""

import unittest


class IntentionalGateFailure(unittest.TestCase):
    def test_intentional_failure_for_ci_gate_evidence(self):
        self.fail('Intentional CF-11 negative CI check; restore after evidence and do not merge.')

import unittest

from feedverdict.evaluation import run_evaluation


class EvaluationTests(unittest.TestCase):
    def test_all_decision_scenarios_meet_their_contract(self) -> None:
        report = run_evaluation()

        self.assertEqual(report.total, 6)
        self.assertTrue(report.all_passed)
        self.assertEqual(report.accuracy, 1.0)


if __name__ == "__main__":
    unittest.main()

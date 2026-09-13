"""
Regression test script
Run golden test set to verify RAG system core functionality
"""
import json
import os
import sys
from datetime import datetime
from typing import Dict, List, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.boundary_detector import boundary_detector
from core.intent_classifier import intent_classifier


class RegressionTester:
    """Regression tester"""

    def __init__(self, test_set_path: str):
        with open(test_set_path, 'r', encoding='utf-8') as f:
            self.test_set = json.load(f)

        self.results = []
        self.passed_count = 0
        self.failed_count = 0

    def _run_test_case(self, test_case: Dict) -> Dict:
        """Run single test case"""
        test_id = test_case['id']
        test_name = test_case['name']
        query = test_case['query']
        expected = test_case['expected']

        result = {
            'id': test_id,
            'name': test_name,
            'query': query,
            'expected': expected,
            'actual': {},
            'passed': True,
            'errors': []
        }

        try:
            boundary_result = boundary_detector.detect(query)
            result['actual']['boundary_result'] = boundary_result
            result['actual']['in_domain'] = boundary_result.get('in_domain', True)

            if 'in_domain' in expected:
                if boundary_result.get('in_domain') != expected['in_domain']:
                    result['passed'] = False
                    result['errors'].append(
                        f"边界检测失败: 期望in_domain={expected['in_domain']}, 实际={boundary_result.get('in_domain')}"
                    )

            if 'status' in expected:
                if boundary_result.get('in_domain') is False:
                    result['actual']['status'] = 'boundary_rejected'
                    if result['actual']['status'] != expected['status']:
                        result['passed'] = False
                        result['errors'].append(
                            f"状态检测失败: 期望status={expected['status']}, 实际={result['actual']['status']}"
                        )

            if boundary_result.get('in_domain', True):
                intent_result = intent_classifier.classify(query)
                result['actual']['intent_result'] = intent_result
                result['actual']['intent_type'] = intent_result.get('intent_type', '')

                if 'intent_type' in expected:
                    expected_intent = expected['intent_type']
                    actual_intent = intent_result.get('intent_type', '')

                    if expected_intent not in actual_intent and actual_intent not in expected_intent:
                        result['passed'] = False
                        result['errors'].append(
                            f"意图分类失败: 期望intent_type包含'{expected_intent}', 实际='{actual_intent}'"
                        )

            if 'answer_contains' in expected:
                answer = boundary_result.get('suggestion', '')
                result['actual']['answer'] = answer

                for keyword in expected['answer_contains']:
                    if keyword not in answer:
                        result['passed'] = False
                        result['errors'].append(
                            f"回答内容检测失败: 期望包含'{keyword}', 实际回答='{answer[:100]}...'"
                        )

        except Exception as e:
            result['passed'] = False
            result['errors'].append(f"测试执行异常: {str(e)}")

        return result

    def run(self) -> Dict[str, Any]:
        """Run all test cases"""
        print(f"\n{'='*60}")
        print(f"开始回归测试")
        print(f"测试集版本: {self.test_set.get('version', 'unknown')}")
        print(f"测试用例数: {len(self.test_set['tests'])}")
        print(f"{'='*60}")

        start_time = datetime.now()

        for test_case in self.test_set['tests']:
            result = self._run_test_case(test_case)
            self.results.append(result)

            if result['passed']:
                self.passed_count += 1
                status = "✅ PASS"
            else:
                self.failed_count += 1
                status = "❌ FAIL"

            print(f"\n{status} [{result['id']}] {result['name']}")
            if not result['passed']:
                for error in result['errors']:
                    print(f"   - {error}")

        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        summary = {
            'total': len(self.test_set['tests']),
            'passed': self.passed_count,
            'failed': self.failed_count,
            'pass_rate': round(self.passed_count / len(self.test_set['tests']) * 100, 2),
            'duration': round(duration, 2),
            'start_time': start_time.isoformat(),
            'end_time': end_time.isoformat(),
            'results': self.results
        }

        print(f"\n{'='*60}")
        print(f"测试结果汇总")
        print(f"{'='*60}")
        print(f"总用例数: {summary['total']}")
        print(f"通过: {summary['passed']}")
        print(f"失败: {summary['failed']}")
        print(f"通过率: {summary['pass_rate']}%")
        print(f"耗时: {summary['duration']}s")
        print(f"{'='*60}")

        return summary


if __name__ == '__main__':
    test_set_path = os.path.join(os.path.dirname(__file__), 'golden_test_set.json')
    tester = RegressionTester(test_set_path)
    summary = tester.run()

    if summary['failed'] > 0:
        sys.exit(1)
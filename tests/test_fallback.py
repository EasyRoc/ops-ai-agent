from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from agent.agents.fallback import (
    FALLBACK_SYSTEM_PROMPT,
    RETRY_SYSTEM_PROMPT,
    _build_fallback_prompt,
    _build_retry_prompt,
    _validate_ai_output,
)


class ValidateAiOutputTest(TestCase):
    def test_valid_output_passes_all_checks(self):
        result = {
            "reasoning": "流量上涨导致 CPU 高，扩容可缓解",
            "steps": [
                {
                    "risk_level": "低风险",
                    "description": "查看当前 Pod 状态",
                    "command": "kubectl get pods -n demo",
                },
                {
                    "risk_level": "中风险",
                    "description": "扩容 order-service 到 4 副本",
                    "command": "kubectl scale deployment order-service -n demo --replicas=4",
                },
            ],
            "rollback": "kubectl rollout undo deployment order-service -n demo",
            "verification": {
                "metric": "cpu",
                "operator": "<",
                "threshold": 70.0,
                "description": "CPU 降至 70% 以下",
            },
            "confidence": 0.8,
        }

        self.assertEqual(_validate_ai_output(result), [])

    def test_rejects_empty_steps(self):
        errors = _validate_ai_output(
            {"steps": [], "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0}}
        )

        self.assertIn("steps 为空或不是列表", errors)

    def test_rejects_missing_steps(self):
        errors = _validate_ai_output({"verification": {"metric": "cpu", "operator": "<", "threshold": 70.0}})

        self.assertGreater(len(errors), 0)

    def test_rejects_non_whitelist_command(self):
        result = {
            "steps": [
                {
                    "risk_level": "低风险",
                    "description": "执行危险脚本",
                    "command": "rm -rf /tmp/data",
                }
            ],
            "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0},
        }

        errors = _validate_ai_output(result)

        self.assertTrue(any("不在白名单" in error for error in errors))

    def test_rejects_invalid_metric(self):
        result = {
            "steps": [{"risk_level": "低风险", "description": "查看 Pod", "command": "kubectl get pods -n demo"}],
            "verification": {"metric": "disk_usage", "operator": "<", "threshold": 80.0},
        }

        errors = _validate_ai_output(result)

        self.assertTrue(any("验证指标" in error for error in errors))

    def test_rejects_invalid_operator(self):
        result = {
            "steps": [{"risk_level": "低风险", "description": "查看 Pod", "command": "kubectl get pods -n demo"}],
            "verification": {"metric": "cpu", "operator": "==", "threshold": 70.0},
        }

        errors = _validate_ai_output(result)

        self.assertTrue(any("验证操作符" in error for error in errors))

    def test_rejects_invalid_risk_level(self):
        result = {
            "steps": [{"risk_level": "极高风险", "description": "删 Pod", "command": "kubectl delete pod test -n demo"}],
            "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0},
        }

        errors = _validate_ai_output(result)

        self.assertTrue(any("风险等级" in error for error in errors))

    def test_rejects_missing_description(self):
        result = {
            "steps": [{"risk_level": "低风险", "command": "kubectl get pods -n demo"}],
            "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0},
        }

        errors = _validate_ai_output(result)

        self.assertTrue(any("缺少 description" in error for error in errors))

    def test_rejects_missing_command(self):
        result = {
            "steps": [{"risk_level": "低风险", "description": "没有命令的步骤"}],
            "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0},
        }

        errors = _validate_ai_output(result)

        self.assertTrue(any("缺少 command" in error for error in errors))


class BuildFallbackPromptTest(TestCase):
    def test_includes_all_context_sections(self):
        context = {
            "metrics": {
                "cpu": {"current": 92.5},
                "memory": {"current": 536870912},
                "qps": {"current": 150.0},
                "rt_avg": {"current": 1.2},
                "error_rate": {"current": 0.03},
            },
            "logs": [{"line": "Connection refused"}],
            "pods": {"total": 3, "ready": 2, "pods": [{"name": "svc-abc"}]},
            "cmdb": {"owner": "ops-team", "team": "SRE", "dependencies": ["payment-service"]},
        }
        alert = {"alertname": "DiskFull", "service": "order-service", "env": "prod", "severity": "P2", "value": "92%"}
        diagnosis = {"root_cause": "磁盘空间不足"}

        prompt = _build_fallback_prompt(context, alert, diagnosis)

        self.assertIn("DiskFull", prompt)
        self.assertIn("order-service", prompt)
        self.assertIn("CPU使用率: 92.5%", prompt)
        self.assertIn("Connection refused", prompt)
        self.assertIn("svc-abc", prompt)
        self.assertIn("磁盘空间不足", prompt)
        self.assertIn("payment-service", prompt)

    def test_handles_empty_logs_and_pods(self):
        prompt = _build_fallback_prompt(
            {"metrics": {}, "logs": [], "pods": {}, "cmdb": {}},
            {"alertname": "UnknownAlert", "service": "test", "env": "staging", "severity": "P3", "value": ""},
            {"root_cause": "未知"},
        )

        self.assertIn("无错误日志", prompt)


class FallbackSystemPromptTest(TestCase):
    def test_system_prompt_contains_whitelist_commands(self):
        self.assertIn("kubectl scale deployment", FALLBACK_SYSTEM_PROMPT)
        self.assertIn("kubectl delete pod", FALLBACK_SYSTEM_PROMPT)
        self.assertIn("kubectl rollout undo deployment", FALLBACK_SYSTEM_PROMPT)
        self.assertIn("kubectl set resources deployment", FALLBACK_SYSTEM_PROMPT)

    def test_system_prompt_forbids_destructive_actions(self):
        self.assertIn("严禁不可逆操作", FALLBACK_SYSTEM_PROMPT)

    def test_system_prompt_specifies_valid_metrics(self):
        self.assertIn("cpu", FALLBACK_SYSTEM_PROMPT)
        self.assertIn("memory", FALLBACK_SYSTEM_PROMPT)
        self.assertIn("rt_avg", FALLBACK_SYSTEM_PROMPT)
        self.assertIn("error_rate", FALLBACK_SYSTEM_PROMPT)


class RetrySystemPromptTest(TestCase):
    def test_retry_prompt_requires_different_strategy(self):
        self.assertIn("必须不同于前几轮", RETRY_SYSTEM_PROMPT)

    def test_retry_prompt_contains_whitelist_commands(self):
        self.assertIn("kubectl scale deployment", RETRY_SYSTEM_PROMPT)
        self.assertIn("kubectl rollout undo deployment", RETRY_SYSTEM_PROMPT)


class BuildRetryPromptTest(TestCase):
    def test_includes_all_retry_context_sections(self):
        previous_plan = {
            "steps": [
                {
                    "risk_level": "中风险",
                    "description": "扩容 order-service",
                    "command": "kubectl scale deployment order-service -n demo --replicas=4",
                }
            ],
            "ai_reasoning": "流量上涨导致 CPU 高",
        }
        execution_result = {
            "status": "failed",
            "stdout": "deployment scaled",
            "stderr": "Error: timeout",
            "exit_code": 1,
        }
        verification_result = {
            "recovered": False,
            "metric": "cpu",
            "current": 88.5,
            "threshold": 70.0,
        }
        retry_history = [
            {
                "round": 1,
                "plan_steps": [{"description": "扩容"}],
                "execution": {"status": "failed"},
                "verification": {"recovered": False},
            }
        ]
        context = {
            "metrics": {
                "cpu": {"current": 88.5},
                "memory": {"current": 536870912},
                "qps": {"current": 120},
                "rt_avg": {"current": 1.5},
                "error_rate": {"current": 0.01},
            },
            "pods": {"total": 4, "ready": 3, "pods": [{"name": "order-xyz"}]},
        }
        alert = {"alertname": "HighCPUUsage", "service": "order-service"}

        prompt = _build_retry_prompt(
            previous_plan,
            execution_result,
            verification_result,
            2,
            retry_history,
            context,
            alert,
        )

        self.assertIn("第 2/5 轮", prompt)
        self.assertIn("上一轮处置方案", prompt)
        self.assertIn("kubectl scale deployment", prompt)
        self.assertIn("Error: timeout", prompt)
        self.assertIn("88.5", prompt)
        self.assertIn("第 1 轮: 扩容", prompt)


class GenerateAiActionPlanTest(IsolatedAsyncioTestCase):
    @patch("agent.agents.fallback.chat_json")
    async def test_returns_structured_plan_on_success(self, mock_chat):
        mock_chat.return_value = {
            "reasoning": "流量上涨导致资源不足",
            "steps": [
                {
                    "risk_level": "中风险",
                    "description": "扩容 order-service 到 4 副本",
                    "command": "kubectl scale deployment order-service -n demo --replicas=4",
                }
            ],
            "rollback": "kubectl rollout undo deployment order-service -n demo",
            "verification": {
                "metric": "cpu",
                "operator": "<",
                "threshold": 70.0,
                "description": "CPU 降至 70%",
            },
            "confidence": 0.85,
        }

        from agent.agents.fallback import generate_ai_action_plan

        result = await generate_ai_action_plan(
            context={"metrics": {}, "logs": [], "pods": {}, "cmdb": {}},
            alert={"alertname": "UnknownAlert", "service": "test", "env": "prod", "severity": "P2", "value": ""},
            diagnosis={"root_cause": "未知"},
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "ai_fallback")
        self.assertTrue(result["ai_generated"])
        self.assertEqual(len(result["steps"]), 1)
        self.assertEqual(result["steps"][0]["risk_level"], "中风险")
        self.assertEqual(result["confidence"], 0.85)
        self.assertEqual(result["ai_reasoning"], "流量上涨导致资源不足")
        self.assertEqual(result["verification"]["metric"], "cpu")

    @patch("agent.agents.fallback.chat_json")
    async def test_returns_none_when_llm_output_invalid(self, mock_chat):
        mock_chat.return_value = {
            "reasoning": "test",
            "steps": [],
            "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0},
        }

        from agent.agents.fallback import generate_ai_action_plan

        result = await generate_ai_action_plan(
            context={"metrics": {}, "logs": [], "pods": {}, "cmdb": {}},
            alert={"alertname": "Unknown", "service": "test", "env": "prod", "severity": "P3", "value": ""},
            diagnosis={"root_cause": "未知"},
        )

        self.assertIsNone(result)

    @patch("agent.agents.fallback.chat_json")
    async def test_returns_none_when_non_whitelist_command(self, mock_chat):
        mock_chat.return_value = {
            "reasoning": "test",
            "steps": [{"risk_level": "低风险", "description": "危险操作", "command": "kubectl delete namespace demo"}],
            "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0},
        }

        from agent.agents.fallback import generate_ai_action_plan

        result = await generate_ai_action_plan(
            context={"metrics": {}, "logs": [], "pods": {}, "cmdb": {}},
            alert={"alertname": "Unknown", "service": "test", "env": "prod", "severity": "P3", "value": ""},
            diagnosis={"root_cause": "未知"},
        )

        self.assertIsNone(result)

    @patch("agent.agents.fallback.chat_json")
    async def test_returns_none_on_llm_exception(self, mock_chat):
        mock_chat.side_effect = RuntimeError("LLM 超时")

        from agent.agents.fallback import generate_ai_action_plan

        result = await generate_ai_action_plan(
            context={"metrics": {}, "logs": [], "pods": {}, "cmdb": {}},
            alert={"alertname": "Unknown", "service": "test", "env": "prod", "severity": "P3", "value": ""},
            diagnosis={"root_cause": "未知"},
        )

        self.assertIsNone(result)


class AnalyzeFailureAndRetryTest(IsolatedAsyncioTestCase):
    @patch("agent.agents.fallback.chat_json")
    async def test_returns_corrected_plan_on_success(self, mock_chat):
        mock_chat.return_value = {
            "retry_reasoning": "扩容未恢复表明不是容量问题，改为重启异常 Pod",
            "failure_analysis": "扩容后 CPU 仍 88%，排除容量瓶颈，疑似单 Pod 热点",
            "steps": [
                {
                    "risk_level": "中风险",
                    "description": "重启异常 Pod 释放热点",
                    "command": "kubectl delete pod order-xyz -n demo",
                }
            ],
            "verification": {
                "metric": "cpu",
                "operator": "<",
                "threshold": 70.0,
                "description": "CPU 降至 70% 以下",
            },
            "confidence": 0.7,
        }

        from agent.agents.fallback import analyze_failure_and_retry

        result = await analyze_failure_and_retry(
            incident_id="INC-TEST",
            previous_plan={
                "steps": [
                    {
                        "risk_level": "中风险",
                        "description": "扩容",
                        "command": "kubectl scale deployment order-service -n demo --replicas=4",
                    }
                ],
                "ai_reasoning": "",
            },
            execution_result={"status": "failed", "stdout": "", "stderr": "", "exit_code": 0},
            verification_result={"recovered": False, "metric": "cpu", "current": 88.5, "threshold": 70.0},
            retry_count=2,
            retry_history=[],
            context={"metrics": {}, "pods": {}},
            alert={"alertname": "HighCPUUsage", "service": "order-service"},
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["retry_reasoning"], "扩容未恢复表明不是容量问题，改为重启异常 Pod")
        self.assertEqual(result["failure_analysis"], "扩容后 CPU 仍 88%，排除容量瓶颈，疑似单 Pod 热点")
        self.assertEqual(len(result["steps"]), 1)
        self.assertEqual(result["steps"][0]["command"], "kubectl delete pod order-xyz -n demo")
        self.assertEqual(result["verification"]["metric"], "cpu")

    @patch("agent.agents.fallback.chat_json")
    async def test_returns_none_when_llm_generates_empty_steps(self, mock_chat):
        mock_chat.return_value = {
            "retry_reasoning": "",
            "failure_analysis": "",
            "steps": [],
            "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0},
        }

        from agent.agents.fallback import analyze_failure_and_retry

        result = await analyze_failure_and_retry(
            incident_id="INC-TEST",
            previous_plan={"steps": []},
            execution_result={},
            verification_result={},
            retry_count=1,
            retry_history=[],
            context={"metrics": {}, "pods": {}},
            alert={"alertname": "Test", "service": "test"},
        )

        self.assertIsNone(result)

    @patch("agent.agents.fallback.chat_json")
    async def test_returns_none_on_llm_exception(self, mock_chat):
        mock_chat.side_effect = RuntimeError("LLM timeout")

        from agent.agents.fallback import analyze_failure_and_retry

        result = await analyze_failure_and_retry(
            incident_id="INC-TEST",
            previous_plan={"steps": []},
            execution_result={},
            verification_result={},
            retry_count=1,
            retry_history=[],
            context={"metrics": {}, "pods": {}},
            alert={"alertname": "Test", "service": "test"},
        )

        self.assertIsNone(result)

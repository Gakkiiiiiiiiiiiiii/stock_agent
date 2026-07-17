import json

from app.claude_agent import ClaudeAgent
from app.skill_loader import SkillDefinition
from app.tool_registry import ClaudeToolRegistry


class FakeClient:
    def __init__(self, responses):
        self._responses = responses
        self.calls = []

    def available(self):
        return True

    def create_chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


def test_claude_agent_runs_tool_loop():
    fake_client = FakeClient(
        [
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "get_market_snapshot", "arguments": json.dumps({})},
                                }
                            ]
                        }
                    }
                ]
            },
            {"choices": [{"message": {"content": "这是 DeepSeek 的最终报告。"}}]},
        ]
    )
    registry = ClaudeToolRegistry()
    agent = ClaudeAgent(
        client=fake_client,
        tools=registry,
        skills=[SkillDefinition(slug="daily-market-decision", name="daily-market-decision", description="", content="Use tools.")],
    )
    result = agent.run("做一次日报", force_skill="daily-market-decision")
    assert result.selected_skill == "daily-market-decision"
    assert "forced" in result.selection_reason.lower()
    assert result.tool_calls[0]["name"] == "get_market_snapshot"
    assert result.trace["steps"][0]["type"] == "skill_selection"
    assert any(step["type"] == "tool_call" for step in result.trace["steps"])
    assert "最终报告" in result.report


def test_claude_agent_preselects_daily_market_decision_for_recent_opportunity_query():
    agent = ClaudeAgent(
        client=FakeClient([]),
        tools=ClaudeToolRegistry(),
        skills=[
            SkillDefinition(slug="daily-market-decision", name="daily-market-decision", description="", content="Use tools."),
            SkillDefinition(slug="industry-logic-research", name="industry-logic-research", description="", content="Use tools."),
        ],
    )
    decision = agent._choose_skill("最近有什么比较好的板块或者赛道可以进行投资")
    assert decision.skill.slug == "daily-market-decision"
    assert "video insights" in decision.reason.lower()

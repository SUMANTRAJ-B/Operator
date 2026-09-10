"""Working memory and message context manager for the Operator agent."""

from typing import Any, Dict, List, Optional
from providers.llm.base import ChatMessage, ToolCall


SYSTEM_PROMPT = """You are Operator, an autonomous Windows computer-use AI agent.
Fulfill the user's goal by observing the desktop, executing precise tool calls, and verifying outcomes.

Rules:
1. Tool Selection: Call registered tools matching schemas (open_application, activate_window, type_text, press_key, hotkey, close_window).
2. Verified Facts: When verified state facts confirm a requested state change, rely on them directly.
3. Finish: Call finish_task with success=True when the objective has been accomplished.
4. Host Safety: Never close or disrupt host IDE, terminal, or shell windows.
5. Concise: Output immediate tool calls with 1 concise sentence of reasoning.
"""


class AgentMemory:
    """Manages working memory and message trajectory for LLM interaction."""

    def __init__(self, system_prompt: str = SYSTEM_PROMPT):
        self.system_prompt = system_prompt
        self.messages: List[ChatMessage] = [
            ChatMessage(role="system", content=self.system_prompt)
        ]
        self.recent_observations: List[str] = []
        self.verified_facts: List[Any] = []

    def set_goal(self, goal: str) -> None:
        """Add initial user objective to memory."""
        self.messages.append(ChatMessage(role="user", content=f"User Goal: {goal}"))

    def add_observation(self, observation_text: str) -> None:
        """Record an environmental observation and provide it as context."""
        self.recent_observations.append(observation_text)
        self.messages.append(
            ChatMessage(
                role="user",
                content=f"[Desktop Observation]:\n{observation_text}",
            )
        )

    def add_verified_fact(self, fact: Any) -> None:
        """Record an explicit verified state transition fact in context."""
        self.verified_facts.append(fact)
        action_fact = getattr(fact, "action_fact", str(fact))
        verification_fact = getattr(fact, "verification_fact", "")
        current_state = getattr(fact, "current_state", "")

        fact_text = (
            "[Verified State Facts]:\n"
            f"- ACTION FACT: {action_fact}\n"
            f"- VERIFICATION FACT: {verification_fact}\n"
            f"- CURRENT STATE: {current_state}"
        )
        self.messages.append(
            ChatMessage(
                role="user",
                content=fact_text,
            )
        )

    def add_assistant_response(
        self,
        content: str = "",
        tool_calls: Optional[List[ToolCall]] = None,
        thinking: Optional[str] = None,
    ) -> None:
        """Record the model's response and planned tool calls."""
        self.messages.append(
            ChatMessage(
                role="assistant",
                content=content or "",
                tool_calls=tool_calls,
            )
        )

    def add_tool_result(self, tool_call_id: str, tool_name: str, result_output: Any) -> None:
        """Record a tool execution result corresponding to a tool call."""
        import json
        if isinstance(result_output, (dict, list)):
            content = json.dumps(result_output)
        else:
            content = str(result_output)

        self.messages.append(
            ChatMessage(
                role="tool",
                content=content,
                tool_call_id=tool_call_id,
                name=tool_name,
            )
        )

    def get_messages(self, compact: bool = True) -> List[ChatMessage]:
        """Return the message trajectory for LLM inference.

        When compact is True:
        - Prunes redundant stale desktop observations, keeping only the latest active desktop observation.
        - Preserves user goals, recovery feedback, and tool results.
        - Compacts older action turns if trajectory exceeds recent window, preserving recovery context without token bloat.
        """
        if not compact or len(self.messages) <= 5:
            return list(self.messages)

        # 1. Identify indices of desktop observation messages
        obs_indices = [
            i for i, m in enumerate(self.messages)
            if m.role == "user" and m.content.startswith("[Desktop Observation]:")
        ]

        # Only the latest desktop observation is relevant to current state
        stale_obs_indices = set(obs_indices[:-1]) if len(obs_indices) > 1 else set()

        filtered_messages = [
            m for i, m in enumerate(self.messages)
            if i not in stale_obs_indices
        ]

        # 2. Trajectory compaction if history is deep (> 3 assistant turns)
        assistant_indices = [i for i, m in enumerate(filtered_messages) if m.role == "assistant"]
        if len(assistant_indices) > 3:
            cutoff_index = assistant_indices[-3]  # keep last 3 assistant turns in full
            system_and_goal = [
                m for m in filtered_messages[:cutoff_index]
                if m.role in ("system", "user") and not m.content.startswith("[Desktop Observation]:")
            ]
            older_actions = []
            for m in filtered_messages[:cutoff_index]:
                if m.role == "assistant" and m.tool_calls:
                    calls = ", ".join(f"{tc.name}({tc.arguments})" for tc in m.tool_calls)
                    older_actions.append(f"Action: {calls}")
                elif m.role == "tool":
                    older_actions.append(f"Result ({m.name}): {m.content[:100]}")

            compacted = []
            compacted.extend(system_and_goal)
            if older_actions:
                compacted.append(
                    ChatMessage(
                        role="user",
                        content="[Prior Actions Summary]:\n" + "\n".join(older_actions),
                    )
                )
            compacted.extend(filtered_messages[cutoff_index:])
            return compacted

        return filtered_messages

    def clear(self) -> None:
        """Reset memory to initial state."""
        self.messages = [ChatMessage(role="system", content=self.system_prompt)]
        self.recent_observations.clear()

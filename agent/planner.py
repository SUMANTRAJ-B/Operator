"""Structured task planning and milestone tracking for the Operator agent."""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class StepStatus(str, Enum):
    """Execution status for an individual plan step."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class PlanStep:
    """Represents a milestone in a structured task plan."""

    step_id: int
    description: str
    status: StepStatus = StepStatus.PENDING
    action_taken: Optional[str] = None
    verification_notes: Optional[str] = None

    def mark_in_progress(self) -> None:
        self.status = StepStatus.IN_PROGRESS

    def mark_completed(self, notes: str = "") -> None:
        self.status = StepStatus.COMPLETED
        if notes:
            self.verification_notes = notes

    def mark_failed(self, notes: str = "") -> None:
        self.status = StepStatus.FAILED
        if notes:
            self.verification_notes = notes


@dataclass
class TaskPlan:
    """A collection of ordered steps decomposing a high-level goal."""

    goal: str
    steps: List[PlanStep] = field(default_factory=list)
    current_step_index: int = 0

    @property
    def current_step(self) -> Optional[PlanStep]:
        if 0 <= self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None

    @property
    def is_completed(self) -> bool:
        return all(s.status == StepStatus.COMPLETED for s in self.steps)

    def advance_step(self) -> Optional[PlanStep]:
        if self.current_step:
            self.current_step.mark_completed()
        self.current_step_index += 1
        if self.current_step:
            self.current_step.mark_in_progress()
        return self.current_step


class TaskPlanner:
    """Generates and maintains structured milestone plans for arbitrary goals."""

    def create_plan(self, goal: str) -> TaskPlan:
        """Create an initial structured milestone plan for the given goal.

        The plan remains generic and application-independent:
        1. Environment Observation: Assess current desktop and active windows.
        2. Application Setup: Launch or switch to target application.
        3. Intent Execution: Perform input or data operations requested by user.
        4. State Verification: Confirm outcomes and state changes.
        5. Completion: Conclude task and summarize findings.
        """
        steps = [
            PlanStep(step_id=1, description=f"Observe desktop state and locate requirements for: '{goal}'"),
            PlanStep(step_id=2, description="Launch or activate target application"),
            PlanStep(step_id=3, description="Perform requested actions and text/data entry"),
            PlanStep(step_id=4, description="Verify application state and content"),
            PlanStep(step_id=5, description="Conclude task and finalize execution"),
        ]
        plan = TaskPlan(goal=goal, steps=steps)
        if plan.steps:
            plan.steps[0].mark_in_progress()
        return plan

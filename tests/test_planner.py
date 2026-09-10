"""Unit tests for structured task planning."""

from agent.planner import PlanStep, StepStatus, TaskPlan, TaskPlanner


def test_plan_step_transitions():
    step = PlanStep(step_id=1, description="Initial observation")
    assert step.status == StepStatus.PENDING

    step.mark_in_progress()
    assert step.status == StepStatus.IN_PROGRESS

    step.mark_completed("Step verified")
    assert step.status == StepStatus.COMPLETED
    assert step.verification_notes == "Step verified"


def test_task_plan_progression():
    s1 = PlanStep(step_id=1, description="Step 1")
    s2 = PlanStep(step_id=2, description="Step 2")
    plan = TaskPlan(goal="Test Goal", steps=[s1, s2])

    assert plan.current_step_index == 0
    assert plan.current_step == s1

    next_step = plan.advance_step()
    assert next_step == s2
    assert plan.current_step_index == 1
    assert s1.status == StepStatus.COMPLETED
    assert s2.status == StepStatus.IN_PROGRESS


def test_task_planner_create_plan():
    planner = TaskPlanner()
    plan = planner.create_plan("Open Notepad and type Hello Operator")

    assert isinstance(plan, TaskPlan)
    assert len(plan.steps) == 5
    assert plan.steps[0].status == StepStatus.IN_PROGRESS
    assert "Open Notepad" in plan.goal

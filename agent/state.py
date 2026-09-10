"""Agent lifecycle state models, resource ownership tracking, and execution history."""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import uuid


class AgentStatus(str, Enum):
    """Execution lifecycle status of the autonomous agent."""

    IDLE = "idle"
    PLANNING = "planning"
    OBSERVING = "observing"
    ACTING = "acting"
    VERIFYING = "verifying"
    CLEANING = "cleaning"
    RECOVERING = "recovering"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class OwnedResource:
    """Represents a window or process resource created and owned by Operator during a task."""

    hwnd: int
    pid: int
    title: str
    app_identity: str
    resource_type: str = "window"
    created_at: float = field(default_factory=time.time)
    initial_state: str = "OPEN"
    current_state: str = "OPEN"  # "OPEN", "CLOSED", "CLOSING"
    cleanup_required: bool = True
    cleanup_verified: bool = False
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hwnd": self.hwnd,
            "pid": self.pid,
            "title": self.title,
            "app_identity": self.app_identity,
            "resource_type": self.resource_type,
            "created_at": self.created_at,
            "initial_state": self.initial_state,
            "current_state": self.current_state,
            "cleanup_required": self.cleanup_required,
            "cleanup_verified": self.cleanup_verified,
            "details": self.details,
        }


@dataclass
class VerifiedFact:
    """Concise, verified state transition fact from an action outcome."""

    action_fact: str
    verification_fact: str
    current_state: str
    target: str
    state_type: str  # "CLOSED", "OPEN", "ACTIVE", "DISPATCHED", etc.
    verified: bool
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_fact": self.action_fact,
            "verification_fact": self.verification_fact,
            "current_state": self.current_state,
            "target": self.target,
            "state_type": self.state_type,
            "verified": self.verified,
            "timestamp": self.timestamp,
        }


@dataclass
class StepExecution:
    """Record of a single action-observation-verification cycle."""

    step_number: int
    phase: str
    observation: str = ""
    thought: Optional[str] = None
    action: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
    verification: Optional[Dict[str, Any]] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step": self.step_number,
            "phase": self.phase,
            "observation": self.observation,
            "thought": self.thought,
            "action": self.action,
            "result": self.result,
            "verification": self.verification,
            "timestamp": self.timestamp,
        }


@dataclass
class AgentState:
    """Complete mutable state of the Operator agent during execution."""

    goal: str
    task_id: str = field(default_factory=lambda: f"task_{uuid.uuid4().hex[:8]}")
    status: AgentStatus = AgentStatus.IDLE
    current_step: int = 0
    history: List[StepExecution] = field(default_factory=list)
    error: Optional[str] = None
    final_summary: Optional[str] = None
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None

    # Resource Ownership Tracking
    owned_resources: List[OwnedResource] = field(default_factory=list)
    pre_existing_hwnds: Set[int] = field(default_factory=set)
    cleanup_completed: bool = False
    cleanup_error: Optional[str] = None
    objective_verified: bool = False

    # Verified state transitions
    verified_facts: List[VerifiedFact] = field(default_factory=list)
    entity_states: Dict[str, str] = field(default_factory=dict)
    modal_recovery_audits: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        """Return True if agent has reached a terminal state."""
        return self.status in (AgentStatus.COMPLETED, AgentStatus.FAILED)

    @property
    def elapsed_seconds(self) -> float:
        """Elapsed time since task initiation."""
        end = self.end_time or time.time()
        return round(end - self.start_time, 2)

    def add_execution(self, step: StepExecution) -> None:
        """Append an execution step to history."""
        self.history.append(step)

    def mark_completed(self, summary: str = "") -> None:
        """Transition agent status to COMPLETED."""
        self.status = AgentStatus.COMPLETED
        self.final_summary = summary
        self.end_time = time.time()

    def mark_failed(self, error: str) -> None:
        """Transition agent status to FAILED."""
        self.status = AgentStatus.FAILED
        self.error = error
        self.end_time = time.time()

    # --- Resource Ownership Management ---

    def record_initial_environment(self, hwnds: Set[int]) -> None:
        """Snapshot pre-existing window HWNDs at task initiation to protect user windows."""
        self.pre_existing_hwnds = set(hwnds)

    def register_owned_resource(
        self,
        hwnd: int,
        pid: int,
        title: str,
        app_identity: str,
        resource_type: str = "window",
        **kwargs,
    ) -> Optional[OwnedResource]:
        """Register a newly created window/process as an Operator-owned resource.

        Strictly rejects registering any HWND that pre-existed the task.
        """
        if not hwnd or hwnd in self.pre_existing_hwnds:
            return None

        for existing in self.owned_resources:
            if existing.hwnd == hwnd:
                return existing

        resource = OwnedResource(
            hwnd=hwnd,
            pid=pid,
            title=title,
            app_identity=app_identity,
            resource_type=resource_type,
            details=kwargs,
        )
        self.owned_resources.append(resource)
        return resource

    def get_owned_resources(self, unclosed_only: bool = False) -> List[OwnedResource]:
        """Retrieve all or unclosed Operator-owned resources."""
        if unclosed_only:
            return [r for r in self.owned_resources if r.current_state != "CLOSED"]
        return list(self.owned_resources)

    def mark_resource_closed(self, hwnd_or_target: Union[int, str]) -> bool:
        """Mark an owned resource as CLOSED when confirmed closed during task execution."""
        matched = False
        target_str = str(hwnd_or_target).strip().lower()

        for res in self.owned_resources:
            if isinstance(hwnd_or_target, int) and res.hwnd == hwnd_or_target:
                res.current_state = "CLOSED"
                res.cleanup_verified = True
                matched = True
            elif target_str and (
                str(res.hwnd) == target_str
                or target_str in res.title.lower()
                or target_str in res.app_identity.lower()
            ):
                res.current_state = "CLOSED"
                res.cleanup_verified = True
                matched = True

        return matched

    def is_resource_owned(self, hwnd: int) -> bool:
        """Check if an HWND is registered as Operator-owned."""
        return any(r.hwnd == hwnd for r in self.owned_resources)

    # --- Verified State Fact Management ---

    def record_verified_transition(
        self,
        action_fact: str,
        verification_fact: str,
        current_state: str,
        target: str,
        state_type: str,
        verified: bool,
    ) -> VerifiedFact:
        """Record an explicit verified state transition."""
        fact = VerifiedFact(
            action_fact=action_fact,
            verification_fact=verification_fact,
            current_state=current_state,
            target=target,
            state_type=state_type,
            verified=verified,
        )
        self.verified_facts.append(fact)
        if verified and target:
            norm_target = target.lower().strip()
            self.entity_states[norm_target] = state_type
            # Also normalize aliases
            if "calc" in norm_target:
                self.entity_states["calculator"] = state_type
                self.entity_states["calc"] = state_type
            if "notepad" in norm_target:
                self.entity_states["notepad"] = state_type
        return fact

    def record_modal_recovery(self, audit: Any) -> None:
        """Record a structured modal recovery audit event."""
        if hasattr(audit, "to_dict"):
            self.modal_recovery_audits.append(audit.to_dict())
        elif isinstance(audit, dict):
            self.modal_recovery_audits.append(audit)

    def is_objective_satisfied(self) -> Tuple[bool, str]:
        """Generic evaluation of whether the user goal has been achieved based on verified state."""
        goal_lower = self.goal.lower().strip()

        # 1. Closure objectives (e.g. "Close Calculator", "Safely close a target application")
        is_closure_goal = any(
            verb in goal_lower
            for verb in ("close", "exit", "quit", "terminate", "shut down", "kill")
        )
        if is_closure_goal:
            for entity, state in self.entity_states.items():
                if state == "CLOSED":
                    # Check if entity or any related alias is referenced in the goal
                    if entity in goal_lower or ("target application" in goal_lower and entity in ("calculator", "calc", "notepad")):
                        return True, f"Requested target '{entity}' has been verified CLOSED."

        # 2. Open file / application objectives where open is the sole goal
        # (e.g. "Open the existing sample.txt file.", "Open Calculator", "Open File Explorer")
        has_subsequent_ops = any(
            kw in goal_lower
            for kw in ("type", "calculate", "switch", "create", "then", "and type", "and calculate")
        )
        if not has_subsequent_ops:
            is_open_goal = any(verb in goal_lower for verb in ("open", "launch", "start"))
            if is_open_goal:
                for entity, state in self.entity_states.items():
                    if state in ("OPEN", "FILE_OPENED"):
                        if entity in goal_lower:
                            return True, f"Requested target '{entity}' has been verified {state}."

        return False, ""

"""Core OperatorAgent implementing the autonomous Observe -> Plan -> Act -> Observe -> Verify loop with session cleanup."""

from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import time

from actions.applications import get_app_controller
from actions.mouse import get_mouse_controller
from actions.registry import ToolRegistry, get_tool_registry
from agent.memory import AgentMemory
from agent.modal_recovery import (
    ModalRecoveryController,
    RecoveryOutcome,
    get_modal_recovery_controller,
)
from agent.planner import StepStatus, TaskPlan, TaskPlanner
from agent.recovery import ErrorCategory, RecoveryManager
from agent.state import AgentState, AgentStatus, StepExecution, OwnedResource
from app.config import get_settings
from app.logging import get_logger
from providers.llm.base import LLMProvider
from providers.llm.ollama import get_llm_provider
from safety.policy import SafetyPolicy, get_safety_policy
from ui.console import DeveloperUI, get_developer_ui
from verification.verifier import ActionVerifier, get_action_verifier

logger = get_logger("agent.core")


class OperatorAgent:
    """Autonomous general-purpose Windows computer-use agent with deterministic session cleanup."""

    def __init__(
        self,
        llm: Optional[LLMProvider] = None,
        registry: Optional[ToolRegistry] = None,
        safety: Optional[SafetyPolicy] = None,
        verifier: Optional[ActionVerifier] = None,
        planner: Optional[TaskPlanner] = None,
        recovery: Optional[RecoveryManager] = None,
        modal_recovery: Optional[ModalRecoveryController] = None,
        ui: Optional[DeveloperUI] = None,
        cleanup_on_finish: Optional[bool] = None,
        timeout_seconds: Optional[float] = None,
    ):
        self.settings = get_settings()
        self.llm = llm or get_llm_provider()
        self.registry = registry or get_tool_registry()
        self.safety = safety or get_safety_policy()
        self.verifier = verifier or get_action_verifier()
        self.planner = planner or TaskPlanner()
        self.recovery = recovery or RecoveryManager()
        self.modal_recovery = modal_recovery or get_modal_recovery_controller()
        self.ui = ui or get_developer_ui()
        self.apps = get_app_controller()
        self.mouse = get_mouse_controller()
        self._last_target_app: Optional[str] = None
        self.cleanup_on_finish = (
            cleanup_on_finish
            if cleanup_on_finish is not None
            else self.settings.cleanup_owned_resources_on_finish
        )
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else self.settings.agent_timeout_seconds
        )

    def cleanup_resources(self, state: AgentState) -> Tuple[bool, List[str]]:
        """Safely close and programmatically verify all Operator-owned resources.

        Strict safety rules:
        1. Never close pre-existing windows (state.pre_existing_hwnds).
        2. Never close protected windows/processes (Antigravity IDE, shells, terminals).
        3. Never blindly send Alt+F4. Use safe WM_CLOSE via AppController.close_window.
        4. Programmatically verify window destruction with win32gui.IsWindow.
        5. Retry if necessary; report failure if cleanup cannot be verified.
        """
        if not self.cleanup_on_finish:
            state.cleanup_completed = True
            return True, ["Cleanup disabled by configuration"]

        state.status = AgentStatus.CLEANING
        self.ui.display_phase("CLEANUP", "Closing Operator-owned session resources")

        unclosed = state.get_owned_resources(unclosed_only=True)
        if not unclosed:
            state.cleanup_completed = True
            return True, ["No Operator-owned resources required cleanup"]

        notes: List[str] = []
        all_cleaned = True

        for res in unclosed:
            # Safety Gate 0: Verify ownership ("cleanup can close only Operator-owned resources")
            if not state.is_resource_owned(res.hwnd):
                logger.warning(f"Safety gate: Refusing to close unowned HWND {res.hwnd}")
                continue

            # Safety Gate 1: Check against pre-existing HWNDs
            if res.hwnd in state.pre_existing_hwnds:
                logger.warning(f"Safety gate: Refusing to close pre-existing HWND {res.hwnd}")
                continue

            # Safety Gate 2: Check against protected host environments
            is_prot = False
            if hasattr(self.apps, "is_protected_target"):
                try:
                    res_prot = self.apps.is_protected_target(hwnd=res.hwnd, pid=res.pid, title=res.title)
                    if res_prot is True:
                        is_prot = True
                except Exception:
                    pass
            if is_prot:
                logger.warning(
                    f"Safety gate: Refusing to close protected resource: {res.title!r} (HWND {res.hwnd})"
                )
                continue

            # Check if already closed
            import win32gui
            if not win32gui.IsWindow(res.hwnd):
                res.current_state = "CLOSED"
                res.cleanup_verified = True
                notes.append(f"Resource '{res.title}' (HWND {res.hwnd}) already closed.")
                continue

            # Attempt safe close via WM_CLOSE
            logger.info(f"Session cleanup: closing Operator-owned window '{res.title}' (HWND {res.hwnd})")
            self.apps.close_window(res.hwnd, force=False)
            time.sleep(0.3)

            # Verification Attempt 1
            if not win32gui.IsWindow(res.hwnd):
                res.current_state = "CLOSED"
                res.cleanup_verified = True
                notes.append(f"Successfully closed and verified '{res.title}' (HWND {res.hwnd}).")
                continue

            # Re-observe & Retry once safely
            time.sleep(0.3)
            is_prot_retry = False
            if hasattr(self.apps, "is_protected_target"):
                try:
                    res_p = self.apps.is_protected_target(hwnd=res.hwnd, pid=res.pid, title=res.title)
                    if res_p is True:
                        is_prot_retry = True
                except Exception:
                    pass
            if not is_prot_retry:
                self.apps.close_window(res.hwnd, force=True)
            else:
                self.apps.close_window(res.hwnd, force=False)
            time.sleep(0.4)

            # Verification Attempt 2
            if not win32gui.IsWindow(res.hwnd):
                res.current_state = "CLOSED"
                res.cleanup_verified = True
                notes.append(f"Successfully closed and verified '{res.title}' (HWND {res.hwnd}) on retry.")
            else:
                # Still open: do not claim success without programmatic verification!
                res.cleanup_verified = False
                all_cleaned = False
                err_msg = f"Failed to verify closure of Operator-owned window '{res.title}' (HWND {res.hwnd})"
                logger.error(err_msg)
                notes.append(err_msg)

        state.cleanup_completed = all_cleaned
        if not all_cleaned:
            state.cleanup_error = "; ".join(notes)
            return False, notes

        return True, notes

    def run(
        self,
        goal: str,
        pre_cleanup_verifier: Optional[Callable[[], bool]] = None,
    ) -> AgentState:
        """Execute the autonomous perception-action loop to fulfill the goal."""
        self._last_target_app = None
        state = AgentState(goal=goal)
        state.status = AgentStatus.PLANNING

        # Register active state with AppController and SafetyPolicy
        if hasattr(self.apps, "set_active_state"):
            self.apps.set_active_state(state)
        if hasattr(self.safety, "set_active_state"):
            self.safety.set_active_state(state)
        if hasattr(self.safety, "safety_gate") and hasattr(self.safety.safety_gate, "set_active_state"):
            self.safety.safety_gate.set_active_state(state)


        # 0. SNAPSHOT INITIAL ENVIRONMENT
        # Record all visible and hidden top-level window HWNDs to protect pre-existing applications
        try:
            initial_windows = self.apps.list_windows(visible_only=False)
            initial_hwnds = {w.hwnd for w in initial_windows}
            # All initial windows except those already created/owned by Operator are pre-existing
            pre_existing = {
                h for h in initial_hwnds
                if not (hasattr(self.apps, "is_owned_hwnd") and self.apps.is_owned_hwnd(h))
            }
            state.record_initial_environment(pre_existing)
            if hasattr(self.apps, "record_pre_existing_hwnds"):
                self.apps.record_pre_existing_hwnds(pre_existing)

            # Sync already-owned windows from apps into state
            if hasattr(self.apps, "get_owned_hwnds"):
                for h in self.apps.get_owned_hwnds():
                    if h in initial_hwnds:
                        win = self.apps.resolve_window_target(h)
                        if win:
                            state.register_owned_resource(
                                hwnd=win.hwnd,
                                pid=win.pid,
                                title=win.title,
                                app_identity=win.title.lower(),
                            )

            logger.info(f"Snapshotted {len(pre_existing)} pre-existing window HWNDs before task execution.")
        except Exception as err:
            logger.warning(f"Error snapshotting initial environment HWNDs: {err}")


        plan = self.planner.create_plan(goal)
        self.ui.display_header(goal, self.settings.ollama_model, state.task_id)
        self.ui.display_plan(plan)

        memory = AgentMemory()
        memory.set_goal(goal)

        max_steps = self.settings.max_agent_steps
        timeout = self.timeout_seconds
        start_time = time.time()

        logger.info(f"Starting agent loop for task: {goal!r} (max_steps={max_steps}, timeout={timeout}s)")

        while not state.is_terminal and state.current_step < max_steps:
            if time.time() - start_time > timeout:
                state.mark_failed(f"Task exceeded maximum timeout of {timeout}s")
                self.ui.display_failure(state.error, state.elapsed_seconds)
                break

            state.current_step += 1
            step_num = state.current_step

            # 1. OBSERVE
            state.status = AgentStatus.OBSERVING
            active_win = self.apps.get_active_window()
            visible_wins = [w.title for w in self.apps.list_windows(visible_only=True) if w.title.strip()][:6]
            scr_w, scr_h = self.mouse.get_screen_size()

            obs_summary = (
                f"Screen: {scr_w}x{scr_h}. "
                f"Active Window: '{active_win.title if active_win else 'None'}' "
                f"(HWND: {active_win.hwnd if active_win else 'N/A'}). "
                f"Open Windows: {visible_wins}."
            )
            self.ui.display_phase("OBSERVE", f"Active window: '{active_win.title if active_win else 'None'}'")
            memory.add_observation(obs_summary)

            # 2. PLAN
            state.status = AgentStatus.PLANNING
            current_plan_step = plan.current_step
            plan_desc = current_plan_step.description if current_plan_step else "Executing goal"
            self.ui.display_phase("PLAN", f"Step {step_num}: {plan_desc}")

            # 3. ACT (LLM Tool Selection & Execution)
            state.status = AgentStatus.ACTING
            tool_schemas = self.registry.get_all_schemas()

            try:
                llm_response = self.llm.chat(
                    messages=memory.get_messages(),
                    tools=tool_schemas,
                )
            except Exception as err:
                logger.error(f"LLM generation failed: {err}")
                rec = self.recovery.handle_failure("llm_chat", str(err), ErrorCategory.TIMEOUT)
                if not rec.can_retry:
                    state.mark_failed(f"LLM communication error: {err}")
                    self.ui.display_failure(state.error, state.elapsed_seconds)
                    break
                memory.add_observation(rec.feedback_for_agent)
                continue

            # Record model response in memory
            memory.add_assistant_response(
                content=llm_response.content,
                tool_calls=llm_response.tool_calls,
                thinking=llm_response.thinking,
            )

            # Handle case where LLM did not call any tools
            if not llm_response.has_tool_calls:
                logger.info(f"Model responded without tool calls: {llm_response.content!r}")
                if "done" in llm_response.content.lower() or "finished" in llm_response.content.lower():
                    # Objective is satisfied, run session cleanup before final completion
                    if pre_cleanup_verifier:
                        try:
                            state.objective_verified = bool(pre_cleanup_verifier())
                        except Exception as e:
                            logger.warning(f"pre_cleanup_verifier error: {e}")

                    cleanup_ok, _ = self.cleanup_resources(state)
                    if not cleanup_ok:
                        state.mark_failed(f"Session cleanup failed: {state.cleanup_error}")
                        self.ui.display_failure(state.error, state.elapsed_seconds)
                        return state

                    state.mark_completed(llm_response.content)
                    self.ui.display_completion(llm_response.content, state.elapsed_seconds)
                    break
                else:
                    memory.add_observation(
                        "Please choose a specific computer tool to advance the task (e.g. open_application, type_text, finish_task)."
                    )
                    continue

            # Execute tool calls
            for tc in llm_response.tool_calls:
                # Track target application if launched or activated
                if tc.name == "open_application" and tc.arguments.get("app_name"):
                    self._last_target_app = tc.arguments["app_name"]
                elif tc.name == "activate_window" and tc.arguments.get("window_title_or_hwnd"):
                    self._last_target_app = str(tc.arguments["window_title_or_hwnd"])

                # Pre-action target locking: prevent focus drift before keyboard/mouse inputs
                if tc.name in ("type_text", "press_key", "hotkey"):
                    target = tc.arguments.get("window_title_or_hwnd") or self._last_target_app
                    if tc.name == "type_text" and not tc.arguments.get("window_title_or_hwnd") and self._last_target_app:
                        tc.arguments["window_title_or_hwnd"] = self._last_target_app

                    if target:
                        focused = self.apps.ensure_target_focused(target)
                        if not focused:
                            # Check if a blocking modal or dialog is intercepting target focus
                            is_block, d_state = self.modal_recovery.is_blocking_modal(target_title_or_query=target)
                            if is_block and d_state:
                                self.ui.display_phase("RECOVER", f"Blocking dialog detected: '{d_state.title}'. Initiating visual recovery...")
                                state.status = AgentStatus.RECOVERING
                                rec_result = self.modal_recovery.attempt_recovery(
                                    task_id=state.task_id,
                                    goal=goal,
                                    target_title_or_query=target,
                                )
                                state.record_modal_recovery(rec_result.audit)
                                if rec_result.success:
                                    self.ui.display_phase("RECOVER", f"Modal recovery verified: {rec_result.message}. Re-establishing target focus.")
                                    time.sleep(0.3)
                                    focused = self.apps.ensure_target_focused(target)
                                elif rec_result.outcome in (
                                    RecoveryOutcome.HIGH_RISK,
                                    RecoveryOutcome.SAFE_STOP,
                                    RecoveryOutcome.NO_SAFE_ACTION,
                                ):
                                    state.mark_failed(f"Modal recovery safe stop: {rec_result.message}")
                                    self.ui.display_failure(state.error, state.elapsed_seconds)
                                    return state

                        if not focused:
                            active_now = self.apps.get_active_window()
                            err_msg = (
                                f"Pre-action target lock failed: target window {target!r} could not be established in foreground. "
                                f"Active window is: '{active_now.title if active_now else 'None'}'. Keystrokes aborted."
                            )
                            logger.warning(err_msg)
                            self.ui.display_phase("RECOVER", f"Target lock failed for {target!r}")
                            rec = self.recovery.handle_failure(
                                tc.name, err_msg, ErrorCategory.VERIFICATION_FAILED
                            )
                            memory.add_tool_result(tc.id, tc.name, {"error": err_msg})
                            memory.add_observation(rec.feedback_for_agent)
                            continue

                # Pre-action Safety Gate
                decision = self.safety.evaluate_action(tc.name, tc.arguments)
                if not decision.allowed:
                    self.ui.display_phase("RECOVER", f"Safety blocked action {tc.name}: {decision.reason}")
                    rec = self.recovery.handle_failure(
                        tc.name, decision.reason, ErrorCategory.SAFETY_BLOCKED
                    )
                    memory.add_tool_result(tc.id, tc.name, {"error": decision.reason})
                    memory.add_observation(rec.feedback_for_agent)
                    continue

                # Execute action safely
                self.ui.display_action(tc.name, decision.sanitized_arguments, thought=llm_response.thinking)
                result = self.registry.execute(tc.name, decision.sanitized_arguments)
                memory.add_tool_result(tc.id, tc.name, result.output if result.success else {"error": result.error})

                # Check for completion tool
                if tc.name == "finish_task":
                    summary = decision.sanitized_arguments.get("summary", "Goal completed.")
                    requested_success = decision.sanitized_arguments.get("success", True)

                    # State synchronization check: if objective has been verified as satisfied,
                    # prevent false completion failure caused by model misinterpreting absent windows
                    satisfied, sat_reason = state.is_objective_satisfied()
                    if state.objective_verified or satisfied:
                        final_success = True
                    else:
                        final_success = requested_success

                    if final_success:
                        # 1. Run live OS verification callback before cleanup
                        if pre_cleanup_verifier:
                            try:
                                state.objective_verified = bool(pre_cleanup_verifier())
                            except Exception as err:
                                logger.warning(f"pre_cleanup_verifier encountered error: {err}")

                        # 2. Enforce Session Cleanup on all Operator-owned resources
                        cleanup_ok, _ = self.cleanup_resources(state)
                        if not cleanup_ok:
                            state.mark_failed(f"Session cleanup failed: {state.cleanup_error}")
                            self.ui.display_failure(state.error, state.elapsed_seconds)
                            return state

                        # 3. Mark completed after verified cleanup
                        state.mark_completed(summary)
                        if plan.current_step:
                            plan.current_step.mark_completed("Task concluded")
                        self.ui.display_completion(summary, state.elapsed_seconds)
                    else:
                        state.mark_failed(summary)
                        self.ui.display_failure(summary, state.elapsed_seconds)
                    return state

                # Track Operator-owned resource if open_application succeeded
                if tc.name == "open_application" and result.success and isinstance(result.output, dict):
                    hwnd = result.output.get("hwnd")
                    pid = result.output.get("pid")
                    title = result.output.get("title") or ""
                    app_identity = result.output.get("app_name") or tc.arguments.get("app_name", "")
                    is_p = False
                    if hasattr(self.apps, "is_protected_target"):
                        try:
                            is_p = (self.apps.is_protected_target(hwnd=hwnd, pid=pid, title=title) is True)
                        except Exception:
                            pass
                    if hwnd and not is_p:
                        if hasattr(self.apps, "register_owned_hwnd"):
                            self.apps.register_owned_hwnd(hwnd)
                        registered = state.register_owned_resource(
                            hwnd=hwnd,
                            pid=pid or 0,
                            title=title,
                            app_identity=app_identity,
                            arguments=result.output.get("arguments"),
                        )
                        if registered:
                            logger.info(
                                f"Tracked Operator-owned resource: {title!r} (HWND: {hwnd}, PID: {pid})"
                            )

                # Track resource closure if close_window succeeded
                if tc.name == "close_window" and result.success:
                    target_param = tc.arguments.get("window_title_or_hwnd")
                    if target_param is not None:
                        state.mark_resource_closed(target_param)

                # 4. OBSERVE (Post-Action state query)
                state.status = AgentStatus.OBSERVING
                post_active = self.apps.get_active_window()

                # 5. VERIFY
                state.status = AgentStatus.VERIFYING
                verif = self.verifier.verify_action(
                    tc.name, decision.sanitized_arguments, result.output
                )
                self.ui.display_verification(verif.verified, verif.details)

                step_exec = StepExecution(
                    step_number=step_num,
                    phase="execute_and_verify",
                    observation=obs_summary,
                    thought=llm_response.thinking,
                    action={"tool": tc.name, "arguments": decision.sanitized_arguments},
                    result=result.to_dict(),
                    verification={"verified": verif.verified, "details": verif.details},
                )
                state.add_execution(step_exec)

                # 6. POST-ACTION VERIFIED STATE SYNCHRONIZATION
                target_entity = ""
                state_type = "VERIFIED"
                if tc.name == "close_window":
                    target_entity = str(tc.arguments.get("window_title_or_hwnd", ""))
                    action_fact = f"close_window target {target_entity!r}"
                    verification_fact = verif.details
                    current_state = f"target {target_entity!r} is CLOSED" if verif.verified else f"target {target_entity!r} is NOT closed"
                    state_type = "CLOSED"
                elif tc.name == "open_application":
                    target_entity = str(tc.arguments.get("app_name", ""))
                    raw_arg = tc.arguments.get("arguments", "")
                    arg_str = f" arguments={raw_arg!r}" if raw_arg else ""
                    action_fact = f"open_application app={target_entity!r}{arg_str}"
                    verification_fact = verif.details
                    current_state = f"application {target_entity!r} is OPEN" if verif.verified else f"application {target_entity!r} failed to open"
                    state_type = "OPEN"
                elif tc.name == "activate_window":
                    target_entity = str(tc.arguments.get("window_title_or_hwnd", ""))
                    action_fact = f"activate_window target {target_entity!r}"
                    verification_fact = verif.details
                    current_state = f"window {target_entity!r} is FOREGROUND_ACTIVE" if verif.verified else f"window {target_entity!r} is NOT active"
                    state_type = "ACTIVE"
                elif tc.name == "type_text":
                    text_preview = str(tc.arguments.get("text", ""))[:40]
                    action_fact = f"type_text text={text_preview!r}"
                    verification_fact = verif.details
                    current_state = "text dispatched into target window" if verif.verified else "text dispatch failed"
                    state_type = "DISPATCHED"
                else:
                    action_fact = f"{tc.name} executed"
                    verification_fact = verif.details
                    current_state = f"{tc.name} verified: {verif.verified}"
                    state_type = "VERIFIED"

                verified_fact = state.record_verified_transition(
                    action_fact=action_fact,
                    verification_fact=verification_fact,
                    current_state=current_state,
                    target=target_entity,
                    state_type=state_type,
                    verified=verif.verified,
                )
                memory.add_verified_fact(verified_fact)

                if not verif.verified:
                    rec = self.recovery.handle_failure(
                        tc.name, verif.details, ErrorCategory.VERIFICATION_FAILED
                    )
                    memory.add_observation(rec.feedback_for_agent)
                else:
                    # Successfully verified, advance plan milestone
                    plan.advance_step()

                    # Check if requested objective is already satisfied by verified state
                    satisfied, sat_reason = state.is_objective_satisfied()
                    if satisfied:
                        state.objective_verified = True
                        logger.info(f"Task objective verified satisfied: {sat_reason}")

                        # If this is a single closure goal, conclude task immediately and cleanly
                        is_closure = any(
                            v in goal.lower() for v in ("close", "exit", "quit", "terminate", "shut down")
                        )
                        if is_closure:
                            if pre_cleanup_verifier:
                                try:
                                    state.objective_verified = bool(pre_cleanup_verifier())
                                except Exception:
                                    pass

                            cleanup_ok, _ = self.cleanup_resources(state)
                            if not cleanup_ok:
                                state.mark_failed(f"Session cleanup failed: {state.cleanup_error}")
                                self.ui.display_failure(state.error, state.elapsed_seconds)
                                return state

                            state.mark_completed(sat_reason)
                            if plan.current_step:
                                plan.current_step.mark_completed("Task concluded")
                            self.ui.display_completion(sat_reason, state.elapsed_seconds)
                            return state

        if not state.is_terminal:
            state.mark_failed(f"Reached maximum agent steps ({max_steps}) without completion.")
            self.ui.display_failure(state.error, state.elapsed_seconds)

        return state


_operator_agent_instance: Optional[OperatorAgent] = None


def get_operator_agent() -> OperatorAgent:
    """Retrieve shared OperatorAgent instance."""
    global _operator_agent_instance
    if _operator_agent_instance is None:
        _operator_agent_instance = OperatorAgent()
    return _operator_agent_instance

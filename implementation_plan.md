# Implementation Plan - Phase B: Generic Visual Recovery & Modal Handling

Extend Operator so that when an unexpected or blocking Windows dialog/modal appears during an autonomous task, Operator can detect the blocked state, capture a fresh observation, inspect UIAutomation controls, score recovery candidates generically (without hardcoding), execute a bounded safe recovery action, verify dismissal through multi-signal state verification, and resume the task or safely stop.

## Core Architectural Corrections & Safety Commitments

> [!IMPORTANT]
> 1. **No Inherently Safe Controls (IDYES is NOT automatically safe)**:
>    Standard control IDs (`IDYES`, `IDOK`, etc.) provide semantic roles, but roles ALONE never authorize an action. Candidate evaluation must weigh control semantics, active task intent, risk classification (e.g. destructive actions, privilege elevation, data deletion), confidence, and ambiguity. High-risk or ambiguous dialogs trigger `SAFE_STOP`.
> 2. **Multi-Signal State-Based Dismissal Verification**:
>    `win32gui.IsWindow(modal_hwnd) == False` is only one signal and NOT the sole success criterion. Dismissal verification checks:
>    - modal is destroyed OR no longer visible/blocking
>    - modal no longer captures/owns task focus
>    - expected target window is foreground-accessible again
>    - target is non-protected
>    - task execution can safely continue
> 3. **Bounded & Specialized Controller (Not a Second Planner)**:
>    `ModalRecoveryController` is bounded to a single structured recovery evaluation cycle per trigger, not an independent long-running planning loop.
> 4. **Explicit Outcome & No-Action States**:
>    Outcomes distinguish: `RECOVERED`, `NOT_BLOCKING`, `AMBIGUOUS`, `HIGH_RISK`, `NO_SAFE_ACTION`, `FAILED`, `SAFE_STOP`. A detected modal does not imply a click must occur; if no safe candidate exists, it halts safely without clicking.
> 5. **Unbroken CentralSafetyGate & Grounding Decoupling**:
>    Protected windows/processes (Antigravity IDE, PowerShell, Terminal, VS Code, runner) are strictly immune. All coordinate actions require valid, fresh `observation_id` verified through `GroundingRegistry`.
> 6. **Screenshot Lifecycle**:
>    Temporary recovery screenshots are deleted ONLY on verified `RECOVERED` success; retained on `FAILED`, `AMBIGUOUS`, `HIGH_RISK`, `NO_SAFE_ACTION`, or `SAFE_STOP`.
> 7. **Honest Text-Only Local Model**:
>    Zero synthetic multimodal perception. Structured UIAutomation and Win32 geometry drive control detection.

---

## Bounded Architecture & Data Flow

```
                      [ NORMAL TASK: OperatorAgent Loop ]
                                       │
                                       ▼ (Modal detected / focus blocked / target lock fails)
                         [ DETECT_BLOCKING_MODAL ]
                                       │
                ┌──────────────────────┴──────────────────────┐
                ▼ (Not blocking or Protected target)          ▼ (Blocking modal detected)
       [ NOT_BLOCKING: Continue Task ]                 [ CAPTURE_FRESH_OBSERVATION ]
                                                              │
                                                              ▼
                                                      [ DISCOVER_CONTROLS ]
                                                              │
                                                              ▼
                                                  [ BOUNDED CANDIDATE EVAL ]
                                                  (Semantics + Risk + Task Intent)
                                                              │
                ┌─────────────────────────────────────────────┼─────────────────────────────────────────────┐
                ▼ (High-Risk / Destructive)                   ▼ (Ambiguous / No Safe Control)               ▼ (Clear Safe Candidate)
         [ HIGH_RISK: SAFE_STOP ]                     [ NO_SAFE_ACTION: SAFE_STOP ]             [ PRE_ACTION_FRESHNESS_CHECK ]
         (Retain Evidence & Stop)                     (Retain Evidence & Stop)                                  │
                                                                                             ┌──────────────────┴──────────────────┐
                                                                                             ▼ (Stale / UI changed)                ▼ (Fresh & Valid)
                                                                               [ REOBSERVE (1 retry) ]                [ EXECUTE_RECOVERY_ACTION ]
                                                                                                                                   │
                                                                                                                      ┌────────────┴────────────┐
                                                                                                                      ▼ (Semantic UIA)          ▼ (Grounded Pixel Click)
                                                                                                               [ UIA InvokePattern ]     [ SafetyGate -> click_at ]
                                                                                                                      │                         │
                                                                                                                      └────────────┬────────────┘
                                                                                                                                   │
                                                                                                                                   ▼
                                                                                                                     [ CAPTURE_POST_OBSERVATION ]
                                                                                                                                   │
                                                                                                                                   ▼
                                                                                                                      [ MULTI-SIGNAL VERIFY ]
                                                                                                                      (Destruction, Visibility,
                                                                                                                       Focus, Target Restored)
                                                                                                                                   │
                                                                                              ┌────────────────────────────────────┴────────────────────────────────────┐
                                                                                              ▼ (All State Signals Pass)                                                ▼ (Signals Fail)
                                                                                [ RECOVERED: Cleanup Screenshots ]                                        [ FAILED: Retain Evidence & Stop ]
                                                                                              │                                                                         │
                                                                                              ▼                                                                         ▼
                                                                                   [ RESUME OperatorAgent ]                                                  [ SAFE_STOP OperatorAgent ]
```

---

## Proposed Changes

### 1. Perception & Dialog Discovery Layer
- **[perception/uiautomation.py](file:///c:/Users/jhonp/OneDrive/Desktop/Operator/perception/uiautomation.py)**:
  - Enhance `detect_modal_dialog()` to extract Win32 dialog control IDs (`IDOK=1`, `IDCANCEL=2`, `IDABORT=3`, `IDRETRY=4`, `IDIGNORE=5`, `IDYES=6`, `IDNO=7`, `IDCLOSE=8`), `BS_DEFPUSHBUTTON` style, owner/parent HWND (`GW_OWNER`), and static dialog text/messages.
- **[perception/controller.py](file:///c:/Users/jhonp/OneDrive/Desktop/Operator/perception/controller.py)**:
  - Store enriched dialog metadata in `ScreenObservation.modal_dialog`.
  - Register discovered modal controls in `GroundingRegistry` with `GroundingSource.UI_AUTOMATION_MODAL`.

### 2. Bounded Modal Recovery Engine
- **[NEW] [agent/modal_recovery.py](file:///c:/Users/jhonp/OneDrive/Desktop/Operator/agent/modal_recovery.py)**:
  - `RecoveryOutcome` (Enum): `RECOVERED`, `NOT_BLOCKING`, `AMBIGUOUS`, `HIGH_RISK`, `NO_SAFE_ACTION`, `FAILED`, `SAFE_STOP`.
  - `ControlRole` (Enum): `AFFIRMATIVE_PROCEED`, `DISMISS_ACKNOWLEDGE`, `CANCEL_ABORT`, `RETRY`, `NEGATIVE_REJECT`, `CLOSE`, `UNKNOWN`.
  - `RiskLevel` (Enum): `SAFE_ACKNOWLEDGE`, `TASK_ALIGNED_CONFIRMATION`, `POTENTIALLY_DESTRUCTIVE`, `HIGH_RISK_UNKNOWN`.
  - `RecoveryCandidate`: Evaluated control with role, score, confidence, risk level, enabled/default status.
  - `RecoveryCandidateEvaluator`:
    - Evaluates role from control IDs and styles (not hardcoded strings).
    - Checks risk classification and task alignment. If action is destructive or high-risk outside explicit task scope -> flags `HIGH_RISK`.
    - If candidates have conflicting scores or no candidate exceeds safety threshold -> flags `NO_SAFE_ACTION` or `AMBIGUOUS`.
  - `ModalRecoveryController`:
    - Bounded execution (single attempt per occurrence, bounded retry).
    - Checks target against `CentralSafetyGate`.
    - Freshness check (< 2.0s).
    - Dispatch: UIAutomation primary, grounded `click_at` fallback.
    - Screenshot lifecycle: cleanup on `RECOVERED`; preserve on `FAILED`, `SAFE_STOP`, `HIGH_RISK`, `AMBIGUOUS`, `NO_SAFE_ACTION`.
    - Structured `ModalRecoveryAudit` recording.

### 3. State-Based Multi-Signal Verification
- **[verification/verifier.py](file:///c:/Users/jhonp/OneDrive/Desktop/Operator/verification/verifier.py)**:
  - Add `verify_modal_dismissed()` evaluating:
    1. Modal destruction (`not win32gui.IsWindow(modal_hwnd)`).
    2. Modal visibility & blocking removal (`not win32gui.IsWindowVisible(modal_hwnd)` or `IsWindowEnabled(owner_hwnd)` restored).
    3. Focus release: modal no longer holds foreground.
    4. Target accessibility: expected application window or non-protected window is accessible.
    5. Host protection confirmation: active window is not protected IDE/runner.

### 4. Agent State & Core Integration
- **[agent/state.py](file:///c:/Users/jhonp/OneDrive/Desktop/Operator/agent/state.py)**:
  - Add `modal_recovery_audits` to `AgentState`.
- **[agent/recovery.py](file:///c:/Users/jhonp/OneDrive/Desktop/Operator/agent/recovery.py)**:
  - Add `ErrorCategory.MODAL_BLOCKED` and `ErrorCategory.MODAL_RECOVERY_FAILED`.
- **[agent/core.py](file:///c:/Users/jhonp/OneDrive/Desktop/Operator/agent/core.py)**:
  - Hook `ModalRecoveryController` before tool execution and on target lock failure.
  - Resume task on `RECOVERED` or `NOT_BLOCKING`; safe stop on `SAFE_STOP`, `HIGH_RISK`, `NO_SAFE_ACTION`.

---

## Targeted Test Suite (`tests/test_phase_b_modal_recovery.py`)
1. **Correction 1**: IDYES / affirmative control is NOT automatically authorized; high-impact/destructive prompts without task alignment halt safely.
2. **Correction 2**: High-risk modal produces `SAFE_STOP` and preserves screenshots/evidence.
3. **Correction 3**: Modal remaining alive but no longer blocking (e.g. hidden or focus returned to target window) verifies successfully via multi-signal state checks.
4. **Correction 4**: `NO_SAFE_ACTION` causes safe stop without dispatching mouse clicks.
5. **Freshness & Grounding**: Coordinates without observation_id or stale (> 2.0s) are rejected.
6. **CentralSafetyGate Authority**: Modals on protected processes are strictly immune.
7. **Screenshot Lifecycle**: Retained on failure/stop; deleted on verified recovery.
8. **Non-live Regression Suite**: Full 164 existing tests pass with 0 regressions.

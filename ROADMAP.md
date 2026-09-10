# Operator Architecture Roadmap: Generic Visual Situational Awareness & Pixel-Level Mouse Interaction

## Executive Summary
This document formalizes the architectural roadmap for Operator's next major evolution: transition from a purely semantic tool-calling agent to a multimodal computer-use agent with **generic visual situational awareness, pixel-level mouse interaction, and perception fusion (UIAutomation + Vision)**.

---

## 1. Architecture Overview: Perception & Action Loop

```
                     DESKTOP ENVIRONMENT
                              │
               ┌──────────────┴──────────────┐
               ▼                             ▼
       UIAutomation Engine           Screen Perception
     (Structured OS Controls)      (MSS / Diagnostic Shots)
               │                             │
               └──────────────┬──────────────┘
                              ▼
                      Perception Fusion
                 (Element Bounding Boxes,
               Labels, Types, Confidence)
                              │
                              ▼
                     Agent Observation ID
               (Timestamped Perception Cache)
                              │
                              ▼
                     LLM Reasoning Engine
              (Intent & Safe Action Determination)
                              │
                              ▼
                     Central Safety Gate
              (Screen Bounds, Target Verification,
               Staleness Check, Protection Invariants)
                              │
                              ▼
                     Pixel Mouse Controller
                (move_mouse, click_at, drag)
                              │
                              ▼
                    Post-Action Perception
                    (Re-observe & Verify)
```

---

## 2. Phased Implementation Strategy

### Phase A: Perceptual Foundation & Coordinate Grounding
1. **First-Class Observation Perception**:
   - `fresh_screen_observation()` produces a diagnostic screen capture with:
     - Unique `observation_id` (e.g. `obs_20260910_134500_001`).
     - Millisecond-accurate timestamp.
     - Screen geometry and DPI scaling metrics.
     - Ephemeral screenshot storage with lifecycle management (auto-delete on success, preserve on failure).
2. **Coordinate-Grounded Pixel Mouse Tools**:
   - `move_mouse(x: int, y: int)`
   - `click_at(x: int, y: int, button: str = "left")`
   - `double_click_at(x: int, y: int)`
   - `right_click_at(x: int, y: int)`
   - `drag(start_x: int, start_y: int, end_x: int, end_y: int)`
   - Every coordinate-based tool call MUST reference an active `observation_id`. Coordinates not grounded in a recent observation are rejected.
3. **Coordinate & Staleness Safety**:
   - Out-of-bounds coordinate rejection.
   - Observation age timeout (rejecting observations older than configurable threshold, e.g. 5.0s).
   - Validation that target window/dialog remains in the foreground before click dispatch.

### Phase B: Generic Modal Detection & Visual Recovery Loop
1. **Generic Modal / Dialog Detection**:
   - Model-independent structural representation:
     ```json
     {
       "type": "modal_dialog",
       "title": "Confirm Folder Replace",
       "text": "This folder already contains a folder named 'OperatorTestFolder'.",
       "controls": [
         {"label": "Yes", "bounds": [860, 600, 950, 635], "confidence": 0.96},
         {"label": "No", "bounds": [960, 600, 1050, 635], "confidence": 0.97}
       ]
     }
     ```
   - Zero hardcoding: No application-specific or dialog-specific special cases.
2. **Context-Driven Reasoning**:
   - Intent grounding: The agent selects actions ("Yes", "No", "Cancel") based strictly on the user's explicit objective, never guessing.
3. **Visual Recovery Loop**:
   - Action -> Verify -> If unexpected state observed -> Capture fresh observation -> Analyze dialog -> Determine safe action -> Verify post-state.

### Phase C: Hybrid Perception Fusion (UIAutomation + Vision)
1. **Preferred Perception Hierarchy**:
   - Priority 1: Windows UIAutomation (deterministic bounding boxes and control patterns).
   - Priority 2: Window API metadata.
   - Priority 3: Visual perception / screenshot analysis when structured data is absent or incomplete.
   - Priority 4: Re-observation on ambiguity.
2. **Visual Action Audit Trail**:
   - Logging full lineage for every pixel interaction: `observation_id`, `bounding_box`, `derived_click_point`, `confidence`, `safety_decision`, `verification_result`.

---

## 3. Strict Safety Invariants
1. **Antigravity IDE, Benchmark Runner, Shells (PowerShell, CMD, Terminal) Protection**:
   - Pixel interactions targeting regions belonging to protected runner/host windows are strictly intercepted and blocked by the Central Safety Gate.
2. **Pre-Existing Environment Isolation**:
   - Pixel interactions cannot close or destroy pre-existing windows.
3. **No-Hallucination Rule**:
   - Coordinates cannot be guessed or fabricated; click coordinates must fall strictly within verified element bounding boxes from a valid, non-stale observation.

---

## 4. Test Strategy
- Regression tests for coordinate validation, bounding box derivation, staleness rejection, visual recovery modal fixtures, diagnostic screenshot lifecycle, and protection against clicking protected host environments.

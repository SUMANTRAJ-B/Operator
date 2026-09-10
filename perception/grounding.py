"""Grounding evidence data structures and registry for perception-grounded interaction.

Provides a clean grounding interface between Perception, Safety, and Actions,
ensuring SafetyPolicy does not directly depend on or instantiate PerceptionController.
"""

from dataclasses import dataclass, field
import time
from typing import Any, Dict, List, Optional, Tuple

from app.config import get_settings
from app.logging import get_logger
from providers.vision.base import BoundingBox

logger = get_logger("perception.grounding")


class GroundingSource:
    """Standard perception source identifiers."""

    UI_AUTOMATION = "uiautomation"
    UI_AUTOMATION_MODAL = "uiautomation_modal"
    WINDOW_GEOMETRY = "window_geometry"
    VISION = "vision"
    UNGROUNDED = "ungrounded"


@dataclass
class GroundingEvidence:
    """Represents verified perception evidence grounding an interaction target."""

    observation_id: str
    observation_timestamp: float
    source: str = GroundingSource.UI_AUTOMATION
    target_query: str = ""
    bounding_box: Optional[BoundingBox] = None
    click_point: Optional[Tuple[int, int]] = None
    control_type: str = ""
    control_metadata: Dict[str, Any] = field(default_factory=dict)
    screen_size: Tuple[int, int] = (0, 0)
    max_age_seconds: float = 5.0
    target_hwnd: Optional[int] = None
    target_window_title: str = ""

    def is_stale(
        self,
        current_time: Optional[float] = None,
        max_age_seconds: Optional[float] = None,
    ) -> bool:
        """Return True if evidence timestamp exceeds freshness threshold."""
        now = current_time if current_time is not None else time.time()
        threshold = max_age_seconds if max_age_seconds is not None else self.max_age_seconds
        return (now - self.observation_timestamp) > threshold

    def contains_point(self, x: int, y: int) -> bool:
        """Check if (x, y) falls inside the verified bounding box or matches click point."""
        if self.bounding_box:
            return self.bounding_box.contains(x, y)
        if self.click_point:
            return (x, y) == self.click_point
        return False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "observation_timestamp": self.observation_timestamp,
            "source": self.source,
            "target_query": self.target_query,
            "bounding_box": self.bounding_box.to_tuple() if self.bounding_box else None,
            "click_point": self.click_point,
            "control_type": self.control_type,
            "control_metadata": self.control_metadata,
            "screen_size": self.screen_size,
            "max_age_seconds": self.max_age_seconds,
            "target_hwnd": self.target_hwnd,
            "target_window_title": self.target_window_title,
        }


class GroundingRegistry:
    """Registry maintaining active observations and grounding evidence.

    Allows SafetyGate to authoritatively validate grounding evidence without
    coupling directly to PerceptionController.
    """

    def __init__(self):
        self._evidence_by_obs_id: Dict[str, List[GroundingEvidence]] = {}
        self._active_observation_id: Optional[str] = None
        self._observation_timestamps: Dict[str, float] = {}
        self._observation_screen_sizes: Dict[str, Tuple[int, int]] = {}

    def register_observation(
        self,
        observation_id: str,
        timestamp: float,
        screen_size: Tuple[int, int],
        evidences: Optional[List[GroundingEvidence]] = None,
    ) -> None:
        """Register a new screen observation and its grounded elements."""
        self._active_observation_id = observation_id
        self._observation_timestamps[observation_id] = timestamp
        self._observation_screen_sizes[observation_id] = screen_size
        self._evidence_by_obs_id[observation_id] = list(evidences or [])
        logger.debug(
            f"Registered observation {observation_id} in GroundingRegistry with {len(self._evidence_by_obs_id[observation_id])} evidence records."
        )

    def add_evidence(self, evidence: GroundingEvidence) -> None:
        """Add grounding evidence for an observation."""
        obs_id = evidence.observation_id
        if obs_id not in self._evidence_by_obs_id:
            self._evidence_by_obs_id[obs_id] = []
            self._observation_timestamps[obs_id] = evidence.observation_timestamp
            if evidence.screen_size != (0, 0):
                self._observation_screen_sizes[obs_id] = evidence.screen_size
        self._evidence_by_obs_id[obs_id].append(evidence)

    def get_evidence_for_observation(self, observation_id: str) -> List[GroundingEvidence]:
        """Retrieve all grounding evidence registered for an observation."""
        return self._evidence_by_obs_id.get(observation_id, [])

    def get_active_observation_id(self) -> Optional[str]:
        """Return ID of most recent active observation."""
        return self._active_observation_id

    def is_observation_registered(self, observation_id: str) -> bool:
        """Check whether observation_id has been recorded."""
        return observation_id in self._observation_timestamps

    def get_observation_age(self, observation_id: str, current_time: Optional[float] = None) -> Optional[float]:
        """Return age in seconds of observation, or None if not registered."""
        ts = self._observation_timestamps.get(observation_id)
        if ts is None:
            return None
        now = current_time if current_time is not None else time.time()
        return max(0.0, now - ts)

    def validate_coordinate_grounding(
        self,
        observation_id: str,
        x: int,
        y: int,
        max_age_seconds: Optional[float] = None,
        current_time: Optional[float] = None,
    ) -> Tuple[bool, str, Optional[GroundingEvidence]]:
        """Authoritatively validate that (x, y) is grounded in a valid, fresh observation.

        Returns:
            (is_grounded, failure_reason_or_success_message, matching_evidence)
        """
        settings = get_settings()
        configured_max_age = (
            max_age_seconds
            if max_age_seconds is not None
            else getattr(settings, "observation_max_age_seconds", 5.0)
        )

        # 1. Observation Existence Check
        if not observation_id or not self.is_observation_registered(observation_id):
            return (
                False,
                f"Observation ID '{observation_id}' is not registered in grounding registry.",
                None,
            )

        # 2. Freshness Check
        age = self.get_observation_age(observation_id, current_time=current_time)
        if age is not None and age > configured_max_age:
            return (
                False,
                f"Observation '{observation_id}' is stale ({age:.2f}s > threshold {configured_max_age:.2f}s).",
                None,
            )

        # 3. Grounding Evidence Matching
        evidences = self._evidence_by_obs_id.get(observation_id, [])
        for ev in evidences:
            if ev.contains_point(x, y):
                return (
                    True,
                    f"Point ({x}, {y}) grounded via {ev.source} in target '{ev.target_query or ev.control_type}'.",
                    ev,
                )

        # If an observation was registered, check if point is within observation screen bounds
        scr_size = self._observation_screen_sizes.get(observation_id)
        if scr_size:
            w, h = scr_size
            if not (0 <= x < w and 0 <= y < h):
                return (
                    False,
                    f"Point ({x}, {y}) is outside observation screen dimensions {w}x{h}.",
                    None,
                )

        # If specific element evidences exist but point didn't match any:
        if evidences:
            return (
                False,
                f"Point ({x}, {y}) does not fall within any verified element bounding box for observation '{observation_id}'.",
                None,
            )

        # Fallback if observation had 0 element evidences (general screen observation)
        return (
            True,
            f"Point ({x}, {y}) validated against desktop observation '{observation_id}'.",
            None,
        )

    def clear(self) -> None:
        """Reset grounding registry state (for tests)."""
        self._evidence_by_obs_id.clear()
        self._active_observation_id = None
        self._observation_timestamps.clear()
        self._observation_screen_sizes.clear()


_registry_instance: Optional[GroundingRegistry] = None


def get_grounding_registry() -> GroundingRegistry:
    """Retrieve shared GroundingRegistry singleton."""
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = GroundingRegistry()
    return _registry_instance

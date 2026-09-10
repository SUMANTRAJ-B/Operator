"""Windows UIAutomation structured control perception provider."""

from typing import Any, Dict, List, Optional, Tuple
import win32gui
import win32process

from app.logging import get_logger
from providers.vision.base import BoundingBox, DetectedUIElement

logger = get_logger("perception.uiautomation")


class UIAutomationProvider:
    """Extracts structured controls, dialogs, and button bounding boxes via UIAutomation and Win32."""

    def __init__(self):
        self._uia_available = False
        try:
            import comtypes.client
            self._uia_available = True
        except Exception:
            self._uia_available = False

    def is_available(self) -> bool:
        return self._uia_available

    def find_controls_in_window(
        self,
        hwnd: int,
        control_types: Optional[List[str]] = None,
    ) -> List[DetectedUIElement]:
        """Find interactive controls (buttons, edits, dialogs) inside target window."""
        if not hwnd or not win32gui.IsWindow(hwnd):
            return []

        elements: List[DetectedUIElement] = []

        # Control Type Map for common UIAutomation controls
        type_id_map = {
            50000: "button",
            50002: "checkbox",
            50003: "combobox",
            50004: "edit",
            50005: "hyperlink",
            50007: "listitem",
            50011: "menuitem",
            50013: "radiobutton",
            50019: "tabitem",
            50020: "text",
        }

        # 1. UIAutomation via comtypes
        if self._uia_available:
            try:
                import comtypes.client
                from comtypes import CoInitialize, CoUninitialize
                CoInitialize()
                try:
                    mod = comtypes.client.GetModule("UIAutomationCore.dll")
                    uia = comtypes.client.CreateObject(mod.CUIAutomation, interface=mod.IUIAutomation)
                    root = uia.ElementFromHandle(hwnd)
                    if root:
                        # Build OR condition across interactive control types or query true condition
                        true_cond = uia.CreateTrueCondition()
                        found_elements = root.FindAll(mod.TreeScope_Descendants, true_cond)
                        if found_elements:
                            count = min(found_elements.Length, 150)
                            for i in range(count):
                                try:
                                    elem = found_elements.GetElement(i)
                                    try:
                                        c_type_id = getattr(elem, "CurrentControlType", 0)
                                    except Exception:
                                        c_type_id = 0
                                    elem_type = type_id_map.get(c_type_id, "control")

                                    # Filter for interactive or named controls
                                    name = getattr(elem, "CurrentName", "") or ""
                                    auto_id = getattr(elem, "CurrentAutomationId", "") or ""
                                    is_enabled = getattr(elem, "CurrentIsEnabled", True)
                                    is_offscreen = getattr(elem, "CurrentIsOffscreen", False)

                                    if is_offscreen:
                                        continue

                                    rect = getattr(elem, "CurrentBoundingRectangle", None)
                                    if rect:
                                        left = getattr(rect, "left", None)
                                        top = getattr(rect, "top", None)
                                        right = getattr(rect, "right", None)
                                        bottom = getattr(rect, "bottom", None)
                                        if left is not None and right is not None and (right > left) and (bottom > top):
                                            label = name.strip() or auto_id.strip()
                                            if label:
                                                bbox = BoundingBox(left=left, top=top, right=right, bottom=bottom)
                                                elements.append(
                                                    DetectedUIElement(
                                                        label=label,
                                                        element_type=elem_type,
                                                        bounds=bbox,
                                                        confidence=1.0,
                                                        attributes={
                                                            "source": "uiautomation",
                                                            "automation_id": auto_id,
                                                            "control_type_id": c_type_id,
                                                            "is_enabled": bool(is_enabled),
                                                        },
                                                    )
                                                )
                                except Exception:
                                    continue
                finally:
                    CoUninitialize()
            except Exception as e:
                logger.debug(f"UIAutomation extraction error: {e}")

        # 2. Fallback: Win32 child window enumeration
        if not elements:
            try:
                def enum_child(child_hwnd, _):
                    if win32gui.IsWindowVisible(child_hwnd):
                        cls = win32gui.GetClassName(child_hwnd)
                        txt = win32gui.GetWindowText(child_hwnd)
                        rect = win32gui.GetWindowRect(child_hwnd)
                        if rect[2] > rect[0] and rect[3] > rect[1]:
                            elem_type = "button" if "button" in cls.lower() else ("edit" if "edit" in cls.lower() else "control")
                            bbox = BoundingBox(left=rect[0], top=rect[1], right=rect[2], bottom=rect[3])
                            elements.append(
                                DetectedUIElement(
                                    label=txt.strip() or cls,
                                    element_type=elem_type,
                                    bounds=bbox,
                                    confidence=0.9,
                                    attributes={"class_name": cls, "hwnd": child_hwnd, "source": "win32"},
                                )
                            )

                win32gui.EnumChildWindows(hwnd, enum_child, None)
            except Exception as e:
                logger.debug(f"Win32 EnumChildWindows fallback error: {e}")

        return elements

    def find_control_by_query(
        self,
        query: str,
        hwnd: Optional[int] = None,
    ) -> Optional[DetectedUIElement]:
        """Find the single best matching interactive control for a query string."""
        target_hwnd = hwnd or win32gui.GetForegroundWindow()
        if not target_hwnd or not win32gui.IsWindow(target_hwnd):
            return None

        controls = self.find_controls_in_window(target_hwnd)
        query_l = query.strip().lower()

        # 1. Exact match
        for c in controls:
            if c.label.strip().lower() == query_l:
                return c
            auto_id = c.attributes.get("automation_id", "").strip().lower()
            if auto_id and auto_id == query_l:
                return c

        # 2. Substring match
        for c in controls:
            lbl_l = c.label.strip().lower()
            if query_l in lbl_l or lbl_l in query_l:
                return c
            auto_id = c.attributes.get("automation_id", "").strip().lower()
            if auto_id and (query_l in auto_id or auto_id in query_l):
                return c

        return None

    def detect_modal_dialog(self, parent_hwnd: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Detect if an active modal dialog (e.g. confirmation, save, replace) is present."""
        top_hwnd = win32gui.GetForegroundWindow()
        if not top_hwnd or not win32gui.IsWindow(top_hwnd):
            return None

        # Owner window check
        owner_hwnd = None
        owner_title = ""
        try:
            owner_hwnd = win32gui.GetWindow(top_hwnd, win32con.GW_OWNER)
            if owner_hwnd and win32gui.IsWindow(owner_hwnd):
                owner_title = win32gui.GetWindowText(owner_hwnd)
        except Exception:
            owner_hwnd = None

        title = win32gui.GetWindowText(top_hwnd)
        cls_name = win32gui.GetClassName(top_hwnd)

        # Check window style for modal / popup characteristics
        import win32con
        style = win32gui.GetWindowLong(top_hwnd, win32con.GW_STYLE)
        ex_style = win32gui.GetWindowLong(top_hwnd, win32con.GW_EXSTYLE)
        has_owner = bool(owner_hwnd and win32gui.IsWindow(owner_hwnd))
        has_modal_frame = bool(ex_style & win32con.WS_EX_DLGMODALFRAME)
        is_dialog = cls_name == "#32770" or has_modal_frame or (has_owner and bool(style & win32con.WS_POPUP))

        # Common dialog class names: #32770 (standard Windows dialog)
        if cls_name == "#32770" or is_dialog or (has_owner and ("confirm" in title.lower() or "dialog" in cls_name.lower())):
            controls = self.find_controls_in_window(top_hwnd)
            control_dicts = []
            message_texts = []
            for c in controls:
                ctrl_id = c.attributes.get("control_id", 0)
                is_def = c.attributes.get("is_default", False)
                c_hwnd = c.attributes.get("hwnd")
                if not ctrl_id and c_hwnd:
                    try:
                        ctrl_id = win32gui.GetDlgCtrlID(c_hwnd)
                    except Exception:
                        pass
                if not is_def and c_hwnd and c.element_type == "button":
                    try:
                        st = win32gui.GetWindowLong(c_hwnd, win32con.GWL_STYLE)
                        is_def = bool(st & win32con.BS_DEFPUSHBUTTON)
                    except Exception:
                        pass

                control_dicts.append(
                    {
                        "label": c.label,
                        "type": c.element_type,
                        "bounds": c.bounds.to_tuple(),
                        "confidence": c.confidence,
                        "control_id": ctrl_id,
                        "is_default": is_def,
                        "automation_id": c.attributes.get("automation_id", ""),
                        "hwnd": c_hwnd,
                        "is_enabled": c.attributes.get("is_enabled", True),
                    }
                )
                if c.element_type in ("text", "control") and c.label and c.label != cls_name:
                    message_texts.append(c.label)

            full_msg = " | ".join(message_texts) if message_texts else ""

            return {
                "type": "modal_dialog",
                "hwnd": top_hwnd,
                "title": title,
                "class_name": cls_name,
                "owner_hwnd": owner_hwnd,
                "owner_title": owner_title,
                "is_modal": is_dialog,
                "message_text": full_msg,
                "controls": control_dicts,
            }

        return None

"""Pure helpers and lightweight Tk hooks for the desktop shell.

The functions in this module deliberately avoid creating a Tk root.  That
keeps contrast and widget-contract tests runnable in headless CI while the
launcher applies the optional focus behavior only when a GUI is requested.
"""

from __future__ import annotations

from collections.abc import Iterator
import unicodedata
from typing import Any


FOCUSABLE_CLASS_NAMES = frozenset(
    {
        "Button",
        "Checkbutton",
        "CTkButton",
        "CTkCheckBox",
        "CTkComboBox",
        "CTkEntry",
        "CTkOptionMenu",
        "CTkRadioButton",
        "CTkScale",
        "CTkSegmentedButton",
        "CTkSlider",
        "CTkSwitch",
        "CTkTextbox",
        "Entry",
        "Radiobutton",
        "Scale",
        "Spinbox",
        "Text",
    }
)

GENERIC_CONTROL_TEXT = frozenset({"add", "clear", "remove", "browse", "inspect", "profile"})


def _widget_text(widget: Any) -> str:
    """Read a Tk text option without requiring a concrete Tk widget in tests."""

    try:
        value = widget.cget("text")
    except Exception:
        value = ""
    return str(value or "").strip()


def _clean_accessible_text(value: str) -> str:
    """Remove decorative symbol glyphs that do not convey a control name."""

    cleaned = "".join(
        character
        for character in str(value)
        if unicodedata.category(character) not in {"So", "Sk"}
    )
    return " ".join(cleaned.split())


def _context_text(widget: Any) -> str:
    """Find nearby visible text for generic actions such as Remove or Clear."""

    current = widget
    for _ in range(3):
        try:
            siblings = current.winfo_children()
        except Exception:
            siblings = []
        for sibling in siblings:
            if sibling is widget:
                continue
            text = _clean_accessible_text(_widget_text(sibling))
            if text and text.lower() not in GENERIC_CONTROL_TEXT:
                return text
        current = getattr(current, "master", None)
        if current is None:
            break
    return ""


def _hex_rgb(value: str) -> tuple[int, int, int]:
    candidate = value.strip().lstrip("#")
    if len(candidate) == 3:
        candidate = "".join(char * 2 for char in candidate)
    if len(candidate) != 6:
        raise ValueError(f"Expected a six-digit RGB color, got {value!r}")
    try:
        return tuple(int(candidate[index:index + 2], 16) for index in (0, 2, 4))
    except ValueError as exc:
        raise ValueError(f"Invalid RGB color: {value!r}") from exc


def _relative_luminance(value: str) -> float:
    channels = []
    for channel in _hex_rgb(value):
        normalized = channel / 255
        channels.append(
            normalized / 12.92
            if normalized <= 0.03928
            else ((normalized + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_ratio(foreground: str, background: str) -> float:
    """Return the WCAG 2 contrast ratio for two opaque RGB colors."""

    first = _relative_luminance(foreground)
    second = _relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def validate_theme_contrast(colors: dict[str, str], minimum: float = 4.5) -> dict[str, float]:
    """Measure the normal-text combinations used by both shipped themes."""

    pairs = {
        "primary_on_surface": ("text_primary", "bg_secondary"),
        "secondary_on_surface": ("text_secondary", "bg_secondary"),
        "muted_on_surface": ("text_muted", "bg_secondary"),
        "primary_on_dark": ("text_primary", "bg_dark"),
        "secondary_on_dark": ("text_secondary", "bg_dark"),
        "muted_on_dark": ("text_muted", "bg_dark"),
    }
    measured = {
        name: contrast_ratio(colors[foreground], colors[background])
        for name, (foreground, background) in pairs.items()
    }
    failures = {name: ratio for name, ratio in measured.items() if ratio < minimum}
    if failures:
        details = ", ".join(f"{name}={ratio:.2f}" for name, ratio in failures.items())
        raise ValueError(f"Theme contrast below {minimum:.1f}: {details}")
    return measured


def iter_widgets(root: Any) -> Iterator[Any]:
    """Yield a widget tree in Tk creation order without requiring Tk imports."""

    yield root
    try:
        children = root.winfo_children()
    except Exception:
        return
    for child in children:
        yield from iter_widgets(child)


def is_focusable(widget: Any, visible_only: bool = True) -> bool:
    """Identify controls that should participate in the shell's Tab order."""

    if getattr(widget, "_csv_power_skip_focus", False):
        return False
    if widget.__class__.__name__ not in FOCUSABLE_CLASS_NAMES:
        return False
    try:
        state = str(widget.cget("state"))
    except Exception:
        state = "normal"
    if state == "disabled":
        return False
    if visible_only:
        for method_name in ("winfo_viewable", "winfo_ismapped"):
            method = getattr(widget, method_name, None)
            if method is None:
                continue
            try:
                if not bool(method()):
                    return False
            except Exception:
                continue
    return True


def collect_focusables(root: Any, visible_only: bool = True) -> list[Any]:
    """Return enabled, visible controls in deterministic visual/creation order."""

    return [
        widget for widget in iter_widgets(root)
        if is_focusable(widget, visible_only=visible_only)
    ]


def set_accessible_name(widget: Any, name: str, description: str | None = None) -> Any:
    """Attach an inspectable accessible name/description to a Tk widget."""

    widget._csv_power_accessible_name = _clean_accessible_text(name)
    widget._csv_power_accessible_description = _clean_accessible_text(description or "")
    return widget


def accessible_name(widget: Any) -> str:
    """Read the explicit name or derive a useful, contextual fallback."""

    explicit = getattr(widget, "_csv_power_accessible_name", "")
    if explicit:
        return _clean_accessible_text(str(explicit))
    text = _clean_accessible_text(_widget_text(widget))
    if text:
        if text.lower() in GENERIC_CONTROL_TEXT:
            context = _context_text(widget)
            if context:
                return f"{text}: {context}"
        return text
    placeholder = ""
    try:
        placeholder = str(widget.cget("placeholder_text") or "").strip()
    except Exception:
        pass
    if placeholder:
        return _clean_accessible_text(placeholder)
    return _clean_accessible_text(widget.__class__.__name__)


def accessible_description(widget: Any) -> str:
    """Read the status/assistive-technology description attached to a control."""

    return _clean_accessible_text(
        str(getattr(widget, "_csv_power_accessible_description", "") or "")
    )


def configure_focus_contract(root: Any, color: str) -> list[Any]:
    """Prepare visible controls and attach stable names for keyboard/AT users."""

    focusables = collect_focusables(root)
    counts: dict[str, int] = {}
    for index, widget in enumerate(focusables, start=1):
        prepare_focus_widget(widget)
        name = accessible_name(widget) or widget.__class__.__name__
        counts[name] = counts.get(name, 0) + 1
        if counts[name] > 1:
            name = f"{name} ({counts[name]})"
        description = accessible_description(widget)
        if not description:
            description = f"Keyboard control {index} of {len(focusables)}"
        set_accessible_name(widget, name, description)
        set_focus_ring(widget, False, color)
    return focusables


def focus_contract_snapshot(root: Any, color: str) -> list[dict[str, str]]:
    """Return a stable, serializable focus contract for smoke tests and support."""

    return [
        {
            "name": accessible_name(widget),
            "description": accessible_description(widget),
        }
        for widget in configure_focus_contract(root, color)
    ]


def widget_contains(parent: Any, child: Any) -> bool:
    """Return whether a focused Tk child belongs to a candidate control."""

    parent_path = str(getattr(parent, "_w", parent))
    child_path = str(getattr(child, "_w", child))
    return child_path == parent_path or child_path.startswith(parent_path + ".")


def prepare_focus_widget(widget: Any) -> None:
    """Make customtkinter's internal canvas/entry eligible for Tk focus."""

    targets = [
        widget,
        getattr(widget, "_canvas", None),
        getattr(widget, "_entry", None),
        getattr(widget, "_text_label", None),
        getattr(widget, "_image_label", None),
    ]
    for target in targets:
        if target is None:
            continue
        try:
            target.configure(takefocus=1)
        except Exception:
            continue


def set_focus_ring(widget: Any, focused: bool, color: str) -> None:
    """Apply a visible, theme-colored focus ring where the widget supports it."""

    if not hasattr(widget, "_csv_power_focus_style"):
        style: dict[str, Any] = {}
        for option in ("border_width", "border_color", "highlightthickness", "highlightbackground", "highlightcolor"):
            try:
                style[option] = widget.cget(option)
            except Exception:
                continue
        widget._csv_power_focus_style = style

    style = widget._csv_power_focus_style
    if "border_width" in style:
        try:
            widget.configure(
                border_width=2 if focused else style["border_width"],
                border_color=color if focused else style.get("border_color"),
            )
            return
        except Exception:
            pass
    if "highlightthickness" in style:
        try:
            widget.configure(
                highlightthickness=2 if focused else style["highlightthickness"],
                highlightbackground=color if focused else style.get("highlightbackground"),
                highlightcolor=color if focused else style.get("highlightcolor"),
            )
        except Exception:
            pass

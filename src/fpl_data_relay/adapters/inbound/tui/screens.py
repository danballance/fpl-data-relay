"""Reusable modal screens for guarded TUI operations."""

from pydantic import BaseModel, ConfigDict, Field
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static


class FormField(BaseModel):
    """One explicitly described textual input."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1)
    placeholder: str
    password: bool


class FormRequest(BaseModel):
    """Definition of one operation parameter form."""

    model_config = ConfigDict(frozen=True)

    target: str = Field(min_length=1)
    title: str = Field(min_length=1)
    fields: tuple[FormField, ...]


class FormValue(BaseModel):
    """One submitted form value."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    value: str


class FormSubmission(BaseModel):
    """Validated collection returned by an operation form."""

    model_config = ConfigDict(frozen=True)

    target: str = Field(min_length=1)
    values: tuple[FormValue, ...]

    def require(self, *, name: str) -> str:
        """Return a named value or fail clearly for a malformed form."""
        matches = [item.value for item in self.values if item.name == name]
        if len(matches) != 1:
            raise ValueError(f"Form value {name!r} was not supplied exactly once.")
        return matches[0]


class ConfirmationScreen(ModalScreen[bool]):
    """Confirm escalation for one resistant managed process."""

    DEFAULT_CSS = """
    ConfirmationScreen { align: center middle; }
    ConfirmationScreen > Vertical {
        width: 72;
        height: auto;
        padding: 1 2;
        border: heavy $warning;
        background: $surface;
    }
    ConfirmationScreen Horizontal {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }
    """

    def __init__(self, *, title: str, impact: str) -> None:
        super().__init__()
        self._title = title
        self._impact = impact

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._title, classes="modal-title")
            yield Static(self._impact, markup=False)
            with Horizontal():
                yield Button("Cancel", id="confirm-cancel")
                yield Button("Run", id="confirm-submit", variant="warning")

    @on(Button.Pressed, "#confirm-cancel")
    def cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#confirm-submit")
    def submit(self) -> None:
        self.dismiss(True)


class ArgumentsScreen(ModalScreen[FormSubmission | None]):
    """Collect explicit command inputs before execution."""

    DEFAULT_CSS = """
    ArgumentsScreen { align: center middle; }
    ArgumentsScreen > Vertical {
        width: 74;
        max-height: 90%;
        height: auto;
        padding: 1 2;
        border: heavy $primary;
        background: $surface;
    }
    ArgumentsScreen Input { margin-bottom: 1; }
    ArgumentsScreen Horizontal { height: auto; align-horizontal: right; }
    """

    def __init__(self, *, request: FormRequest) -> None:
        super().__init__()
        self._request = request

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._request.title, classes="modal-title")
            for field in self._request.fields:
                yield Label(field.label)
                yield Input(
                    placeholder=field.placeholder,
                    password=field.password,
                    id=f"field-{field.name}",
                )
            yield Static("", id="arguments-error", classes="error", markup=False)
            with Horizontal():
                yield Button("Cancel", id="arguments-cancel")
                yield Button("Continue", id="arguments-submit", variant="primary")

    @on(Button.Pressed, "#arguments-cancel")
    def cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#arguments-submit")
    def submit(self) -> None:
        values = tuple(
            FormValue(
                name=field.name,
                value=self.query_one(f"#field-{field.name}", Input).value.strip(),
            )
            for field in self._request.fields
        )
        empty = [value.name for value in values if value.value == ""]
        if empty:
            self.query_one("#arguments-error", Static).update(
                "Required values are empty: " + ", ".join(empty),
            )
            return
        self.dismiss(FormSubmission(target=self._request.target, values=values))


class InformationScreen(ModalScreen[None]):
    """Display long structured or raw command output."""

    DEFAULT_CSS = """
    InformationScreen { align: center middle; }
    InformationScreen > Vertical {
        width: 90%;
        height: 85%;
        padding: 1 2;
        border: heavy $primary;
        background: $surface;
    }
    InformationScreen Static { height: 1fr; overflow-y: auto; }
    """

    def __init__(self, *, title: str, content: str) -> None:
        super().__init__()
        self._title = title
        self._content = content

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._title, classes="modal-title")
            yield Static(self._content, id="information-content", markup=False)
            yield Button("Close", id="information-close", variant="primary")

    @on(Button.Pressed, "#information-close")
    def close(self) -> None:
        self.dismiss(None)

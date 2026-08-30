from textual.app import App
from textual.widgets import Input, Static

from fpl_data_relay.adapters.inbound.tui.screens import (
    ArgumentsScreen,
    ConfirmationScreen,
    FormField,
    FormRequest,
    FormSubmission,
    FormValue,
)


def test_form_submission_requires_exactly_one_named_value() -> None:
    submission = FormSubmission(
        target="target",
        values=(FormValue(name="reason", value="planned work"),),
    )

    assert submission.require(name="reason") == "planned work"

    for invalid in (
        FormSubmission(target="target", values=()),
        FormSubmission(
            target="target",
            values=(
                FormValue(name="reason", value="one"),
                FormValue(name="reason", value="two"),
            ),
        ),
    ):
        try:
            invalid.require(name="reason")
        except ValueError as error:
            assert "was not supplied exactly once" in str(error)
        else:
            raise AssertionError("Malformed form submission was accepted.")


async def test_confirmation_screen_can_cancel() -> None:
    app = App[None]()
    results: list[bool | None] = []

    async with app.run_test() as pilot:
        app.push_screen(
            ConfirmationScreen(title="Run target", impact="Expected effect"),
            callback=results.append,
        )
        await pilot.pause()
        await pilot.click("#confirm-cancel")
        await pilot.pause()

    assert results == [False]


async def test_arguments_screen_validates_and_submits_explicit_fields() -> None:
    app = App[None]()
    results: list[FormSubmission | None] = []
    request = FormRequest(
        target="prod-maintenance-begin",
        title="Begin maintenance",
        fields=(
            FormField(
                name="reason",
                label="Reason",
                placeholder="planned work",
                password=False,
            ),
        ),
    )

    async with app.run_test() as pilot:
        app.push_screen(ArgumentsScreen(request=request), callback=results.append)
        await pilot.pause()

        await pilot.click("#arguments-submit")
        assert "Required values are empty: reason" in str(
            app.screen.query_one("#arguments-error", Static).content,
        )

        app.screen.query_one("#field-reason", Input).value = "  planned work  "
        assert isinstance(app.screen, ArgumentsScreen)
        app.screen.submit()
        await pilot.pause()

    assert results == [
        FormSubmission(
            target="prod-maintenance-begin",
            values=(FormValue(name="reason", value="planned work"),),
        ),
    ]


async def test_arguments_screen_can_cancel() -> None:
    app = App[None]()
    results: list[FormSubmission | None] = []
    request = FormRequest(
        target="aws-status",
        title="AWS status",
        fields=(),
    )

    async with app.run_test() as pilot:
        app.push_screen(ArgumentsScreen(request=request), callback=results.append)
        await pilot.pause()
        await pilot.click("#arguments-cancel")
        await pilot.pause()

    assert results == [None]

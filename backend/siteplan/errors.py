"""Errors the layout engine raises for conditions the user can actually fix."""


class LayoutError(Exception):
    """A layout could not be produced. `code` is stable for the UI, `message` is prose."""

    def __init__(self, code: str, message: str, **context):
        super().__init__(message)
        self.code = code
        self.message = message
        self.context = context

    def to_dict(self):
        return {"ok": False, "error": {"code": self.code, "message": self.message, **self.context}}

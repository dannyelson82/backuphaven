"""``${VAR}`` and ``${VAR:-default}`` in plugin values, filled from an app's
template variables (ADR-0014). Pure functions."""

import re

_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class MissingVariableError(ValueError):
    """A ``${VAR}`` without a default that the template doesn't set."""


def fill(expression: str, variables: dict[str, str]) -> str:
    """Replace every reference; an empty or missing variable uses its default."""

    def one(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        value = variables.get(name, "")
        if value:
            return value
        if default is not None:
            return default
        raise MissingVariableError(f"the app's template doesn't set {name}")

    return _REFERENCE.sub(one, expression)


def names(expression: str) -> list[str]:
    """The variable names an expression uses."""
    return [m.group(1) for m in _REFERENCE.finditer(expression)]

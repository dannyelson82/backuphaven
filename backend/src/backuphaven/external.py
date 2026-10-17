"""Apps' databases in other containers (ADR-0014): from a plugin's
``external_databases`` and the app's template to a connection, plus the owner's
per-app address overrides (Apps page), stored in settings (``db_addresses``)."""

from dataclasses import dataclass

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backuphaven import settings_store, templating
from backuphaven.dbdump import Target
from backuphaven.discovery import App
from backuphaven.plugins.manifest import ExternalDatabase

SETTING_KEY = "db_addresses"
DEFAULT_PORTS = {"mariadb": 3306, "postgres": 5432}
SUFFIX = {"mariadb": "sql", "postgres": "dump"}


class Address(BaseModel):
    host: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9._:\[\]-]+$")
    port: int = Field(ge=1, le=65535)


def load_overrides(session: Session) -> dict[str, dict[str, dict[str, object]]]:
    return dict(settings_store.get(session, SETTING_KEY) or {})


def save_override(session: Session, app: str, db_id: str, address: Address | None) -> None:
    stored = load_overrides(session)
    per_app = dict(stored.get(app.lower(), {}))
    if address is None:
        per_app.pop(db_id, None)
    else:
        per_app[db_id] = address.model_dump()
    stored[app.lower()] = per_app
    settings_store.put(session, SETTING_KEY, stored)


@dataclass(frozen=True)
class Resolved:
    target: Target
    from_template: bool  # False: the owner's override


def resolve(
    app: App, spec: ExternalDatabase, overrides: dict[str, dict[str, dict[str, object]]]
) -> Resolved:
    """The connection for one of the app's databases. Raises ``ValueError`` (with a
    message for the owner) when the template lacks a value."""
    variables = app.variables
    host = templating.fill(spec.host, variables)
    port_text = templating.fill(spec.port, variables) if spec.port else ""
    override = overrides.get(app.name.lower(), {}).get(spec.id)
    if override:
        host, port_text = str(override["host"]), str(override["port"])
    try:
        port = int(port_text) if port_text else DEFAULT_PORTS[spec.engine]
    except ValueError:
        raise ValueError(f"the database port {port_text!r} isn't a number") from None
    target = Target(
        engine=spec.engine,
        host=host,
        port=port,
        database=templating.fill(spec.database, variables),
        user=templating.fill(spec.user, variables),
        password=templating.fill(spec.password, variables) if spec.password else "",
    )
    return Resolved(target, from_template=override is None)

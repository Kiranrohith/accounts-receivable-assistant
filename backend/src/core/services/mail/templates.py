"""Our fixed e-mail wording: the Jinja templates in src/templates/email.

Each template extends email/base.j2 (header, footer) and defines two blocks: `subject` and `content`.
`render_email` fills in the variables and returns the subject and the full plain-text body. Every
variable a template uses must be passed (an unknown one is an error, never a silent blank), so a
template and the code that fills it cannot drift apart unnoticed.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from config.settings import get_settings

TEMPLATE_DIR = Path(__file__).resolve().parents[3] / "templates"
DEFAULT_COMPANY = "Accounts Receivable"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    undefined=StrictUndefined,
    autoescape=False,  # plain-text mail
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=False,
)


@dataclass(frozen=True)
class Rendered:
    subject: str
    body: str


def company_context() -> dict[str, Any]:
    settings = get_settings()
    return {
        "company_name": settings.our_company_name or DEFAULT_COMPANY,
        "company_contact_line": settings.our_company_contact_line or "",
    }


def render_email(name: str, **variables: Any) -> Rendered:
    """Render email/<name>.j2. `name` is e.g. "dispute_resolved"."""
    template = _env.get_template(f"email/{name}.j2")
    context = {**company_context(), **variables}
    subject = "".join(template.blocks["subject"](template.new_context(context)))
    body = template.render(context)
    return Rendered(subject=" ".join(subject.split()), body=_tidy(body))


def _tidy(text: str) -> str:
    """No run of more than one blank line, no trailing space, one final newline."""
    lines = [line.rstrip() for line in text.strip().splitlines()]
    out: list[str] = []
    for line in lines:
        if line == "" and out and out[-1] == "":
            continue
        out.append(line)
    return "\n".join(out) + "\n"

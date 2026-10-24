"""Request models of the HTTP API: the only place where lead form input is validated."""
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, EmailStr, Field, field_validator

from lead_responder.limits import MAX_EMAIL, MAX_MESSAGE, MAX_NAME, MAX_PHONE, clean_text


# One lead form post. Order of checks: pydantic first applies the size limits (so a huge string
# is refused before any of our code touches it), then the validators below strip NUL and
# whitespace and reject what is left empty or cannot be encoded. The database CHECKs stay behind
# this as the last line of defence; the repository no longer validates lead fields itself.
class LeadIn(BaseModel):
    name: Annotated[str, Field(max_length=MAX_NAME)]
    email: Annotated[EmailStr, Field(max_length=MAX_EMAIL)]
    phone: Annotated[str, Field(max_length=MAX_PHONE)] | None = None
    message: Annotated[str, Field(max_length=MAX_MESSAGE)]
    consent: bool

    # Strip NUL and surrounding whitespace; a lone surrogate (not valid UTF-8) is refused
    # because it would crash the database driver later.
    @field_validator("name", "email", "phone", "message", mode="after")
    @classmethod
    def _clean(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = clean_text(value).strip()
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("text contains characters that are not valid UTF-8") from None
        return value

    @field_validator("name", "message", mode="after")
    @classmethod
    def _not_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("must not be empty")
        return value

    # Consent is a legal precondition (DSGVO), so "false" is an error, not a stored flag.
    @field_validator("consent", mode="after")
    @classmethod
    def _consent_given(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("consent is required")
        return value

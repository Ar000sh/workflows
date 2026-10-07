"""The shapes the classifier must return. Pydantic turns these into a JSON schema for Claude."""
from enum import StrEnum

from pydantic import BaseModel, Field


class Category(StrEnum):
    APPOINTMENT = "appointment"
    NEW_PATIENT = "new_patient"
    INSURANCE = "insurance"
    OPENING_HOURS = "opening_hours"
    PAIN_EMERGENCY = "pain_emergency"
    MEDICAL_QUESTION = "medical_question"
    COMPLAINT = "complaint"
    PRICING = "pricing"
    OTHER = "other"


class Risk(StrEnum):
    ROUTINE = "routine"
    SENSITIVE = "sensitive"


class Urgency(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class Classification(BaseModel):
    category: Category
    risk: Risk
    urgency: Urgency
    language: str = Field(description="ISO 639-1 code of the inquiry's language, e.g. 'de', 'en', 'tr'")
    reason: str = Field(description="One short sentence in German for the practice staff")

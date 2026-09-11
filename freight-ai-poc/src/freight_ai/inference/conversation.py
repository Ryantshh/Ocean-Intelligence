"""Typed per-conversation state; never add private state to the public intent schema."""

import re

from pydantic import Field

from freight_ai.data.models import Intent, StrictModel


class ConversationState(StrictModel):
    previous_intent: Intent | None = None
    cargo_fraction: float | None = Field(default=None, gt=0, le=1)
    fraction_overridden: bool = False

    def updated_assumption(self, question):
        state = self.model_copy(deep=True)
        text = question.casefold()
        if re.search(
            r"\b(reset|clear|remove)\b.*\b(assumption|allowance|percentage|cargo fraction)\b",
            text,
        ):
            state.cargo_fraction = None
            state.fraction_overridden = False
        else:
            values = re.findall(
                r"(?<![\w.])([-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*%", question
            )
            if len(values) > 1:
                raise ValueError(
                    "Please specify one cargo-capacity percentage at a time."
                )
            if values:
                value = float(values[0]) / 100
                if not 0 < value <= 1:
                    raise ValueError(
                        "Cargo-capacity percentage must be greater than 0 and at most 100."
                    )
                state.cargo_fraction = value
                state.fraction_overridden = True
        return state

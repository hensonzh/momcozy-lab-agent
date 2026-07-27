from pydantic import BaseModel, ConfigDict


class PumpModelsReadArguments(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

__all__ = ["PumpModelsReadArguments"]

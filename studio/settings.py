from pydantic import BaseModel, ConfigDict, Field


class TurboSettings(BaseModel):
    """Supported sampling and reference options for Chatterbox Turbo."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    seed: int = Field(default=35, ge=0, le=2147483647, strict=True)
    temperature: float = Field(default=0.8, ge=0.05, le=2.0)
    top_p: float = Field(default=0.95, gt=0, le=1.0)
    top_k: int = Field(default=1000, ge=0, le=1000, strict=True)
    repetition_penalty: float = Field(default=1.2, ge=1.0, le=2.0)
    normalize_reference: bool = Field(default=True, strict=True)

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class APIClient(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    token: SecretStr = Field(min_length=32)
    subject_ref: str = Field(min_length=1, max_length=200)
    domain_ref: str = Field(min_length=1, max_length=200)
    role: Literal["operator", "viewer"]


class EdgeClient(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    token: SecretStr = Field(min_length=32)
    edge_ref: str = Field(min_length=1, max_length=200)
    domain_ref: str = Field(min_length=1, max_length=200)


class TrustedIssuer(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    issuer: str = Field(min_length=1, max_length=200)
    client_id: str = Field(min_length=1, max_length=200)
    key_id: str = Field(min_length=1, max_length=100)
    public_key: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENTICIOT_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://agenticiot:agenticiot_local@127.0.0.1:55432/agenticiot"
    )

    @field_validator("database_url")
    @classmethod
    def require_postgres(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
        except Exception:
            raise ValueError("A valid PostgreSQL URL is required") from None
        if url.drivername != "postgresql+psycopg":
            raise ValueError("Use the postgresql+psycopg database driver")
        return value

    api_clients: list[APIClient] = Field(default_factory=list, repr=False)
    edge_clients: list[EdgeClient] = Field(default_factory=list, repr=False)
    trusted_issuers: list[TrustedIssuer] = Field(default_factory=list)
    token_audience: str = "agenticiot-platform"
    inference_hmac_key: SecretStr | None = Field(default=None, min_length=32, repr=False)
    inference_hmac_previous_keys: list[SecretStr] = Field(
        default_factory=list, max_length=3, repr=False
    )

    @field_validator("inference_hmac_key", mode="before")
    @classmethod
    def optional_empty_key(cls, value):
        return None if value == "" else value

    @field_validator("inference_hmac_previous_keys")
    @classmethod
    def previous_key_lengths(cls, keys):
        if any(len(key.get_secret_value()) < 32 for key in keys):
            raise ValueError("Previous HMAC keys require at least 32 characters")
        return keys

    @model_validator(mode="after")
    def unique_tokens(self):
        tokens = [
            client.token.get_secret_value() for client in [*self.api_clients, *self.edge_clients]
        ]
        if len(tokens) != len(set(tokens)):
            raise ValueError("API client tokens must be unique")
        return self

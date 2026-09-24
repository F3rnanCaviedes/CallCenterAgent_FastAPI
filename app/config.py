from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_env: Literal["development", "staging", "production"] = "development"
    app_port: int = 8000
    log_level: str = "INFO"
    timezone: str = "America/Bogota"
    # URL publica exacta registrada en Twilio. Se usa para validar la
    # firma del webhook: detras de un proxy la URL reconstruida no es
    # fiable y una discrepancia invalida firmas legitimas.
    public_base_url: str = "http://localhost:8000"
    session_ttl_seconds: int = 1800
    max_conversation_turns: int = 20

    # LLM
    llm_provider: Literal["anthropic", "openai"] = "anthropic"
    anthropic_api_key: SecretStr = Field(...)
    openai_api_key: SecretStr | None = None
    llm_model: str = "claude-sonnet-4-6"

    # Database
    database_url: SecretStr = Field(...)
    redis_url: str = "redis://localhost:6379/0"

    # Security
    api_secret_key: SecretStr = Field(...)
    encryption_key: SecretStr = Field(...)
    # Claves de los backends de cliente autorizados a pedir tokens.
    # Vacío = endpoint /v1/auth/token desactivado (503).
    client_api_keys: list[SecretStr] = []
    access_token_ttl_seconds: int = 3600
    # Claves del cron/scheduler interno para POST /v1/reminders/trigger.
    # Separadas de client_api_keys a proposito. Vacia = endpoint cerrado (503).
    internal_api_keys: list[SecretStr] = []
    allowed_origins: list[str] = ["http://localhost:3000"]
    rate_limit_per_minute: int = 30
    # Hosts que fill_web_form puede abrir. Vacío = función desactivada.
    form_filler_allowed_hosts: list[str] = []

    # Notifications
    twilio_account_sid: SecretStr | None = None
    twilio_auth_token: SecretStr | None = None
    twilio_phone_number: str | None = None
    sendgrid_api_key: SecretStr | None = None
    notification_email_from: str | None = None

    # Voice - STT (Deepgram)
    stt_provider: str = "deepgram"
    deepgram_api_key: SecretStr | None = None
    deepgram_model: str = "nova-2"
    # es-419 = español latinoamericano. "es" tira a peninsular.
    deepgram_language: str = "es-419"
    # Silencio (ms) antes de dar la frase por cerrada. Mas bajo = Sofia
    # responde antes, pero corta a quien piensa a media frase. Calibrar con
    # llamadas reales: los pacientes mayores hacen pausas mas largas.
    deepgram_endpointing_ms: int = 300
    deepgram_utterance_end_ms: int = 1000
    azure_speech_key: SecretStr | None = None
    azure_speech_region: str | None = None

    # Voice - TTS (Azure)
    tts_provider: str = "azure_neural"
    azure_tts_key: SecretStr | None = None
    azure_tts_region: str | None = None
    azure_tts_voice: str = "es-CO-SalomeNeural"
    azure_tts_language: str = "es-CO"
    elevenlabs_api_key: SecretStr | None = None

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()

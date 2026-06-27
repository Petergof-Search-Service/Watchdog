from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )

    API_BASE_URL: str          # https://your-api.example.com/api/v1
    CLOUD_FUNCTION_API_KEY: str


settings = Settings()

# Time limits per status in seconds. Files stuck longer than this are marked "dead".
STATUS_TIMEOUTS: dict[str, int] = {
    "pending_upload":  600,   # 10 min
    "uploading":       900,   # 15 min
    "uploaded":        180,   #  3 min  (OCR trigger should fire almost instantly)
    "ocr_processing": 3600,   # 60 min
    "ocr_done":        180,   #  3 min
    "rag_indexing":    900,   # 15 min
}

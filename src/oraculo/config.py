from __future__ import annotations

from pydantic import AliasChoices, AnyUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ===== QDRANT CLOUD =====
    qdrant_url: AnyUrl = Field(validation_alias="QDRANT_URL")
    qdrant_api_key: SecretStr = Field(validation_alias="QDRANT_API_KEY")
    qdrant_collection: str = Field(
        default="cer_chunks", validation_alias="QDRANT_COLLECTION"
    )
    qdrant_cer_chunks_vector_dim: int = Field(
        default=768,
        validation_alias=AliasChoices(
            "QDRANT_CER_CHUNKS_VECTOR_DIM",
            "QDRANT_COLLECTION_VECTOR_DIM",
        ),
    )
    qdrant_sag_collection: str = Field(
        default="SAG", validation_alias="QDRANT_SAG_COLLECTION"
    )
    qdrant_sag_vector_dim: int = Field(
        default=769,
        validation_alias=AliasChoices(
            "QDRANT_SAG_VECTOR_DIM",
            "QDRANT_SAG_COLLECTION_VECTOR_DIM",
        ),
    )

    # ===== GEMINI API =====
    gemini_api_key: SecretStr = Field(validation_alias="GEMINI_API_KEY")
    gemini_model: str = Field(
        default="gemini-2.5-flash",
        validation_alias="GEMINI_MODEL",
    )
    gemini_fallback_model: str = Field(
        default="gemini-3-pro-preview",
        validation_alias="GEMINI_FALLBACK_MODEL",
    )
    gemini_refine_model: str = Field(
        default="gemini-3-flash-preview",
        validation_alias="GEMINI_REFINE_MODEL",
    )
    gemini_timeout_ms: int = Field(
        default=60000,
        validation_alias="GEMINI_TIMEOUT_MS",
    )
    gemini_max_output_tokens: int = Field(
        default=1800,
        validation_alias="GEMINI_MAX_OUTPUT_TOKENS",
    )
    gemini_thinking_budget: int = Field(
        default=0,
        validation_alias="GEMINI_THINKING_BUDGET",
    )
    gemini_refine_timeout_ms: int = Field(
        default=20000,
        validation_alias="GEMINI_REFINE_TIMEOUT_MS",
    )
    gemini_router_model: str = Field(
        default="gemini-2.5-flash",
        validation_alias="GEMINI_ROUTER_MODEL",
    )
    gemini_router_fallback_model: str = Field(
        default="gemini-2.5-flash",
        validation_alias="GEMINI_ROUTER_FALLBACK_MODEL",
    )
    gemini_router_timeout_ms: int = Field(
        default=15000,
        validation_alias="GEMINI_ROUTER_TIMEOUT_MS",
    )
    gemini_router_max_output_tokens: int = Field(
        default=400,
        validation_alias="GEMINI_ROUTER_MAX_OUTPUT_TOKENS",
    )
    gemini_router_thinking_budget: int = Field(
        default=0,
        validation_alias="GEMINI_ROUTER_THINKING_BUDGET",
    )
    gemini_complex_model: str = Field(
        default="gemini-3-pro-preview",
        validation_alias="GEMINI_COMPLEX_MODEL",
    )
    gemini_complex_fallback_model: str = Field(
        default="gemini-2.5-flash",
        validation_alias="GEMINI_COMPLEX_FALLBACK_MODEL",
    )
    gemini_complex_timeout_ms: int = Field(
        default=120000,
        validation_alias="GEMINI_COMPLEX_TIMEOUT_MS",
    )
    gemini_complex_max_output_tokens: int = Field(
        default=4096,
        validation_alias="GEMINI_COMPLEX_MAX_OUTPUT_TOKENS",
    )
    gemini_complex_thinking_budget: int = Field(
        default=1536,
        validation_alias="GEMINI_COMPLEX_THINKING_BUDGET",
    )
    rag_use_query_refiner: bool = Field(
        default=True,
        validation_alias="RAG_USE_QUERY_REFINER",
    )

    # ===== RAG CONTEXTO =====
    rag_top_docs: int = Field(default=8, validation_alias="RAG_TOP_DOCS")
    rag_total_context_char_budget: int = Field(
        default=96000,
        validation_alias="RAG_TOTAL_CONTEXT_CHAR_BUDGET",
    )
    rag_min_doc_char_budget: int = Field(
        default=6000,
        validation_alias="RAG_MIN_DOC_CHAR_BUDGET",
    )
    rag_max_doc_char_budget: int = Field(
        default=20000,
        validation_alias="RAG_MAX_DOC_CHAR_BUDGET",
    )
    rag_sag_top_k: int = Field(default=8, validation_alias="RAG_SAG_TOP_K")
    sag_excel_path: str = Field(
        default="SAG - copia.xlsx",
        validation_alias="SAG_EXCEL_PATH",
    )

    # ===== TELEGRAM BOT =====
    telegram_bot_token: SecretStr = Field(validation_alias="TELEGRAM_BOT_TOKEN")

    # ===== EDISON API =====
    edison_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="EDISON_API_KEY",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


def get_settings() -> Settings:
    return Settings()

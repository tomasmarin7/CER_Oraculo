"""Quick import validation script."""
import sys
sys.path.insert(0, "src")

# Core
from oraculo.config import get_settings
from oraculo.main import run_telegram_bot

# Conversation - new modules
from oraculo.conversation.flow_helpers import normalize_text, serialize_seed_hits, deserialize_seed_hits
from oraculo.conversation.cer_response import build_cer_first_response_from_hits, generate_cer_detail_followup_response
from oraculo.conversation.sag_response import generate_sag_response
from oraculo.conversation.flujo_guiado import GuidedFlowResult, execute_guided_action_from_router, try_handle_guided_flow

# Aplicacion
from oraculo.aplicacion import ServicioConversacionOraculo, RespuestaOraculo

# Telegram
from oraculo.telegram import TelegramBot

# Providers
from oraculo.providers.llm import generate_answer
from oraculo.providers.embeddings import embed_retrieval_query
from oraculo.providers.query_refiner import refine_user_question

# Router
from oraculo.router import route_global_action, GlobalRouterDecision

# RAG
from oraculo.rag.retriever import retrieve, retrieve_sag
from oraculo.rag.doc_context import DocContext, build_doc_contexts_from_hits

# Sources
from oraculo.sources.cer_csv_lookup import load_cer_index
from oraculo.sources.sag_csv_lookup import find_products_by_ingredient
from oraculo.sources.resolver import format_sources_from_hits

# Vectorstore
from oraculo.vectorstore.qdrant_client import get_qdrant_client
from oraculo.vectorstore.search import query_top_chunks

# Query enhancer
from oraculo.query_enhancer import enhance_cer_query, enhance_sag_query

# Followup
from oraculo.followup import route_guided_followup, build_detail_followup_prompt

print("ALL IMPORTS OK")

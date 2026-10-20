"""统一管理项目路径和默认配置。"""
from pathlib import Path
import os

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = PROJECT_ROOT / "models"
YUNET_MODEL_PATH = MODELS_DIR / "face_detection_yunet_2023mar.onnx"
PHOTOS_DIR = PROJECT_ROOT / "photos"
LORE_WIKI_SOURCE = PROJECT_ROOT / "data" / "external" / "arknights_lore_wiki"
KNOWLEDGE_DB = PROJECT_ROOT / "data" / "runtime" / "terra_lore.db"

OLLAMA_MODEL = os.getenv("FRISTON_PLANNER_MODEL", "qwen3.5:2b-q4_K_M")
OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"
NPC_CONTEXT_TOKENS = 4096
NPC_REPLY_TOKENS = 240
NPC_HISTORY_TURNS = 3
NPC_HISTORY_TOKEN_BUDGET = 1100
CAMERA_INDEX = 0

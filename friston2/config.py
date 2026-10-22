"""统一管理项目路径和默认配置。"""
from pathlib import Path
import os

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = PROJECT_ROOT / "models"
YUNET_MODEL_PATH = MODELS_DIR / "face_detection_yunet_2023mar.onnx"
PHOTOS_DIR = PROJECT_ROOT / "photos"
LORE_WIKI_SOURCE = PROJECT_ROOT / "data" / "external" / "arknights_lore_wiki"
KNOWLEDGE_DB = PROJECT_ROOT / "data" / "runtime" / "terra_lore.db"

OLLAMA_MODEL = os.getenv("FRISTON_CHAT_MODEL", "qwen3.5:9b")
OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"
NPC_CONTEXT_TOKENS = 4096
# One resident model and one context size avoid swapping weights between routing
# and dialogue. The configured 9B Q4 model fits this machine's 8 GB GPU.
OLLAMA_OPTIONS = {"num_ctx": NPC_CONTEXT_TOKENS, "num_gpu": 99}
OLLAMA_KEEP_ALIVE = os.getenv("FRISTON_OLLAMA_KEEP_ALIVE", -1)
NPC_REPLY_TOKENS = 192
NPC_HISTORY_TURNS = 3
NPC_HISTORY_TOKEN_BUDGET = 1100
CAMERA_INDEX = 0

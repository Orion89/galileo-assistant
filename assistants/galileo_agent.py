import json
import logging
import os
from pathlib import Path
from typing import List, Optional

from agents import Agent, CodeInterpreterTool, FileSearchTool, ModelSettings
from tools.functions.download_external_file import download_external_file_tool
from tools.functions.rww_evaluate import rww_evaluate_tool

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).parent / "config_galileo_v1.1.0.json"


def load_assistant_config(config_path: Path = DEFAULT_CONFIG_PATH) -> dict:
    """Carga la configuración base (instrucciones, hyperparámetros) desde el JSON de Galileo."""
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error cargando archivo de configuración {config_path}: {e}")
    return {}


def create_galileo_agent(
    vector_store_id: Optional[str] = None,
    model_override: Optional[str] = None,
) -> Agent:
    """
    Crea y configura una instancia del agente Galileo con OpenAI Agents SDK.
    
    Args:
        vector_store_id: ID del Vector Store (efímero por sesión o persistente).
        model_override: Modelo a utilizar (prioridad sobre variable de entorno OPENAI_MODEL).
    """
    config_data = load_assistant_config()
    instructions = config_data.get("instructions")
    if not instructions:
        instructions = (
            "Eres GALILEO, un agente de IA unificado que fusiona rigor analítico, "
            "evaluación experta de propuestas de inversión (Venture Capital / CVC) "
            "y comunicación ejecutiva clara para innovación corporativa."
        )

    # Parametrización del modelo vía entorno (OPENAI_MODEL) o fallback a config
    model = (
        model_override
        or os.getenv("OPENAI_MODEL")
        or config_data.get("model")
        or "gpt-4.1"
    )

    temperature = float(config_data.get("temperature", 0.25))
    top_p = float(config_data.get("top_p", 0.95))

    # Ensamblar herramientas
    tools = []

    # 1. Búsqueda de archivos en Vector Store
    effective_vs_id = vector_store_id or os.getenv("VECTOR_STORE_ID")
    if effective_vs_id:
        tools.append(FileSearchTool(vector_store_ids=[effective_vs_id]))
    else:
        # FileSearchTool con lista vacía inicial si aún no se ha creado el Vector Store efímero
        tools.append(FileSearchTool(vector_store_ids=[]))

    # 2. Intérprete de código para cálculos y gráficos
    tools.append(CodeInterpreterTool(tool_config={"type": "code_interpreter"}))

    # 3. Herramienta de descarga de archivos externos
    tools.append(download_external_file_tool)

    # 4. Herramienta de evaluación R-W-W
    tools.append(rww_evaluate_tool)

    return Agent(
        name="Galileo",
        instructions=instructions,
        model=model,
        model_settings=ModelSettings(
            temperature=temperature,
            top_p=top_p,
        ),
        tools=tools,
    )

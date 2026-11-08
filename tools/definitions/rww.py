from tools.schemas.rww_models import RWWInput
from tools.functions.rww_evaluate import rww_evaluate_tool

# Definición JSON schema para compatibilidad
rww_evaluate = {
    "name": "rww_evaluate",
    "description": "Evalúa una idea con la matriz R-W-W (Real/Win/Worth) y devuelve score, decisión y próximos pasos.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["payload"],
        "properties": {
            "payload": RWWInput.model_json_schema()
        }
    }
}

__all__ = ["rww_evaluate", "rww_evaluate_tool"]

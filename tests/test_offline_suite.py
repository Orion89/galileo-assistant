import os
import sys
from pathlib import Path

# Agregar raíz del proyecto al sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from assistants.galileo_agent import create_galileo_agent, load_assistant_config
from tools.functions.rww_evaluate import rww_evaluate, rww_evaluate_tool, evaluate_rww_logic
from tools.schemas.rww_models import (
    RWWInput, TriState, ItemAssessment, KillerQuestion, Financials, IdeaMetadata
)
from agents import SQLiteSession


def test_agent_creation():
    print("[1/4] Verificando creación e instanciación del agente...")
    agent = create_galileo_agent(vector_store_id="vs_test_123")
    assert agent.name == "Galileo", f"Nombre inesperado: {agent.name}"
    assert agent.model in ["gpt-4.1", "gpt-4o"], f"Modelo inesperado: {agent.model}"
    
    tool_names = [getattr(t, "name", str(t)) for t in agent.tools]
    print(f"       Herramientas registradas: {tool_names}")
    assert "file_search" in tool_names
    assert "code_interpreter" in tool_names
    assert "download_external_file" in tool_names
    assert "rww_evaluate" in tool_names
    print("       [OK] Creacion de agente y tools exitosa.")


def test_rww_scoring_offline():
    print("[2/4] Verificando scoring y lógica de R-W-W sin llamadas a API...")
    # Caso 1: Entrada con datos validados
    inp = RWWInput(
        metadata=IdeaMetadata(idea_title="Optimización de Flota Eléctrica", objective="cost_reduction"),
        financials=Financials(
            currency="USD",
            periodicity="annual",
            capex=50000,
            implementation_cost=10000,
            savings=40000,
            variable_costs_new=5000,
            opex_incremental=5000,
        ),
        killer_questions=[
            KillerQuestion(question="¿Cumple regulación ambiental?", answer=True, kill_if_false=True)
        ]
    )
    # Marcar viabilidad en Yes
    inp.real.technical_feasibility = ItemAssessment(status=TriState.yes, justification="Validado con piloto previo")
    inp.real.problem_validated = ItemAssessment(status=TriState.yes, justification="Datos de costos históricos")
    inp.real.buyer_identified = ItemAssessment(status=TriState.yes, justification="Gerente de Logística aprueba presupuesto")
    inp.win.differentiation = ItemAssessment(status=TriState.yes, justification="Algoritmo propio de ruteo")
    inp.win.access_sponsor_data_channel = ItemAssessment(status=TriState.yes, justification="Acceso directo a telemetría")
    inp.win.internal_capabilities = ItemAssessment(status=TriState.partial, justification="Requiere contratar 1 data engineer")
    inp.worth.unit_economics_tco = ItemAssessment(status=TriState.yes, justification="TCO 30% menor que diésel")
    inp.worth.impact_magnitude = ItemAssessment(status=TriState.yes, justification="Ahorro proyectado > 30k USD/año")
    inp.worth.option_value_learning = ItemAssessment(status=TriState.yes, justification="Habilita electrificación total a 2028")

    evaluation = evaluate_rww_logic(inp)
    print(f"       Score obtenido: {evaluation.total_score}/100 -> Decision: {evaluation.decision}")
    assert evaluation.total_score > 80, f"Score esperado > 80, obtenido {evaluation.total_score}"
    assert evaluation.decision == "GO", f"Decisión esperada GO, obtenida {evaluation.decision}"
    assert evaluation.financials_summary is not None
    assert evaluation.financials_summary.ebitda_incremental_annual == 30000.0  # 40000 - 5000 - 5000
    assert evaluation.financials_summary.investment_total == 60000.0          # 50000 + 10000
    assert evaluation.financials_summary.payback_years == 2.0                 # 60000 / 30000

    # Serialización JSON
    json_output = rww_evaluate(inp)
    assert "version_corta" in json_output
    print("       [OK] Logica R-W-W y calculo financiero offline validados con exito.")


def test_killer_question_kill():
    print("[3/4] Verificando activacion de Killer Questions...")
    inp = RWWInput(
        killer_questions=[
            KillerQuestion(question="Es legal en Chile?", answer=False, kill_if_false=True, rationale="Prohibido por ley")
        ]
    )
    evaluation = evaluate_rww_logic(inp)
    assert evaluation.decision == "KILL", f"Esperado KILL, obtenido {evaluation.decision}"
    assert len(evaluation.killer_hits) > 0
    print("       [OK] Activacion de Killer Question verificada.")


def test_sqlite_session_offline():
    print("[4/4] Verificando inicializacion de SQLiteSession...")
    test_db = Path("conversations") / "test_session_offline.db"
    if test_db.exists():
        test_db.unlink()
    
    session = SQLiteSession(session_id="thread_test_offline", db_path=str(test_db))
    assert session.session_id == "thread_test_offline"
    print("       [OK] SQLiteSession inicializado correctamente.")
    
    if test_db.exists():
        test_db.unlink()


if __name__ == "__main__":
    print("=== INICIANDO SUITE DE PRUEBAS OFFLINE (SIN LLAMADAS A OPENAI) ===")
    test_agent_creation()
    test_rww_scoring_offline()
    test_killer_question_kill()
    test_sqlite_session_offline()
    print("=== TODAS LAS PRUEBAS OFFLINE COMPLETADAS EXITOSAMENTE ===")

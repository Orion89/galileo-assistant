from __future__ import annotations
import json
from typing import List, Tuple, Optional
from datetime import datetime
from tools.schemas.rww_models import (
    TriState, EvidenceLevel, ItemAssessment,
    RealInputs, WinInputs, WorthInputs,
    RWWWeights, RWWThresholds, Financials, KillerQuestion, RuleConfig, IdeaMetadata,
    RWWInput, ScoreItem, PillarScore, FinancialsSummary, PilotPlan, RWWEvaluation
)

# ─────────── Utilidades internas ───────────
def _tri_to_num(status: TriState, treat_unknown_as_zero: bool = True) -> float:
    mapping = {TriState.yes: 1.0, TriState.partial: 0.5, TriState.no: 0.0, TriState.unknown: 0.0}
    return mapping[status] if treat_unknown_as_zero else (mapping[status] if status != TriState.unknown else 0.0)

def _pillar_items():
    return {
        "real": [
            ("problem_validated",   "Problema validado"),
            ("technical_feasibility","Factibilidad técnica/regulatoria"),
            ("buyer_identified",    "Decisor y presupuesto"),
        ],
        "win": [
            ("differentiation",             "Diferenciación"),
            ("access_sponsor_data_channel", "Acceso: sponsor/datos/canal"),
            ("internal_capabilities",       "Capacidades internas"),
        ],
        "worth": [
            ("unit_economics_tco",  "Unit economics/TCO"),
            ("impact_magnitude",    "Magnitud del impacto"),
            ("option_value_learning","Opcionalidad/aprendizaje"),
        ],
    }

def _default_questions_map():
    return {
        "real.problem_validated": "¿Qué evidencia cuantitativa tienes del problema/WTP (datos, cohortes, $$ del dolor)?",
        "real.technical_feasibility": "¿Nivel TRL y prerequisitos (seguridad TI/OT, permisos) para un piloto de X semanas?",
        "real.buyer_identified": "¿Quién decide y paga? ¿Cuál es el presupuesto disponible y el timing del proceso de compra?",
        "win.differentiation": "¿Contra qué alternativas compites y cuál es tu ventaja defensiva (IP, switching cost, data moats)?",
        "win.access_sponsor_data_channel": "¿Quién es el sponsor interno y qué datos/canales/activos asegurarás para el piloto?",
        "win.internal_capabilities": "¿Qué capacidades internas, integraciones y recursos (personas/tiempo) requiere la ejecución?",
        "worth.unit_economics_tco": "Comparado con el 'as-is', ¿cómo quedan ingresos/ahorros menos costos variables y OPEX incremental (TCO)?",
        "worth.impact_magnitude": "¿Cuál es la magnitud del impacto anual (CLP/UF/US$) y los KPI que lo evidencian?",
        "worth.option_value_learning": "¿Qué aprendizajes/opcionalidad estratégica habilita (sinergias, aceleración de roadmap, barreras futuras)?",
        "financials": "Indica CAPEX, implementación, licencias, capacitación, OPEX inc., ingresos nuevos, ahorros y costos variables (moneda y periodicidad).",
    }

def _collect_missing_and_questions(inp: RWWInput) -> Tuple[list[str], list[str]]:
    missing, questions = [], []
    qmap = _default_questions_map()
    for pillar_name, items in _pillar_items().items():
        pillar_obj = getattr(inp, pillar_name)
        for key, _ in items:
            item: ItemAssessment = getattr(pillar_obj, key)
            if item.status == TriState.unknown:
                path = f"{pillar_name}.{key}"
                missing.append(path)
                if path in qmap:
                    questions.append(qmap[path])
    if inp.financials is None:
        missing.append("financials")
        questions.append(qmap["financials"])
    return missing, questions

def _compute_financials(fin: Financials) -> FinancialsSummary:
    annualize = fin.periodicity == "monthly"
    mult = 12 if annualize else 1
    annual_new_revenue = fin.new_revenue * mult
    annual_savings = fin.savings * mult
    annual_variable_costs = fin.variable_costs_new * mult
    annual_licenses = fin.licenses_cost * mult
    annual_opex_inc = fin.opex_incremental * mult
    ebitda_inc = (annual_new_revenue + annual_savings) - (annual_variable_costs + annual_licenses + annual_opex_inc)
    investment_total = fin.capex + fin.implementation_cost + fin.training_cost
    payback = (investment_total / ebitda_inc) if ebitda_inc > 0 else None
    return FinancialsSummary(
        currency=fin.currency,
        annualized=annualize,
        investment_total=round(investment_total, 2),
        ebitda_incremental_annual=round(ebitda_inc, 2),
        payback_years=round(payback, 2) if payback is not None else None
    )

def _auto_kill_hits(inp: RWWInput) -> list[str]:
    hits = []
    if inp.rules.auto_kill_on_no:
        if inp.real.technical_feasibility.status == TriState.no:
            hits.append("Imposibilidad técnica/regulatoria declarada (Real: factibilidad=No).")
        if inp.real.buyer_identified.status == TriState.no:
            hits.append("No existe comprador/decisor/presupuesto (Real: buyer=No).")
        if inp.worth.unit_economics_tco.status == TriState.no:
            hits.append("Unit economics/TCO intrínsecamente negativos (Worth: unit_economics=No).")
    return hits

def _score(inp: RWWInput) -> tuple[float, list[PillarScore], list[ScoreItem]]:
    w = inp.weights
    treat_unknown = inp.rules.treat_unknown_as_zero
    items = _pillar_items()
    weight_map = {
        "real.problem_validated": w.problem_validated,
        "real.technical_feasibility": w.technical_feasibility,
        "real.buyer_identified": w.buyer_identified,
        "win.differentiation": w.differentiation,
        "win.access_sponsor_data_channel": w.access_sponsor_data_channel,
        "win.internal_capabilities": w.internal_capabilities,
        "worth.unit_economics_tco": w.unit_economics_tco,
        "worth.impact_magnitude": w.impact_magnitude,
        "worth.option_value_learning": w.option_value_learning,
    }
    breakdown, pillar_scores = [], []
    total_contrib = 0.0
    for pillar in ["real","win","worth"]:
        p_items = items[pillar]
        p_weight, p_contrib = 0.0, 0.0
        for key, label in p_items:
            path = f"{pillar}.{key}"
            weight = weight_map[path]
            p_weight += weight
            assessment: ItemAssessment = getattr(getattr(inp, pillar), key)
            num = _tri_to_num(assessment.status, treat_unknown)
            contrib = weight * num
            p_contrib += contrib
            breakdown.append(ScoreItem(
                pillar=pillar,
                item_key=key,
                item_name=label,
                weight=weight,
                status=assessment.status,
                numeric_value=num,
                score_contribution=round(contrib, 2),
                justification=assessment.justification
            ))
        pillar_scores.append(PillarScore(
            pillar=pillar,
            max_points=round(p_weight, 2),
            score_points=round(p_contrib, 2),
            score_percent=round((p_contrib / p_weight * 100.0) if p_weight > 0 else 0.0, 2)
        ))
        total_contrib += p_contrib
    total_weight = w.total if w.total > 0 else 100.0
    total_score = round(total_contrib / total_weight * 100.0, 2)
    return total_score, pillar_scores, breakdown

def _decide(score: float, thresholds: RWWThresholds, killer_hits: List[str]) -> tuple[str, List[str]]:
    if killer_hits:
        return "KILL", ["Se activaron killer questions:"] + killer_hits
    if score >= thresholds.go_min:
        return "GO", [f"Score ≥ {thresholds.go_min}."]
    if score >= thresholds.pilot_min:
        return "PILOT", [f"{thresholds.pilot_min} ≤ Score < {thresholds.go_min}."]
    if score >= thresholds.hold_min:
        return "HOLD", [f"{thresholds.hold_min} ≤ Score < {thresholds.pilot_min}."]
    return "KILL", [f"Score < {thresholds.hold_min}."]

def _pilot_plan(metadata: Optional[IdeaMetadata], decision: str) -> PilotPlan:
    if decision not in ("PILOT","HOLD"):
        return PilotPlan(
            recommended=False,
            rationale="No aplica: decisión final no requiere piloto.",
            hypothesis=[], success_metrics=[], duration_weeks=0, dependencies=[]
        )
    obj = (metadata.objective if metadata and metadata.objective else "other")
    hypotheses = {
        "cost_reduction": ["Si implementamos X, reduciremos costo unitario en ≥Y% en Z semanas."],
        "revenue_growth": ["Si lanzamos X, aumentaremos conversión/ARPU en ≥Y% en Z semanas."],
        "cx": ["X reducirá tiempo de ciclo y NPS subirá ≥Y pts."],
        "safety": ["X reducirá incidentes tipo A en ≥Y%."],
        "compliance": ["X eliminará incumplimientos críticos en proceso Y."],
        "other": ["El piloto demostrará viabilidad técnica y tracción inicial medible."]
    }
    metrics = {
        "cost_reduction": ["% ahorro", "TCO", "tiempo ciclo"],
        "revenue_growth": ["% conversión", "ARPU", "LTV:CAC"],
        "cx": ["NPS", "SLA", "tiempo de atención"],
        "safety": ["# incidentes A", "MTBF/MTTR"],
        "compliance": ["# hallazgos críticos", "tiempo de remediación"],
        "other": ["KPI específico del proceso", "tiempo y costo de integración"]
    }
    return PilotPlan(
        recommended=True,
        rationale="Se recomienda piloto acotado para validar supuestos críticos.",
        hypothesis=hypotheses.get(obj, hypotheses["other"]),
        success_metrics=metrics.get(obj, metrics["other"]),
        duration_weeks=8,
        dependencies=["Sponsor interno", "Acceso a datos", "Ambiente de prueba", "Criterios de éxito definidos"]
    )

# ─────────── Función principal (para Assistants API function-calling) ───────────
def rww_evaluate(payload: dict) -> str:
    """
    Ejecuta la evaluación R-W-W.
    Argumento:
        payload (dict): Objeto que cumple el schema de RWWInput.
    Retorna:
        str (JSON): RWWEvaluation serializado.
    """
    # Validación y normalización
    inp = RWWInput(**payload)

    # 1) Faltantes + preguntas
    missing, questions = _collect_missing_and_questions(inp)

    # 2) Killer questions (custom) + auto-kill
    killer_hits = [k.rationale or k.question for k in inp.killer_questions if (k.kill_if_false and not k.answer)]
    killer_hits += _auto_kill_hits(inp)

    # 3) Scoring
    total_score, pillar_scores, breakdown = _score(inp)

    # 4) Finanzas
    fin_summary = _compute_financials(inp.financials) if inp.financials else None

    # 5) Decisión
    decision, rationale = _decide(total_score, inp.thresholds, killer_hits)

    # 6) Riesgos (items parcial/unknown)
    risk_flags = [f"Riesgo en {it.pillar}.{it.item_key}: {it.status}" for it in breakdown if it.status in (TriState.partial, TriState.unknown)]

    # 7) Textos opcionales
    version_corta = None
    version_larga = None
    if inp.generate_text_summary:
        titulo = inp.metadata.idea_title if (inp.metadata and inp.metadata.idea_title) else "Idea"
        version_corta = (
            f"{titulo} — RWW={total_score}/100 → {decision}. "
            f"Real {pillar_scores[0].score_percent} | Win {pillar_scores[1].score_percent} | Worth {pillar_scores[2].score_percent}. "
            f"{'Killer activadas. ' if killer_hits else ''}"
            f"{'Faltan datos: ' + ', '.join(missing) if missing else ''}"
        )
        bullets = []
        bullets.append(f"• Score total: {total_score}/100 → **{decision}**.")
        bullets.append(f"• Real/Win/Worth: {pillar_scores[0].score_percent}/{pillar_scores[1].score_percent}/{pillar_scores[2].score_percent}.")
        if fin_summary:
            bullets.append(f"• Finanzas: Inversión={fin_summary.investment_total} {fin_summary.currency}; "
                           f"EBITDA+={fin_summary.ebitda_incremental_annual} {fin_summary.currency}/año; "
                           f"Payback={fin_summary.payback_years or 'NA'} años.")
        if killer_hits:
            bullets.append("• Killer activadas: " + " | ".join(killer_hits))
        if missing:
            bullets.append("• Datos faltantes: " + ", ".join(missing))
            bullets.append("• Próximas preguntas: " + " | ".join(questions[:5]))
        version_larga = "\n".join(bullets)

    # 8) Plan de piloto (si aplica)
    pilot = _pilot_plan(inp.metadata, decision)

    # 9) Ensamblar salida
    out = RWWEvaluation(
        timestamp=datetime.utcnow().isoformat(),
        metadata=inp.metadata,
        total_score=total_score,
        pillar_scores=pillar_scores,
        breakdown=breakdown,
        decision=decision,
        decision_rationale=rationale,
        thresholds_used=inp.thresholds,
        killer_hits=killer_hits,
        risk_flags=risk_flags,
        missing_inputs=missing,
        next_questions=questions,
        financials_summary=fin_summary,
        pilot_plan=pilot,
        version_corta=version_corta,
        version_larga=version_larga
    )
    return out.model_dump_json(exclude_none=True, ensure_ascii=False)


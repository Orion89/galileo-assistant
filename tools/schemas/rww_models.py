from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, field_validator, conint, confloat

# ─────────── Tipos básicos ───────────
class TriState(str, Enum):
    yes = "yes"
    partial = "partial"
    no = "no"
    unknown = "unknown"  # puntúa 0 y genera preguntas

class EvidenceLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"

class ItemAssessment(BaseModel):
    status: TriState = Field(default=TriState.unknown)
    justification: Optional[str] = None
    evidence_level: EvidenceLevel = Field(default=EvidenceLevel.medium)
    evidence_refs: List[str] = Field(default_factory=list)  # URLs, docs, IDs, etc.

# ─────────── Estructuras R / W / W ───────────
class RealInputs(BaseModel):
    problem_validated: ItemAssessment = Field(default_factory=ItemAssessment)
    technical_feasibility: ItemAssessment = Field(default_factory=ItemAssessment)
    buyer_identified: ItemAssessment = Field(default_factory=ItemAssessment)

class WinInputs(BaseModel):
    differentiation: ItemAssessment = Field(default_factory=ItemAssessment)
    access_sponsor_data_channel: ItemAssessment = Field(default_factory=ItemAssessment)
    internal_capabilities: ItemAssessment = Field(default_factory=ItemAssessment)

class WorthInputs(BaseModel):
    unit_economics_tco: ItemAssessment = Field(default_factory=ItemAssessment)
    impact_magnitude: ItemAssessment = Field(default_factory=ItemAssessment)
    option_value_learning: ItemAssessment = Field(default_factory=ItemAssessment)

# ─────────── Pesos y umbrales ───────────
class RWWWeights(BaseModel):
    problem_validated: confloat(ge=0) = 15.0
    technical_feasibility: confloat(ge=0) = 15.0
    buyer_identified: confloat(ge=0) = 10.0
    differentiation: confloat(ge=0) = 12.0
    access_sponsor_data_channel: confloat(ge=0) = 12.0
    internal_capabilities: confloat(ge=0) = 11.0
    unit_economics_tco: confloat(ge=0) = 9.0
    impact_magnitude: confloat(ge=0) = 8.0
    option_value_learning: confloat(ge=0) = 8.0
    @property
    def total(self) -> float:
        return (
            self.problem_validated + self.technical_feasibility + self.buyer_identified +
            self.differentiation + self.access_sponsor_data_channel + self.internal_capabilities +
            self.unit_economics_tco + self.impact_magnitude + self.option_value_learning
        )

class RWWThresholds(BaseModel):
    go_min: conint(ge=0, le=100) = 80
    pilot_min: conint(ge=0, le=100) = 65
    hold_min: conint(ge=0, le=100) = 50
    @field_validator("pilot_min")
    @classmethod
    def _check_order(cls, v, info):
        go_min = info.data.get("go_min", 80)
        hold_min = info.data.get("hold_min", 50)
        if not (hold_min < v < go_min):
            raise ValueError("Se requiere hold_min < pilot_min < go_min")
        return v

# ─────────── Finanzas (opcional) ───────────
class Financials(BaseModel):
    currency: str = Field(default="CLP")
    periodicity: Literal["annual", "monthly"] = "annual"
    capex: float = 0.0
    implementation_cost: float = 0.0
    licenses_cost: float = 0.0
    training_cost: float = 0.0
    opex_incremental: float = 0.0
    new_revenue: float = 0.0
    savings: float = 0.0
    variable_costs_new: float = 0.0

# ─────────── Killer questions y reglas ───────────
class KillerQuestion(BaseModel):
    question: str
    answer: bool
    rationale: Optional[str] = None
    kill_if_false: bool = True

class RuleConfig(BaseModel):
    auto_kill_on_no: bool = True
    treat_unknown_as_zero: bool = True
    strict_json_schema: bool = True

# ─────────── Metadatos opcionales ───────────
class IdeaMetadata(BaseModel):
    idea_title: Optional[str] = None
    one_liner: Optional[str] = None
    objective: Optional[Literal["cost_reduction","revenue_growth","cx","safety","compliance","other"]] = None
    business_unit: Optional[str] = None
    horizon: Optional[Literal["H1","H2","H3"]] = None
    okr_alignment: Optional[str] = None

# ─────────── Entrada principal ───────────
class RWWInput(BaseModel):
    metadata: Optional[IdeaMetadata] = None
    real: RealInputs = Field(default_factory=RealInputs)
    win: WinInputs = Field(default_factory=WinInputs)
    worth: WorthInputs = Field(default_factory=WorthInputs)
    weights: RWWWeights = Field(default_factory=RWWWeights)
    thresholds: RWWThresholds = Field(default_factory=RWWThresholds)
    financials: Optional[Financials] = None
    killer_questions: List[KillerQuestion] = Field(default_factory=list)
    rules: RuleConfig = Field(default_factory=RuleConfig)
    generate_text_summary: bool = True

# ─────────── Salida ───────────
class ScoreItem(BaseModel):
    pillar: Literal["real","win","worth"]
    item_key: str
    item_name: str
    weight: float
    status: TriState
    numeric_value: float
    score_contribution: float
    justification: Optional[str] = None

class PillarScore(BaseModel):
    pillar: Literal["real","win","worth"]
    max_points: float
    score_points: float
    score_percent: float

class FinancialsSummary(BaseModel):
    currency: str
    annualized: bool
    investment_total: float
    ebitda_incremental_annual: float
    payback_years: Optional[float]

class PilotPlan(BaseModel):
    recommended: bool
    rationale: str
    hypothesis: List[str]
    success_metrics: List[str]
    duration_weeks: int
    dependencies: List[str]

class RWWEvaluation(BaseModel):
    version: str = "rww-tool-1.0"
    timestamp: str
    metadata: Optional[IdeaMetadata] = None
    total_score: float
    pillar_scores: List[PillarScore]
    breakdown: List[ScoreItem]
    decision: Literal["GO","PILOT","HOLD","KILL"]
    decision_rationale: List[str]
    thresholds_used: RWWThresholds
    killer_hits: List[str]
    risk_flags: List[str]
    missing_inputs: List[str]
    next_questions: List[str]
    financials_summary: Optional[FinancialsSummary] = None
    pilot_plan: Optional[PilotPlan] = None
    version_corta: Optional[str] = None
    version_larga: Optional[str] = None


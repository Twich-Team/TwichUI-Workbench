from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from workbench.regression_evidence import BehavioralPreparation

Choice = Literal["confirm", "change"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Claim(StrictModel):
    id: str
    text: str
    source: str


class Evidence(StrictModel):
    candidate: str
    patch_sha256: str
    checks: str
    caveat: str
    claims: list[Claim]
    excerpt: str


class Decision(StrictModel):
    command_id: UUID
    version: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    choices: Annotated[dict[str, Choice], Field(min_length=1, max_length=20)]
    notes: Annotated[str, Field(max_length=1600)] = ""


class Receipt(StrictModel):
    id: str
    task_id: str
    version: str
    payload: Decision
    reviewed_evidence: Evidence
    reviewed_revision: int
    status: Literal["pending", "applied", "stale", "failed"]
    message: str
    created_at: str
    finished_at: str | None


class Task(StrictModel):
    id: str
    title: str
    summary: str
    subsystem: str
    state: Literal["held", "review_confirmed", "changes_requested"]
    revision: int
    version: str
    evidence: Evidence
    attempt_outcome: Literal["accepted"]
    verification: Literal["passed"]
    publication: Literal["not_published"]
    eligibility: Literal["contract_hold", "needs_publication_checks"]
    review_available: bool
    latest_command: Receipt | None


class Session(StrictModel):
    csrf_token: str
    mode: Literal["local_fixture"] = "local_fixture"


class Problem(StrictModel):
    detail: str


class Freshness(StrictModel):
    state: Literal['fixture', 'fresh', 'stale', 'unavailable']
    observed_at: str | None
    message: str


class AttemptSummary(StrictModel):
    outcome: str
    model: str


class CatalogRecord(StrictModel):
    scope_review_required: bool = False
    context: bool = False
    reason: str = ''
    id: str
    title: str
    parent: str | None
    kind: str
    summary: str
    state: str
    attempt_outcome: str
    verification: str
    publication: str
    subsystem: str
    observed_at: str | None
    updated_at: str | None
    date_basis: str
    currently_observed: bool
    working: bool = False
    review_task_id: str | None
    attempts: list[AttemptSummary]
    source_version: str | None
    children: int = 0
    intake_id: str | None = None
    planning_acceptance: list[str] = []
    dependencies: list[str] = []
    proposal_id: str | None = None
    planning_model: str | None = None


class CatalogMeta(StrictModel):
    local_records: bool = False
    source: Literal['fixture', 'pilot']
    freshness: Freshness
    partial: bool
    version: str


class CatalogPage(CatalogMeta):
    facets: dict[str, list[str]] = {}
    items: list[CatalogRecord]
    total: int
    page: int
    page_size: int
    parent: str | None
    filtered: bool


class CatalogDetail(CatalogMeta):
    record: CatalogRecord
    ancestors: list[CatalogRecord]
    children: list[CatalogRecord]
    children_total: int


class WorkOverview(CatalogMeta):
    current: list[CatalogRecord]
    waiting: list[CatalogRecord]
    needs_you: int
    waiting_application: int
    current_message: str


class HistoryEvent(StrictModel):
    id: str
    task_id: str
    title: str
    at: str
    date_basis: str
    outcome: str
    message: str


class HistoryPage(CatalogMeta):
    items: list[HistoryEvent]
    total: int
    page: int
    page_size: int
    message: str


class DisplaySettings(StrictModel):
    revision: Annotated[int, Field(strict=True, ge=0)]
    resource_hours: Literal[1, 24, 168]
    refresh_seconds: Literal[15, 30, 60]

    @field_validator('resource_hours', 'refresh_seconds', mode='before')
    @classmethod
    def exact_integer(cls, value):
        if type(value) is not int:
            raise ValueError('An integer is required')
        return value


class ResourcePoint(StrictModel):
    at: int
    cpu: float | None
    gpu: float | None
    ram_used: float | None
    ram_total: float | None
    vram_used: float | None
    vram_total: float | None
    gpu_device: str | None


class Resources(StrictModel):
    source: Literal['demo', 'observed']
    label: str
    start: int
    end: int
    interval_s: int
    retention_days: int
    points: list[ResourcePoint]
    latest: ResourcePoint | None
    freshness: Literal['demo', 'fresh', 'stale', 'unavailable']
    age_s: float | None
    first_at: int | None
    last_at: int | None
    message: str


class Policy(StrictModel):
    available: bool
    revision: int | None
    observed_revision: int | None
    observed_at: float | None
    acknowledgment: str
    freshness: str
    values: dict[str, bool | int]
    observed_values: dict[str, bool | int]
    message: str


class ActivityDistribution(CatalogMeta):
    counts: dict[str, int]
    basis: str
    omissions: list[str]

# Planning content remains data. None of these contracts contains execution permission.
ShortText = Annotated[str, Field(min_length=1, max_length=800)]
TextList = Annotated[list[ShortText], Field(min_length=1, max_length=12)]


VisualRequirement = Literal['visual', 'nonvisual', 'unclassified']


class HumanScope(StrictModel):
    revision: int
    visual_requirement: VisualRequirement
    stage: Literal['implementation', 'research']
    origin: Literal['human', 'legacy']
    note: str
    created_at: str


class ScopeRevisionInput(StrictModel):
    command_id: UUID
    proposal_id: UUID
    binding: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    visual_requirement: VisualRequirement
    stage: Literal['implementation', 'research']
    note: Annotated[str, Field(min_length=1, max_length=1600)]


class RegressionTextExample(StrictModel):
    input: Annotated[str, Field(max_length=512)]
    expected: Annotated[str, Field(max_length=512)]
    incorrect: Annotated[str, Field(max_length=512)]

    @model_validator(mode='after')
    def distinct_results(self):
        if self.expected == self.incorrect:
            raise ValueError('The expected and incorrect results must differ')
        return self


class RegressionTestRequest(StrictModel):
    test_paths: Annotated[list[Annotated[str, Field(max_length=240, pattern=r'^tests/test_[A-Za-z0-9_-]+\.lua$')]], Field(max_length=3)] = []
    acceptance: Annotated[list[ShortText], Field(min_length=1, max_length=8)]
    text_example: RegressionTextExample | None = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator('acceptance')
    @classmethod
    def meaningful_acceptance(cls, value):
        if any(not item.strip() for item in value):
            raise ValueError('Describe observable expected results')
        return value


class IntakeInput(StrictModel):
    visual_requirement: VisualRequirement = 'unclassified'
    request_id: UUID
    kind: Literal['issue', 'feature']
    supported_task: Annotated[str | None, Field(pattern=r'^[a-f0-9]{64}$', exclude_if=lambda value: value is None)] = None
    regression_tests: RegressionTestRequest | None = Field(default=None, exclude_if=lambda value: value is None)
    local_planning: Literal['qwen-once'] | None = Field(default=None, exclude_if=lambda value: value is None)
    title: Annotated[str, Field(min_length=1, max_length=160)]
    description: Annotated[str, Field(min_length=1, max_length=6000)]
    lua_error: Annotated[str, Field(max_length=6000)] = ''

    @field_validator('title', 'description')
    @classmethod
    def meaningful(cls, value):
        if not value.strip():
            raise ValueError('Required')
        return value  # Preserve exactly what the user supplied.


class EvidenceReference(StrictModel):
    kind: Literal['product', 'addon'] = 'product'
    source_revision: str | None = None
    id: Annotated[str, Field(min_length=1, max_length=80)]
    label: ShortText
    sha256: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    excerpt: Annotated[str, Field(max_length=2400)]


class ProposalContent(StrictModel):
    summary: ShortText
    value: ShortText
    feasibility: ShortText
    uncertainty: TextList
    evidence_ids: Annotated[list[str], Field(max_length=12)]
    behavior: TextList
    scope: TextList
    exclusions: TextList
    acceptance: TextList
    dependencies: Annotated[list[ShortText], Field(max_length=12)]
    risks: TextList
    stages: TextList
    visual: Literal['visual', 'nonvisual', 'unknown']
    delivery: Literal['implementation', 'research']


class PlanningOutput(StrictModel):
    summary: ShortText
    clarification: Annotated[str, Field(max_length=800)]
    evidence_ids: Annotated[list[str], Field(max_length=12)]
    related_ids: Annotated[list[str], Field(max_length=6)]
    proposal: ProposalContent | None


class TaskSpec(StrictModel):
    key: Annotated[str, Field(pattern=r'^[a-z][a-z0-9_-]{0,39}$')]
    kind: Literal['milestone', 'task']
    parent: str | None
    title: Annotated[str, Field(min_length=1, max_length=160)]
    purpose: ShortText
    acceptance: TextList
    depends_on: Annotated[list[str], Field(max_length=12)]
    scope_indices: Annotated[list[int], Field(min_length=1, max_length=12)]


class DecompositionOutput(StrictModel):
    tasks: Annotated[list[TaskSpec], Field(min_length=1, max_length=16)]


class TaskBudget(StrictModel):
    activate_once: bool = Field(default=False, exclude_if=lambda value: value is False)
    model_config = {'extra':'forbid'}
    offer_id: UUID
    offer_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    overall_seconds: Literal[5400] = 5400
    implementation_attempts: Literal[1] = 1
    turns: Literal[20] = 20
    generated_tokens: Literal[24000] = 24000
    response_tokens: Literal[2500] = 2500
    implementation_seconds: Literal[1200] = 1200
    complete_run_seconds: Literal[2400] = 2400
    publication_seconds: Literal[900] = 900
    publication: Literal['hold','draft-pr'] = 'hold'
    fallback: Literal[False] = False



class TaskRunApprovalInput(StrictModel):
    command_id: UUID
    scope_approval_id: UUID
    binding: str = Field(pattern=r'^[a-f0-9]{64}$')
    run_budget: TaskBudget
    note: str = Field(default='', max_length=1600)


class BrowserApprovalProvenance(StrictModel):
    actor: Literal['owner']
    purpose: Literal['browser-reviewed-offer-v1']
    command_id: UUID


class ProposalDecisionInput(StrictModel):
    expectation_review: Annotated[str | None, Field(pattern=r'^[a-f0-9]{64}$', exclude_if=lambda value: value is None)] = None
    run_budget: TaskBudget | None = Field(default=None, exclude_if=lambda value: value is None)
    scope_revision: Annotated[int | None, Field(ge=0)] = None
    command_id: UUID
    proposal_id: UUID
    binding: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    action: Literal['approve', 'changes', 'decline']
    note: Annotated[str, Field(max_length=1600)] = ''

    @field_validator('note')
    @classmethod
    def note_bound(cls, value):
        return value


class OwnerInstructionProvenance(StrictModel):
    authorization_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    authorization_path: str = Field(min_length=1, max_length=1024)
    actor: Literal['owner']
    purpose: Literal['supported-task-to-draft-pr-v1']


class ExecutionContinuationView(StrictModel):
    browser_request: dict | None = Field(default=None, exclude_if=lambda value: value is None)
    authoring_mode: Literal['explicit-current-session'] | None = Field(default=None, exclude_if=lambda value: value is None)
    scope_approval_id: UUID
    scope_binding: str = Field(pattern=r'^[a-f0-9]{64}$')
    owner_provenance: OwnerInstructionProvenance | BrowserApprovalProvenance
    continuation_id: str = Field(default='', max_length=200)


class StoredProposalDecisionPayload(ProposalDecisionInput):
    # Output only. Public commands still use ProposalDecisionInput and reject it.
    execution_continuation: ExecutionContinuationView | None = Field(default=None, exclude_if=lambda value: value is None)


class ArtifactInput(StrictModel):
    artifact_id: UUID
    proposal_id: UUID
    binding: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    role: Literal['mockup', 'game_context', 'evidence']
    label: Annotated[str, Field(min_length=1, max_length=160)]
    media_type: Literal['image/png', 'text/plain']
    data_base64: Annotated[str, Field(max_length=1400000)]


class ArtifactView(StrictModel):
    id: str
    role: str
    label: str
    sha256: str
    media_type: str
    created_at: str


class ProposedCheck(StrictModel):
    argv: list[str]
    cwd: Literal['.', 'tests']
    purpose: str
    status: Literal['not_run'] = 'not_run'


class PreparationEvidence(StrictModel):
    path: str
    sha256: str
    git_blob: str


class VerificationProfileSelection(StrictModel):
    id: Literal['lua-regression-tests-v1', 'lua-offline-bugfix-v1']
    requirements: list[str]
    status: Literal['not_run'] = 'not_run'
    execution_authorized: Literal[False] = False


class ScenarioModelSelection(StrictModel):
    policy_revision: int = Field(ge=1)
    model: str = Field(max_length=160, pattern=r'^[A-Za-z0-9_.:/-]+$')


class ScenarioGrounding(StrictModel):
    evidence_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    policy_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    basis: Literal['demo', 'local']
    model_selection: ScenarioModelSelection | None = Field(default=None, exclude_if=lambda value: value is None)


class RegressionTaskContract(StrictModel):
    scenario_grounding: ScenarioGrounding | None = Field(default=None, exclude_if=lambda value: value is None)
    behavioral_evidence: BehavioralPreparation | None = Field(default=None, exclude_if=lambda value: value is None)
    verification_profile: VerificationProfileSelection | None = Field(default=None, exclude_if=lambda value: value is None)
    category: Literal['lua-regression-tests', 'lua-offline-bugfix'] = 'lua-regression-tests'
    version: Literal[1] = 1
    repository: Literal['Twich-Team/TwichUI'] = 'Twich-Team/TwichUI'
    base_ref: Literal['HEAD'] = 'HEAD'
    resolved_base: str | None
    intended_behavior: str
    acceptance: list[str]
    review_feedback: str
    writable_paths: list[str]
    path_candidates: list[str]
    change_policy: Literal['additions-only-existing-test-files', 'bounded-existing-lua-files'] = 'additions-only-existing-test-files'
    verification_commands: list[ProposedCheck]
    independent_checks: list[str]
    evidence: list[PreparationEvidence]
    resource_limits: dict[str, int]
    pr_repository: Literal['Twich-Team/TwichUI'] = 'Twich-Team/TwichUI'
    pr_base_branch: Literal['main'] = 'main'
    pr_draft: Literal[True] = True
    scope_missing: list[str]
    execution_blockers: list[str]
    execution_authorized: Literal[False] = False
    publication_authorized: Literal[False] = False


class ProposalView(StrictModel):
    task_contract: RegressionTaskContract | None = Field(default=None, exclude_if=lambda value: value is None)
    scope_revision: int
    human_scope: HumanScope
    required_artifacts: list[str]
    scope_conflicts: list[str]
    research_only: bool
    id: str
    revision: int
    content: ProposalContent
    model: str
    basis: Literal['demo', 'local', 'owner', 'template', 'preparation']
    created_at: str
    artifacts: list[ArtifactView]
    binding: str
    approval_blockers: list[str]


class PlanningJobView(StrictModel):
    diagnostics: dict[str, str | int | float] = {}
    id: str
    kind: Literal['planning', 'decomposition', 'behavioral_preparation']
    revision: int
    status: Literal['queued', 'running', 'completed', 'failed']
    attempts: int
    message: str
    model: str | None
    created_at: str
    finished_at: str | None


class ProposalReceipt(StrictModel):
    scope_revision: int | None
    required_artifacts: list[str] | None
    scope_review_required: bool
    id: str
    proposal_id: str
    binding: str
    payload: StoredProposalDecisionPayload
    status: Literal['pending', 'applied', 'stale', 'rejected', 'failed']
    message: str
    created_at: str
    finished_at: str | None


class PriorityView(StrictModel):
    severity: str
    priority: int
    reason: str
    execution: str
    manual: bool


class PlannedTaskView(StrictModel):
    scope_review_required: bool
    id: str
    parent: str | None
    kind: str
    title: str
    purpose: str
    acceptance: list[str]
    proposal_id: str | None
    model: str
    dependencies: list[str]


class ClarificationView(StrictModel):
    text: str
    created_at: str
    revision: int


class IntakeView(StrictModel):
    human_scope: HumanScope
    scope_history: list[HumanScope]
    id: str
    original: IntakeInput
    clarifications: list[ClarificationView]
    status: Literal['queued', 'investigating', 'completed', 'waiting_for_information', 'failed', 'approved', 'tasks_ready', 'decomposition_failed', 'declined', 'scope_review_required']
    created_at: str
    revision: int
    jobs: list[PlanningJobView]
    proposals: list[ProposalView]
    decisions: list[ProposalReceipt]
    result: PlanningOutput | None
    evidence: list[EvidenceReference]
    tasks: list[PlannedTaskView]
    priority: PriorityView


class IntakePage(StrictModel):
    items: list[IntakeView]
    total: int
    page: int
    planner: str


class PlanningRetry(StrictModel):
    job_id: UUID


class ClarificationInput(StrictModel):
    command_id: UUID
    revision: Annotated[int, Field(ge=0)]
    text: Annotated[str, Field(min_length=1, max_length=1600)]


class ResearchRevision(StrictModel):
    binding: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    explanation: Annotated[str, Field(min_length=1, max_length=1600)]


class PriorityInput(StrictModel):
    priority: Annotated[int | None, Field(ge=1, le=5)]

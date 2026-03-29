from datetime import datetime
from enum import Enum
from uuid import uuid4
from pydantic import BaseModel, Field
from src.models.plan import MergePhase


class AgentType(str, Enum):
    PLANNER = "planner"
    PLANNER_JUDGE = "planner_judge"
    CONFLICT_ANALYST = "conflict_analyst"
    EXECUTOR = "executor"
    JUDGE = "judge"
    HUMAN_INTERFACE = "human_interface"
    ORCHESTRATOR = "orchestrator"
    BROADCAST = "broadcast"


class MessageType(str, Enum):
    INFO = "info"
    REQUEST = "request"
    RESPONSE = "response"
    STATE_UPDATE = "state_update"
    ERROR = "error"
    PHASE_COMPLETED = "phase_completed"
    HUMAN_INPUT_NEEDED = "human_input_needed"
    HUMAN_INPUT_RECEIVED = "human_input_received"


class AgentMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: str(uuid4()))
    sender: AgentType
    receiver: AgentType
    phase: MergePhase
    message_type: MessageType
    subject: str
    payload: dict = Field(default_factory=dict)
    correlation_id: str | None = None
    priority: int = Field(default=5, ge=1, le=10)
    timestamp: datetime = Field(default_factory=datetime.now)
    is_processed: bool = False
    processing_error: str | None = None

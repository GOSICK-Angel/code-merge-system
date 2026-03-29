import yaml
from pathlib import Path
from src.agents.base_agent import BaseAgent
from src.models.config import AgentLLMConfig
from src.models.message import AgentType, AgentMessage, MessageType
from src.models.plan import MergePhase
from src.models.decision import MergeDecision
from src.models.human import HumanDecisionRequest
from src.models.state import MergeState
from src.tools.report_writer import write_human_decision_report


class HumanInterfaceAgent(BaseAgent):
    agent_type = AgentType.HUMAN_INTERFACE

    def __init__(self, llm_config: AgentLLMConfig):
        super().__init__(llm_config)

    async def run(self, state: MergeState) -> AgentMessage:
        pending = [
            req for req in state.human_decision_requests.values()
            if req.human_decision is None
        ]

        return AgentMessage(
            sender=AgentType.HUMAN_INTERFACE,
            receiver=AgentType.ORCHESTRATOR,
            phase=MergePhase.HUMAN_REVIEW,
            message_type=MessageType.HUMAN_INPUT_NEEDED,
            subject=f"{len(pending)} decisions awaiting human input",
            payload={"pending_count": len(pending)},
        )

    async def generate_report(
        self,
        requests: list[HumanDecisionRequest],
        output_path: str,
    ) -> str:
        from src.models.state import MergeState, SystemStatus
        from src.models.config import MergeConfig

        report_lines = [
            "# Human Decision Report",
            "",
        ]

        for req in requests:
            rec_val = req.analyst_recommendation.value if hasattr(req.analyst_recommendation, "value") else req.analyst_recommendation
            report_lines += [
                f"## {req.file_path} (priority={req.priority})",
                "",
                f"**Context**: {req.context_summary}",
                f"**Upstream**: {req.upstream_change_summary}",
                f"**Fork**: {req.fork_change_summary}",
                f"**Recommendation**: {rec_val} (confidence={req.analyst_confidence:.2f})",
                f"**Rationale**: {req.analyst_rationale}",
                "",
                "### Options",
            ]
            for opt in req.options:
                opt_dec = opt.decision.value if hasattr(opt.decision, "value") else opt.decision
                report_lines.append(f"- **{opt.option_key}** (`{opt_dec}`): {opt.description}")
            report_lines.append("")

        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text("\n".join(report_lines), encoding="utf-8")
        return str(output_file)

    async def collect_decisions_cli(
        self,
        requests: list[HumanDecisionRequest],
    ) -> list[HumanDecisionRequest]:
        from datetime import datetime
        results = list(requests)

        for req in results:
            if req.human_decision is not None:
                continue

            print(f"\n{'='*60}")
            print(f"File: {req.file_path}")
            print(f"Context: {req.context_summary}")
            print(f"\nOptions:")
            for opt in req.options:
                opt_dec = opt.decision.value if hasattr(opt.decision, "value") else opt.decision
                print(f"  {opt.option_key}: {opt_dec} - {opt.description}")

            print("\nEnter option key (or press Enter to skip):", end=" ")
            try:
                user_input = input().strip()
            except (EOFError, KeyboardInterrupt):
                print("\nSkipping remaining decisions.")
                break

            if not user_input:
                continue

            selected_option = next(
                (opt for opt in req.options if opt.option_key.lower() == user_input.lower()),
                None,
            )

            if selected_option is None:
                print(f"Unknown option '{user_input}', skipping.")
                continue

            if not self.validate_decision_option(req, selected_option.option_key):
                continue

            custom_content: str | None = None
            if selected_option.decision == MergeDecision.MANUAL_PATCH:
                print("Enter custom content (end with '---END---' on a new line):")
                lines: list[str] = []
                try:
                    while True:
                        line = input()
                        if line == "---END---":
                            break
                        lines.append(line)
                except (EOFError, KeyboardInterrupt):
                    pass
                custom_content = "\n".join(lines)

            print("Enter reviewer name (optional):", end=" ")
            try:
                reviewer_name = input().strip() or None
            except (EOFError, KeyboardInterrupt):
                reviewer_name = None

            print("Enter notes (optional):", end=" ")
            try:
                reviewer_notes = input().strip() or None
            except (EOFError, KeyboardInterrupt):
                reviewer_notes = None

            updated_req = req.model_copy(
                update={
                    "human_decision": selected_option.decision,
                    "custom_content": custom_content,
                    "reviewer_name": reviewer_name,
                    "reviewer_notes": reviewer_notes,
                    "decided_at": datetime.now(),
                }
            )
            idx = results.index(req)
            results[idx] = updated_req

        return results

    async def collect_decisions_file(
        self,
        yaml_path: str,
        requests: list[HumanDecisionRequest],
    ) -> list[HumanDecisionRequest]:
        from datetime import datetime
        decisions_file = Path(yaml_path)
        if not decisions_file.exists():
            raise FileNotFoundError(f"Decisions file not found: {yaml_path}")

        raw = yaml.safe_load(decisions_file.read_text(encoding="utf-8"))
        decisions_map: dict[str, dict] = {
            item["file_path"]: item
            for item in (raw.get("decisions", []) if isinstance(raw, dict) else [])
        }

        results = list(requests)
        for i, req in enumerate(results):
            decision_data = decisions_map.get(req.file_path)
            if not decision_data:
                continue

            decision_raw = decision_data.get("decision")
            if not decision_raw:
                continue

            try:
                decision = MergeDecision(decision_raw)
            except ValueError:
                self.logger.warning(f"Invalid decision value '{decision_raw}' for {req.file_path}")
                continue

            if not self._validate_decision_value(decision):
                continue

            custom_content = decision_data.get("custom_content")
            if decision == MergeDecision.MANUAL_PATCH and not custom_content:
                self.logger.warning(f"MANUAL_PATCH for {req.file_path} has no custom_content, skipping")
                continue

            results[i] = req.model_copy(
                update={
                    "human_decision": decision,
                    "custom_content": custom_content,
                    "reviewer_name": decision_data.get("reviewer_name"),
                    "reviewer_notes": decision_data.get("reviewer_notes"),
                    "decided_at": datetime.now(),
                }
            )

        return results

    def validate_decision(self, request: HumanDecisionRequest) -> bool:
        if request.human_decision is None:
            return False
        if not self._validate_decision_value(request.human_decision):
            return False
        if request.human_decision == MergeDecision.MANUAL_PATCH and not request.custom_content:
            return False
        return True

    def validate_decision_option(self, request: HumanDecisionRequest, option_key: str) -> bool:
        option = next((opt for opt in request.options if opt.option_key == option_key), None)
        if option is None:
            return False
        return self._validate_decision_value(option.decision)

    def _validate_decision_value(self, decision: MergeDecision) -> bool:
        if decision == MergeDecision.ESCALATE_HUMAN:
            self.logger.warning(
                "ESCALATE_HUMAN cannot be used as a human decision — it would cause a loop"
            )
            return False
        return True

    def can_handle(self, state: MergeState) -> bool:
        from src.models.state import SystemStatus
        return state.status == SystemStatus.AWAITING_HUMAN

"""WebSocket bridge between Orchestrator state and Web UI clients."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Literal

import websockets
from websockets.asyncio.server import Server, ServerConnection

from src.core.phases.base import ActivityEvent
from src.models.config import MergeConfig
from src.models.decision import MergeDecision
from src.models.plan_review import PlanHumanDecision, PlanHumanReview
from src.models.setup import SetupPayload
from src.models.state import MergeState
from src.web.serializers import serialize_state

BridgeMode = Literal["run", "setup"]

logger = logging.getLogger(__name__)


class MergeWSBridge:
    """Bridges MergeState changes to WebSocket Web UI clients."""

    DEBOUNCE_SECONDS = 0.3
    ACTIVITY_BUFFER_MAX = 200

    def __init__(
        self,
        state: MergeState | None = None,
        mode: BridgeMode = "run",
        repo_path: str = ".",
    ) -> None:
        if mode == "run" and state is None:
            raise ValueError("MergeWSBridge(mode='run') requires a MergeState")
        self._state: MergeState | None = state
        self._mode: BridgeMode = mode
        self._repo_path: str = repo_path
        self._clients: set[ServerConnection] = set()
        self._server: Server | None = None
        if state is not None:
            self._last_status: str = (
                state.status.value
                if hasattr(state.status, "value")
                else str(state.status)
            )
        else:
            self._last_status = "setup"
        self._debounce_handle: asyncio.TimerHandle | None = None
        self._pending_broadcast: bool = False
        self._last_snapshot_hash: str = ""
        self._client_connected: asyncio.Event = asyncio.Event()
        self._plan_review_received: asyncio.Event = asyncio.Event()
        self._human_decisions_received: asyncio.Event = asyncio.Event()
        self._judge_resolution_received: asyncio.Event = asyncio.Event()
        self._cancel_event: asyncio.Event = asyncio.Event()
        self._setup_complete: asyncio.Event = asyncio.Event()
        self._setup_result: MergeConfig | None = None
        self._setup_payload: SetupPayload | None = None
        self._activity_buffer: list[dict[str, Any]] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def mode(self) -> BridgeMode:
        return self._mode

    @property
    def last_setup_payload(self) -> SetupPayload | None:
        """The SetupPayload most recently accepted via ``setup.submit``.

        The orchestrator launcher reads ``dry_run`` / ``workflow`` /
        ``init_forks_profile`` off this payload — they're session-scoped
        runtime hints that are *not* persisted to ``.merge/config.yaml``,
        so the bridge is the only place they live after submit."""
        return self._setup_payload

    async def start(self, host: str = "localhost", port: int = 8765) -> None:
        self._loop = asyncio.get_running_loop()
        self._server = await websockets.serve(
            self._handler,
            host,
            port,
        )
        logger.info("WebSocket bridge listening on ws://%s:%d", host, port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def wait_for_client(self, timeout: float = 30.0) -> bool:
        """Block until at least one Web UI client connects."""
        try:
            await asyncio.wait_for(self._client_connected.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def wait_for_plan_review(self) -> None:
        """Block until a plan review decision arrives from the Web UI."""
        await self._plan_review_received.wait()
        self._plan_review_received.clear()

    async def wait_for_human_decisions(self) -> None:
        """Block until all pending conflict decisions are submitted from the Web UI."""
        await self._human_decisions_received.wait()
        self._human_decisions_received.clear()

    async def wait_for_setup(self) -> MergeConfig:
        """Block until the Web UI submits a valid ``setup.submit`` payload.

        Only meaningful when the bridge was constructed with
        ``mode="setup"``. The returned ``MergeConfig`` has already been
        written to ``.merge/config.yaml`` by ``apply_setup_payload``;
        the caller is responsible for constructing a ``MergeState`` and
        calling ``transition_to_run`` so connected clients flip from
        the Setup view to the Dashboard.
        """
        if self._mode != "setup":
            raise RuntimeError("wait_for_setup is only valid in setup mode")
        await self._setup_complete.wait()
        if self._setup_result is None:  # pragma: no cover - defensive
            raise RuntimeError("setup_complete fired without a result")
        return self._setup_result

    async def transition_to_run(self, state: MergeState) -> None:
        """Promote a setup-mode bridge to run mode and push the first snapshot.

        Idempotent on the state slot — calling it twice with the same
        state is a no-op beyond a second snapshot push (clients dedupe
        via ``_last_snapshot_hash`` anyway). Raises if the bridge was
        never in setup mode, since the run-mode startup path already
        owns its own state.
        """
        if self._mode != "setup":
            raise RuntimeError("transition_to_run only valid from setup mode")
        self._state = state
        self._mode = "run"
        self._last_status = (
            state.status.value if hasattr(state.status, "value") else str(state.status)
        )
        await self.broadcast_state_patch()

    async def wait_for_judge_resolution(self) -> None:
        """Block until an L4 judge resolution arrives from the Web UI.

        Uses a dedicated event so plan-review and judge-resolution
        signals don't share a waker — the run loop in
        ``cli/commands/web.py`` picks the right ``wait_for_*`` call
        based on which gate parked the state (judge_verdict pending vs
        pending_user_decisions vs human_decision_requests)."""
        await self._judge_resolution_received.wait()
        self._judge_resolution_received.clear()

    def is_cancelled(self) -> bool:
        """Return True if a ``cancel_run`` command was accepted at an
        ``AWAITING_HUMAN`` gate. The caller (``_run_web`` loop) reads this
        between phase runs to decide whether to stop the orchestrator."""
        return self._cancel_event.is_set()

    async def _handler(self, ws: ServerConnection) -> None:
        self._clients.add(ws)
        self._client_connected.set()
        logger.info("Web UI client connected (%d total)", len(self._clients))
        try:
            await self._send_snapshot(ws)
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                    await self._handle_command(ws, msg)
                except json.JSONDecodeError:
                    pass
        except websockets.exceptions.ConnectionClosed:
            logger.debug("Client disconnected (remote=%s)", ws.remote_address)
        finally:
            self._clients.discard(ws)
            logger.info("Web UI client disconnected (%d remaining)", len(self._clients))

    async def _send_snapshot(self, ws: ServerConnection) -> None:
        if self._mode == "setup":
            await ws.send(
                json.dumps(
                    {
                        "type": "setup_snapshot",
                        "payload": self._serialize_setup_context(),
                    },
                    default=str,
                )
            )
            return

        snapshot = self._serialize_state()
        await ws.send(
            json.dumps(
                {
                    "type": "state_snapshot",
                    "payload": snapshot,
                },
                default=str,
            )
        )
        if self._activity_buffer:
            await ws.send(
                json.dumps(
                    {
                        "type": "agent_activity_replay",
                        "payload": {"events": list(self._activity_buffer)},
                    }
                )
            )

    def _serialize_state(self) -> dict[str, Any]:
        assert self._state is not None, "run mode requires a state"
        return serialize_state(self._state)

    def _serialize_setup_context(self) -> dict[str, Any]:
        """Lazy import keeps ``cli.commands.setup`` out of bridge bootstrap.

        ``setup.py`` pulls in Rich + git subprocesses; importing it at
        module load would inflate WS bridge startup for every run mode
        consumer too. The detect call itself runs git twice (current
        branch + ``remote show origin``) so we keep it on the request
        path — clients can re-fetch on reconnect to pick up changes."""
        from src.cli.commands.setup import detect_setup_context

        context = detect_setup_context(self._repo_path)
        return context.model_dump()

    async def _handle_command(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        cmd_type = msg.get("type", "")
        payload = msg.get("payload", {})

        if cmd_type == "setup.detect":
            await self._handle_setup_detect(ws)
            return

        if cmd_type == "setup.submit":
            await self._handle_setup_submit(ws, payload)
            return

        if cmd_type == "setup.test_connection":
            await self._handle_setup_test_connection(ws, payload)
            return

        if self._mode == "setup":
            # All other commands require a run-mode state. Reject loudly so
            # a front-end that misroutes (e.g. submits a plan decision
            # before setup completed) sees the error instead of a silent
            # drop.
            await ws.send(
                json.dumps(
                    {
                        "type": "command_error",
                        "payload": {
                            "reason": "setup_required",
                            "command": cmd_type,
                        },
                    }
                )
            )
            return

        if cmd_type == "submit_decision":
            # Accept both camelCase ``filePath`` (legacy) and snake_case
            # ``file_path`` so the wire format aligns with the pydantic
            # model naming over time. Optional fields stay None when
            # absent — see _apply_decision for the persistence rules.
            self._apply_decision(
                file_path=payload.get("file_path") or payload.get("filePath", ""),
                decision=payload.get("decision", ""),
                reviewer_notes=payload.get("reviewer_notes"),
                custom_content=payload.get("custom_content"),
            )
            await self.broadcast_state_patch()

        elif cmd_type == "submit_conflict_decisions_batch":
            self._apply_conflict_decisions_batch(payload.get("items", []))
            await self.broadcast_state_patch()

        elif cmd_type == "submit_plan_review":
            self._apply_plan_review(payload)
            await self.broadcast_state_patch()

        elif cmd_type == "submit_user_plan_decisions":
            self._apply_user_plan_decisions(payload.get("items", []))
            await self.broadcast_state_patch()

        elif cmd_type == "submit_judge_resolution":
            self._apply_judge_resolution(payload.get("resolution", ""))
            await self.broadcast_state_patch()

        elif cmd_type == "cancel_run":
            await self._handle_cancel_run(ws)

        elif cmd_type == "pause":
            logger.info("Pause requested by client")

        elif cmd_type == "resume":
            logger.info("Resume requested by client")

    async def _handle_setup_detect(self, ws: ServerConnection) -> None:
        """Re-send the setup context (current branch, key hints, ...) on demand.

        Clients also get this in the initial ``setup_snapshot``; the
        explicit ``setup.detect`` round-trip exists so the UI can
        refresh after the user changes the target branch field (which
        in turn changes the fork-divergence count)."""
        if self._mode != "setup":
            await ws.send(
                json.dumps(
                    {
                        "type": "command_error",
                        "payload": {
                            "reason": "not_in_setup_mode",
                            "command": "setup.detect",
                        },
                    }
                )
            )
            return
        await ws.send(
            json.dumps(
                {
                    "type": "setup_snapshot",
                    "payload": self._serialize_setup_context(),
                }
            )
        )

    async def _handle_setup_submit(
        self, ws: ServerConnection, payload: dict[str, Any]
    ) -> None:
        """Validate + persist a setup form and signal ``wait_for_setup``.

        Reject duplicates / wrong-mode invocations with a
        ``setup_error`` frame so the UI can keep the form state and
        let the user retry. A successful submit fires ``setup_ready``
        carrying the resolved config path so the front-end can show
        "config saved to …" while it waits for the orchestrator
        launcher to flip the bridge into run mode."""
        if self._mode != "setup":
            await ws.send(
                json.dumps(
                    {
                        "type": "setup_error",
                        "payload": {"reason": "not_in_setup_mode"},
                    }
                )
            )
            return
        if self._setup_complete.is_set():
            await ws.send(
                json.dumps(
                    {
                        "type": "setup_error",
                        "payload": {"reason": "already_submitted"},
                    }
                )
            )
            return

        try:
            setup_payload = SetupPayload.model_validate(payload)
        except Exception as e:  # pydantic ValidationError or unexpected
            logger.warning("setup.submit validation failed: %s", e)
            await ws.send(
                json.dumps(
                    {
                        "type": "setup_error",
                        "payload": {
                            "reason": "invalid_payload",
                            "details": str(e),
                        },
                    }
                )
            )
            return

        from src.cli.commands.setup import apply_setup_payload
        from src.cli.paths import get_config_path

        try:
            config = apply_setup_payload(setup_payload, self._repo_path)
        except Exception as e:
            logger.exception("setup.submit apply failed")
            await ws.send(
                json.dumps(
                    {
                        "type": "setup_error",
                        "payload": {
                            "reason": "apply_failed",
                            "details": str(e),
                        },
                    }
                )
            )
            return

        self._setup_result = config
        self._setup_payload = setup_payload
        self._setup_complete.set()

        await ws.send(
            json.dumps(
                {
                    "type": "setup_ready",
                    "payload": {
                        "config_path": str(get_config_path(self._repo_path)),
                        "dry_run": setup_payload.dry_run,
                        "workflow": setup_payload.workflow,
                        "init_forks_profile": setup_payload.init_forks_profile,
                    },
                }
            )
        )
        logger.info(
            "setup.submit applied — config persisted, awaiting orchestrator launch"
        )

    async def _handle_setup_test_connection(
        self, ws: ServerConnection, payload: dict[str, Any]
    ) -> None:
        """Probe a provider's models with the supplied (or on-disk) creds.

        Blank ``api_key`` / ``base_url`` in the payload fall back to the
        resolved env chain (shell > project ``.env`` > global ``.env``) so
        the test mirrors what a run would actually pick up. Replies with a
        ``setup_test_result`` frame carrying a per-model verdict, or a
        provider-level ``error`` when no key can be resolved."""
        if self._mode != "setup":
            await ws.send(
                json.dumps(
                    {
                        "type": "command_error",
                        "payload": {
                            "reason": "not_in_setup_mode",
                            "command": "setup.test_connection",
                        },
                    }
                )
            )
            return

        provider = payload.get("provider", "")
        if provider not in ("anthropic", "openai"):
            await ws.send(
                json.dumps(
                    {
                        "type": "setup_test_result",
                        "payload": {
                            "provider": provider,
                            "error": f"unknown provider {provider!r}",
                            "results": [],
                        },
                    }
                )
            )
            return

        from src.cli.commands.setup import (
            PROVIDER_API_KEY_ENV,
            PROVIDER_BASE_URL_ENV,
            _resolve_env_value,
        )
        from src.llm.connectivity import probe_provider

        models = [m for m in payload.get("models", []) if isinstance(m, str) and m]
        api_key = (payload.get("api_key") or "").strip() or _resolve_env_value(
            PROVIDER_API_KEY_ENV[provider], self._repo_path
        )
        base_url = (payload.get("base_url") or "").strip() or _resolve_env_value(
            PROVIDER_BASE_URL_ENV[provider], self._repo_path
        )

        if not api_key:
            await ws.send(
                json.dumps(
                    {
                        "type": "setup_test_result",
                        "payload": {
                            "provider": provider,
                            "error": "no API key supplied and none found on disk",
                            "results": [],
                        },
                    }
                )
            )
            return
        if not models:
            await ws.send(
                json.dumps(
                    {
                        "type": "setup_test_result",
                        "payload": {
                            "provider": provider,
                            "error": "no models listed to test",
                            "results": [],
                        },
                    }
                )
            )
            return

        probes = await probe_provider(provider, models, api_key, base_url)
        await ws.send(
            json.dumps(
                {
                    "type": "setup_test_result",
                    "payload": {
                        "provider": provider,
                        "error": None,
                        "results": [
                            {
                                "model": p.model,
                                "ok": p.ok,
                                "latency_ms": p.latency_ms,
                                "detail": p.detail,
                            }
                            for p in probes
                        ],
                    },
                }
            )
        )
        logger.info(
            "setup.test_connection probed %d %s model(s)", len(probes), provider
        )

    async def _handle_cancel_run(self, ws: ServerConnection) -> None:
        """Cancel only takes effect when the run is parked at
        ``AWAITING_HUMAN`` — that's the only point where the orchestrator
        loop yields control back to ``_run_web``. Outside that gate we
        reply with a ``cancel_error`` frame so the UI can surface a
        tooltip / disabled-button state."""
        assert self._state is not None, "cancel_run unreachable in setup mode"
        status = self._state.status
        status_val = status.value if hasattr(status, "value") else str(status)
        if status_val != "awaiting_human":
            await ws.send(
                json.dumps(
                    {
                        "type": "cancel_error",
                        "payload": {
                            "reason": "not_in_human_gate",
                            "current_status": status_val,
                        },
                    }
                )
            )
            logger.info(
                "cancel_run rejected: status=%s (not in human gate)", status_val
            )
            return

        self._cancel_event.set()
        # Wake up any waiter parked on plan / conflict / judge events so
        # the run loop can re-check ``is_cancelled()`` and break out
        # cleanly regardless of which gate parked the state.
        self._plan_review_received.set()
        self._human_decisions_received.set()
        self._judge_resolution_received.set()
        logger.info("cancel_run accepted at AWAITING_HUMAN gate")

    def _apply_decision(
        self,
        file_path: str,
        decision: str,
        reviewer_notes: str | None = None,
        custom_content: str | None = None,
    ) -> None:
        """Persist a single conflict decision.

        ``reviewer_notes`` and ``custom_content`` are the L3 free-form
        fields. They land on ``HumanDecisionRequest`` so downstream
        consumers (``executor_agent`` reads ``request.custom_content``
        for the MANUAL_PATCH apply path; ``request.reviewer_notes``
        feeds ``FileDecisionRecord.rationale``) see the user's input.
        Missing values stay ``None`` for backwards compatibility with
        clients that only send ``{file_path, decision}``.
        """
        assert self._state is not None, "submit_decision unreachable in setup mode"
        req = self._state.human_decision_requests.get(file_path)
        if req is None:
            return
        try:
            merge_decision = MergeDecision(decision)
        except ValueError:
            return

        update: dict[str, Any] = {"human_decision": merge_decision}
        if reviewer_notes is not None:
            update["reviewer_notes"] = reviewer_notes
        if custom_content is not None:
            update["custom_content"] = custom_content
        updated = req.model_copy(update=update)
        self._state.human_decision_requests[file_path] = updated
        self._state.human_decisions[file_path] = merge_decision
        logger.info("Web decision: %s -> %s", file_path, decision)

        all_decided = all(
            r.human_decision is not None
            for r in self._state.human_decision_requests.values()
        )
        if all_decided:
            self._human_decisions_received.set()
            logger.info(
                "All human conflict decisions received — signalling orchestrator"
            )

    def _apply_conflict_decisions_batch(self, items: list[dict[str, Any]]) -> None:
        assert self._state is not None, "batch decision unreachable in setup mode"
        applied = 0
        for entry in items:
            file_path = entry.get("file_path") or entry.get("filePath", "")
            decision = entry.get("decision", "")
            if not file_path or not decision:
                continue
            req = self._state.human_decision_requests.get(file_path)
            if req is None:
                continue
            try:
                merge_decision = MergeDecision(decision)
            except ValueError:
                logger.warning(
                    "Skipping invalid decision %r for %s", decision, file_path
                )
                continue
            update: dict[str, Any] = {"human_decision": merge_decision}
            reviewer_notes = entry.get("reviewer_notes")
            custom_content = entry.get("custom_content")
            if reviewer_notes is not None:
                update["reviewer_notes"] = reviewer_notes
            if custom_content is not None:
                update["custom_content"] = custom_content
            updated = req.model_copy(update=update)
            self._state.human_decision_requests[file_path] = updated
            self._state.human_decisions[file_path] = merge_decision
            applied += 1

        logger.info("Web batch conflict decisions: %d/%d applied", applied, len(items))

        all_decided = bool(self._state.human_decision_requests) and all(
            r.human_decision is not None
            for r in self._state.human_decision_requests.values()
        )
        if all_decided:
            self._human_decisions_received.set()
            logger.info(
                "All human conflict decisions received (batch) — signalling orchestrator"
            )

    def _apply_plan_review(self, payload: Any) -> None:
        assert self._state is not None, "plan_review unreachable in setup mode"
        if isinstance(payload, str):
            decision_str = payload
            notes = None
        else:
            decision_str = payload.get("decision", "")
            notes = payload.get("notes")

        decision_map = {
            "approve": PlanHumanDecision.APPROVE,
            "reject": PlanHumanDecision.REJECT,
            "modify": PlanHumanDecision.MODIFY,
        }
        pd = decision_map.get(decision_str)
        if pd is None:
            return

        self._state.plan_human_review = PlanHumanReview(
            decision=pd,
            reviewer_name="web_user",
            reviewer_notes=notes,
            item_decisions=list(self._state.pending_user_decisions),
            decided_at=datetime.now(),
        )
        self._plan_review_received.set()
        logger.info("Web UI plan review decision: %s", decision_str)

    def _apply_user_plan_decisions(self, items: list[dict[str, Any]]) -> None:
        assert self._state is not None, "user_plan_decisions unreachable in setup mode"
        item_map = {item.item_id: item for item in self._state.pending_user_decisions}
        for item_data in items:
            item_id = item_data.get("item_id", "")
            if item_id not in item_map:
                continue
            existing = item_map[item_id]
            user_choice = item_data.get("user_choice")
            user_input = item_data.get("user_input")
            # Reviewer's free-text travels in three slots depending on
            # the chosen option's kind:
            #   * ``llm_with_instruction`` → also surfaced as
            #     ``custom_instruction`` (per-file LLM hint).
            #   * ``manual_paste`` → also surfaced as
            #     ``manual_resolution`` (verbatim final file content).
            #   * any other kind → ``user_input`` stays a passive note.
            # The frontend can fill these slots explicitly; when it
            # doesn't, the bridge infers from user_input + the chosen
            # option key.
            custom_instruction = item_data.get("custom_instruction")
            if (
                custom_instruction is None
                and user_choice == "llm_with_instruction"
                and isinstance(user_input, str)
                and user_input.strip()
            ):
                custom_instruction = user_input
            manual_resolution = item_data.get("manual_resolution")
            if (
                manual_resolution is None
                and user_choice == "manual_paste"
                and isinstance(user_input, str)
                and user_input.strip()
            ):
                manual_resolution = user_input
            updated = existing.model_copy(
                update={
                    "user_choice": user_choice,
                    "user_input": user_input,
                    "custom_instruction": custom_instruction,
                    "manual_resolution": manual_resolution,
                }
            )
            idx = next(
                i
                for i, it in enumerate(self._state.pending_user_decisions)
                if it.item_id == item_id
            )
            self._state.pending_user_decisions[idx] = updated
        logger.info("Web UI user plan decisions received: %d items", len(items))

        self._state.plan_human_review = PlanHumanReview(
            decision=PlanHumanDecision.APPROVE,
            reviewer_name="web_user",
            reviewer_notes=None,
            item_decisions=list(self._state.pending_user_decisions),
            decided_at=datetime.now(),
        )
        self._plan_review_received.set()
        logger.info("User plan decisions applied — signalling orchestrator")

    def _apply_judge_resolution(self, resolution: str) -> None:
        """L4 — write ``state.judge_resolution`` so ``human_review`` phase
        can route to accept / abort / rerun on its next pass.

        Wakes the dedicated ``_judge_resolution_received`` event so the
        run loop in ``cli/commands/web.py`` can pick this gate
        independently from plan-review and conflict decisions. Keeping
        the three signals separate preserves the invariant that each
        ``wait_for_*`` corresponds to exactly one gate kind.
        """
        assert self._state is not None, "judge_resolution unreachable in setup mode"
        if resolution not in {"accept", "abort", "rerun"}:
            logger.warning("Ignoring invalid judge resolution %r", resolution)
            return
        self._state.judge_resolution = resolution  # type: ignore[assignment]
        self._judge_resolution_received.set()
        logger.info("Judge resolution recorded: %s", resolution)

    async def broadcast_state_patch(self) -> None:
        """Send full state to all connected clients, skipping if unchanged."""
        if not self._clients:
            return
        if self._mode == "setup":
            # No state to serialize yet — callers (orchestrator observers
            # / activity hooks) fire blindly during the setup-mode window,
            # so just no-op rather than crashing on a None state.
            return
        data = json.dumps(
            {
                "type": "state_snapshot",
                "payload": self._serialize_state(),
            },
            default=str,
        )
        data_hash = hashlib.md5(data.encode()).hexdigest()
        if data_hash == self._last_snapshot_hash:
            return
        self._last_snapshot_hash = data_hash
        results = await asyncio.gather(
            *(ws.send(data) for ws in list(self._clients)),
            return_exceptions=True,
        )
        dead = {
            ws
            for ws, result in zip(list(self._clients), results)
            if isinstance(result, Exception)
        }
        self._clients -= dead

    def notify_state_change(self, reason: str = "") -> None:
        """Called by the orchestrator observer hook (sync or thread context).

        Thread-safe: uses call_soon_threadsafe to schedule on the event loop.
        Debounces broadcasts within a 300ms window.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return

        loop.call_soon_threadsafe(self._schedule_debounced_broadcast)

    def _schedule_debounced_broadcast(self) -> None:
        """Schedule a debounced broadcast (must run on event loop thread)."""
        self._pending_broadcast = True

        if self._debounce_handle is not None:
            self._debounce_handle.cancel()

        loop = self._loop
        if loop is None:
            return

        self._debounce_handle = loop.call_later(
            self.DEBOUNCE_SECONDS,
            self._flush_broadcast,
        )

    def _flush_broadcast(self) -> None:
        """Fire the debounced broadcast."""
        if self._pending_broadcast and self._loop:
            self._pending_broadcast = False
            self._loop.create_task(self.broadcast_state_patch())

    def notify_agent_activity(self, event: ActivityEvent) -> None:
        """Push structured agent activity to clients (thread-safe).

        Also stored in a ring buffer (max ``ACTIVITY_BUFFER_MAX``) so a
        client that connects mid-run receives the recent history via
        ``agent_activity_replay`` on handshake — without this, refreshing
        the browser would wipe the rolling stream.

        Thread-safety: when an event loop is bound, ``_activity_buffer``
        mutation **and** the broadcast are both marshalled to the loop
        thread via ``call_soon_threadsafe`` so they observe the same
        single-writer invariant as ``_send_snapshot`` (which reads the
        buffer on the loop thread). When no loop is bound (unit tests
        / single-threaded driver) we apply the buffer mutation in-line
        because there is no other reader/writer to race with.
        """
        payload: dict[str, Any] = {
            "agent": event.agent,
            "action": event.action,
            "phase": event.phase,
            "event_type": event.event_type,
            "elapsed": event.elapsed,
            "target": event.target,
            "ts": event.ts,
        }
        loop = self._loop
        if loop is None or loop.is_closed():
            self._append_to_activity_buffer(payload)
            return
        loop.call_soon_threadsafe(self._on_activity_event, payload)

    def _on_activity_event(self, payload: dict[str, Any]) -> None:
        """Runs on the event-loop thread — exclusive access to the
        activity buffer + client set."""
        self._append_to_activity_buffer(payload)
        data = json.dumps({"type": "agent_activity", "payload": payload})
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.create_task(self._broadcast_raw(data))

    def _append_to_activity_buffer(self, payload: dict[str, Any]) -> None:
        """Bounded ring buffer append. Single-writer guarantee from the
        caller — see ``notify_agent_activity`` for the threading model."""
        self._activity_buffer.append(payload)
        if len(self._activity_buffer) > self.ACTIVITY_BUFFER_MAX:
            self._activity_buffer = self._activity_buffer[-self.ACTIVITY_BUFFER_MAX :]

    async def _broadcast_raw(self, data: str) -> None:
        await asyncio.gather(
            *(ws.send(data) for ws in self._clients),
            return_exceptions=True,
        )

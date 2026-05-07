"""CLI subcommands.

Submodules are imported lazily by ``src.cli.main`` callbacks to avoid
pulling the orchestrator / phases dependency graph at CLI setup time.
Import the submodule directly (e.g. ``from src.cli.commands.run import
run_command_impl``) — no re-exports live here.
"""

"""Northstar CLI (spec §38 Phase 1).

    northstar scenario start INC-1042
    northstar lab exec AUS-RTR1 "show ip ospf neighbor" --session <id>
    northstar scenario verify <session-id>
    northstar scenario destroy <session-id>
    northstar cleanup-labs [--dry-run]
    northstar milestone
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from app.config import load_settings
from app.labs import JsonSessionStore, make_provider
from app.labs.reconcile import reconcile
from app.scenarios import find_scenario
from app.services.milestone import Step, run_milestone
from app.services.session_service import SessionService

app = typer.Typer(help="Northstar Technologies — lab runtime CLI", no_args_is_help=True, pretty_exceptions_enable=False)
scenario_app = typer.Typer(help="Scenario sessions", no_args_is_help=True)
lab_app = typer.Typer(help="Lab device access", no_args_is_help=True)
prober_app = typer.Typer(help="Background prober", no_args_is_help=True)
app.add_typer(scenario_app, name="scenario")
app.add_typer(lab_app, name="lab")
app.add_typer(prober_app, name="prober")
console = Console()


def _svc(provider_override: str | None = None) -> SessionService:
    settings = load_settings()
    if provider_override:
        settings = type(settings)(**{**settings.__dict__, "lab_provider": provider_override}).resolved(Path.cwd())
    provider = make_provider(settings)
    store = JsonSessionStore(settings.state_dir)
    return SessionService(settings, provider, store)


def _resolve_session(svc: SessionService, session: str | None) -> str:
    if session:
        return session
    env = os.environ.get("NORTHSTAR_SESSION")
    if env:
        return env
    live = [s for s in svc.store.list() if s.state.value in {"ACTIVE", "INVESTIGATING", "MITIGATED", "RESOLVED"}]
    if len(live) == 1:
        return live[0].id
    raise typer.BadParameter("pass --session <id> (or set NORTHSTAR_SESSION); no single live session found")


@app.callback()
def _root(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


# --------------------------------------------------------------------- scenario
@scenario_app.command("start")
def scenario_start(
    scenario_id: str = typer.Argument("INC-1042"),
    learner: str = typer.Option("henry", "--learner"),
    provider: str | None = typer.Option(None, "--provider", help="mock | containerlab (overrides LAB_PROVIDER)"),
    prober: bool = typer.Option(False, "--prober/--no-prober", help="Run the prober in the foreground after start"),
) -> None:
    """Provision the topology, inject the fault and open the incident."""
    svc = _svc(provider)

    async def go() -> None:
        session = await svc.start(scenario_id, learner, start_prober=prober)
        console.print(f"[bold green]session[/] {session.id}  [bold]lab[/] {session.lab_id}  state={session.state}")
        console.print(f"provider={svc.provider.name}  state dir={svc.settings.state_dir}")
        console.print(f"export NORTHSTAR_SESSION={session.id}")
        if prober:
            console.print("prober running (Ctrl-C to stop; lab stays up)")
            try:
                while True:
                    await asyncio.sleep(3600)
            except (KeyboardInterrupt, asyncio.CancelledError):
                pass
            finally:
                await svc.stop_prober(session.id)

    asyncio.run(go())


@scenario_app.command("verify")
def scenario_verify(session: str = typer.Argument(None), provider: str | None = typer.Option(None, "--provider"), as_json: bool = typer.Option(False, "--json")) -> None:
    """Run the scenario's deterministic assertions."""
    svc = _svc(provider)
    sid = _resolve_session(svc, session)

    async def go() -> None:
        result = await svc.verify(sid)
        if as_json:
            console.print_json(result.model_dump_json())
            return
        t = Table(title=f"Verification — session {sid}")
        t.add_column("category"); t.add_column("check"); t.add_column("result"); t.add_column("detail")
        for c in result.checks:
            t.add_row(c.category.value, c.id, "[green]PASS[/]" if c.passed else "[red]FAIL[/]", c.detail)
        console.print(t)
        for cat, s in result.categories.items():
            console.print(f"  {cat:<11} passed={s.passed} failed={s.failed}")
        console.print("[bold]scenario success:[/] " + ("[green]YES[/]" if result.scenario_success else "[red]NO[/]") + f"  (quality ok: {result.quality_ok})")

    asyncio.run(go())


@scenario_app.command("destroy")
def scenario_destroy(session: str = typer.Argument(None), provider: str | None = typer.Option(None, "--provider")) -> None:
    svc = _svc(provider)
    sid = _resolve_session(svc, session)
    s = asyncio.run(svc.destroy(sid))
    console.print(f"session {s.id} destroyed; container-minutes={s.container_minutes}")


@scenario_app.command("status")
def scenario_status(session: str = typer.Argument(None), provider: str | None = typer.Option(None, "--provider")) -> None:
    svc = _svc(provider)
    sid = _resolve_session(svc, session)
    s = svc.get(sid)
    console.print_json(s.model_dump_json())
    events = svc.bus(sid).read_log(svc.session_dir(sid) / "events.jsonl")
    for e in events[-30:]:
        console.print(f"{e.timestamp.strftime('%H:%M:%S')}  {e.event_type:<30} {e.source:<10} {json.dumps(e.metadata, default=str)[:100]}")


@scenario_app.command("list")
def scenario_list(provider: str | None = typer.Option(None, "--provider")) -> None:
    svc = _svc(provider)
    t = Table(title="Sessions")
    for col in ("id", "scenario", "learner", "state", "lab", "started"):
        t.add_column(col)
    for s in svc.store.list():
        t.add_row(s.id, s.scenario_id, s.learner_id, s.state.value, s.lab_id or "", s.started_at.strftime("%Y-%m-%d %H:%M"))
    console.print(t)


@scenario_app.command("lint")
def scenario_lint(scenario_id: str = typer.Argument("INC-1042")) -> None:
    """Validate a scenario definition and its authoring checklist (spec §49)."""
    settings = load_settings()
    sc = find_scenario(settings.scenarios_dir, scenario_id, max_nodes=settings.max_nodes)
    console.print(f"[green]ok[/] {sc.id}: {sc.title}")
    console.print(f"  countable nodes: {len(sc.topology.countable_nodes)}/{settings.max_nodes}")
    for cat in ("resolution", "regression", "quality"):
        console.print(f"  {cat}: {[a.id for a in sc.verification if a.category.value == cat]}")
    console.print(f"  prober pairs: {[p.id for p in sc.prober_matrix]}")
    console.print("  hidden truth present: yes (never exposed to characters — see tests/unit/test_character_context.py)")


# -------------------------------------------------------------------------- lab
@lab_app.command("exec")
def lab_exec(
    device: str = typer.Argument(...),
    command: str = typer.Argument(...),
    session: str | None = typer.Option(None, "--session", "-s"),
    provider: str | None = typer.Option(None, "--provider"),
) -> None:
    """Run a command on a device. Routers take vtysh lines separated by ';'."""
    svc = _svc(provider)
    sid = _resolve_session(svc, session)
    res = asyncio.run(svc.exec(sid, device, command))
    if res.stdout:
        console.print(res.stdout, end="" if res.stdout.endswith("\n") else "\n", markup=False, highlight=False)
    if res.stderr:
        console.print(res.stderr, style="red", markup=False)
    raise typer.Exit(code=min(res.exit_code, 1))


@lab_app.command("ls")
def lab_ls(provider: str | None = typer.Option(None, "--provider")) -> None:
    svc = _svc(provider)
    labs = asyncio.run(svc.provider.list_labs())
    console.print("\n".join(labs) if labs else "no northstar labs running")


# ------------------------------------------------------------------------ prober
@prober_app.command("run")
def prober_run(session: str = typer.Argument(None), provider: str | None = typer.Option(None, "--provider"), cycles: int = typer.Option(0, help="0 = run until Ctrl-C")) -> None:
    """Run the 1 Hz prober for a session in the foreground."""
    svc = _svc(provider)
    sid = _resolve_session(svc, session)

    async def go() -> None:
        p = await svc.start_prober(sid)
        try:
            n = 0
            while cycles == 0 or n < cycles:
                await asyncio.sleep(svc.settings.prober_interval_seconds)
                n += 1
                snap = p.snapshot()
                console.print("  ".join(f"{k}={v['state']}" for k, v in snap.items()), end="\r")
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await svc.stop_prober(sid)
            console.print()
            for o in p.stats.outages:
                console.print(f"outage {o.pair_id} from {o.started_at.strftime('%H:%M:%S')} duration={o.duration_s}s")

    asyncio.run(go())


# ------------------------------------------------------------------------ admin
@app.command("cleanup-labs")
def cleanup_labs(dry_run: bool = typer.Option(False, "--dry-run"), provider: str | None = typer.Option(None, "--provider")) -> None:
    """Destroy orphaned or expired labs (spec §29)."""
    svc = _svc(provider)
    rep = asyncio.run(reconcile(svc.provider, svc.store, svc.settings, dry_run=dry_run))
    console.print(f"running: {rep.running}")
    console.print(f"live sessions: {rep.live_sessions}")
    console.print(f"orphans: {rep.orphans}")
    console.print(f"expired: {rep.expired}")
    console.print(f"destroyed: {rep.destroyed}{'  [dry-run]' if dry_run else ''}")


@app.command("milestone")
def milestone(provider: str | None = typer.Option(None, "--provider", help="mock | containerlab")) -> None:
    """Run the 12-step Phase 1 technical milestone (spec §48)."""
    svc = _svc(provider)
    console.print(f"provider: [bold]{svc.provider.name}[/]")

    def show(s: Step) -> None:
        mark = "[green]PASS[/]" if s.passed else "[red]FAIL[/]"
        console.print(f"{s.n:>2}. {mark}  {s.title}\n      {s.detail}")
        if not s.passed and s.evidence.get("diagnostics"):
            console.print("[dim]diagnostics:[/]")
            console.print(str(s.evidence["diagnostics"]), markup=False, highlight=False)

    rep = asyncio.run(run_milestone(svc.settings, svc.provider, svc.store, report=show))
    console.print("\n[bold]milestone:[/] " + ("[green]OK[/]" if rep.ok else "[red]FAILED[/]"))
    raise typer.Exit(code=0 if rep.ok else 1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()

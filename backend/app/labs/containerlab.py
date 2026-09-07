"""Real lab provider: containerlab + docker (spec §7, §8, §28).

Every command the platform runs is a subprocess of ``containerlab`` or
``docker``; the browser never touches either. Per-lab state is a workdir
under the state dir containing the rendered topology and a copy of the
scenario's healthy configs (bind-mounted, so ``write memory`` persists per
session and never touches the scenario source).

Safety caps (spec §28): wall-clock timeout and max output bytes on every exec.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from pathlib import Path

from app.config import Settings
from app.models.domain import CommandResult, Device, DeviceKind, LabInstance, LabState, PingResult
from app.scenarios.schema import ScenarioDefinition

from .base import LabError, LabNotFound, device_lookup
from .naming import container_name, is_northstar_lab
from .pingparse import parse_ping

log = logging.getLogger("northstar.labs.containerlab")


class ContainerlabProvider:
    name = "containerlab"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.labs_dir = settings.state_dir / "labs"
        self.labs_dir.mkdir(parents=True, exist_ok=True)
        self._devices: dict[str, list[Device]] = {}

    # ------------------------------------------------------------------ helpers
    def _workdir(self, lab_id: str) -> Path:
        return self.labs_dir / lab_id

    async def _run(
        self,
        argv: list[str],
        *,
        timeout: float | None = None,
        cwd: Path | None = None,
        check: bool = True,
    ) -> CommandResult:
        timeout = timeout or self.settings.exec_timeout_seconds
        started = time.perf_counter()
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd) if cwd else None,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise LabError(f"command timed out after {timeout}s: {' '.join(argv[:6])}...")
        cap = self.settings.exec_max_output_bytes
        truncated = len(out) > cap or len(err) > cap
        result = CommandResult(
            device="",
            command=" ".join(argv),
            stdout=out[:cap].decode(errors="replace"),
            stderr=err[:cap].decode(errors="replace"),
            exit_code=proc.returncode or 0,
            duration_ms=(time.perf_counter() - started) * 1000,
            truncated=truncated,
        )
        if check and result.exit_code != 0:
            raise LabError(f"{argv[0]} failed ({result.exit_code}): {result.stderr.strip()[:500]}")
        return result

    def _load_devices(self, lab_id: str) -> list[Device]:
        if lab_id in self._devices:
            return self._devices[lab_id]
        meta = self._workdir(lab_id) / "lab.json"
        if not meta.exists():
            raise LabNotFound(lab_id)
        inst = LabInstance.model_validate_json(meta.read_text(encoding="utf-8"))
        self._devices[lab_id] = inst.devices
        return inst.devices

    def devices(self, lab_id: str) -> list[Device]:
        return self._load_devices(lab_id)

    def _container(self, lab_id: str, device: str) -> tuple[Device, str]:
        dev = device_lookup(self._load_devices(lab_id), device)
        return dev, container_name(lab_id, dev.node)

    # ---------------------------------------------------------------- lifecycle
    async def provision(self, scenario: ScenarioDefinition, *, lab_id: str) -> LabInstance:
        if not is_northstar_lab(lab_id):
            raise LabError("lab ids must carry the northstar prefix")
        workdir = self._workdir(lab_id)
        if workdir.exists():
            shutil.rmtree(workdir)
        workdir.mkdir(parents=True)
        shutil.copytree(scenario.configs_path, workdir / "configs")
        template = scenario.template_path.read_text(encoding="utf-8")
        topo = workdir / "topology.clab.yml"
        topo.write_text(template.replace("__LAB_NAME__", lab_id), encoding="utf-8")

        inst = LabInstance(
            id=lab_id,
            scenario_id=scenario.id,
            provider=self.name,
            devices=list(scenario.topology.devices)
            + (
                [Device(name="PROBER", node=scenario.topology.prober.node, kind=DeviceKind.INFRA)]
                if scenario.topology.prober
                else []
            ),
            workdir=str(workdir),
        )
        (workdir / "lab.json").write_text(inst.model_dump_json(indent=2), encoding="utf-8")
        self._devices[lab_id] = inst.devices

        log.info("deploying lab %s from %s", lab_id, topo)
        await self._run(
            [self.settings.containerlab_bin, "deploy", "--reconfigure", "--topo", str(topo)],
            timeout=600,
            cwd=workdir,
        )
        await self._wait_for_frr(lab_id)
        await self._wait_for_ospf(lab_id)
        return inst

    async def _wait_for_frr(self, lab_id: str, attempts: int = 30) -> None:
        routers = [d for d in self._load_devices(lab_id) if d.kind == DeviceKind.ROUTER]
        for dev in routers:
            for _ in range(attempts):
                res = await self.vtysh(lab_id, dev.name, ["show version"], check=False)
                if res.exit_code == 0 and "FRRouting" in res.stdout:
                    break
                await asyncio.sleep(1)
            else:
                raise LabError(f"FRR did not come up on {dev.name}")

    async def _wait_for_ospf(self, lab_id: str, timeout_s: float = 150.0) -> None:
        """Block until every OSPF-speaking router has a Full neighbor and routes settle.

        Real FRR needs hello/dead timers (10/40 s) plus SPF before the far-side
        prefixes are installed; asserting before that is a false failure.
        """
        routers = [d for d in self._load_devices(lab_id) if d.kind == DeviceKind.ROUTER]
        deadline = time.monotonic() + timeout_s
        pending = set()
        for dev in routers:
            cfg = await self.vtysh(lab_id, dev.name, ["show running-config"], check=False)
            if "router ospf" in cfg.stdout:
                pending.add(dev.name)
        while pending and time.monotonic() < deadline:
            for name in list(pending):
                res = await self.vtysh(lab_id, name, ["show ip ospf neighbor json"], check=False)
                try:
                    data = json.loads(res.stdout)
                except json.JSONDecodeError:
                    continue
                states = [
                    str(n.get("converged") or n.get("nbrState") or n.get("state") or "")
                    for lst in data.get("neighbors", {}).values()
                    for n in lst
                ]
                if any(st.lower().startswith("full") for st in states):
                    pending.discard(name)
            if pending:
                await asyncio.sleep(2)
        if pending:
            log.warning("OSPF did not converge on %s within %ss", sorted(pending), timeout_s)
        else:
            await asyncio.sleep(5)  # let SPF install routes on both sides

    async def destroy(self, lab_id: str) -> None:
        workdir = self._workdir(lab_id)
        topo = workdir / "topology.clab.yml"
        if topo.exists():
            await self._run(
                [self.settings.containerlab_bin, "destroy", "--cleanup", "--topo", str(topo)],
                timeout=300,
                cwd=workdir,
                check=False,
            )
        # Belt and braces: remove anything still labelled with this lab name.
        await self._docker_rm_by_lab(lab_id)
        if workdir.exists():
            shutil.rmtree(workdir, ignore_errors=True)
        self._devices.pop(lab_id, None)

    async def _docker_rm_by_lab(self, lab_id: str) -> None:
        res = await self._run(
            [self.settings.docker_bin, "ps", "-aq", "--filter", f"label=containerlab={lab_id}"],
            check=False,
        )
        ids = res.stdout.split()
        if ids:
            await self._run([self.settings.docker_bin, "rm", "-f", *ids], check=False)

    async def list_labs(self) -> list[str]:
        """Lab names currently deployed, via docker labels (works even if clab's inspect dir is gone)."""
        res = await self._run(
            [
                self.settings.docker_bin,
                "ps",
                "-a",
                "--filter",
                "label=containerlab",
                "--format",
                "{{.Label \"containerlab\"}}",
            ],
            check=False,
        )
        names = {n.strip() for n in res.stdout.splitlines() if n.strip()}
        return sorted(n for n in names if is_northstar_lab(n))

    # ------------------------------------------------------------------ commands
    async def exec(self, lab_id: str, device: str, command: str) -> CommandResult:
        dev, cname = self._container(lab_id, device)
        res = await self._run(
            [self.settings.docker_bin, "exec", cname, "sh", "-c", command],
            check=False,
        )
        return res.model_copy(update={"device": dev.name, "command": command})

    async def vtysh(
        self, lab_id: str, device: str, lines: list[str], *, check: bool = True
    ) -> CommandResult:
        dev, cname = self._container(lab_id, device)
        if dev.kind != DeviceKind.ROUTER:
            raise LabError(f"{dev.name} is not a router")
        argv = [self.settings.docker_bin, "exec", cname, "vtysh"]
        for line in lines:
            argv += ["-c", line]
        res = await self._run(argv, check=False)
        if check and res.exit_code != 0:
            raise LabError(f"vtysh on {dev.name} failed: {res.stderr or res.stdout}")
        return res.model_copy(update={"device": dev.name, "command": " ; ".join(lines)})

    async def ping(
        self,
        lab_id: str,
        device: str,
        destination: str,
        *,
        source: str | None = None,
        count: int = 1,
        timeout_s: float = 1.0,
    ) -> PingResult:
        dev, cname = self._container(lab_id, device)
        argv = [self.settings.docker_bin, "exec", cname]
        if dev.kind == DeviceKind.ROUTER:
            # FRR containers are Alpine underneath; use the OS ping, not vtysh.
            argv += ["ping"]
        else:
            argv += ["ping"]
        argv += ["-c", str(count), "-W", str(max(1, int(timeout_s)))]
        if source:
            argv += ["-I", source]
        argv += [destination]
        res = await self._run(argv, check=False, timeout=timeout_s * count + 5)
        ok, rtt = parse_ping(res.stdout)
        return PingResult(
            source=dev.name if not source else f"{dev.name}:{source}",
            destination=destination,
            success=ok and res.exit_code == 0,
            rtt_ms=rtt,
            raw=res.stdout[-2000:],
        )

    async def get_state(self, lab_id: str) -> LabState:
        devices = self._load_devices(lab_id)
        configs: dict[str, str] = {}
        for d in devices:
            if d.kind == DeviceKind.ROUTER:
                res = await self.vtysh(lab_id, d.name, ["show running-config"], check=False)
                configs[d.name] = res.stdout
        return LabState(lab_id=lab_id, devices=devices, running_configs=configs)

    async def inspect(self, lab_id: str) -> dict:
        topo = self._workdir(lab_id) / "topology.clab.yml"
        res = await self._run(
            [self.settings.containerlab_bin, "inspect", "--topo", str(topo), "--format", "json"],
            check=False,
        )
        try:
            return json.loads(res.stdout)
        except json.JSONDecodeError:
            return {"raw": res.stdout}

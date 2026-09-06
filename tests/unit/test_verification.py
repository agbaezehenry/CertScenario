"""Phase 1 exit criterion (spec §38) plus sledgehammer scoring (§19.3)."""

from __future__ import annotations

import pytest
from app.verification import VerificationContext, Verifier, build_assertion


async def _ctx(p, lab):
    return VerificationContext(baseline_configs=(await p.get_state(lab)).running_configs)


async def _fix(p, lab, lines):
    await p.vtysh(lab, "AUS-RTR1", ["configure terminal", "router ospf", *lines, "end"])


async def test_broken_topology_resolution_fails_regression_passes(broken_lab, scenario):
    p, lab = broken_lab
    r = await Verifier(p).run_scenario(lab, scenario, await _ctx(p, lab))
    assert not r.resolution_ok
    assert r.regression_ok
    assert r.quality_ok
    assert not r.scenario_success
    failed = {c.id for c in r.checks if not c.passed}
    assert failed == {"austin-to-app", "hq-has-austin-prefix"}


async def test_correct_fix_everything_passes(broken_lab, scenario):
    p, lab = broken_lab
    ctx = await _ctx(p, lab)
    await _fix(p, lab, ["network 10.20.10.0/24 area 0"])
    r = await Verifier(p).run_scenario(lab, scenario, ctx)
    assert r.resolution_ok and r.regression_ok and r.quality_ok and r.scenario_success
    assert r.failed == 0


@pytest.mark.parametrize(
    "lines,failing_quality",
    [
        (["redistribute connected"], "no-redistribute-connected"),
        (["network 0.0.0.0/0 area 0"], "no-catch-all-network"),
    ],
)
async def test_sledgehammer_resolves_but_fails_quality(broken_lab, scenario, lines, failing_quality):
    p, lab = broken_lab
    ctx = await _ctx(p, lab)
    await _fix(p, lab, lines)
    r = await Verifier(p).run_scenario(lab, scenario, ctx)
    assert r.resolution_ok and r.regression_ok
    assert not r.quality_ok
    assert r.scenario_success  # completes; scored lower
    assert {c.id for c in r.checks if not c.passed} == {failing_quality}


async def test_static_route_on_hq_resolves_but_fails_quality(broken_lab, scenario):
    p, lab = broken_lab
    ctx = await _ctx(p, lab)
    await p.vtysh(lab, "HQ-RTR1", ["configure terminal", "ip route 10.20.10.0/24 10.255.0.2", "end"])
    r = await Verifier(p).run_scenario(lab, scenario, ctx)
    # ping works, but the OSPF route assertion is still absent -> resolution not satisfied
    by_id = {c.id: c for c in r.checks}
    assert by_id["austin-to-app"].passed
    assert not by_id["hq-has-austin-prefix"].passed
    assert not by_id["no-static-bypass"].passed


async def test_shutting_wan_interface_fails_regression(broken_lab, scenario):
    p, lab = broken_lab
    ctx = await _ctx(p, lab)
    await p.vtysh(lab, "AUS-RTR1", ["configure terminal", "interface eth1", "shutdown", "end"])
    r = await Verifier(p).run_scenario(lab, scenario, ctx)
    assert not r.resolution_ok
    assert not r.regression_ok
    failed = {c.id for c in r.checks if not c.passed}
    assert {"ospf-adjacency-full", "austin-learns-hq", "wan-interface-up"} <= failed


async def test_reverting_decoy_acl_fails_regression_even_after_real_fix(broken_lab, scenario):
    p, lab = broken_lab
    ctx = await _ctx(p, lab)
    await p.vtysh(lab, "HQ-RTR1", ["configure terminal", "no access-list MGMT-SSH seq 20 deny any", "end"])
    await _fix(p, lab, ["network 10.20.10.0/24 area 0"])
    r = await Verifier(p).run_scenario(lab, scenario, ctx)
    assert r.resolution_ok
    assert not r.regression_ok
    assert {c.id for c in r.checks if not c.passed} == {"decoy-acl-untouched"}


async def test_result_format_matches_spec_19_4(broken_lab, scenario):
    p, lab = broken_lab
    r = await Verifier(p).run_scenario(lab, scenario, await _ctx(p, lab))
    d = r.model_dump()
    assert set(d["categories"]) == {"resolution", "regression", "quality"}
    assert d["categories"]["resolution"] == {"passed": 0, "failed": 2}
    assert d["passed"] + d["failed"] == len(d["checks"])


def test_build_assertion_rejects_unknown_check():
    with pytest.raises(ValueError, match="unknown check"):
        build_assertion({"id": "x", "check": "quantum", "category": "resolution", "params": {}})


def test_build_assertion_maps_from_to_source():
    a = build_assertion({"id": "x", "check": "ping", "category": "resolution", "params": {"from": "A", "destination": "1.1.1.1"}})
    assert a.source == "A"

from app.labs.faults import FaultInjector


async def test_fault_is_idempotent_and_reversible(healthy_lab, scenario):
    p, lab = healthy_lab
    inj = FaultInjector(p)
    fault = scenario.faults[0]
    assert not await inj.is_applied(lab, fault)
    assert await inj.apply(lab, fault) is True
    assert await inj.apply(lab, fault) is False  # idempotent
    assert await inj.is_applied(lab, fault)
    assert not (await p.ping(lab, "AUS-CLIENT1", "10.10.10.10")).success
    assert await inj.revert(lab, fault) is True
    assert await inj.revert(lab, fault) is False
    assert (await p.ping(lab, "AUS-CLIENT1", "10.10.10.10")).success
    # broken -> fixed -> broken without reprovisioning
    assert await inj.apply(lab, fault) is True
    assert not (await p.ping(lab, "AUS-CLIENT1", "10.10.10.10")).success


async def test_unknown_fault_action_rejected(healthy_lab):
    import pytest
    from app.labs.base import LabError
    from app.models.domain import Fault

    p, lab = healthy_lab
    with pytest.raises(LabError, match="unsupported"):
        await FaultInjector(p).apply(lab, Fault(id="x", type="configuration", device="AUS-RTR1", action="mtu_mismatch", target="eth1"))

"""Extension must wait for the provider before either gateway generation starts."""
import pytest

from anvil_serving.connect import extend as extension
from tests.connect.test_extend import environment, runner, enrollment_log


@pytest.mark.parametrize('provider_available', [True, False])
def test_provider_barrier_orders_activation_and_rollback(
    environment, runner, enrollment_log, monkeypatch, provider_available,
):
    events = []

    def record_run(argv, *args):
        if len(argv) > 2 and argv[1] in {'restart', 'enable'}:
            events.append(argv[-1])
        return runner(argv, *args)

    def provider_ready(*args):
        events.append('candidate-provider-ready')
        if not provider_available:
            raise extension.ManageError('provider unavailable')

    def restored_ready(*args, before_start=False):
        events.append('restored-provider-ready' if before_start else 'restored-gateway-ready')

    monkeypatch.setattr(extension.manage, '_oidc_ready', provider_ready)
    monkeypatch.setattr(extension.manage, '_restored_gateway_ready', restored_ready)
    monkeypatch.setattr(extension.manage, '_gateway_ready', lambda *a, **k: None)
    monkeypatch.setattr(extension.manage, '_started_units', lambda *a, **k: None)
    arguments = dict(confirm=True, runner=record_run, unit_root=environment['tmp'] / 'systemd')
    if provider_available:
        extension.extend(environment['manifest_path'], environment['target'], **arguments)
        assert events[:4] == [
            'anvil-connect-authelia.service', 'anvil-connect-caddy.service',
            'candidate-provider-ready', 'anvil-connect-gateway.service',
        ]
    else:
        with pytest.raises(extension.ExtendError, match='no enrollment changes'):
            extension.extend(environment['manifest_path'], environment['target'], **arguments)
        assert enrollment_log == []
        assert events == [
            'anvil-connect-authelia.service', 'anvil-connect-caddy.service', 'candidate-provider-ready',
            'anvil-connect-authelia.service', 'anvil-connect-caddy.service', 'restored-provider-ready',
            'anvil-connect-gateway.service', 'restored-gateway-ready',
        ]

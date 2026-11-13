import pytest
from anvil_serving.connect.config import ManifestError, validate_manifest
from anvil_serving.connect.render import render
from tests.connect.test_render import isolated_manifest


@pytest.mark.parametrize('method', [None, 'client_secret_basic', 'client_secret_post'])
def test_additional_confidential_client_uses_its_declared_auth_method(method):
    value=isolated_manifest()
    client={'client_id':'native-dashboard','client_name':'Native dashboard',
            'client_secret_file':'/etc/anvil-connect/secrets/dashboard-oidc',
            'redirect_uris':['https://dashboard.example.test/auth/callback']}
    if method is not None:client['token_endpoint_auth_method']=method
    value['authelia']['additional_oidc_clients']=[client]
    validate_manifest(value)
    text=render(value)['files']['authelia/configuration.yml']
    client_block=text.split("client_id: 'native-dashboard'\n",1)[1]
    assert 'token_endpoint_auth_method: '+(method or 'client_secret_post') in client_block


@pytest.mark.parametrize('method', ['none', 'client_secret_jwt', '', None, [], {}])
def test_invalid_confidential_auth_method_is_rejected(method):
    value=isolated_manifest()
    value['authelia']['additional_oidc_clients']=[{
        'client_id':'native-dashboard','client_name':'Native dashboard',
        'client_secret_file':'/etc/anvil-connect/secrets/dashboard-oidc',
        'redirect_uris':['https://dashboard.example.test/auth/callback'],
        'token_endpoint_auth_method':method}]
    with pytest.raises(ManifestError):validate_manifest(value)


def test_public_clients_cannot_select_secret_authentication():
    value=isolated_manifest()
    value['authelia']['additional_oidc_clients']=[{
        'client_id':'native-app','client_name':'Native app','public':True,
        'redirect_uris':['app.example.native:/oauth/callback'],
        'token_endpoint_auth_method':'client_secret_basic'}]
    with pytest.raises(ManifestError):validate_manifest(value)

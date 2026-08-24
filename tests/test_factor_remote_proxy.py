from clients.factor_client import RemoteFactorClient


def test_factor_client_has_read_only_surface():
    assert not hasattr(RemoteFactorClient, "create_mining_job")
    assert not hasattr(RemoteFactorClient, "cancel_mining_job")
    assert not hasattr(RemoteFactorClient, "evaluate")
    assert hasattr(RemoteFactorClient, "list_factors")
    assert hasattr(RemoteFactorClient, "get_factor_evidence")

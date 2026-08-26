import builtins


def test_always_collects_without_seed():
    assert True


if getattr(builtins, "CANONICAL_COLLECTION_STATE_READY", False):

    def test_only_after_seed_import():
        assert True

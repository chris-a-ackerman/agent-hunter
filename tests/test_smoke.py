def test_packages_import():
    import contracts
    import core

    assert contracts and core

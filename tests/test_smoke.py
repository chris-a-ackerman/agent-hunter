def test_packages_import():
    import contracts
    import core
    import gateway

    assert contracts and core and gateway

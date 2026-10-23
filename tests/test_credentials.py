def test_credentials_failure_never_writes_plaintext(tmp_path, monkeypatch, caplog):
    import keyring
    import pytest

    from boss_say_hello.storage.credentials import CredentialStore, CredentialUnavailable

    def fail(*args):
        raise RuntimeError("secret-for-test")

    monkeypatch.setattr(keyring, "set_password", fail)
    with pytest.raises(CredentialUnavailable) as caught:
        CredentialStore().save("model", "secret-for-test")
    assert "secret-for-test" not in str(caught.value) + caplog.text
    assert list(tmp_path.iterdir()) == []

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


def test_store_normalizes_pasted_key_and_rejects_embedded_newline(monkeypatch):
    import keyring
    import pytest

    from boss_say_hello.storage.credentials import CredentialStore

    saved = {}
    monkeypatch.setattr(keyring, "set_password", lambda service, name, value: saved.update({name: value}))
    store = CredentialStore()
    store.save("model", "  test-key\r\n")
    assert saved == {"model": "test-key"}
    with pytest.raises(ValueError):
        store.save("model", "invalid\nkey")
    assert saved == {"model": "test-key"}

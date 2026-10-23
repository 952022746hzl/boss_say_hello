import keyring


class CredentialUnavailable(RuntimeError):
    pass


class CredentialStore:
    service = 'BossSayHello'

    def save(self, id: str, secret: str) -> None:
        try:
            keyring.set_password(self.service, id, secret)
        except Exception:
            raise CredentialUnavailable('系统凭据存储不可用；可仅在当前会话使用密钥') from None

    def get(self, id: str) -> str | None:
        try:
            return keyring.get_password(self.service, id)
        except Exception:
            raise CredentialUnavailable('无法读取系统凭据，请重新输入会话密钥') from None

    def delete(self, id: str) -> None:
        try:
            keyring.delete_password(self.service, id)
        except keyring.errors.PasswordDeleteError:
            pass
        except Exception:
            raise CredentialUnavailable('无法删除系统凭据') from None

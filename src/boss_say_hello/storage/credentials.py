import hashlib
from urllib.parse import urlsplit, urlunsplit

import keyring


def normalize_secret(secret: str) -> str:
    secret = secret.strip()
    if not secret:
        raise ValueError("请输入非空 API 密钥")
    if any(not 33 <= ord(char) <= 126 for char in secret):
        raise ValueError("API 密钥含空格、换行或非英文字符，请重新复制完整密钥，不要包含说明文字")
    return secret


def credential_scope(config):
    config.validate()
    if not config.base_url:
        raise ValueError("请先填写 API 地址，再保存或使用对应密钥")
    parts = urlsplit(config.base_url)
    endpoint = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))
    return config.credential_id + ":" + hashlib.sha256(endpoint.encode()).hexdigest()


class CredentialUnavailable(RuntimeError):
    pass


class CredentialStore:
    service = "BossSayHello"

    def save(self, id: str, secret: str) -> None:
        secret = normalize_secret(secret)
        try:
            keyring.set_password(self.service, id, secret)
        except Exception:
            raise CredentialUnavailable("系统凭据存储不可用；可仅在当前会话使用密钥") from None

    def get(self, id: str) -> str | None:
        try:
            return keyring.get_password(self.service, id)
        except Exception:
            raise CredentialUnavailable("无法读取系统凭据，请重新输入会话密钥") from None

    def delete(self, id: str) -> None:
        try:
            keyring.delete_password(self.service, id)
        except keyring.errors.PasswordDeleteError:
            pass
        except Exception:
            raise CredentialUnavailable("无法删除系统凭据") from None

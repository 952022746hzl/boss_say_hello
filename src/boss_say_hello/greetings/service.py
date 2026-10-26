from boss_say_hello.domain import CheckResult

GREETING_MAX_LENGTH = 200


def message_length(text):
    # Match JavaScript String.length used by the actual browser send guard.
    return len(text.encode("utf-16-le", errors="surrogatepass")) // 2


def validate_greeting(greeting, max_length):
    text = greeting.text.strip()
    if not text or any(
        token in text.lower() for token in ("traceback", "```", "as an ai", "api_key", "<script")
    ):
        return CheckResult("reject", ("招呼语为空或包含异常内容",))
    if max_length is None:
        return CheckResult("pending", ("平台消息长度限制尚未确认，仅预览",))
    length = message_length(text)
    if length > max_length:
        return CheckResult("reject", (f"招呼语为 {length} 个字符，超过本次发送上限 {max_length}；未发送",))
    return CheckResult("pass")

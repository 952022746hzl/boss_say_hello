"""Use the recruiter's displayed address; neutral surname fallback needs no model call."""

import re
from dataclasses import replace

COMPOUND_SURNAMES = {
    "欧阳",
    "司马",
    "上官",
    "诸葛",
    "司徒",
    "司空",
    "夏侯",
    "东方",
    "皇甫",
    "尉迟",
    "公孙",
    "慕容",
    "令狐",
    "长孙",
    "宇文",
    "轩辕",
    "独孤",
    "南宫",
}


def salutation(display_name):
    name = re.split(r"[·•|｜\n]", display_name.strip(), maxsplit=1)[0].strip()
    if not name or len(name) > 24 or any(word in name for word in ("招聘", "人事", "人力资源")):
        return "您好"
    if re.fullmatch(r"[\u4e00-\u9fff]{1,6}(先生|女士|小姐|老师|经理|主管|总监|总)", name):
        return name + "您好"
    if re.fullmatch(r"[\u4e00-\u9fff]{2,5}", name):
        surname = name[:2] if name[:2] in COMPOUND_SURNAMES else name[0]
        return surname + "老师您好"
    return "您好"


def address_greeting(greeting, display_name):
    prefix = salutation(display_name)
    body = re.sub(
        r"^(?:[\u4e00-\u9fffA-Za-z·]{1,16})?(?:您好|你好)[，,。.!！：:\s]*",
        "",
        greeting.text.strip(),
        count=1,
    )
    return replace(greeting, text=prefix + ("，" + body if body else ""))

LABELS = {
    "preview": "预览草稿",
    "sent": "已发送",
    "unknown": "结果待核实",
    "failed": "失败",
    "blocked": "未发送",
    "paused": "已暂停",
    "running": "运行中",
    "completed": "列表处理完成",
    "limit_reached": "已达到本轮联系上限",
    "stopped": "已停止",
    "interrupted": "运行中断",
    "pass": "符合硬条件",
    "reject": "不符合",
    "pending": "待确认",
    "skipped": "跳过",
    "low_score": "分数不足",
    "changed": "岗位已变化",
    "session_changed": "登录状态变化",
}


def details(events):
    lines = []
    for e in events:
        p = e.payload
        if e.kind == "job":
            j = p["job"]
            lines.extend(
                [f"{j['company']} / {j['title']}", f"{j['city']} · {j['salary_raw']}", j["text"], j["url"]]
            )
        elif e.kind == "assessment":
            lines.extend([f"匹配分：{p['score']}", f"理由：{p['reason']}"])
            for key, label in [("evidence", "依据"), ("gaps", "缺口"), ("missing", "信息不足")]:
                lines.append(label + "：" + "；".join(p.get(key, [])))
        elif e.kind == "greeting":
            lines.append("招呼语：" + p["text"])
        else:
            if "status" in p:
                lines.append(LABELS.get(p["status"], p["status"]))
            if "reason" in p:
                lines.append(p["reason"])
            if "reasons" in p:
                lines.extend(p["reasons"])
            for stage, result in p.get("stages", {}).items():
                lines.append(
                    f"{ {'contact': '联系', 'message': '消息', 'attachment': '附件'}.get(stage, stage) }：{result.get('detail', result.get('outcome', ''))}"
                )
        lines.append("")
    return "\n".join(lines)

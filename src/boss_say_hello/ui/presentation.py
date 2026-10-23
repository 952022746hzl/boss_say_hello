import json

LABELS = {
    "agent_finished": "AI 已结束本轮（详见步骤理由）",
    "budget_reached": "已达到决策预算",
    "excluded": "外包嫌疑已排除",
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
        elif e.kind == "tool_action":
            lines.append(f"行动 {p['tool']}：{p['reason']}")
        elif e.kind == "tool_result":
            lines.append(f"结果 {p['tool']}：" + json.dumps(p["result"], ensure_ascii=False)[:3000])
        elif e.kind == "company_sources":
            lines.append("公司主体：" + p["company"].get("name", "待核实"))
            for source in p["sources"]:
                lines.extend(
                    [
                        f"来源 {source['id']} · {source.get('title', '')} · {source.get('queried_at', '')}",
                        source["url"],
                        source["text"],
                    ]
                )
        elif e.kind == "background":
            lines.extend(
                [
                    f"公司背景分：{p['score']} · {LABELS.get(p['status'], p['status'])}",
                    p["reason"],
                    "来源：" + "、".join(p["source_ids"]),
                ]
            )
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

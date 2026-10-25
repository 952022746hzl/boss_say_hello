from boss_say_hello.agent.progress import TOOL_LABELS

LABELS = {
    "agent_finished": "AI 已结束本轮",
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


def tool_result_summary(tool, result):
    if "error" in result:
        return "未完成：" + result["error"]
    if result.get("code") == "job_unavailable":
        return result["reason"]
    if "controls" in result:
        action = result.get("action_result", {})
        parts = []
        if action.get("message"):
            parts.append(action["message"])
        elif action:
            verb = "悬停展开" if action.get("action") == "hover" else "点击"
            parts.append(f"已{verb}「{action.get('label', '')}」，已回读页面")
        selected = [e["label"] for e in result.get("expectations", []) if e["selected"]]
        parts.append("已选标签：" + ("、".join(selected) or "未选择"))
        parts.append(f"当前可操作控件 {len(result['controls'])} 个")
        return "；".join(parts)
    if tool == "verify_preferences":
        return "目标求职标签核验通过" if result.get("verified") else "标签尚未通过核验"
    if tool == "list_jobs":
        return f"读取 {len(result.get('jobs', []))} 个岗位" + (
            "；已到本轮范围末尾" if result.get("ended") else ""
        )
    if tool == "read_job":
        return "岗位详情已读取，硬条件已检查"
    if tool == "search_company":
        return f"已获得 {len(result.get('sources', []))} 条公司来源，待核对证据"
    if tool == "draft_greeting":
        return "招呼语草稿已生成，可在岗位详情查看"
    if tool == "finish":
        return f"本轮已结束：发现 {result.get('discovered', 0)} 个岗位，已处理 {result.get('handled', 0)} 个"
    if result.get("reason"):
        return result["reason"]
    if result.get("status"):
        return LABELS.get(result["status"], result["status"])
    if tool == "send_greeting":
        return "发送处理已返回，送达状态请查看岗位详情"
    return "操作结果已记录，可在本地历史查看"


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
            actor = "程序操作" if p.get("actor") == "program" else "AI 行动"
            lines.append(f"{actor} · {TOOL_LABELS.get(p['tool'], p['tool'])}：{p['reason']}")
        elif e.kind == "tool_result":
            lines.append(
                f"结果 · {TOOL_LABELS.get(p['tool'], p['tool'])}："
                + tool_result_summary(p["tool"], p["result"])
            )
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

"""DOM-backed preference controls. No caller-supplied selector or script is accepted."""

import json
import re


def compact(value):
    return re.sub(r"\s+", "", value).replace("（", "(").replace("）", ")")


def parse_expectations(labels):
    items, seen = [], set()
    for label in labels:
        match = re.fullmatch(r"\s*(.+)[(（]([^()（）]+)[)）]\s*", label)
        if not match:
            continue
        title, city = (part.strip() for part in match.groups())
        key = (compact(title), compact(city))
        if title and city and key not in seen:
            seen.add(key)
            items.append({"label": label.strip(), "title": title, "city": city})
    return items


# Read-only: refreshing the UI must not replace the agent's control IDs.
EXISTING_EXPECTATIONS = r"""/* EXISTING_EXPECTATIONS */ JSON.stringify(
 [...document.querySelectorAll('.c-expect-select .expect-item,.dropdown-expect-list li')]
 .map(e=>(e.innerText||e.textContent||'').trim())
)"""


def preferences_match(snapshot, config):
    if len(config.cities) != 1:
        return False
    label = compact(f"{config.target_title}({config.cities[0]})")
    return any(compact(e["label"]) == label and e["selected"] for e in snapshot.get("expectations", []))


SNAPSHOT = r"""/* PREFERENCES */ JSON.stringify((() => {
 const visible = e => !!e && !!e.getClientRects().length && getComputedStyle(e).visibility!=='hidden';
 const text = e => (e?.innerText || e?.textContent || '').trim();
 const active = e => /(^|\s)(active|selected|checked|ui-select-item-selected)(\s|$)/.test(e.className||'') || e.getAttribute('aria-selected')==='true';
 const scopes=[...document.querySelectorAll('.c-expect-select,.dropdown-expect-list')].filter(visible);
 const token=String(performance.timeOrigin)+':'+String(Math.random());
 const controls=[]; const nodes=[]; const seen=new Set();
 for(const scope of scopes) for(const e of scope.querySelectorAll('.expect-item,.recommend-job-btn,li')) {
   if(seen.has(e)||!visible(e)||e.disabled||e.closest('.expectation-form,.position-dialog,.add-expect-btn')) continue;
   seen.add(e);
   const label=text(e).slice(0,100)||e.getAttribute('placeholder')||'';
   if(!label || /添加|新增|删除|编辑|保存|完成|立即沟通|继续沟通|发送|上传/.test(label)) continue;
   if(e.tagName==='LI' && e.querySelector('li,a')) continue;
   const id=token+':'+nodes.length;
   const kind='click';
   nodes.push({e,label,scope,kind}); controls.push({id,label,kind});
 }
 window.__bossPreferenceControls={token,nodes};
 const expectations=[...document.querySelectorAll('.c-expect-select .expect-item,.recommend-job-btn,.dropdown-expect-list li')].filter(visible).map(e=>({label:text(e),selected:active(e)}));
 return {document:String(performance.timeOrigin),path:location.pathname,controls,expectations};
})())"""


def action_script(option_id):
    return (
        "/* PREFERENCE_ACTION */ JSON.stringify((() => {const id="
        + json.dumps(option_id)
        + r""";
      const state=window.__bossPreferenceControls;
      if(!state||!id.startsWith(state.token+':')) return {acted:false};
      const item=state.nodes[Number(id.slice(state.token.length+1))];
      if(!item||!item.e.isConnected||!item.e.getClientRects().length||item.e.disabled) return {acted:false};
      const e=item.e;
      if(!item.scope.contains(e)||e.closest('.expectation-form,.position-dialog,.add-expect-btn')) return {acted:false};
      const label=(e.innerText||e.textContent||'').trim().slice(0,100)||e.getAttribute('placeholder')||'';
      if(label!==item.label||getComputedStyle(e).visibility==='hidden') return {acted:false};
      e.click();
      return {acted:true};
    })())"""
    )

"""DOM-backed preference controls. No caller-supplied selector or script is accepted."""

import json
import re


def compact(value):
    return re.sub(r"\s+", "", value).replace("（", "(").replace("）", ")")


def required_filters(config):
    filters = dict(config.native_filters)
    if "薪资" not in filters:
        buckets = [
            (0, 3000, "3K以下"),
            (3000, 5000, "3-5K"),
            (5000, 10000, "5-10K"),
            (10000, 20000, "10-20K"),
            (20000, 50000, "20-50K"),
            (50000, float("inf"), "50K以上"),
        ]
        for low, high, label in buckets:
            if (
                config.salary_min is not None
                and config.salary_max is not None
                and low <= config.salary_min <= config.salary_max <= high
            ):
                filters["薪资"] = label
                break
        else:
            raise ValueError("薪资跨越平台档位，请在原生薪资筛选中明确选择一档；不会自动改成不限")
    return filters


def preferences_match(snapshot, config):
    label = compact(f"{config.target_title}({config.cities[0]})")
    if not any(compact(e["label"]) == label and e["selected"] for e in snapshot.get("expectations", [])):
        return False
    if compact(snapshot.get("city", "")) != compact(config.cities[0]):
        return False
    available = {f["field"]: f["selected"] for f in snapshot.get("filters", [])}
    try:
        return all(available.get(key) == [value] for key, value in required_filters(config).items())
    except ValueError:
        return False


def expectation_form_matches(values, config):
    def amount(value):
        match = re.fullmatch(r"(\d+)([kK]?)", value.strip())
        return int(match[1]) * (1000 if match[2] else 1) if match else None

    return (
        compact(values.get("期望职位", "")) == compact(config.target_title)
        and compact(values.get("工作城市", "")) == compact(config.cities[0])
        and amount(values.get("薪资下限", "")) == config.salary_min
        and amount(values.get("薪资上限", "")) == config.salary_max
        and values.get("求职类型") == "全职"
    )


SNAPSHOT = r"""/* PREFERENCES */ JSON.stringify((() => {
 const visible = e => !!e && !!e.getClientRects().length && getComputedStyle(e).visibility!=='hidden';
 const text = e => (e?.innerText || e?.textContent || '').trim();
 const active = e => /(^|\s)(active|selected|checked|ui-select-item-selected)(\s|$)/.test(e.className||'') || e.getAttribute('aria-selected')==='true';
 const scopes=[...document.querySelectorAll('.c-expect-select,.c-filter-condition,.expectation-form')].filter(visible);
 // Only preference-related dialogs, never the whole page or a messaging panel.
 for(const e of document.querySelectorAll('.dialog-wrap,.dialog-container,[role=dialog]')) {
   const heading=text(e.querySelector('.dialog-title,.title,h3'));
   if(visible(e) && /^(选择期望职位|选择职位|选择城市|选择行业|工作城市)$/.test(heading)) scopes.push(e);
 }
 const token=String(performance.timeOrigin)+':'+String(Math.random());
 const controls=[]; const nodes=[]; const seen=new Set();
 for(const scope of scopes) for(const e of scope.querySelectorAll('a,button,input:not([type=hidden]),li,.expect-item,.add-expect-btn,.city-label,.current-select,.ui-select-selection,.ui-cascader-selection')) {
   if(seen.has(e)||!visible(e)||e.disabled) continue;
   seen.add(e);
   const label=text(e).slice(0,100)||e.getAttribute('placeholder')||'';
   if(!label || /删除|编辑|立即沟通|继续沟通|发送|上传/.test(label)) continue;
   if(e.tagName==='LI' && e.querySelector('li,a')) continue;
   const field=text(e.closest('.form-item')?.querySelector('.item-label'));
   const kind=e.tagName==='INPUT'&&!e.readOnly&&e.type==='text'?'input':'click';
   const id=token+':'+nodes.length;
   const submit=!!e.closest('.expectation-form') && /^(完成|保存|确定)$/.test(label);
   nodes.push({e,label,kind,field}); controls.push({id,label,kind,field,submit});
 }
 window.__bossPreferenceControls={token,nodes};
 const expectations=[...document.querySelectorAll('.c-expect-select .expect-item,.recommend-job-btn,.dropdown-expect-list li')].filter(visible).map(e=>({label:text(e),selected:active(e)}));
 const fields={exp:'经验',degree:'学历',salary:'薪资',scale:'公司规模',finance:'融资阶段',jobType:'求职类型'};
 const filters=[...document.querySelectorAll('.c-filter-condition .condition-filter-select')].map(e=>{
   const options=[...e.querySelectorAll('[ka^="sel-"]')];
   const ka=options[0]?.getAttribute('ka')||'';
   const part=ka.match(/^sel-job-rec-([^-]+)-/);
   const field=part?fields[part[1]]:ka.startsWith('sel-industry-')?'行业':text(e.querySelector('.placeholder-text'));
   const selected=options.filter(active).map(text);
   return {field,selected,options:options.map(text)};
 });
 const form=document.querySelector('.expectation-form');
 const values={};
 if(visible(form)) for(const row of form.querySelectorAll('.form-item')) {
   const label=text(row.querySelector('.item-label'));
   if(label==='薪资要求') {
     const selected=[...row.querySelectorAll('.ui-select-selection')].map(e=>text(e.querySelector('.ui-select-selected-value'))||text(e.querySelector('.ui-select-inner')).replace('选择薪资范围','').trim());
     values['薪资下限']=selected[0]||'';values['薪资上限']=selected[1]||'';
   } else values[label]=row.querySelector('input[type=text]')?.value || text(row.querySelector('.radio-checked'));
 }
 return {document:String(performance.timeOrigin),path:location.pathname,controls,expectations,filters,city:text(document.querySelector('.cur-city-label')),form_values:values,adding:visible(form)&&text(form).includes('添加求职期望')};
})())"""


def action_script(option_id, value=None):
    return (
        "/* PREFERENCE_ACTION */ JSON.stringify((() => {const id="
        + json.dumps(option_id)
        + ",value="
        + json.dumps(value, ensure_ascii=False)
        + r""";
      const state=window.__bossPreferenceControls;
      if(!state||!id.startsWith(state.token+':')) return {acted:false};
      const item=state.nodes[Number(id.slice(state.token.length+1))];
      if(!item||!item.e.isConnected||!item.e.getClientRects().length||item.e.disabled) return {acted:false};
      const e=item.e;
      const label=(e.innerText||e.textContent||'').trim().slice(0,100)||e.getAttribute('placeholder')||'';
      if(label!==item.label||getComputedStyle(e).visibility==='hidden') return {acted:false};
      if(item.kind==='input') {
        if(typeof value!=='string')return {acted:false};
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,value);
        e.dispatchEvent(new Event('input',{bubbles:true})); e.dispatchEvent(new Event('change',{bubbles:true}));
      } else {e.click();}
      return {acted:true};
    })())"""
    )

"""Scoped DOM actions for the observed BOSS chat UI; no blind sends."""

import json


def script(action, job, value="", *, baseline_ids=None):
    args = json.dumps(
        {
            "action": action,
            "job": job.id,
            "boss": job.recruiter_id,
            "value": value,
            "baseline_ids": baseline_ids,
        },
        ensure_ascii=True,
    )
    return (
        "/* ACTION */ JSON.stringify((() => { const a = "
        + args
        + r""";
      const visible=e=>e && e.getClientRects().length>0 && getComputedStyle(e).visibility!=='hidden';
      const unique=(sel,root=document)=>{const es=[...root.querySelectorAll(sel)].filter(visible);return es.length===1?es[0]:null;};
      const enabled=e=>visible(e)&&!e.disabled&&e.getAttribute('aria-disabled')!=='true'&&!e.classList.contains('disabled');
      const chat=unique('.chat-conversation');
      const friend=chat?.__vue__?.['selectedFriend$'];
      const matched=Boolean(chat && a.boss && friend?.encryptJobId===a.job && friend?.encryptBossId===a.boss);
      const messages=matched?[...chat.querySelectorAll('.message-item.item-myself')]:[];
      const matching=messages.filter(e=>a.action==='observe_attachment'
        ? e.querySelector('[data-resume-id]')?.getAttribute('data-resume-id')===a.value
        : (typeof e.__vue__?.$props?.message?.text==='string'?e.__vue__.$props.message.text:e.querySelector('.text-content')?.innerText)===a.value);
      const seen_ids=matching.map(e=>e.getAttribute('data-mid')).filter(Boolean);
      const ids=matching.filter(e=>{
        const delivered=Boolean(e.querySelector('.message-status.status-delivery,.message-status.status-read'));
        return delivered&&!e.querySelector('.status-error,.status-fail,.status-loading');
      }).map(e=>e.getAttribute('data-mid')).filter(Boolean);
      const count=ids.length;
      const pending=window.__bossContactAttempt;
      const bound=pending?.job===a.job&&pending?.boss===a.boss;
      const dialogs=[...document.querySelectorAll('.dialog-wrap,.dialog-container,.dialog,[role=dialog]')].filter(e=>visible(e)&&/已向\s*BOSS\s*发送消息|已发送.*招呼|招呼.*已发送/i.test(e.innerText));
      const continues=[...new Set(dialogs.flatMap(d=>[...d.querySelectorAll('button,a,.btn')]))].filter(e=>enabled(e)&&e.innerText.trim()==='继续沟通');
      const canContinue=bound&&continues.length===1;
      if(a.action==='observe'||a.action==='observe_attachment') return {matched,count,ids,seen_ids,can_continue:canContinue,input_ready:!!unique('#chat-input',chat||document)};
      if(a.action==='contact') {
        const box=unique('.job-detail-box');
        const info=box?.__vue__?.$props?.data?.jobInfo;
        const b=box&&unique('.op-btn-chat',box);
        if(info?.encryptId!==a.job||info?.encryptUserId!==a.boss||!enabled(b)||b.innerText.trim()!=='立即沟通') return {clicked:false,code:'contact_target_mismatch'};
        window.__bossContactAttempt={job:a.job,boss:a.boss};
        b.click(); return {clicked:true};
      }
      if(a.action==='continue_contact') {
        if(!canContinue) return {clicked:false,code:'dialog_unverified'};
        continues[0].click(); return {clicked:true};
      }
      if(!matched) return {clicked:false,code:'target_changed'};
      if(a.action==='fill_message'||a.action==='send_message'||a.action==='message_ready') {
        // Check in the same task as filling/clicking: a manual send during the
        // readiness wait must never be mistaken for initialization clearing.
        if(Array.isArray(a.baseline_ids)&&matching.some(e=>!a.baseline_ids.includes(e.getAttribute('data-mid'))))
          return {clicked:false,filled:false,ready:false,code:'matching_message_appeared'};
        const input=unique('#chat-input',chat);
        const editor=input?.closest('.editor-container')?.__vue__;
        const max=Number(input?.getAttribute('maxlength')||input?.getAttribute('data-maxlength')||editor?.$data?.MAX_PASTE_LENGTH);
        if(!input||!Number.isInteger(max)||max<=0||!a.value||a.value.length>Math.min(max,200)) return {clicked:false,filled:false,code:'input_limit_unverified'};
        if(a.action==='fill_message') {
          const existing=input.isContentEditable?input.innerText:input.value;
          if(existing.trim()&&existing!==a.value) return {filled:false,code:'existing_draft'};
          input.focus();
          if(input.isContentEditable) input.textContent=a.value;
          else {const setter=Object.getOwnPropertyDescriptor(input.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value')?.set;
            if(!setter) return {filled:false}; setter.call(input,a.value);}
          input.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:a.value}));
          return {filled:true,limit:max};
        }
        const text=input.isContentEditable?input.innerText:input.value;
        const send=unique('.btn-send',chat);
        const state={clicked:false,ready:false,matched,text_matches:text===a.value,button_enabled:!!enabled(send),char_count:text.length,limit:max};
        if(text!==a.value) return {...state,code:text.trim()?'draft_changed':'draft_empty'};
        if(!send||send.innerText.trim()!=='发送') return {...state,code:'send_control_unverified'};
        if(!enabled(send)) return {...state,code:'send_control_not_ready'};
        if(a.action==='message_ready') return {...state,ready:true};
        send.click(); return {clicked:true};
      }
      if(a.action==='attachment') {
        const choices=[...chat.querySelectorAll('[data-resume-id]')].filter(e=>visible(e)&&e.getAttribute('data-resume-id')===a.value);
        const send=choices.length===1?choices[0].querySelector('button[data-action="send-resume"]'):null;
        if(!enabled(send)) return {clicked:false};
        send.click(); return {clicked:true};
      }
      return {clicked:false};
    })())"""
    )

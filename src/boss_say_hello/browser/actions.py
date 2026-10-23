"""Conservative DOM actions. Missing identity/evidence never authorizes a click."""
import json


def script(action, job, value=''):
    args = json.dumps({'action': action, 'job': job.id, 'boss': job.recruiter_id, 'value': value}, ensure_ascii=True)
    return '/* ACTION */ JSON.stringify((() => { const a = ' + args + r''';
      const visible=e=>e && e.getClientRects().length>0;
      const unique=sel=>{const es=[...document.querySelectorAll(sel)].filter(visible);return es.length===1?es[0]:null;};
      const chat=unique('.chat-conversation, .chat-container');
      const matched=Boolean(chat && a.boss && chat.getAttribute('data-job-id')===a.job && chat.getAttribute('data-boss-id')===a.boss);
      const messages=matched?[...chat.querySelectorAll('.message-item.is-self, .message-item.item-my')]:[];
      const count=messages.filter(e=>a.action.includes('attachment')?
        e.querySelector('[data-resume-id]')?.getAttribute('data-resume-id')===a.value:
        e.querySelector('.text')?.innerText===a.value).length;
      if(a.action==='observe') return {matched,count};
      if(a.action==='observe_attachment') return {matched,count};
      if(a.action==='contact') {
        const id=location.pathname.match(/^\/job_detail\/([A-Za-z0-9_-]+)\.html$/)?.[1];
        const b=unique('.op-btn-chat');
        if(id!==a.job || !b || b.innerText.trim()!=='立即沟通') return {clicked:false};
        b.click(); return {clicked:true};
      }
      if(!matched) return {clicked:false};
      if(a.action==='message') {
        const input=chat.querySelector('#chat-input');
        const buttons=[...chat.querySelectorAll('button,.btn-send')].filter(e=>visible(e)&&e.innerText.trim()==='发送'&&!e.disabled);
        const max=Number(input?.getAttribute('maxlength')||input?.getAttribute('data-maxlength'));
        if(!input || !max || a.value.length>max || buttons.length!==1) return {clicked:false};
        if(input.isContentEditable) input.textContent=a.value;
        else {const setter=Object.getOwnPropertyDescriptor(input.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value')?.set;
          if(!setter) return {clicked:false}; setter.call(input,a.value);}
        input.dispatchEvent(new Event('input',{bubbles:true}));
        if(buttons[0].disabled) return {clicked:false};
        buttons[0].click(); return {clicked:true};
      }
      if(a.action==='attachment') {
        const choices=[...chat.querySelectorAll('[data-resume-id]')].filter(e=>visible(e)&&e.getAttribute('data-resume-id')===a.value);
        const send=choices.length===1?choices[0].querySelector('button[data-action="send-resume"]'):null;
        if(!visible(send)||send.disabled) return {clicked:false};
        send.click(); return {clicked:true};
      }
      return {clicked:false};
    })())'''

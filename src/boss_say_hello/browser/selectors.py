"""Read-only DOM snapshots; selector failures are surfaced, never treated as success."""

RECOMMEND_URL = "https://www.zhipin.com/web/geek/job-recommend"


def select_job(url):
    import json

    return (
        r"""/* SELECT_JOB */ JSON.stringify((() => {
      const url="""
        + json.dumps(url)
        + r""";
      const card=[...document.querySelectorAll('.job-card-box')].find(e=>{
        const a=e.querySelector('a.job-name[href*="/job_detail/"]');
        return a && new URL(a.href).pathname===new URL(url).pathname;
      });
      if(!card) return false;
      card.click(); return true;
    })())"""
    )


SESSION = r"""/* SESSION */ JSON.stringify((() => {
 const visible = el => el && el.getClientRects().length > 0;
 const login = [...document.querySelectorAll('.header-login-btn,[ka="header-login"],.sign-form')].some(visible);
 const profile = document.querySelector('.user-nav, .user-info, [ka="header-username"]');
 let id = profile && (profile.getAttribute('data-user-id') || profile.getAttribute('data-geek-id') || profile.getAttribute('data-uid'));
 const state=document.querySelector('#wrap')?.__vue__?.$store?.state;
 if (!id && state?.hasLoadedUser===true && typeof state.userInfo?.encryptUserId==='string') id=state.userInfo.encryptUserId;
 const uid = document.cookie.split(';').map(s=>s.trim()).find(s=>/^(uid|userId)=/.test(s));
 if (!id && uid) id = decodeURIComponent(uid.slice(uid.indexOf('=')+1));
 return {logged_in: !login && Boolean(profile), account_id: !login && id ? String(id) : null};
})())"""

CARDS = r"""/* CARDS */ JSON.stringify((() => {
 const text=(el,sel)=>el.querySelector(sel)?.innerText?.trim() || '';
 const cards=[...document.querySelectorAll('.job-card-box')].map(el=>({
  url:el.querySelector('a[href*="/job_detail/"]')?.href || '',
  title:text(el,'.job-name'), company:text(el,'.company-name, .boss-name'), salary:text(el,'.job-salary, .salary'),
  city:text(el,'.job-area, .company-location'), recruiter_id:el.getAttribute('data-boss-id') || null
 }));
 const ended=[...document.querySelectorAll('.load-more,.job-list-empty,.no-more')]
   .some(el=>el.getClientRects().length && /没有更多|暂无职位|暂无推荐|到底了/.test(el.innerText));
 return {cards,ended};
})())"""

DETAIL = r"""/* DETAIL */ JSON.stringify((() => {
 const box=document.querySelector('.job-detail-box');
 const data=box?.__vue__?.$props?.data;
 const info=data?.jobInfo;
 const text=sel=>(box||document).querySelector(sel)?.innerText?.trim() || '';
 return {url:info?.encryptId ? 'https://www.zhipin.com/job_detail/'+info.encryptId+'.html' : location.href,
  text:info?.postDescription || text('.job-detail-body, .job-sec-text'),
  title:info?.jobName || text('.job-name, .name h1'), company:data?.brandComInfo?.brandName || text('.company-name'),
  salary:info?.salaryDesc || text('.job-salary, .salary'), city:info?.locationName || text('.job-area, .job-location'),
  contact:text('.op-btn-chat'), recruiter_id:info?.encryptUserId || (box||document).querySelector('[data-boss-id]')?.getAttribute('data-boss-id') || null};
})())"""

SCROLL = r"""/* SCROLL */ JSON.stringify((() => {
 const card=document.querySelector('.job-card-box');
 let el=card?.parentElement;
 while(el && el!==document.body){
   if(el.scrollHeight>el.clientHeight+20 && /auto|scroll/.test(getComputedStyle(el).overflowY)){
     el.scrollTop+=Math.max(el.clientHeight,600); return true;
   } el=el.parentElement;
 }
 window.scrollBy(0,Math.max(innerHeight,600)); return true;
})())"""

CAPABILITIES = r"""/* CAPABILITIES */ JSON.stringify((() => {
 const input=document.querySelector('#chat-input');
 const raw=input?.getAttribute('maxlength') || input?.getAttribute('data-maxlength');
 const limit=raw && /^[1-9]\d*$/.test(raw) ? Number(raw) : null;
 const contact=document.querySelector('.op-btn-chat');
 const hint=contact?.getAttribute('data-greeting-behavior');
 const notice=[...document.querySelectorAll('.greeting-tip,.chat-tip')].map(el=>el.innerText).join(' ');
 const behavior=hint==='none' || hint==='default_message' ? hint : /自动发送.*招呼|已向BOSS发送消息/.test(notice) ? 'default_message' : 'unknown';
 return {greeting_limit:limit,initial_contact_behavior:behavior};
})())"""

ATTACHMENTS = r"""/* ATTACHMENTS */ JSON.stringify([...document.querySelectorAll('[data-resume-id]')]
 .filter(el=>el.getClientRects().length).map(el=>({id:el.getAttribute('data-resume-id'),name:el.innerText.trim()})))"""

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
  company_id:data?.brandComInfo?.encryptBrandId || '',company_full_name:data?.brandComInfo?.companyName || text('.company-info .company-name'),
  company_url:(box||document).querySelector('a[href*="/gongsi/"]')?.href || '',company_text:text('.company-info, .job-company-info'),
  salary:info?.salaryDesc || text('.job-salary, .salary'), city:info?.locationName || text('.job-area, .job-location'),
  contact:text('.op-btn-chat'), recruiter_id:info?.encryptUserId || (box||document).querySelector('[data-boss-id]')?.getAttribute('data-boss-id') || null,
  recruiter_name:typeof data?.bossInfo?.name==='string'?data.bossInfo.name:text('.boss-info .name, .boss-info-attr .name')};
})())"""

_LIST_ROOT = r"""
 const card=document.querySelector('.job-card-box');
 let el=card?.parentElement;
 let root=document.scrollingElement || document.documentElement;
 while(el && el!==document.body){
   if(el.scrollHeight>el.clientHeight+20 && /auto|scroll/.test(getComputedStyle(el).overflowY)){
     root=el; break;
   } el=el.parentElement;
 }
"""

SCROLL = (
    "/* SCROLL */ JSON.stringify((() => {"
    + _LIST_ROOT
    + "root.scrollTop+=Math.max(root.clientHeight,600); return true;})())"
)

LIST_PROGRESS = (
    "/* LIST_PROGRESS */ JSON.stringify((() => {"
    + _LIST_ROOT
    + r"""
 const cards=[...document.querySelectorAll('.job-card-box')];
 const viewportTop=root===document.scrollingElement?0:root.getBoundingClientRect().top;
 const viewportBottom=viewportTop+root.clientHeight;
 const visible=cards.filter(e=>{
   const rect=e.getBoundingClientRect();
   return rect.bottom>viewportTop && rect.top<viewportBottom && e.getClientRects().length;
 });
 const anchors=visible.map(e=>e.querySelector('a[href*="/job_detail/"]')?.href || '').filter(Boolean);
 const ended=[...document.querySelectorAll('.load-more,.job-list-empty,.no-more')]
   .some(e=>e.getClientRects().length && /没有更多|暂无职位|暂无推荐|到底了/.test(e.innerText));
 return {document:String(performance.timeOrigin),scroll_top:root.scrollTop,
   anchors,anchor_offset:visible[0]?.getBoundingClientRect().top-viewportTop,
   loaded_count:cards.length,ended};
})())"""
)


def restore_list_progress(progress):
    import json

    return (
        "/* RESTORE_LIST_PROGRESS */ JSON.stringify((() => {"
        + _LIST_ROOT
        + "const saved="
        + json.dumps(progress)
        + ";\n"
        + r"""
 const paths=new Set(saved.anchors.map(url=>new URL(url).pathname));
 const anchor=[...document.querySelectorAll('.job-card-box')].find(e=>{
   const url=e.querySelector('a[href*="/job_detail/"]')?.href;
   return url && paths.has(new URL(url).pathname);
 });
 const viewportTop=root===document.scrollingElement?0:root.getBoundingClientRect().top;
 const top=anchor && Number.isFinite(saved.anchor_offset)
   ? root.scrollTop+anchor.getBoundingClientRect().top-viewportTop-saved.anchor_offset
   : saved.scroll_top;
 root.scrollTop=Math.max(0,top); return true;
})())"""
    )

CAPABILITIES = r"""/* CAPABILITIES */ JSON.stringify((() => {
 const input=document.querySelector('#chat-input');
 const raw=input?.getAttribute('maxlength') || input?.getAttribute('data-maxlength') || input?.closest('.editor-container')?.__vue__?.$data?.MAX_PASTE_LENGTH;
 const limit=raw && /^[1-9]\d*$/.test(String(raw)) ? Number(raw) : null;
 const contact=document.querySelector('.op-btn-chat');
 const hint=contact?.getAttribute('data-greeting-behavior');
 const notice=[...document.querySelectorAll('.greeting-tip,.chat-tip')].map(el=>el.innerText).join(' ');
 const behavior=hint==='none' || hint==='default_message' ? hint : /自动发送.*招呼|已向BOSS发送消息/.test(notice) ? 'default_message' : 'unknown';
 // Before contact the editor does not exist. Use a conservative application cap,
 // then verify the real editor limit again immediately before sending.
 const knownPage=['www.zhipin.com','zhipin.com'].includes(location.hostname)&&Boolean(document.querySelector('.c-expect-select,.chat-conversation'));
 return {greeting_limit:limit?Math.min(limit,200):knownPage?200:null,limit_source:limit?'editor':'application',initial_contact_behavior:behavior==='unknown'&&knownPage?'may_default_message':behavior};
})())"""

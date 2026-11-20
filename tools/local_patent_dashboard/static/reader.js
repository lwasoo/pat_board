const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const query = new URLSearchParams(location.search);
const id = Number(query.get('id'));
const tabs = {basic:'基本信息',claims:'权利要求',description:'说明书',drawings:'附图',pdf:'原 PDF',fees:'年费与费用'};
const labels = {title:'专利名称',applicationNumber:'申请号',publicationNumber:'公开号/授权号',applicationDate:'申请日',owner:'申请人/权利人',inventors:'发明人',classification:'IPC 分类号',abstract:'摘要'};
const state = {patent:null,document:null,fees:[],tabs:{left:'claims',right:'drawings'},filters:{left:'all',right:'all'},page:{left:1,right:1},selected:null,annotation:false,timer:null};
let channel;
try { channel = new BroadcastChannel('patent-reader-'+id); } catch (_) { /* independent reading still works */ }
async function api(path,options={}) {
  const r=await fetch(path,options), data=await r.json().catch(()=>({}));
  if (!r.ok) throw new Error(data.error || '请求失败，请重试');
  return data;
}
function toast(text) { $('toast').textContent=text; $('toast').classList.add('show'); clearTimeout(toast.timer); toast.timer=setTimeout(()=>$('toast').classList.remove('show'),3500); }
function analysis() { return state.document?.analysis || {}; }
function docURL(suffix='') { return '/api/patents/document'+suffix+'?id='+id+'&v='+encodeURIComponent(state.document?.version || ''); }
function empty(title,detail='') { return `<div class="empty"><strong>${esc(title)}</strong>${esc(detail)}</div>`; }
function requireDocument() {
  if (!state.document) return empty('上传专利 PDF，开始对照阅读','支持文字版与扫描版 PDF，最大 50 MB / 150 页。文件仅保存在本机。');
  if (analysis().status==='failed') return empty('识别未完成',analysis().message || '请检查 OCR 配置后重新识别，原 PDF 仍可查看。');
  if (analysis().status!=='ready') return empty('识别尚未完成','可先切换到原 PDF 阅读；文字结果将在识别完成后显示。');
  return null;
}
function card(title,body) { return `<article class="card"><h2>${title}</h2>${body}</article>`; }
function infoGrid(data,fields) { return `<dl class="info-grid">${fields.map(key=>`<dt>${esc(labels[key] || key)}</dt><dd>${esc(data[key] || '—')}</dd>`).join('')}</dl>`; }
function renderBasic() {
  const p=state.patent || {}, a=analysis();
  return card('台账基本信息',infoGrid(p,['title','applicationNumber','publicationNumber','applicationDate','owner','inventors']))+
    (a.status==='ready'?card('PDF 识别信息',`<p class="muted">以下为原文件提取结果，未自动覆盖台账。可通过顶部“校对识别文字”修改。</p>${infoGrid(a.metadata || {},Object.keys(labels))}`)+
      (a.warnings?.length?card('待核对事项',`<ul class="warning-list">${a.warnings.map(w=>`<li>${esc(w)}</li>`).join('')}</ul>`):''):'')+
    card('文件',state.document?`<p>${esc(state.document.fileName)}</p><p class="muted">${(state.document.size/1024/1024).toFixed(2)} MB · ${a.pageCount || '—'} 页 · ${esc(a.reviewed?'已人工校对':'识别内容待核对')}</p>`:'<p class="muted">还没有上传 PDF。</p>');
}
function claimHTML(text) {
  const refs=new Map((analysis().references || []).map(r=>[r.number,r]));
  const raw=String(text || '');
  let output='',cursor=0;
  const pattern=/([（(])\s*(\d{1,5}[a-zA-Z]?)\s*([）)])|(\d{1,5}[a-zA-Z]?)/g;
  for(const match of raw.matchAll(pattern)) {
    const number=match[2] || match[4],reference=refs.get(number),index=match.index;
    output+=esc(raw.slice(cursor,index));
    const prefix=raw.slice(Math.max(0,index-90),index).trim().replace(/\s+/g,' ').toLowerCase();
    const name=String(reference?.name || '').replace(/\s+/g,' ').toLowerCase();
    const wholeNumber=!/[0-9A-Za-z]/.test(raw[index-1] || '') && !/[0-9A-Za-z]/.test(raw[index+match[0].length] || '');
    const named=reference && (prefix.endsWith(name) || prefix.replace(/s$/,'').endsWith(name.replace(/s$/,'')));
    const linked=reference && (match[2] || (wholeNumber && named));
    output+=linked?`<button class="reference ${state.selected===number?'selected':''}" data-reference="${number}" title="${esc(reference.name)}：查看附图中的候选编号 ${number}">${esc(match[0])}</button>`:esc(match[0]);
    cursor=index+match[0].length;
  }
  output+=esc(raw.slice(cursor));
  return output.replace(/其特征在于/g,'<span class="characterizing">其特征在于</span>');
}

function renderClaims(side) {
  const missing=requireDocument(); if(missing)return missing;
  const claims=analysis().claims || [], filter=state.filters[side];
  const independent=claims.filter(c=>c.kind==='independent').length;
  const controls=`<div class="claims-filter">${[['all','全部 '+claims.length],['independent','独权 '+independent],['dependent','从权 '+(claims.length-independent)]].map(([k,v])=>`<button data-filter="${k}" class="${filter===k?'active':''}">${v}</button>`).join('')}</div>`;
  const list=claims.filter(c=>filter==='all' || c.kind===filter).map(c=>`<article class="claim ${c.kind}"><div class="claim-head"><strong>权利要求 ${c.number}</strong><span class="pill">${c.kind==='independent'?'独立权利要求':'从属权利要求'}</span>${c.dependencies?.length?`<span class="muted">引用 ${c.dependencies.join('、')}</span>`:''}</div><div class="claim-body">${claimHTML(c.text)}</div></article>`).join('');
  const refs=analysis().references || [];
  return controls+(list || empty('未识别出权利要求','请对照原 PDF，使用顶部校对入口补充。'))+
    card('部件与附图编号',`<p class="muted">点击部件编号在另一栏查看候选位置。OCR 和编号匹配需对照原图核对。</p><div class="refs-list">${refs.map(r=>`<button data-reference="${esc(r.number)}">${esc(r.name)} <b>${esc(r.number)}</b><span class="ref-state">${r.matches.length?'候选 '+r.matches.length:'待核对'}</span></button>`).join('') || '<p class="muted">尚未识别出带编号的部件。</p>'}</div>`);
}
function renderDescription() {
  const missing=requireDocument(); if(missing)return missing;
  const text=analysis().description;
  return card('说明书',text?`<div class="description">${claimHTML(text)}</div>`:empty('未识别出说明书','可在原 PDF 中核对，并通过校对入口补充。'));
}
function renderDrawings(side) {
  const missing=requireDocument(); if(missing)return missing;
  const a=analysis(), pages=a.pages || [], figures=pages.filter(p=>p.section==='drawings');
  // All pages remain selectable, including scans whose heading was not recognized.
  if(!pages.length)return empty('没有可显示的页面');
  const target=pages.find(p=>p.number===state.page[side]) || figures[0] || pages[0];
  state.page[side]=target.number;
  const reference=(a.references || []).find(r=>r.number===state.selected);
  const matches=reference?.matches.filter(m=>m.page===target.number) || [];
  return `<div class="figure-toolbar"><select data-page aria-label="选择附图页面">${pages.map(p=>`<option value="${p.number}" ${p.number===target.number?'selected':''}>第 ${p.number} 页 · ${(p.sections || [p.section]).map(k=>tabs[k] || k).join(' / ')}</option>`).join('')}</select><button data-clear-annotations>撤销本页人工标注</button><button data-annotate>${state.annotation?'取消手工标注':'＋ 手工标注图号'}</button><span class="muted">${figures.length} 页附图 / 共 ${pages.length} 页</span></div>`+
    (reference?`<div class="notice">当前部件：<b>${esc(reference.name)} (${esc(reference.number)})</b> · 本页 ${matches.length} 个候选位置。${matches.length?'请对照引线和原图核对。':'未找到匹配位置，可切换页面或手工标注。'}</div>`:'<div class="notice">点击另一栏权利要求中的部件编号，可高亮本页候选位置。</div>')+
    (state.annotation?'<p class="muted">点击图中编号位置，再输入正确的编号。手工标注会保存在当前 PDF 的识别结果中。</p>':'')+
    `<div class="figure-page ${state.annotation?'annotating':''}" data-figure="${target.number}"><img src="${docURL('/page')}&page=${target.number}" alt="专利 PDF 第 ${target.number} 页" draggable="false">${matches.map(m=>`<span class="number-box ${m.source==='manual'?'manual':''}" title="${esc(m.text)}" style="left:${Math.max(0,m.box[0]-.004)*100}%;top:${Math.max(0,m.box[1]-.004)*100}%;width:${(m.box[2]+.008)*100}%;height:${(m.box[3]+.008)*100}%"></span>`).join('')}</div><p class="figure-caption">第 ${target.number} 页 · ${target.ocr?'扫描页 OCR':'PDF 文字提取'} · 黄色虚线为候选位置，绿色为人工补标</p>`;
}
function renderFees() {
  const groups=[['年费子项',state.fees.filter(f=>f.feeType.includes('年费'))],['其他费用子项',state.fees.filter(f=>!f.feeType.includes('年费'))]];
  return `<article class="card"><div class="card-heading"><h2>本专利的年费与费用</h2><button class="primary" data-add-fee>＋ 新增子项</button></div><p class="muted">费用归属于 ${esc(state.patent.applicationNumber)}，与本专利的文件和阅读内容集中管理。</p></article>`+
    groups.map(([title,fees])=>`<article class="card"><h2 class="fee-category">${title}<span class="count">${fees.length} 项</span></h2>${fees.map(f=>{
      const late=f.dueDate && ['待缴费','已逾期'].includes(f.status) && f.dueDate<new Date().toLocaleDateString('sv-SE');
      return `<div class="fee-item"><h3><span>${esc(f.feeType)} · ${esc(f.feeRegion || '—')}</span><span>${f.amount==null?'—':esc(f.currency)+' '+Number(f.amount).toFixed(2)}</span></h3><div class="fee-detail"><span class="${late?'overdue':''}">${late?'已逾期':esc(f.status)}</span><span>期限：${esc(f.dueDate || '未设置')}</span><span>缴费日：${esc(f.paidDate || '—')}</span></div>${f.note?`<p class="muted">${esc(f.note)}</p>`:''}<div class="fee-actions"><button data-edit-fee="${f.id}">编辑子项</button><button data-delete-fee="${f.id}">删除</button></div></div>`;
    }).join('') || '<p class="muted">暂无记录</p>'}</article>`).join('');
}
function renderPane(side) {
  const root=$(side+'Pane'), tab=state.tabs[side];
  const oldScroll=root.querySelector('.pane-content')?.scrollTop || 0;
  let body='';
  if(tab==='basic')body=renderBasic();
  if(tab==='claims')body=renderClaims(side);
  if(tab==='description')body=renderDescription();
  if(tab==='drawings')body=renderDrawings(side);
  if(tab==='fees')body=renderFees();
  if(tab==='pdf')body=state.document?`<iframe class="pdf-frame" title="原始专利 PDF" src="${docURL('/file')}#page=${state.page[side]}"></iframe><p class="muted">如果浏览器未显示 PDF，可使用顶部“下载原 PDF”。</p>`:requireDocument();
  root.innerHTML=`<nav class="pane-tabs" aria-label="${side==='left'?'左栏':'右栏'}内容">${Object.entries(tabs).map(([k,v])=>`<button data-tab="${k}" class="${k===tab?'active':''}">${v}${k==='fees'?' ('+state.fees.length+')':''}</button>`).join('')}</nav><div class="pane-content">${body}</div>`;
  root.querySelector('.pane-content').scrollTop=oldScroll;
  root.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{state.tabs[side]=b.dataset.tab;renderPane(side);root.querySelector('.pane-content').scrollTop=0;});
  root.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{state.filters[side]=b.dataset.filter;renderPane(side);});
  root.querySelectorAll('[data-reference]').forEach(b=>b.onclick=()=>selectReference(b.dataset.reference,side,true));
  const page=root.querySelector('[data-page]');if(page)page.onchange=()=>{state.page[side]=+page.value;renderPane(side);};
  const clear=root.querySelector('[data-clear-annotations]');if(clear)clear.onclick=async()=>{if(!confirm('撤销本页所有人工补标？自动识别结果会保留。'))return;try{await patchAnalysis({clearAnnotations:state.page[side]});render();}catch(e){toast(e.message);}};
  const annotate=root.querySelector('[data-annotate]');if(annotate)annotate.onclick=()=>{state.annotation=!state.annotation;renderPane(side);};
  const figure=root.querySelector('[data-figure]');if(figure)figure.onclick=async event=>{
    if(!state.annotation)return;
    const number=prompt('输入图中正确编号（例如 1、701）：',state.selected || '');if(!number)return;
    if(!/^\d{1,5}[a-zA-Z]?$/.test(number.trim()))return toast('编号格式不正确');
    const rect=figure.getBoundingClientRect(), x=Math.max(0,Math.min(.96,(event.clientX-rect.left)/rect.width-.02)), y=Math.max(0,Math.min(.98,(event.clientY-rect.top)/rect.height-.01));
    try { await patchAnalysis({annotation:{page:+figure.dataset.figure,number:number.trim(),box:[x,y,.04,.02]}});state.selected=number.trim();state.annotation=false;render();toast('已保存手工标注'); } catch(e){toast(e.message);}
  };
  root.querySelectorAll('[data-add-fee]').forEach(b=>b.onclick=()=>openFee());
  root.querySelectorAll('[data-edit-fee]').forEach(b=>b.onclick=()=>openFee(state.fees.find(f=>f.id===+b.dataset.editFee)));
  root.querySelectorAll('[data-delete-fee]').forEach(b=>b.onclick=async()=>{
    if(!confirm('确定删除这条费用子项？'))return;
    try {await api('/api/fees?id='+b.dataset.deleteFee,{method:'DELETE'});await loadFees();render();broadcast({type:'fees'});}catch(e){toast(e.message);}
  });
}
function render() { renderPane('left');renderPane('right'); }
function broadcast(message) { channel?.postMessage(message); }
function selectReference(number,side,broadcastEvent=false) {
  state.selected=number;
  const reference=(analysis().references || []).find(r=>r.number===number), other=side==='left'?'right':'left';
  const target=document.body.classList.contains('popout')?'right':document.body.classList.contains('single')?side:other;
  if(reference?.matches.length)state.page[target]=reference.matches[0].page;
  state.tabs[target]='drawings';render();
  if(!reference?.matches.length)toast('尚未找到该编号的位置，可在附图中手工补标');
  if(broadcastEvent)broadcast({type:'reference',number});
}
if(channel)channel.onmessage=async event=>{
  if(event.data.type==='reference')selectReference(String(event.data.number),'left');
  if(event.data.type==='document')await refreshDocument();
  if(event.data.type==='fees'){await loadFees();render();}
};
async function loadFees() { state.fees=(await api('/api/fees')).fees.filter(f=>f.patentId===id); }
function statusUI() {
  const d=state.document,a=analysis(), busy=d && ['queued','processing'].includes(a.status);
  $('retry').hidden=!d || busy;$('review').hidden=!d || a.status!=='ready';$('download').hidden=!d;
  $('download').href=docURL('/file');$('download').download=d?.fileName || '专利.pdf';
  $('upload').textContent=d?'↑ 更换 PDF':'↑ 上传专利 PDF';
  $('documentStatus').classList.toggle('error',a.status==='failed' || a.status==='interrupted' || a.quality?.passed===false);
  $('documentStatus').innerHTML=!d?'还没有专利文件 · 上传 PDF 后会按基本信息、权利要求、说明书、附图分类。':`<span>${esc(d.fileName)}</span> · ${esc(a.message || '待识别')}${busy?`<progress value="${Number(a.progress || 0)}" max="100"></progress>`:''}${a.needsReanalysis?' · 识别规则已更新，请点击“重新识别”':''}${a.warnings?.length?` · ${a.warnings.length} 项待核对`:''}`;
  clearTimeout(state.timer);
  if(busy)state.timer=setTimeout(()=>refreshDocument().catch(e=>{toast(e.message);state.timer=setTimeout(()=>refreshDocument().catch(e=>toast(e.message)),5000);}),1500);
}
async function refreshDocument() {
  const previous=state.document?.analysis.status;
  state.document=(await api('/api/patents/document?id='+id)).document;
  statusUI();
  if(previous!==analysis().status || analysis().status==='ready') {
    const first=analysis().pages?.find(p=>p.section==='drawings');
    if(first && previous!=='ready')state.page.right=first.number;
    if(analysis().status==='ready' && state.awaitingDrawings){state.tabs.right='drawings';state.awaitingDrawings=false;}
    render();
  }
}
$('upload').onclick=()=>$('pdfFile').click();
$('pdfFile').onchange=async()=>{
  const file=$('pdfFile').files[0];if(!file)return;
  if(file.size>50*1024*1024){toast('PDF 最大 50 MB');$('pdfFile').value='';return;}
  if(state.document && !confirm('更换 PDF 将替换本专利的原文件和识别结果，台账及费用不变。继续？')){$('pdfFile').value='';return;}
  $('upload').disabled=true;
  try {
    const result=await api('/api/patents/document?id='+id+'&filename='+encodeURIComponent(file.name),{method:'POST',headers:{'Content-Type':'application/pdf'},body:file});
    state.document=result.document;state.tabs.left='claims';state.tabs.right='pdf';state.awaitingDrawings=true;statusUI();render();broadcast({type:'document'});toast('上传成功，正在后台识别');
  } catch(e){toast(e.message);} finally {$('upload').disabled=false;$('pdfFile').value='';}
};
$('retry').onclick=async()=>{
  if(analysis().reviewed && !confirm('重新识别会替换人工校对结果，是否继续？'))return;
  try {state.document=(await api('/api/patents/document/retry?id='+id,{method:'POST'})).document;statusUI();render();broadcast({type:'document'});}catch(e){toast(e.message);}
};
$('review').onclick=()=>{
  const a=analysis();$('claimsText').value=(a.claims || []).map(c=>c.number+'. '+c.text).join('\n\n');$('descriptionText').value=a.description || '';
  $('metadataFields').innerHTML=Object.entries(labels).map(([key,label])=>`<label>${label}<input data-meta="${key}" value="${esc(a.metadata?.[key] || '')}"></label>`).join('');
  $('reviewError').textContent='';$('reviewDialog').showModal();
};
async function patchAnalysis(payload) {
  state.document=(await api('/api/patents/document?id='+id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({version:state.document.version,...payload})})).document;
  statusUI();broadcast({type:'document'});
}
$('reviewForm').onsubmit=async event=>{
  event.preventDefault();const metadata={};document.querySelectorAll('[data-meta]').forEach(input=>metadata[input.dataset.meta]=input.value);
  try{await patchAnalysis({claimsText:$('claimsText').value,description:$('descriptionText').value,metadata});$('reviewDialog').close();render();toast('校对已保存');}catch(e){$('reviewError').textContent=e.message;}
};
function openFee(f) {
  $('feeForm').reset();$('feeId').value=f?.id || '';$('feeDialogTitle').textContent=f?'编辑费用子项':'新增费用子项';$('feeError').textContent='';
  const values={feeType:f?.feeType || '年费',feeRegion:f?.feeRegion || state.patent.countryRegion || '',feeDue:f?.dueDate || '',feeAmount:f?.amount ?? '',feeCurrency:f?.currency || 'CNY',feeStatus:f?.status || '待缴费',feePaid:f?.paidDate || '',feeNote:f?.note || ''};
  for(const [key,value] of Object.entries(values))$(key).value=value;
  $('feeDialog').showModal();
}
$('feeForm').onsubmit=async event=>{
  event.preventDefault();const feeId=$('feeId').value;
  const existing=state.fees.find(f=>f.id===+feeId);
  const payload={patentId:id,feeType:$('feeType').value,feeRegion:$('feeRegion').value,dueDate:$('feeDue').value,amount:$('feeAmount').value===''?null:Number($('feeAmount').value),currency:$('feeCurrency').value,status:$('feeStatus').value,paidDate:$('feePaid').value,note:$('feeNote').value,rawValue:existing?.rawValue || ''};
  try {await api('/api/fees'+(feeId?'?id='+feeId:''),{method:feeId?'PATCH':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});await loadFees();$('feeDialog').close();render();broadcast({type:'fees'});}catch(e){$('feeError').textContent=e.message;}
};
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$(b.dataset.close).close());
$('popout').onclick=()=>{
  const popup=window.open('/reader.html?id='+id+'&mode=single&side=right&tab='+state.tabs.right,'patent-reader-'+id,'popup,width=1100,height=900,resizable=yes,scrollbars=yes');
  if(!popup)toast('浏览器阻止了弹窗，请允许本站弹出窗口');else toast('窗口已打开，可拖到第二块屏幕');
};
$('singleToggle').onclick=()=>{
  document.body.classList.toggle('single');$('singleToggle').textContent=document.body.classList.contains('single')?'左右分栏':'单栏阅读';
};
const divider=$('divider');
function moveDivider(x) { const r=$('workspace').getBoundingClientRect();$('workspace').style.setProperty('--left-width',Math.max(25,Math.min(75,(x-r.left)/r.width*100))+'%'); }
divider.onpointerdown=event=>{divider.setPointerCapture(event.pointerId);divider.onpointermove=e=>moveDivider(e.clientX);divider.onpointerup=()=>{divider.onpointermove=null;};};
divider.onkeydown=event=>{if(['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();const x=$('leftPane').getBoundingClientRect().right+(event.key==='ArrowRight'?30:-30);moveDivider(x);}};
async function start() {
  try {
    if(!Number.isInteger(id)||id<=0)throw new Error('请从专利台账选择一件专利后打开。');
    const patents=(await api('/api/patents')).patents;state.patent=patents.find(p=>p.id===id);
    if(!state.patent)throw new Error('专利不存在或已删除。');
    $('patentNumber').textContent=state.patent.applicationNumber;$('patentTitle').textContent=state.patent.title;document.title=state.patent.title+' · 专利阅读';
    $('patentTags').innerHTML=[state.patent.countryRegion,state.patent.patentType,state.patent.legalStatus].map((v,i)=>`<span class="tag ${i===2?'status':''}">${esc(v || '—')}</span>`).join('');
    await loadFees();await refreshDocument();
    if(query.get('mode')==='single')document.body.classList.add('single');
    if(query.get('side')==='right')document.body.classList.add('popout');
    const initial=query.get('tab');if(initial in tabs)state.tabs[query.get('side')==='right'?'right':'left']=initial;
    $('singleToggle').textContent=document.body.classList.contains('single')?'左右分栏':'单栏阅读';render();
  } catch(e){$('documentStatus').textContent=e.message;$('documentStatus').classList.add('error');$('upload').disabled=true;}
}
window.addEventListener('beforeunload',()=>{clearTimeout(state.timer);channel?.close();});
start();

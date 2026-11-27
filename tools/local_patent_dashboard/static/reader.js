const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const query = new URLSearchParams(location.search);
const id = Number(query.get('id'));
const library = query.get('library') === '1';
const scope = library ? 'library' : 'ledger';
const tabs = {basic:'基本信息',claims:'权利要求',description:'说明书',drawings:'附图',pdf:'原 PDF'};
const labels = {title:'专利名称',applicationNumber:'申请号',publicationNumber:'公开号/授权号',applicationDate:'申请日',owner:'申请人/权利人',inventors:'发明人',classification:'IPC 分类号',abstract:'摘要'};
const state = {patent:null,document:null,online:null,textSource:"pdf",onlineTimer:null,tabs:{left:'claims',right:'drawings'},filters:{left:'all',right:'all'},page:{left:1,right:1},selected:null,annotation:false,timer:null};
let channel;
try { channel = new BroadcastChannel('patent-reader-'+scope+'-'+id); } catch (_) { /* independent reading still works */ }
async function api(path,options={}) {
  if (library && path.startsWith('/api/patents/document')) path = path.replace('/api/patents/document', '/api/reader/document');
  const r=await fetch(path,options), data=await r.json().catch(()=>({}));
  if (!r.ok) throw new Error(data.error || '请求失败，请重试');
  return data;
}
function toast(text) { $('toast').textContent=text; $('toast').classList.add('show'); clearTimeout(toast.timer); toast.timer=setTimeout(()=>$('toast').classList.remove('show'),3500); }
function analysis() {
  const pdf = state.document?.analysis || {};
  if (state.textSource !== 'online' || !state.online?.data) return pdf;
  const text = state.online.data;
  const samePublication = String(pdf.metadata?.publicationNumber || '').replace(/[\s,-]/g, '').toUpperCase() === text.source?.publicationNumber;
  const references = (text.references || []).map(reference => ({...reference, matches: samePublication ? ((pdf.references || []).find(r => r.number === reference.number)?.matches || []) : []}));
  return {...pdf, ...text, pages: pdf.pages || [], references};
}
function docURL(suffix='') { return (library ? '/api/reader/document' : '/api/patents/document')+suffix+'?id='+id+'&v='+encodeURIComponent(state.document?.version || ''); }
function empty(title,detail='') { return `<div class="empty"><strong>${esc(title)}</strong>${esc(detail)}</div>`; }
function requireDocument() {
  if (state.textSource === 'online' && state.online?.data) return null;
  if (!state.document) return empty('上传专利 PDF，开始对照阅读','支持文字版与扫描版 PDF，最大 50 MB / 150 页。文件仅保存在本机。');
  if (analysis().status==='failed') return empty('识别未完成',analysis().message || '请检查 OCR 配置后重新识别，原 PDF 仍可查看。');
  if (analysis().status!=='ready') return empty('识别尚未完成','可先切换到原 PDF 阅读；文字结果将在识别完成后显示。');
  return null;
}
function card(title,body) { return `<article class="card"><h2>${title}</h2>${body}</article>`; }
function infoGrid(data,fields) { return `<dl class="info-grid">${fields.map(key=>`<dt>${esc(labels[key] || key)}</dt><dd>${esc(data[key] || '—')}</dd>`).join('')}</dl>`; }
function renderBasic() {
  const p=state.patent || {}, a=analysis();
  return card('文献信息',infoGrid(p,['title','applicationNumber','publicationNumber','applicationDate','owner','inventors']))+
    (a.status==='ready'?card(state.textSource === 'online' ? '电子全文信息' : 'PDF 提取信息',`<p class="muted">${state.textSource === 'online' ? '来源：USPTO Public Search · '+esc(a.source?.publicationNumber || '')+' · 获取时间：'+esc(a.source?.fetchedAt || '') : '可对照原 PDF，通过顶部校对入口修改提取内容。'}</p>${infoGrid(a.metadata || {},Object.keys(labels))}`)+
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
  const paragraphs=String(text || '').split(/\n\s*\n|(?=\[\d{4}\])/).filter(s=>s.trim()).map(block=>{
    const joined=block.replace(/([A-Za-z])-\n\s*([a-z])/g,'$1$2').replace(/\n/g,' ').replace(/([\u3400-\u9fff]) +(?=[\u3400-\u9fff])/g,'$1');
    return `<p style="white-space:normal;line-height:1.95;margin:0 0 1em;overflow-wrap:break-word">${claimHTML(joined)}</p>`;
  }).join('');
  return card('说明书',text?`<div class="description" style="white-space:normal">${paragraphs}</div>`:empty('未识别出说明书','可在原 PDF 中核对，并通过校对入口补充。'));
}
function renderDrawings(side) {
  const missing=requireDocument(); if(missing)return missing;
  const a=analysis(), pages=a.pages || [], figures=pages.filter(p=>p.section==='drawings');
  if(!figures.length)return empty('尚未识别出附图页','请重新识别，或切换原 PDF 核对。');
  const target=figures.find(p=>p.number===state.page[side]) || figures[0];
  state.page[side]=target.number;
  const reference=(a.references || []).find(r=>r.number===state.selected);
  const matches=reference?.matches.filter(m=>m.page===target.number) || [];
  return `<div class="figure-toolbar"><select data-page aria-label="选择附图页面">${figures.map(p=>`<option value="${p.number}" ${p.number===target.number?'selected':''}>第 ${p.number} 页 · 附图</option>`).join('')}</select><button data-clear-annotations>撤销本页人工标注</button><button data-annotate>${state.annotation?'取消手工标注':'＋ 手工标注图号'}</button><span class="muted">${figures.length} 页附图，可向下滚动连续浏览</span></div>`+
    (reference?`<div class="notice">当前部件：<b>${esc(reference.name)} (${esc(reference.number)})</b> · 本页 ${matches.length} 个候选位置。${matches.length?'请对照引线和原图核对。':'未找到匹配位置，可切换页面或手工标注。'}</div>`:'<div class="notice">点击另一栏权利要求中的部件编号，可高亮本页候选位置。</div>')+
    (state.annotation?'<p class="muted">点击图中编号位置，再输入正确的编号。手工标注会保存在当前 PDF 的识别结果中。</p>':'')+
    figures.map(p=>{
      const regions=!state.annotation && p.figureRegions?.length?p.figureRegions:[{top:0,height:1,label:'附图'}];
      return regions.map((region,index)=>`<div class="figure-page ${state.annotation?'annotating':''}" data-figure="${p.number}" style="aspect-ratio:${(p.aspectRatio || .707)/region.height}"><img loading="lazy" style="position:absolute;top:${-region.top/region.height*100}%;width:100%;min-height:0" src="${docURL('/page')}&page=${p.number}" alt="${esc(region.label)}，PDF 第 ${p.number} 页" draggable="false">${(reference?.matches || []).filter(m=>m.page===p.number && m.box[1]>=region.top && m.box[1]<region.top+region.height).map(m=>`<span class="number-box ${m.source==='manual'?'manual':''}" title="${esc(m.text)}" style="left:${m.box[0]*100}%;top:${(m.box[1]-region.top)/region.height*100}%;width:${m.box[2]*100}%;height:${m.box[3]/region.height*100}%"></span>`).join('')}</div><p class="figure-caption">${esc(region.label)} · PDF 第 ${p.number} 页</p>`).join('');
    }).join('');
}
function renderPane(side) {
  const root=$(side+'Pane'), tab=state.tabs[side];
  const oldScroll=root.querySelector('.pane-content')?.scrollTop || 0;
  let body='';
  if(tab==='basic')body=renderBasic();
  if(tab==='claims')body=renderClaims(side);
  if(tab==='description')body=renderDescription();
  if(tab==='drawings')body=renderDrawings(side);
  if(tab==='pdf')body=state.document?`<iframe class="pdf-frame" title="原始专利 PDF" src="${docURL('/file')}#page=${state.page[side]}"></iframe><p class="muted">如果浏览器未显示 PDF，可使用顶部“下载原 PDF”。</p>`:requireDocument();
  root.innerHTML=`<nav class="pane-tabs" aria-label="${side==='left'?'左栏':'右栏'}内容">${Object.entries(tabs).map(([k,v])=>`<button data-tab="${k}" class="${k===tab?'active':''}">${v}</button>`).join('')}</nav><div class="pane-content">${body}</div>`;
  root.querySelector('.pane-content').scrollTop=oldScroll;
  root.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{state.tabs[side]=b.dataset.tab;renderPane(side);root.querySelector('.pane-content').scrollTop=0;});
  root.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{state.filters[side]=b.dataset.filter;renderPane(side);});
  root.querySelectorAll('[data-reference]').forEach(b=>b.onclick=()=>selectReference(b.dataset.reference,side,true));
  const page=root.querySelector('[data-page]');if(page)page.onchange=()=>{state.page[side]=+page.value;root.querySelector(`[data-figure="${page.value}"]`)?.scrollIntoView({block:'start',behavior:'smooth'});};
  const clear=root.querySelector('[data-clear-annotations]');if(clear)clear.onclick=async()=>{if(!confirm('撤销本页所有人工补标？自动识别结果会保留。'))return;try{await patchAnalysis({clearAnnotations:state.page[side]});render();}catch(e){toast(e.message);}};
  const annotate=root.querySelector('[data-annotate]');if(annotate)annotate.onclick=()=>{state.annotation=!state.annotation;renderPane(side);};
  root.querySelectorAll('[data-figure]').forEach(figure=>figure.onclick=async event=>{
    if(!state.annotation)return;
    const number=prompt('输入图中正确编号（例如 1、701）：',state.selected || '');if(!number)return;
    if(!/^\d{1,5}[a-zA-Z]?$/.test(number.trim()))return toast('编号格式不正确');
    const rect=figure.getBoundingClientRect(), x=Math.max(0,Math.min(.96,(event.clientX-rect.left)/rect.width-.02)), y=Math.max(0,Math.min(.98,(event.clientY-rect.top)/rect.height-.01));
    try { await patchAnalysis({annotation:{page:+figure.dataset.figure,number:number.trim(),box:[x,y,.04,.02]}});state.selected=number.trim();state.annotation=false;render();toast('已保存手工标注'); } catch(e){toast(e.message);}
  });

}
function render() {
  const title = state.patent?.title;
  $('patentTitle').textContent = title === state.patent?.publicationNumber && analysis().metadata?.title ? analysis().metadata.title : title || '专利详情';
  renderPane('left');renderPane('right');
}
function broadcast(message) { channel?.postMessage(message); }
function selectReference(number,side,broadcastEvent=false) {
  state.selected=number;
  const reference=(analysis().references || []).find(r=>r.number===number), other=side==='left'?'right':'left';
  const target=document.body.classList.contains('popout')?'right':document.body.classList.contains('single')?side:other;
  if(reference?.matches.length)state.page[target]=reference.matches[0].page;
  state.tabs[target]='drawings';render();
  $(target+'Pane').querySelector(`[data-figure="${state.page[target]}"]`)?.scrollIntoView({block:'start',behavior:'smooth'});
  if(!reference?.matches.length)toast('尚未找到该编号的位置，可在附图中手工补标');
  if(broadcastEvent)broadcast({type:'reference',number});
}
if(channel)channel.onmessage=async event=>{
  if(event.data.type==='reference')selectReference(String(event.data.number),'left');
  if(event.data.type==='document')await refreshDocument();
  if(event.data.type==='fulltext')await refreshOnline();
};
function statusUI() {
  const d=state.document,a=d?.analysis || {}, busy=d && ['queued','processing'].includes(a.status);
  $('retry').hidden=!d || busy;$('review').hidden=!d || a.status!=='ready' || state.textSource==='online';$('download').hidden=!d;
  $('download').href=docURL('/file');$('download').download=d?.fileName || '专利.pdf';
  $('upload').textContent=d?'↑ 更换 PDF':'↑ 上传专利 PDF';
  $('documentStatus').classList.toggle('error',a.status==='failed' || a.status==='interrupted' || a.quality?.passed===false);
  $('documentStatus').innerHTML=!d?(state.online?.data?'电子全文已保存在本机，可上传 PDF 对照阅读。':'还没有专利文件 · 上传 PDF 后会按基本信息、权利要求、说明书、附图分类。'):`<span>${esc(d.fileName)}</span> · ${esc(a.message || '待识别')}${busy?`<progress value="${Number(a.progress || 0)}" max="100"></progress>`:''}${a.needsReanalysis?' · 识别规则已更新，请点击“重新识别”':''}${a.warnings?.length?` · ${a.warnings.length} 项待核对`:''}`;
  clearTimeout(state.timer);
  if(busy)state.timer=setTimeout(()=>refreshDocument().catch(e=>{toast(e.message);state.timer=setTimeout(()=>refreshDocument().catch(e=>toast(e.message)),5000);}),1500);
}
async function refreshDocument() {
  const previous=state.document?.analysis.status;
  state.document=(await api('/api/patents/document?id='+id)).document;
  if (!$('onlineNumber').value) $('onlineNumber').value = state.document?.analysis?.metadata?.publicationNumber || '';
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
  if(state.document && !confirm('更换 PDF 将替换本专利的原文件和识别结果，继续？')){$('pdfFile').value='';return;}
  $('upload').disabled=true;
  try {
    const result=await api('/api/patents/document?id='+id+'&filename='+encodeURIComponent(file.name),{method:'POST',headers:{'Content-Type':'application/pdf'},body:file});
    state.document=result.document;state.textSource='pdf';state.tabs.left='claims';state.tabs.right='pdf';state.awaitingDrawings=true;statusUI();render();broadcast({type:'document'});toast('上传成功，正在后台识别');
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
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$(b.dataset.close).close());
$('popout').onclick=()=>{
  const popup=window.open('/reader.html?id='+id+(library?'&library=1':'')+'&mode=single&side=right&tab='+state.tabs.right,'patent-reader-'+scope+'-'+id,'popup,width=1100,height=900,resizable=yes,scrollbars=yes');
  if(!popup)toast('浏览器阻止了弹窗，请允许本站弹出窗口');else toast('窗口已打开，可拖到第二块屏幕');
};
$('singleToggle').onclick=()=>{
  document.body.classList.toggle('single');$('singleToggle').textContent=document.body.classList.contains('single')?'左右分栏':'单栏阅读';
};
const divider=$('divider');
function moveDivider(x) { const r=$('workspace').getBoundingClientRect();$('workspace').style.setProperty('--left-width',Math.max(25,Math.min(75,(x-r.left)/r.width*100))+'%'); }
divider.onpointerdown=event=>{divider.setPointerCapture(event.pointerId);divider.onpointermove=e=>moveDivider(e.clientX);divider.onpointerup=()=>{divider.onpointermove=null;};};
divider.onkeydown=event=>{if(['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();const x=$('leftPane').getBoundingClientRect().right+(event.key==='ArrowRight'?30:-30);moveDivider(x);}};
function onlineUI() {
  const result = state.online || {};
  const busy = result.status === 'fetching';
  $('fetchText').disabled = busy;
  $('fetchText').textContent = busy ? '正在获取…' : '获取美国全文';
  $('useOnline').disabled = !result.data;
  $('useOnline').classList.toggle('active', state.textSource === 'online');
  $('usePdf').classList.toggle('active', state.textSource === 'pdf');
  $('onlineStatus').classList.toggle('error', result.status === 'failed');
  $('onlineStatus').textContent = result.status === 'failed' ? result.error : busy ? '正在连接 USPTO Public Search…' : result.data ? `已缓存 ${result.data.source.publicationNumber} · ${new Date(result.data.source.fetchedAt).toLocaleString()} · 可离线阅读` : '尚未获取电子全文';
}
async function refreshOnline() {
  clearTimeout(state.onlineTimer);
  const previous = state.online?.status;
  state.online = await api('/api/reader/fulltext?id=' + id + '&scope=' + scope);
  if (state.online.data && ((!state.document && !previous) || (previous === 'fetching' && state.online.status === 'ready'))) state.textSource = 'online';
  if (!state.document && state.online.data) state.tabs.right = 'description';
  onlineUI();statusUI();render();
  if (state.online.status === 'fetching') state.onlineTimer = setTimeout(() => refreshOnline().catch(error => {
    $('onlineStatus').textContent = error.message;
  }), 1500);
}
$('fetchText').onclick = async () => {
  try {
    state.online = await api('/api/reader/fulltext?id=' + id + '&scope=' + scope, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({publicationNumber:$('onlineNumber').value})});
    onlineUI();await refreshOnline();broadcast({type:'fulltext'});
  } catch (error) { toast(error.message); }
};
$('useOnline').onclick = () => { state.textSource = 'online';onlineUI();statusUI();render(); };
$('usePdf').onclick = () => { state.textSource = 'pdf';onlineUI();statusUI();render(); };
async function start() {
  try {
    if(!Number.isInteger(id)||id<=0)throw new Error('请从文献库选择一件专利后打开。');
    state.patent=(await api('/api/reader/item?id='+id+'&scope='+scope)).item;
    $('onlineNumber').value=state.patent.publicationNumber || '';
    if(!state.patent)throw new Error('专利不存在或已删除。');
    $('patentNumber').textContent=state.patent.publicationNumber || state.patent.applicationNumber || '本地文献';$('patentTitle').textContent=state.patent.title;document.title=state.patent.title+' · 专利阅读';
    $('patentTags').innerHTML=[state.patent.countryRegion,state.patent.patentType,state.patent.legalStatus].filter(Boolean).map((v,i)=>`<span class="tag ${i===2?'status':''}">${esc(v || '—')}</span>`).join('');
    await refreshDocument();await refreshOnline();
    if(query.get('mode')==='single')document.body.classList.add('single');
    if(query.get('side')==='right')document.body.classList.add('popout');
    const initial=query.get('tab');if(initial in tabs)state.tabs[query.get('side')==='right'?'right':'left']=initial;
    $('singleToggle').textContent=document.body.classList.contains('single')?'左右分栏':'单栏阅读';render();
  } catch(e){$('documentStatus').textContent=e.message;$('documentStatus').classList.add('error');$('upload').disabled=true;}
}
window.addEventListener('beforeunload',()=>{clearTimeout(state.timer);clearTimeout(state.onlineTimer);channel?.close();});
start();

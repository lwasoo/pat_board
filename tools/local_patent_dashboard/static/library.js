const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let items = [];
async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '请求失败');
  return data;
}
function itemUrl(item) {
  return `/reader.html?id=${item.id}${item.scope === 'library' ? '&library=1' : ''}`;
}
function render() {
  const search = $('librarySearch').value.trim().toLowerCase();
  const matches = items.filter(item => [item.title, item.publicationNumber, item.fileName].join(' ').toLowerCase().includes(search));
  $('libraryItems').innerHTML = matches.map(item => `<article class="library-card"><p class="muted">${esc(item.publicationNumber || '本地 PDF')}</p><h2><a href="${itemUrl(item)}">${esc(item.title)}</a></h2><p class="muted">${esc(item.fileName || '打开后可获取全文或上传 PDF')}</p><footer><a class="button primary" href="${itemUrl(item)}">打开阅读</a>${item.scope === 'library' ? `<button data-remove="${item.id}">删除</button>` : ''}</footer></article>`).join('') || '<div class="empty library-empty"><strong>暂无匹配文献</strong>添加公开号或上传专利 PDF，开始阅读。</div>';
  $('libraryItems').querySelectorAll('[data-remove]').forEach(button => {
    button.onclick = async () => {
      if (!confirm('删除这条文献及本机保存的文件和全文？')) return;
      try {
        await api('/api/reader/items?id=' + button.dataset.remove, {method: 'DELETE'});
        await load();
      } catch (error) { $('libraryStatus').textContent = error.message; }
    };
  });
}
async function load() {
  items = (await api('/api/reader/items')).items;
  $('libraryStatus').textContent = `${items.length} 篇文献`;
  render();
}
$('librarySearch').oninput = render;
$('addNumber').onclick = () => { $('addError').textContent = ''; $('addDialog').showModal(); };
$('closeAdd').onclick = () => $('addDialog').close();
$('addForm').onsubmit = async event => {
  event.preventDefault();
  $('createItem').disabled = true;
  try {
    const {item} = await api('/api/reader/items', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({title: $('newTitle').value, publicationNumber: $('newNumber').value})});
    location.href = itemUrl({...item, scope: 'library'});
  } catch (error) { $('addError').textContent = error.message; }
  finally { $('createItem').disabled = false; }
};
$('addPdf').onclick = () => $('libraryPdf').click();
$('libraryPdf').onchange = async () => {
  const file = $('libraryPdf').files[0];
  if (!file) return;
  if (file.size > 50 * 1024 * 1024) { $('libraryStatus').textContent = 'PDF 最大 50 MB'; return; }
  $('addPdf').disabled = true;
  $('libraryStatus').textContent = '正在保存 PDF…';
  try {
    const {item} = await api('/api/reader/items', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({title: file.name.replace(/\.pdf$/i, '')})});
    await api('/api/reader/document?id=' + item.id + '&filename=' + encodeURIComponent(file.name), {method: 'POST', headers: {'Content-Type': 'application/pdf'}, body: file});
    location.href = itemUrl({...item, scope: 'library'});
  } catch (error) { await load(); $('libraryStatus').textContent = error.message; }
  finally { $('addPdf').disabled = false; $('libraryPdf').value = ''; }
};
load().catch(error => { $('libraryStatus').textContent = error.message; });

$('libraryShutdown').onclick = async () => {
  if (!confirm('退出专利看板并停止本机服务？')) return;
  try {
    await api('/api/shutdown', {method: 'POST'});
    $('libraryStatus').textContent = '本机服务已停止，可以关闭此页面。';
    document.querySelectorAll('button').forEach(button => { button.disabled = true; });
  } catch (error) { $('libraryStatus').textContent = error.message; }
};

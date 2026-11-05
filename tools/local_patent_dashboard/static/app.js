const $ = (id) => document.getElementById(id);
const state = {
  patents: [],
  fees: [],
  selected: new Set(),
  selectedFees: new Set(),
  expandedFeePatents: new Set(),
  patentFilters: { country: new Set(), type: new Set(), status: new Set() },
  view: "overview",
  year: new Date().getFullYear(),
  deleteId: null,
};
const meta = {
  overview: ["专利统计总览", "核心指标、专利族与临期缴费提醒。"],
  families: ["专利族管理", "同一技术主题或同族申请集中展示。"],
  ledger: ["专利台账", "Excel 批量导入、手动维护并按需批量导出。"],
  statistics: ["统计分析", "月度、季度、年度统计统一查看。"],
  fees: ["缴费管理", "管理年费和其他费用，自动提示临期及逾期。"],
  notifications: ["消息通知", "把缴费临期和逾期事项推送到企业微信群。"],
};
const esc = (v = "") =>
  String(v).replace(
    /[&<>'"]/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[
        c
      ],
  );
const daysUntil = (date) =>
  Math.ceil(
    (new Date(date + "T00:00:00") - new Date(new Date().toDateString())) /
      86400000,
  );
function toast(text) {
  const e = $("toast");
  e.textContent = text;
  e.classList.add("show");
  setTimeout(() => e.classList.remove("show"), 2200);
}
async function api(url, options = {}) {
  const r = await fetch(url, options),
    b = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(b.error || "操作失败");
  return b;
}
function statusBadge(s) {
  const c =
    s === "已授权"
      ? "granted"
      : ["审中", "实审", "OA", "复审"].includes(s)
        ? "pending"
        : "";
  return `<span class="badge ${c}">${esc(s)}</span>`;
}
function patentTable(rows, actions = true, selectable = false) {
  if (!rows.length) return `<div class="empty">暂无符合条件的专利记录</div>`;
  return `<div class="table-wrap"><table><thead><tr>${selectable ? '<th class="check-col"><input class="select-all" type="checkbox" title="全选当前结果"></th>' : ""}<th>专利名称 / 申请号</th><th>国家 / 类型</th><th>申请 / 授权</th><th>公开号 / 到期日</th><th>发明人 / 设计人</th><th>专利族</th><th>状态</th>${actions ? "<th>操作</th>" : ""}</tr></thead><tbody>${rows.map((p) => `<tr>${selectable ? `<td class="check-col"><input class="row-check" type="checkbox" data-select="${p.id}" ${state.selected.has(p.id) ? "checked" : ""}></td>` : ""}<td class="title-cell"><strong>${esc(p.title)}</strong><small>${esc(p.applicationNumber)}${p.agentReference ? ` · ${esc(p.agentReference)}` : ""}</small></td><td class="country-type">${esc(p.countryRegion || "—")}<span class="subline">${esc(p.patentType)}</span></td><td>${esc(p.applicationDate || "—")}<span class="subline">${p.grantDate ? `授权 ${esc(p.grantDate)}` : ""}</span></td><td>${esc(p.publicationNumber || "—")}<span class="subline">${p.expirationDate ? `到期 ${esc(p.expirationDate)}` : ""}</span></td><td>${esc(p.inventors || "—")}</td><td>${esc(p.familyName || "未归族")}<span class="subline">${esc(p.familyNumber || "")}</span></td><td>${statusBadge(p.legalStatus)}</td>${actions ? `<td class="actions"><button data-edit="${p.id}">详情 / 编辑</button><button class="delete" data-delete="${p.id}">删除</button></td>` : ""}</tr>`).join("")}</tbody></table></div>`;
}
function bindPatentActions(root, selectable = false) {
  root
    .querySelectorAll("[data-edit]")
    .forEach(
      (b) =>
        (b.onclick = () =>
          openPatent(state.patents.find((p) => p.id === +b.dataset.edit))),
    );
  root.querySelectorAll("[data-delete]").forEach(
    (b) =>
      (b.onclick = () => {
        state.deleteId = +b.dataset.delete;
        $("confirmDialog").showModal();
      }),
  );
  if (selectable) {
    root.querySelectorAll("[data-select]").forEach(
      (c) =>
        (c.onchange = () => {
          const id = +c.dataset.select;
          c.checked ? state.selected.add(id) : state.selected.delete(id);
          renderLedger();
        }),
    );
    const all = root.querySelector(".select-all"),
      rows = filteredPatents();
    if (all) {
      all.checked =
        rows.length > 0 && rows.every((p) => state.selected.has(p.id));
      all.onchange = () => {
        rows.forEach((p) =>
          all.checked ? state.selected.add(p.id) : state.selected.delete(p.id),
        );
        renderLedger();
      };
    }
  }
}
function quarter(d) {
  return Math.floor((+d.slice(5, 7) - 1) / 3) + 1;
}
function years() {
  const list = [
    ...new Set(
      state.patents.map((p) => +p.applicationDate.slice(0, 4)).filter(Boolean),
    ),
  ];
  list.push(new Date().getFullYear());
  return [...new Set(list)].sort((a, b) => b - a);
}

function renderOverview() {
  const all = state.patents,
    year = +$("yearSelect").value || state.year;
  state.year = year;
  const current = all.filter((p) => +p.applicationDate.slice(0, 4) === year),
    inv = all.filter((p) => p.patentType === "发明");
  const openFees = state.fees.filter(
    (f) => f.dueDate && !["已缴费", "无需缴费", "费用记录"].includes(f.status),
  );
  const overdue = openFees.filter((f) => daysUntil(f.dueDate) < 0),
    soon = openFees.filter(
      (f) => daysUntil(f.dueDate) >= 0 && daysUntil(f.dueDate) <= 90,
    );
  $("metrics").innerHTML = [
    ["本年度申请", current.length, `${year} 年`],
    [
      "有效专利总量",
      all.filter((p) => p.legalStatus !== "终止").length,
      "自动去重",
    ],
    [
      "专利族",
      new Set(
        all.map((p) => p.familyNumber || p.familyName || p.applicationNumber),
      ).size,
      "按族归集",
    ],
    ["待处理缴费", openFees.length, `${overdue.length} 条已逾期`],
  ]
    .map(
      (x) =>
        `<article class="metric"><span class="label">${x[0]}</span><strong>${x[1]}</strong><small>${x[2]}</small></article>`,
    )
    .join("");
  const alerts = [...overdue, ...soon].sort((a, b) =>
    a.dueDate.localeCompare(b.dueDate),
  );
  $("deadlineAlerts").innerHTML = alerts.length
    ? `<div class="alert-list">${alerts
        .slice(0, 8)
        .map((f) => {
          const d = daysUntil(f.dueDate),
            late = d < 0;
          return `<div class="alert-item ${late ? "overdue" : ""}"><span><b>${esc(f.patentTitle)}</b> · ${esc(f.feeType)}</span><span>${esc(f.dueDate)} · ${late ? `逾期 ${-d} 天` : `剩余 ${d} 天`}</span></div>`;
        })
        .join("")}</div>`
    : `<div class="empty">未来 90 天没有待缴事项</div>`;
  const qs = [1, 2, 3, 4].map(
      (q) => current.filter((p) => quarter(p.applicationDate) === q).length,
    ),
    max = Math.max(...qs, 1);
  $("quarterBars").innerHTML = qs
    .map(
      (n, i) =>
        `<div class="bar-item"><strong>${n}</strong><div class="bar" style="height:${Math.max(6, (n / max) * 155)}px"></div>Q${i + 1}</div>`,
    )
    .join("");
  const td = [
    ["发明", "#0d766e"],
    ["实用新型", "#e99545"],
    ["外观设计", "#6f91a9"],
  ].map(([name, color]) => ({
    name,
    color,
    count: current.filter((p) => p.patentType === name).length,
  }));
  let total = Math.max(current.length, 1),
    acc = 0;
  const seg = td.map((x) => {
    let s = acc;
    acc += (x.count / total) * 360;
    return `${x.color} ${s}deg ${acc}deg`;
  });
  $("typeDonut").innerHTML =
    `<div class="donut" style="background:conic-gradient(${seg.join(",") || "#e7efed 0 360deg"})"></div><div class="legend">${td.map((x) => `<div><span class="dot" style="background:${x.color}"></span>${x.name}：<b>${x.count}</b></div>`).join("")}</div>`;
  $("recentTable").innerHTML = patentTable(all.slice(0, 6), false);
}
function renderYears() {
  $("yearSelect").innerHTML = years()
    .map((y) => `<option ${y === state.year ? "selected" : ""}>${y}</option>`)
    .join("");
}
function renderFamilies() {
  const groups = new Map();
  state.patents.forEach((p) => {
    const key = p.familyNumber || p.familyName || `single-${p.id}`;
    if (!groups.has(key))
      groups.set(key, {
        number: p.familyNumber || "未设置族编号",
        name: p.familyName || "未归族专利",
        rows: [],
      });
    groups.get(key).rows.push(p);
  });
  $("familyGroups").innerHTML =
    [...groups.values()]
      .map((g) => {
        const dates = g.rows
          .map((x) => x.applicationDate)
          .filter(Boolean)
          .sort();
        return `<article class="family-card"><div class="family-head"><div><h2>${esc(g.name)}</h2><p>${esc(g.number)}${dates.length ? ` · 最早申请 ${dates[0]}` : ""}</p></div><span class="family-count">${g.rows.length} 件</span></div>${patentTable(g.rows)}</article>`;
      })
      .join("") || '<div class="empty">暂无专利族</div>';
  bindPatentActions($("familyGroups"));
}
function filteredPatents() {
  const q = $("searchInput").value.trim().toLowerCase(),
    f = state.patentFilters;
  return state.patents.filter(
    (p) =>
      (!q ||
        [
          p.title,
          p.applicationNumber,
          p.publicationNumber,
          p.inventors,
          p.department,
          p.productLine,
          p.owner,
          p.familyName,
          p.familyNumber,
          p.agentReference,
          p.relatedNumbers,
          p.caseNotes,
        ].some((v) =>
          String(v || "")
            .toLowerCase()
            .includes(q),
        )) &&
      (!f.country.size || f.country.has(p.countryRegion)) &&
      (!f.type.size || f.type.has(p.patentType)) &&
      (!f.status.size || f.status.has(p.legalStatus)),
  );
}
function renderMultiFilter(id, label, values, key) {
  const root = $(id),
    selected = state.patentFilters[key];
  [...selected].forEach((v) => {
    if (!values.includes(v)) selected.delete(v);
  });
  root.querySelector("summary").textContent = selected.size
    ? `${label} ${selected.size} 项`
    : `全部${label}`;
  root.querySelector(".multi-filter-menu").innerHTML =
    values
      .map(
        (v) =>
          `<label><input type="checkbox" value="${esc(v)}" ${selected.has(v) ? "checked" : ""}>${esc(v)}</label>`,
      )
      .join("") || '<span class="muted">暂无选项</span>';
  root.querySelectorAll('input[type="checkbox"]').forEach(
    (box) =>
      (box.onchange = () => {
        box.checked ? selected.add(box.value) : selected.delete(box.value);
        root.querySelector("summary").textContent = selected.size
          ? `${label} ${selected.size} 项`
          : `全部${label}`;
        renderLedger();
      }),
  );
}
function renderFilterOptions() {
  const feeRegion = $("feeRegionFilter").value,
    countries = [
      ...new Set(state.patents.map((p) => p.countryRegion).filter(Boolean)),
    ].sort(),
    types = [
      ...new Set(state.patents.map((p) => p.patentType).filter(Boolean)),
    ].sort(),
    statuses = [
      ...new Set(state.patents.map((p) => p.legalStatus).filter(Boolean)),
    ].sort(),
    feeRegions = [
      ...new Set(state.fees.map((f) => f.feeRegion).filter(Boolean)),
    ].sort();
  renderMultiFilter("countryFilter", "国家/地区", countries, "country");
  renderMultiFilter("typeFilter", "类型", types, "type");
  renderMultiFilter("statusFilter", "状态", statuses, "status");
  $("feeRegionFilter").innerHTML =
    '<option value="">全部国家/地区</option>' +
    feeRegions
      .map(
        (v) =>
          `<option ${v === feeRegion ? "selected" : ""}>${esc(v)}</option>`,
      )
      .join("");
}
function updateSelectionHint() {
  const count = state.selected.size;
  $("selectionHint").textContent = count
    ? `已勾选 ${count} 条；可批量导出或删除。`
    : `未勾选记录；点击“批量导出”将导出当前筛选结果。`;
  $("batchDeleteBtn").disabled = !count;
}
function renderLedger() {
  const rows = filteredPatents(),
    families = new Set(
      rows.map((p) => p.familyNumber || p.familyName || p.applicationNumber),
    ).size,
    granted = rows.filter((p) => p.legalStatus === "已授权").length;
  $("ledgerSummary").innerHTML =
    `<div><strong>${rows.length}</strong>筛选结果</div><div><strong>${families}</strong>专利族</div><div><strong>${granted}</strong>已授权</div><div><strong>${state.selected.size}</strong>已勾选</div>`;
  $("ledgerTable").innerHTML = patentTable(rows, true, true);
  bindPatentActions($("ledgerTable"), true);
  updateSelectionHint();
}
async function importExcel() {
  const file = $("excelFile").files[0];
  if (!file) return;
  try {
    toast("正在导入 Excel…");
    const result = await api("/api/patents/excel", {
      method: "POST",
      headers: {
        "Content-Type":
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      },
      body: file,
    });
    $("excelFile").value = "";
    await load();
    switchView("ledger");
    const feeText = result.feeImported
      ? `，${result.feeImported} 条费用记录已进入缴费管理`
      : "";
    toast(`已导入 ${result.imported} 条专利记录${feeText}`);
  } catch (err) {
    $("excelFile").value = "";
    toast(err.message);
  }
}
async function exportExcel() {
  const rows = state.selected.size
    ? state.patents.filter((p) => state.selected.has(p.id))
    : filteredPatents();
  if (!rows.length) return toast("当前没有可导出的记录");
  try {
    const ids = rows.map((p) => p.id).join(","),
      response = await fetch(
        `/api/patents/export?ids=${encodeURIComponent(ids)}`,
      );
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.error || "导出失败");
    }
    const blob = await response.blob(),
      url = URL.createObjectURL(blob),
      link = document.createElement("a");
    link.href = url;
    link.download = `专利台账导出-${new Date().toISOString().slice(0, 10).replaceAll("-", "")}.xlsx`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    toast(`已导出 ${rows.length} 条专利记录`);
  } catch (err) {
    toast(err.message);
  }
}
async function batchDeletePatents() {
  const ids = [...state.selected];
  if (!ids.length) return toast("请先勾选要删除的专利");
  if (
    !confirm(
      `确定删除已勾选的 ${ids.length} 条专利？\n其关联缴费记录也会一并删除，且无法撤销。`,
    )
  )
    return;
  try {
    const result = await api(`/api/patents?ids=${ids.join(",")}`, {
      method: "DELETE",
    });
    state.selected.clear();
    await load();
    toast(`已删除 ${result.deleted} 条专利记录`);
  } catch (err) {
    toast(err.message);
  }
}

function renderStatsControls() {
  const mode = $("statsMode").value,
    ys = years(),
    opts = [];
  if (mode === "annual") ys.forEach((y) => opts.push([String(y), `${y} 年`]));
  else if (mode === "quarterly")
    ys.forEach((y) =>
      [1, 2, 3, 4].forEach((q) => opts.push([`${y}-Q${q}`, `${y} 年 Q${q}`])),
    );
  else
    ys.forEach((y) => {
      for (let m = 1; m <= 12; m++)
        opts.push([`${y}-${String(m).padStart(2, "0")}`, `${y} 年 ${m} 月`]);
    });
  $("statsPeriod").innerHTML = opts
    .map(([v, l]) => `<option value="${v}">${l}</option>`)
    .join("");
  renderStatistics();
}
function renderStatistics() {
  const mode = $("statsMode").value,
    v = $("statsPeriod").value;
  let rows = [];
  if (mode === "annual")
    rows = state.patents.filter((p) => p.applicationDate.startsWith(v + "-"));
  else if (mode === "quarterly") {
    const [y, q] = v.split("-Q");
    rows = state.patents.filter(
      (p) =>
        p.applicationDate.startsWith(y + "-") &&
        quarter(p.applicationDate) === +q,
    );
  } else rows = state.patents.filter((p) => p.applicationDate.startsWith(v));
  $("periodSummary").innerHTML =
    `<div><strong>${rows.length}</strong>申请</div><div><strong>${rows.filter((p) => p.legalStatus === "已授权").length}</strong>已授权</div><div><strong>${rows.filter((p) => p.patentType === "发明").length}</strong>发明</div><div><strong>${new Set(rows.map((p) => p.familyNumber || p.familyName || p.applicationNumber)).size}</strong>专利族</div>`;
  $("periodTable").innerHTML = patentTable(rows, false);
}

function feeStatus(f) {
  if (!f.dueDate) return [f.status, ""];
  const d = daysUntil(f.dueDate);
  if (f.status === "待缴费" && d < 0) return ["已逾期", "fee-overdue"];
  if (f.status === "待缴费" && d <= 90) return [f.status, "fee-soon"];
  return [f.status, ""];
}
function filteredFees() {
  const filter = $("feeStatusFilter").value,
    region = $("feeRegionFilter").value;
  return state.fees.filter((f) => {
    const s = feeStatus(f)[0];
    return (!filter || s === filter) && (!region || f.feeRegion === region);
  });
}
function feeGroups() {
  const groups = new Map();
  filteredFees().forEach((f) => {
    if (!groups.has(f.patentId))
      groups.set(f.patentId, {
        patentId: f.patentId,
        applicationNumber: f.applicationNumber,
        patentTitle: f.patentTitle,
        items: [],
      });
    groups.get(f.patentId).items.push(f);
  });
  return [...groups.values()];
}
function updateFeeSelectionHint() {
  const count = state.selectedFees.size;
  $("feeSelectionHint").textContent = count
    ? `已勾选 ${count} 件专利，可删除这些专利的全部费用。`
    : "每件专利只显示一行；展开后查看费用明细。";
  $("feeBatchDeleteBtn").disabled = !count;
}
function feeGroupStatus(items) {
  const states = items.map((f) => feeStatus(f)[0]);
  if (states.includes("已逾期")) return ["已逾期", "fee-overdue"];
  if (states.includes("待缴费")) return ["有待缴", "fee-soon"];
  if (states.every((s) => s === "已缴费")) return ["已缴费", ""];
  return ["费用记录", ""];
}
function feeTotals(items) {
  const totals = {};
  items.forEach((f) => {
    if (f.amount != null)
      totals[f.currency] = (totals[f.currency] || 0) + Number(f.amount);
  });
  return (
    Object.entries(totals)
      .map(([c, n]) => `${esc(c)} ${n.toFixed(2)}`)
      .join(" / ") || "—"
  );
}
function feeDetailRows(group) {
  return group.items
    .map((f) => {
      const [s, c] = feeStatus(f),
        amount =
          f.amount == null
            ? ""
            : `${esc(f.currency)} ${Number(f.amount).toFixed(2)}`,
        raw =
          f.rawValue && String(f.rawValue) !== String(f.amount ?? "")
            ? esc(f.rawValue)
            : "";
      return `<tr class="fee-detail-row"><td>${esc(f.feeRegion || "—")}</td><td>${esc(f.feeType)}</td><td class="${c}">${esc(f.dueDate || "—")}<span class="subline">${f.paidDate ? `缴费 ${esc(f.paidDate)}` : ""}</span></td><td>${amount || raw || "—"}${amount && raw ? `<span class="subline">原值 ${raw}</span>` : ""}</td><td>${statusBadge(s)}</td><td>${esc(f.note || "")}</td><td class="actions"><button data-fee-edit="${f.id}">编辑</button><button class="delete" data-fee-delete="${f.id}">删除</button></td></tr>`;
    })
    .join("");
}
function renderFees() {
  const groups = feeGroups();
  $("feesTable").innerHTML = groups.length
    ? `<div class="table-wrap"><table><thead><tr><th class="check-col"><input class="fee-select-all" type="checkbox" title="全选当前专利"></th><th>专利</th><th>国家/地区</th><th>费用项</th><th>金额合计</th><th>最近待缴期限</th><th>状态</th><th>操作</th></tr></thead><tbody>${groups
        .map((g) => {
          const regions =
              [
                ...new Set(g.items.map((x) => x.feeRegion).filter(Boolean)),
              ].join(" / ") || "—",
            pending = g.items
              .filter(
                (x) =>
                  x.dueDate &&
                  !["已缴费", "无需缴费", "费用记录"].includes(x.status),
              )
              .sort((a, b) => a.dueDate.localeCompare(b.dueDate))[0],
            status = feeGroupStatus(g.items),
            expanded = state.expandedFeePatents.has(g.patentId);
          return `<tr><td class="check-col"><input class="fee-row-check" type="checkbox" data-fee-select="${g.patentId}" ${state.selectedFees.has(g.patentId) ? "checked" : ""}></td><td class="title-cell"><strong>${esc(g.patentTitle)}</strong><small>${esc(g.applicationNumber)}</small></td><td>${esc(regions)}</td><td>${g.items.length} 项</td><td class="fee-amount">${feeTotals(g.items)}</td><td>${pending ? esc(pending.dueDate) : "—"}</td><td><span class="badge ${status[1]}">${status[0]}</span></td><td class="actions"><button data-fee-toggle="${g.patentId}">${expanded ? "收起" : "查看明细"}</button><button data-fee-add="${g.patentId}">新增费用</button><button class="delete" data-fee-group-delete="${g.patentId}">删除全部</button></td></tr>${expanded ? `<tr class="fee-detail-wrap"><td></td><td colspan="7"><table class="fee-detail-table"><thead><tr><th>国家</th><th>费用项目</th><th>期限 / 缴费日</th><th>金额 / 原始值</th><th>状态</th><th>备注</th><th>操作</th></tr></thead><tbody>${feeDetailRows(g)}</tbody></table></td></tr>` : ""}`;
        })
        .join("")}</tbody></table></div>`
    : '<div class="empty">暂无缴费记录</div>';
  $("feesTable")
    .querySelectorAll("[data-fee-select]")
    .forEach(
      (c) =>
        (c.onchange = () => {
          const id = +c.dataset.feeSelect;
          c.checked
            ? state.selectedFees.add(id)
            : state.selectedFees.delete(id);
          updateFeeSelectionHint();
        }),
    );
  const all = $("feesTable").querySelector(".fee-select-all");
  if (all) {
    all.checked =
      groups.length > 0 &&
      groups.every((g) => state.selectedFees.has(g.patentId));
    all.onchange = () => {
      groups.forEach((g) =>
        all.checked
          ? state.selectedFees.add(g.patentId)
          : state.selectedFees.delete(g.patentId),
      );
      renderFees();
    };
  }
  $("feesTable")
    .querySelectorAll("[data-fee-toggle]")
    .forEach(
      (b) =>
        (b.onclick = () => {
          const id = +b.dataset.feeToggle;
          state.expandedFeePatents.has(id)
            ? state.expandedFeePatents.delete(id)
            : state.expandedFeePatents.add(id);
          renderFees();
        }),
    );
  $("feesTable")
    .querySelectorAll("[data-fee-add]")
    .forEach((b) => (b.onclick = () => openFee(null, +b.dataset.feeAdd)));
  $("feesTable")
    .querySelectorAll("[data-fee-edit]")
    .forEach(
      (b) =>
        (b.onclick = () =>
          openFee(state.fees.find((f) => f.id === +b.dataset.feeEdit))),
    );
  $("feesTable")
    .querySelectorAll("[data-fee-delete]")
    .forEach(
      (b) =>
        (b.onclick = async () => {
          if (!confirm("删除这条费用明细？")) return;
          await api(`/api/fees?id=${b.dataset.feeDelete}`, {
            method: "DELETE",
          });
          await load();
          toast("费用明细已删除");
        }),
    );
  $("feesTable")
    .querySelectorAll("[data-fee-group-delete]")
    .forEach(
      (b) => (b.onclick = () => deleteFeeGroups([+b.dataset.feeGroupDelete])),
    );
  updateFeeSelectionHint();
}
async function deleteFeeGroups(ids) {
  if (!ids.length) return toast("请先勾选专利");
  if (
    !confirm(`确定删除 ${ids.length} 件专利的全部费用记录？专利本身不会删除。`)
  )
    return;
  try {
    const result = await api(`/api/fees?patentIds=${ids.join(",")}`, {
      method: "DELETE",
    });
    ids.forEach((id) => state.selectedFees.delete(id));
    await load();
    toast(`已删除 ${result.deleted} 条费用明细`);
  } catch (err) {
    toast(err.message);
  }
}
async function batchDeleteFees() {
  return deleteFeeGroups([...state.selectedFees]);
}
function openFee(f = null, patentId = null) {
  $("feeForm").reset();
  $("feeFormError").textContent = "";
  $("feeId").value = f?.id || "";
  $("feeDialogTitle").textContent = f ? "编辑缴费" : "新增缴费";
  $("feePatentId").innerHTML = state.patents
    .map(
      (p) =>
        `<option value="${p.id}">${esc(p.applicationNumber)} · ${esc(p.title)}</option>`,
    )
    .join("");
  $("feeType").value = "年费";
  $("feeStatus").value = "待缴费";
  $("currency").value = "CNY";
  if (patentId) $("feePatentId").value = patentId;
  if (f) {
    $("feePatentId").value = f.patentId;
    $("feeRegion").value = f.feeRegion || "";
    $("feeType").value = f.feeType;
    $("dueDate").value = f.dueDate || "";
    $("feeAmount").value = f.amount ?? "";
    $("feeRawValue").value = f.rawValue || "";
    $("currency").value = f.currency;
    $("feeStatus").value = f.status;
    $("paidDate").value = f.paidDate || "";
    $("feeNote").value = f.note || "";
  }
  $("feeDialog").showModal();
}
async function saveFee(e) {
  e.preventDefault();
  const id = $("feeId").value,
    p = {
      patentId: +$("feePatentId").value,
      feeRegion: $("feeRegion").value,
      feeType: $("feeType").value,
      dueDate: $("dueDate").value,
      amount: $("feeAmount").value === "" ? null : +$("feeAmount").value,
      rawValue: $("feeRawValue").value,
      currency: $("currency").value,
      status: $("feeStatus").value,
      paidDate: $("paidDate").value,
      note: $("feeNote").value,
    };
  try {
    await api(`/api/fees${id ? `?id=${id}` : ""}`, {
      method: id ? "PATCH" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(p),
    });
    $("feeDialog").close();
    await load();
    toast("缴费记录已保存");
  } catch (err) {
    $("feeFormError").textContent = err.message;
  }
}

function renderNotificationStatus(n) {
  const last = n.lastRun;
  $("notificationStatus").innerHTML =
    `<strong>${n.enabled ? "自动推送已启用" : "自动推送未启用"}</strong><div>Webhook：${n.webhookConfigured ? esc(n.webhookMasked) : "未配置"}</div><div>每日检查：${esc(n.scheduleTime || "09:00")}</div><div>最近运行：${last ? `${esc(last.runAt)} · ${last.status === "success" ? "成功" : "失败"}<br>${esc(last.message)}` : "尚未运行"}</div>`;
}
async function loadNotificationConfig() {
  try {
    const n = await api("/api/notifications");
    $("notificationEnabled").checked = n.enabled;
    $("notificationTime").value = n.scheduleTime || "09:00";
    $("webhookUrl").value = "";
    $("webhookUrl").placeholder = n.webhookConfigured
      ? `已保存 ${n.webhookMasked}；留空表示不修改`
      : "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...";
    renderNotificationStatus(n);
  } catch (err) {
    $("notificationError").textContent = err.message;
  }
}
async function saveNotification(e) {
  e.preventDefault();
  $("notificationError").textContent = "";
  try {
    const result = await api("/api/notifications/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        enabled: $("notificationEnabled").checked,
        scheduleTime: $("notificationTime").value,
        webhook: $("webhookUrl").value.trim(),
      }),
    });
    $("webhookUrl").value = "";
    renderNotificationStatus(result);
    toast(result.message || "通知设置已保存");
  } catch (err) {
    $("notificationError").textContent = err.message;
  }
}
async function testNotification() {
  try {
    $("notificationError").textContent = "";
    const result = await api("/api/notifications/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ webhook: $("webhookUrl").value.trim() }),
    });
    toast(result.message);
  } catch (err) {
    $("notificationError").textContent = err.message;
  }
}
async function runNotification() {
  try {
    $("notificationError").textContent = "";
    const result = await api("/api/notifications/run", { method: "POST" });
    toast(result.message);
    await loadNotificationConfig();
  } catch (err) {
    $("notificationError").textContent = err.message;
  }
}

function switchView(name) {
  state.view = name;
  document
    .querySelectorAll("nav button")
    .forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  document
    .querySelectorAll(".view")
    .forEach((v) => v.classList.remove("active"));
  $(name + "View").classList.add("active");
  $("viewTitle").textContent = meta[name][0];
  $("viewSubtitle").textContent = meta[name][1];
  if (name === "overview") renderOverview();
  if (name === "families") renderFamilies();
  if (name === "ledger") renderLedger();
  if (name === "statistics") renderStatsControls();
  if (name === "fees") renderFees();
  if (name === "notifications") loadNotificationConfig();
}
const patentFields = [
  "applicationNumber",
  "title",
  "countryRegion",
  "patentType",
  "applicationDate",
  "publicationDate",
  "publicationNumber",
  "grantDate",
  "expirationDate",
  "legalStatus",
  "department",
  "productLine",
  "familyNumber",
  "familyName",
  "inventors",
  "owner",
  "agentReference",
  "claimCount",
  "independentClaimCount",
  "pageCount",
  "cnDraftStatus",
  "foreignDraftStatus",
  "relatedNumbers",
  "caseNotes",
];
function patentPayload() {
  const p = {};
  patentFields.forEach((id) => (p[id] = $(id).value));
  ["claimCount", "independentClaimCount", "pageCount"].forEach(
    (id) => (p[id] = p[id] === "" ? null : +p[id]),
  );
  return p;
}
function openPatent(p = null) {
  $("patentForm").reset();
  $("formError").textContent = "";
  $("patentId").value = p?.id || "";
  $("dialogTitle").textContent = p ? "专利详情与编辑" : "新增专利";
  const d = {
    countryRegion: "中国",
    patentType: "发明",
    legalStatus: "审中",
    department: "研发部",
    productLine: "未归类",
    owner: "本公司",
  };
  patentFields.forEach((id) => ($(id).value = p?.[id] ?? d[id] ?? ""));
  $("patentDialog").showModal();
}
async function savePatent(e) {
  e.preventDefault();
  $("formError").textContent = "";
  try {
    const id = $("patentId").value;
    await api(`/api/patents${id ? `?id=${id}` : ""}`, {
      method: id ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patentPayload()),
    });
    $("patentDialog").close();
    await load();
    toast("专利记录已保存");
  } catch (err) {
    $("formError").textContent = err.message;
  }
}
async function deletePatent() {
  try {
    await api(`/api/patents?id=${state.deleteId}`, { method: "DELETE" });
    state.selected.delete(state.deleteId);
    $("confirmDialog").close();
    await load();
    toast("记录已删除");
  } catch (e) {
    toast(e.message);
  }
}
async function load() {
  try {
    const [p, f] = await Promise.all([api("/api/patents"), api("/api/fees")]);
    state.patents = p.patents || [];
    state.fees = f.fees || [];
    const ids = new Set(state.patents.map((x) => x.id)),
      feePatentIds = new Set(state.fees.map((x) => x.patentId));
    state.selected = new Set([...state.selected].filter((id) => ids.has(id)));
    state.selectedFees = new Set(
      [...state.selectedFees].filter((id) => feePatentIds.has(id)),
    );
    state.expandedFeePatents = new Set(
      [...state.expandedFeePatents].filter((id) => feePatentIds.has(id)),
    );
    renderYears();
    renderFilterOptions();
    switchView(state.view);
  } catch (e) {
    toast(e.message);
  }
}

document
  .querySelectorAll("nav button")
  .forEach((b) => (b.onclick = () => switchView(b.dataset.view)));
document
  .querySelectorAll("[data-go]")
  .forEach((b) => (b.onclick = () => switchView(b.dataset.go)));
$("addBtn").onclick = () => openPatent();
$("refreshBtn").onclick = load;
$("yearSelect").onchange = renderOverview;
$("searchInput").oninput = renderLedger;
$("excelImportBtn").onclick = () => $("excelFile").click();
$("excelFile").onchange = importExcel;
$("exportBtn").onclick = exportExcel;
$("batchDeleteBtn").onclick = batchDeletePatents;
$("patentForm").onsubmit = savePatent;
$("closeDialog").onclick = $("cancelDialog").onclick = () =>
  $("patentDialog").close();
$("cancelDelete").onclick = () => $("confirmDialog").close();
$("confirmDelete").onclick = deletePatent;
$("addFeeBtn").onclick = () => openFee();
$("feeBatchDeleteBtn").onclick = batchDeleteFees;
$("feeForm").onsubmit = saveFee;
$("closeFeeDialog").onclick = $("cancelFeeDialog").onclick = () =>
  $("feeDialog").close();
$("feeRegionFilter").onchange = renderFees;
$("feeStatusFilter").onchange = renderFees;
$("statsMode").onchange = renderStatsControls;
$("statsPeriod").onchange = renderStatistics;
$("notificationForm").onsubmit = saveNotification;
$("testNotificationBtn").onclick = testNotification;
$("runNotificationBtn").onclick = runNotification;
$("shutdownBtn").onclick = async () => {
  if (!confirm("停止本地看板服务？需要使用时可再次双击桌面图标。")) return;
  try {
    await api("/api/shutdown", { method: "POST" });
    document.body.innerHTML =
      '<main><section class="hero"><div><p class="eyebrow">LOCAL SERVICE</p><h1>本地看板已退出</h1><p>需要使用时，再次双击桌面的“企业专利统计看板”。</p></div></section></main>';
  } catch (e) {
    toast(e.message);
  }
};
load();

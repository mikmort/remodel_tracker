"use strict";

const $ = id => document.getElementById(id);
let project, historyEntries = null, editing = null, page = 0;
const filters = new Map();
const pages = [
  ["overview", "Project overview", "Current choices and preserved evidence, not a live jobsite or accounting feed."],
  ["selections", "Selections", "Choices, reference prices, procurement, and purchaser remain separate. Record owner decisions with a basis."],
  ["doors", "Door hardware", "Current opening-level choices. Quantities, source drawings, and supplier packages retain their own review status."],
  ["contacts", "Team and contacts", "Project roles and contact evidence."],
  ["meetings", "Meeting notes", "Printed meeting dates and source qualifications, not simply email dates."],
  ["decisions", "Decisions and directions", "Scoped source-supported directions. A later email is not blanket approval."],
  ["liveBudget", "Contractor budget", "Reviewed allowances, changes, and cost-code detail. Source snapshot, not a fixed-price or final-cost forecast."],
  ["ownerBudget", "Owner budget", "Reviewed owner estimates, costs, and documented payments. Values are not recomputed from editable selection prices."],
  ["spending", "All spending", "Reviewed totals and their scope. Deposits, parent invoices, and pass-through detail must not be added as new expenses."],
  ["purchases", "Orders and invoices", "Original order and invoice parents remain distinct from included lines and settlement evidence."],
  ["drawings", "Drawings", "Plans and scoped revisions. No single coordinated latest construction issue is inferred."],
  ["changeOrders", "Change orders", "Execution evidence is separate from invoices, payments, and the master agreement."],
  ["timeline", "Timeline", "Planning, authorization, vendor reports, and confirmed transactions retain their different meanings."],
  ["issues", "Coordination items", "Active issues, historical gaps, and unresolved source evidence remain distinct."],
  ["contracts", "Contracts", "Drafts and execution gaps remain explicit."],
  ["documents", "Document library", "Preserved original evidence and prior revisions. Being in an export folder does not mean superseded."],
  ["registers", "Downloads", "Current app exports and the separately dated original publication registers."],
  ["provenance", "History and provenance", "App audit history, source coverage, review limitations, and imported reconciliation details."],
];
const labels = {
  id: "ID", selectionState: "Choice", purchaseState: "Purchase", purchaser: "Purchaser",
  priceBasis: "Price basis", selectionBasis: "Choice basis", selectionFollowUp: "Follow-up",
  ownerEdit: "Owner decision", sourceSha256: "Source SHA-256", ownerBudget: "Owner budget",
  liveBudget: "Contractor budget", contractorForecast: "Source contractor forecast",
};
const label = key => labels[key] || key.replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/_/g, " ").replace(/^./, c => c.toUpperCase());
const isObject = value => value !== null && typeof value === "object";
function node(tag, attributes = {}, ...children) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (key.startsWith("on")) element.addEventListener(key.slice(2).toLowerCase(), value);
    else if (value !== false && value != null) element.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child != null) element.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return element;
}
function showMessage(message, error = false) {
  $("message").textContent = message;
  $("message").className = error ? "error" : "";
  $("message").hidden = !message;
}
async function api(url, options = {}) {
  const response = await fetch(url, { credentials: "same-origin", cache: "no-store", ...options });
  if (!response.ok) {
    let message = `Request failed (${response.status}).`;
    try { message = (await response.json()).detail || message; }
    catch { /* A gateway can return an HTML error rather than JSON. */ }
    if (response.status === 401) showAccess("Your session expired. Sign in again to continue.");
    throw new Error(message);
  }
  return response.json();
}
function safeUrl(url) {
  if (typeof url !== "string") return null;
  try {
    const parsed = new URL(url, location.origin);
    if (parsed.origin === location.origin && parsed.pathname.startsWith("/api/documents/")) return parsed.href;
    if (parsed.protocol === "https:" && !parsed.username && !parsed.password) return parsed.href;
  } catch { return null; }
  return null;
}
function evidence(links = []) {
  return node("div", { class: "links" }, links.map(item => {
    const url = safeUrl(item.url);
    return url
      ? node("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, item.label || item.title || "Source document")
      : node("span", { class: "muted" }, "Unsupported source link");
  }));
}
function download(path, title) {
  return node("a", { class: "button secondary", href: `/api/exports/${path}` }, title);
}
function badge(value) {
  return node("span", { class: `badge ${String(value).toLowerCase().replace(/[^a-z]/g, "")}` }, value);
}
function lazyDetails(title, build) {
  const details = node("details", {}, node("summary", {}, title));
  let built = false;
  details.addEventListener("toggle", () => {
    if (details.open && !built) {
      details.append(build());
      built = true;
    }
  });
  return details;
}
function structured(value, title = "Details") {
  if (!isObject(value)) {
    if (typeof value === "boolean") return node("span", {}, value ? "Yes" : "No");
    return node("span", { class: "record" }, value === "" || value == null ? "Not recorded" : value);
  }
  if (Array.isArray(value)) {
    if (!value.length) return node("span", { class: "muted" }, "None recorded");
    if (value.every(item => isObject(item) && !Array.isArray(item) && "url" in item)) return evidence(value);
    if (value.every(item => !isObject(item))) return node("span", {}, value.join("; "));
    return lazyDetails(`${title} (${value.length})`, () => {
      const container = node("div");
      let next = 0;
      const more = node("button", { type: "button", class: "secondary" }, "Show more");
      const add = () => {
        const batch = value.slice(next, next + 25);
        for (const [index, item] of batch.entries()) {
          const entry = isObject(item) ? item : {};
          container.insertBefore(lazyDetails(
            entry.title || entry.description || entry.name || entry.item || entry.id || `Record ${next + index + 1}`,
            () => structured(item)
          ), more);
        }
        next += batch.length;
        more.hidden = next >= value.length;
      };
      more.addEventListener("click", add);
      container.append(more);
      add();
      return container;
    });
  }
  if (value.url) return evidence([value]);
  const list = node("dl");
  for (const [key, item] of Object.entries(value)) {
    if (key.endsWith("Base64") || key === "download") continue;
    list.append(node("dt", {}, label(key)), node("dd", {}, structured(item, label(key))));
  }
  return list;
}
function table(rows, fields, editKind = null) {
  if (!rows.length) return node("div", { class: "empty" }, "No matching records. Try another filter.");
  const head = node("tr", {}, fields.map(key => node("th", { scope: "col" }, label(key))), node("th", { scope: "col" }, "Evidence / details"));
  const body = node("tbody");
  for (const row of rows) {
    const extra = Object.fromEntries(Object.entries(row).filter(([key]) => !fields.includes(key) && key !== "links"));
    const cells = fields.map(key => {
      let content;
      if (["selectionState", "purchaseState", "confirmation"].includes(key)) content = badge(row[key] || "Not recorded");
      else if (key === "product" && row.image) {
        const url = safeUrl(row.image.url);
        content = node("div", {}, row[key],
          url && new URL(url).origin === location.origin
            ? node("img", { src: url, alt: row.product || "Reviewed product image", class: "preview product-image", loading: "lazy" })
            : null);
      }
      else if (key === "email" && typeof row[key] === "string") {
        content = row[key].split(";").filter(Boolean).map(email => node("div", {}, node("a", { href: `mailto:${encodeURIComponent(email.trim())}` }, email.trim())));
      } else content = structured(row[key], label(key));
      return node("td", {}, content);
    });
    const details = node("td", {}, evidence(row.links), lazyDetails("Record details", () => structured(extra)));
    if (editKind) {
      details.append(node("div", { class: "actions" }, node("button", {
        type: "button", class: "secondary", onclick: () => openEditor(editKind, row),
      }, "Edit choice")));
      if (row.ownerEdit) details.append(badge("Owner edited"));
    }
    body.append(node("tr", { "data-record-id": row.id || "" }, ...cells, details));
  }
  return node("div", { class: "table-wrap" }, node("table", {}, node("thead", {}, head), body));
}
function filterSelect(key, title, values) {
  const control = node("select", { "aria-label": title });
  for (const value of ["All", ...new Set(values.filter(Boolean))]) {
    const option = node("option", { value }, value);
    option.selected = value === (filters.get(key) || "All");
    control.append(option);
  }
  control.addEventListener("change", () => { filters.set(key, control.value); page = 0; render(); });
  $("toolbar").append(node("label", {}, title, control));
}
function matches(row, keys) {
  return keys.every(key => !filters.get(key) || filters.get(key) === "All" || row[key] === filters.get(key));
}
function currentRows(rows) {
  const isHistorical = row => row.history || /background|earlier research/i.test(row.group || "");
  if (!rows.some(isHistorical)) return rows;
  const toggle = node("input", { type: "checkbox" });
  toggle.checked = filters.get("includeHistory") === "yes" || filters.get("selectionState") === "Historical";
  toggle.addEventListener("change", () => {
    filters.set("includeHistory", toggle.checked ? "yes" : "no");
    page = 0;
    render();
  });
  $("toolbar").append(node("label", {}, node("span", {}, toggle, " Include historical / background records")));
  return toggle.checked ? rows : rows.filter(row => !isHistorical(row));
}
function paginated(rows, fields, kind = null) {
  const pageSize = 50;
  page = Math.min(page, Math.max(0, Math.ceil(rows.length / pageSize) - 1));
  const controls = node("div", { class: "actions" });
  const previous = node("button", { class: "secondary", type: "button", onclick: () => { page--; render(); } }, "Previous");
  previous.disabled = page === 0;
  const next = node("button", { class: "secondary", type: "button", onclick: () => { page++; render(); } }, "Next");
  next.disabled = (page + 1) * pageSize >= rows.length;
  controls.append(previous, node("span", { class: "muted" }, `${rows.length} records | Page ${page + 1} of ${Math.max(1, Math.ceil(rows.length / pageSize))}`), next);
  return node("div", {}, controls, table(rows.slice(page * pageSize, (page + 1) * pageSize), fields, kind));
}
function currentView() { return location.hash.slice(1).split("?")[0] || "overview"; }
function renderOverview(model) {
  const selections = model.selections || [];
  const stats = [
    ["Finalized choices", selections.filter(row => row.selectionState === "Finalized").length],
    ["Open choices", selections.filter(row => row.selectionState === "Open").length],
    ["Purchased", selections.filter(row => row.purchaseState === "Purchased").length],
    ["Owner-edited records", model.appEdits.selectionIds.length + model.appEdits.doorIds.length],
  ];
  return node("div", {},
    node("div", { class: "notice" }, "Evidence snapshot, not live project status. Imported ", project.importedAt, ". Original records are preserved; web edits are a separate audited owner-direction layer."),
    node("div", { class: "grid" }, stats.map(([title, count]) => node("div", { class: "card stat" }, node("span", { class: "muted" }, title), node("strong", {}, count)))),
    node("h2", {}, "Project essentials"),
    node("div", { class: "grid" }, pages.slice(1, 15).map(([id, title, description]) =>
      node("a", { href: `#${id}`, class: "card" }, node("h3", {}, title), node("p", { class: "muted" }, description)))),
    node("h2", {}, "Coordination priorities"),
    table((model.issues || []).filter(row => !row.history).slice(0, 5), ["title", "status"]),
    ...model.appEdits.warnings.map(warning => node("div", { class: "notice" }, warning))
  );
}
function renderDrawings(model) {
  const cards = (model.drawingSheets || []).map(row => {
    const url = safeUrl(row.thumbnail?.url);
    const image = url ? node("img", { src: url, alt: row.title || row.sheet, class: "preview", loading: "lazy" }) : null;
    return node("article", { class: "card" }, image, node("h3", {}, row.title), node("p", { class: "muted" }, row.status), evidence(row.links), lazyDetails("Sheet and revision details", () => structured(row)));
  });
  return node("div", {}, node("div", { class: "grid" }, cards), structured(model.drawingReviewNote || {}), structured(model.drawings || [], "Additional drawing sources"));
}
function renderDownloads(model) {
  const changed = !model.appEdits.sourceExportsCurrent;
  const output = node("div", {},
    node("h2", {}, "Current app revision"),
    node("div", { class: "actions" }, download("selections.csv", "Current selections (CSV)"), download("doors.csv", "Current doors (CSV)"), download("doors.pdf", "Current door rows (PDF)"), download("edits.json", "Owner edits and audit history (JSON)")),
    node("p", { class: "muted" }, "Exports include the current owner overrides. The door PDF is a row schedule, not a replacement architectural plan or component takeoff."),
  );
  if (model.doorHardware?.excelSha256) {
    output.append(node("h2", {}, "Excel schedule and native pivot"));
    output.append(model.appEdits.doorWorkbookCurrent
      ? download("source-workbook.xlsx", "Imported Excel schedule + pivot")
      : node("div", { class: "notice" }, "The imported workbook is not current after door edits. Reconcile component allocations and regenerate the native Excel pivot in the source workflow before republishing."));
  }
  output.append(node("h2", {}, "Imported source registers"));
  output.append(node("div", { class: "notice" }, changed
    ? "These registers describe the imported source publication, not your newer web edits. Use current app exports above for edited choices."
    : "These are the preserved registers from the imported publication. Their original dates and qualifications remain authoritative."));
  output.append(evidence(model.sourceRegisters || []));
  return output;
}
async function renderProvenance(model) {
  const target = $("content");
  target.append(node("h2", {}, "App audit history"), node("p", { class: "muted" }, "Every successful save and source publication is recorded with its revision and basis. Original history is never removed when restoring source values."));
  if (!historyEntries) {
    try { historyEntries = (await api("/api/history")).entries; }
    catch (error) { showMessage(error.message, true); return; }
  }
  if (currentView() !== "provenance") return;
  target.append(paginated([...historyEntries].reverse(), ["revision", "action", "at", "kind", "id", "basis"]));
  target.append(node("h2", {}, "Imported review and source provenance"));
  const covered = new Set(["contacts", "selections", "meetings", "decisions", "liveBudget", "ownerBudget", "spending", "purchases", "drawingSheets", "drawings", "changeOrders", "timeline", "issues", "contracts", "documents", "doorHardware", "sourceRegisters", "appEdits"]);
  for (const [key, value] of Object.entries(model)) {
    if (!covered.has(key)) target.append(lazyDetails(label(key), () => structured(value, label(key))));
  }
}
function renderSearch(model, query) {
  const results = [];
  const collections = [
    ...Object.entries(model),
    ["Contractor cost codes", model.liveBudget?.rows || []],
    ["Contractor forecast", model.contractorForecast?.pending || []],
    ["Owner budget estimates", model.ownerBudget?.rows || []],
    ["Additional owner costs", model.ownerBudget?.additionalCategories || []],
    ["Order and invoice records", model.purchases?.rows || []],
    ["Included invoice detail (not additional expenses)", (model.purchases?.rows || []).flatMap(row => row.children || [])],
  ];
  for (const [key, value] of collections) {
    if (!Array.isArray(value)) continue;
    for (const row of value) {
      if (isObject(row) && JSON.stringify(row).toLocaleLowerCase().includes(query)) {
        results.push({
          group: label(key), title: row.title || row.item || row.name || row.id || "Record",
          summary: row.status || row.product || row.notes || "", links: row.links || (row.url ? [{ url: row.url, label: row.title || "Document" }] : []),
          record: row,
        });
      }
    }
  }
  return paginated(results, ["group", "title", "summary"]);
}
function render() {
  if (!project) return;
  const view = currentView();
  const [, title, description] = pages.find(([id]) => id === view) || pages[0];
  const model = project.model;
  $("title").textContent = title;
  document.title = `${title} | Remodel tracker`;
  $("intro").textContent = description;
  $("source-state").hidden = model.appEdits.sourceExportsCurrent;
  $("source-state").textContent = "Newer owner decisions are applied to selections and door rows. Source decisions, financial reports, plans, and original registers still describe the imported publication. Review app history for changes; these edits do not create orders or payments.";
  $("revision").textContent = `App revision ${project.revision}`;
  $("toolbar").replaceChildren();
  $("content").replaceChildren();
  $("footer").textContent = `Source compiled ${model.compiled || "date not recorded"} | Mail evidence through ${model.cutoff || "date not recorded"} | Imported ${project.importedAt}. Source coverage and gaps remain explicit. No project content is stored in this site's browser storage.`;
  for (const anchor of $("navigation").querySelectorAll("a")) {
    if (anchor.hash === `#${view}`) anchor.setAttribute("aria-current", "page");
    else anchor.removeAttribute("aria-current");
  }
  const query = $("search").value.trim().toLocaleLowerCase();
  if (query) {
    $("title").textContent = "Search results";
    $("content").append(renderSearch(model, query));
    return;
  }
  let content;
  switch (view) {
    case "overview": content = renderOverview(model); break;
    case "selections": {
      const rows = model.selections || [];
      for (const key of ["category", "room", "selectionState", "purchaseState", "purchaser"]) filterSelect(key, label(key), rows.map(row => row[key]));
      content = paginated(currentRows(rows).filter(row => matches(row, ["category", "room", "selectionState", "purchaseState", "purchaser"])),
        ["category", "room", "item", "product", "model", "finish", "selectionState", "price", "purchaseState", "purchaser"], "selection");
      break;
    }
    case "doors": {
      const hardware = model.doorHardware || {};
      const rows = hardware.schedule || [];
      filterSelect("floor", "Floor", rows.map(row => row.floor));
      filterSelect("confirmation", "Confirmation", rows.map(row => row.confirmation));
      content = node("div", {},
        node("div", { class: "actions" }, download("doors.csv", "Current CSV"), download("doors.pdf", "Current row schedule (PDF)")),
        paginated(rows.filter(row => matches(row, ["floor", "confirmation"])), ["id", "floor", "location", "lever", "function", "finish", "rosette", "hinges", "confirmation"], "door"),
        node("h2", {}, "Preserved plan references"),
        node("p", { class: "muted" }, "Source packet and drawings describe the imported publication. Current row changes do not revise plan geometry or prove component quantities."),
        node("div", { class: "grid" }, (hardware.plans || []).map(plan => {
          const url = safeUrl(plan.image?.url);
          return node("article", { class: "card" },
            url && new URL(url).origin === location.origin
              ? node("img", { src: url, alt: plan.title || "Numbered door plan", class: "preview", loading: "lazy" })
              : null,
            node("h3", {}, plan.title), evidence(plan.pdf ? [plan.pdf] : []),
            lazyDetails("Plan details", () => structured(plan)));
        })), evidence(hardware.links),
      );
      break;
    }
    case "contacts": content = paginated(currentRows(model.contacts || []), ["name", "company", "role", "email", "phone"]); break;
    case "meetings": content = paginated(model.meetings || [], ["date", "title", "status"]); break;
    case "decisions": content = paginated(currentRows(model.decisions || []), ["category", "title", "status", "summary"]); break;
    case "liveBudget":
    case "ownerBudget":
    case "spending":
    case "purchases":
      content = node("div", {}, node("div", { class: "notice" }, "Reviewed financial snapshot. Selection reference-price edits do not change these source-supported totals."), structured(model[view] || {}));
      if (view === "liveBudget") content.append(lazyDetails("Source contractor forecast", () => structured(model.contractorForecast || {})));
      if (view === "spending") content.append(node("h2", {}, "Underlying financial records"), paginated(currentRows(model.finance || []), ["date", "party", "title", "amount", "currency", "paidDate", "status", "costScope"]));
      break;
    case "drawings": content = renderDrawings(model); break;
    case "changeOrders": content = paginated(model.changeOrders || [], ["number", "date", "title", "amount", "currency", "execution"]); break;
    case "timeline": content = paginated(currentRows(model.timeline || []), ["date", "title", "status"]); break;
    case "issues": content = paginated(currentRows(model.issues || []), ["title", "status", "issueGroup", "history"]); break;
    case "contracts": content = table(model.contracts || [], ["title", "status"]); break;
    case "documents": {
      const documents = (model.documents || []).map(row => ({ ...row, links: [{ url: row.url, label: row.title || "Open source" }] }));
      filterSelect("section", "Section", documents.map(row => row.section));
      content = paginated(documents.filter(row => matches(row, ["section"])), ["title", "date", "type", "status"]);
      break;
    }
    case "registers": content = renderDownloads(model); break;
    case "provenance": void renderProvenance(model); return;
    default: content = renderOverview(model);
  }
  $("content").append(content);
}
function openEditor(kind, row) {
  editing = { kind, row, etag: project.etag, inputs: new Map() };
  $("editor-title").textContent = `Owner decision: ${row.item || row.location || row.id}`;
  $("edit-fields").replaceChildren();
  $("edit-basis").value = "";
  $("edit-error").textContent = "";
  $("restore-edit").hidden = !row.ownerEdit;
  for (const key of project.editable[kind]) {
    const options = key === "selectionState" ? ["Finalized", "Open", "Historical"]
      : key === "confirmation" ? ["Confirmed", "Unconfirmed"] : null;
    let control;
    if (options) {
      control = node("select", { name: key }, options.map(value => node("option", { value }, value)));
      control.value = row[key] || options[0];
    } else {
      control = node(/notes?|basis|followup/i.test(key) ? "textarea" : "input", { name: key, maxlength: "10000" });
      control.value = row[key] || "";
    }
    editing.inputs.set(key, control);
    $("edit-fields").append(node("label", { class: "field" }, label(key), control));
  }
  $("editor").showModal();
}
async function saveEditor(restore = false) {
  if (!editing) return;
  const basis = $("edit-basis").value.trim();
  if (!basis) { $("edit-error").textContent = "Record the decision or evidence basis first."; return; }
  const values = {};
  for (const [key, input] of editing.inputs) {
    if (input.value !== String(editing.row[key] ?? "")) values[key] = input.value;
  }
  if ("price" in values) values.priceBasis = editing.inputs.get("priceBasis").value;
  if (!restore && !Object.keys(values).length) { $("edit-error").textContent = "No fields changed."; return; }
  $("save-edit").disabled = true;
  $("restore-edit").disabled = true;
  try {
    project = await api(`/api/${restore ? "restore" : "edits"}/${editing.kind}/${encodeURIComponent(editing.row.id)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "If-Match": editing.etag, "X-Requested-With": "RemodelTracker" },
      body: JSON.stringify(restore ? { basis } : { values, basis }),
    });
    historyEntries = null;
    $("editor").close();
    editing = null;
    showMessage("Owner decision saved with its basis and prior values. Financial source records are unchanged.");
    render();
  } catch (error) {
    $("edit-error").textContent = error.message;
  } finally {
    $("save-edit").disabled = false;
    $("restore-edit").disabled = false;
  }
}
function showAccess(message) {
  project = null;
  historyEntries = null;
  editing = null;
  $("workspace").hidden = true;
  $("access").hidden = false;
  $("access-status").textContent = message;
  $("content").replaceChildren();
  $("edit-fields").replaceChildren();
  if ($("editor").open) $("editor").close();
}
async function start() {
  for (const [id, title] of pages) $("navigation").append(node("a", { href: `#${id}` }, title));
  try {
    const options = await api("/auth-options");
    $("microsoft-signin").hidden = !options.microsoft;
    $("google-signin").hidden = !options.google;
    $("provider-status").textContent = "";
    if (!options.microsoft && !options.google) {
      showAccess("The app is deployed and locked. Sign-in configuration is pending.");
      return;
    }
  } catch (error) {
    $("provider-status").textContent = "Sign-in configuration could not be loaded.";
    showAccess(error.message);
    return;
  }
  try {
    project = await api("/api/project");
    $("access").hidden = true;
    $("workspace").hidden = false;
    render();
  } catch (error) {
    showAccess(error.message);
    try {
      const identity = await api("/api/identity");
      $("identity").textContent = JSON.stringify(identity, null, 2);
      $("identity-panel").hidden = false;
    } catch { /* The original access error is already visible. */ }
  }
}
$("close-editor").addEventListener("click", () => $("editor").close());
$("edit-form").addEventListener("submit", event => { event.preventDefault(); void saveEditor(); });
$("restore-edit").addEventListener("click", () => void saveEditor(true));
let searchTimer;
$("search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { page = 0; render(); }, 150);
});
window.addEventListener("hashchange", () => { page = 0; filters.clear(); $("search").value = ""; render(); });
window.addEventListener("pagehide", () => { project = null; historyEntries = null; $("content").replaceChildren(); });
window.addEventListener("pageshow", event => { if (event.persisted) location.reload(); });
void start();

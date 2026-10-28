const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const staticRoot = path.join(__dirname, "..", "app", "static");
const script = fs.readFileSync(path.join(staticRoot, "app.js"), "utf8");
const html = fs.readFileSync(path.join(staticRoot, "index.html"), "utf8");

function fixture() {
  const link = { label: "Synthetic evidence", url: "/api/documents/test-document#page=1" };
  const model = {
    compiled: "2026-01-02", cutoff: "2026-01-01",
    selections: [{
      id: "selection-1", category: "Lighting", room: "Study", item: "Example lamp",
      product: "Example product", model: "TEST-1", finish: "Nickel", price: "$10.00",
      priceBasis: "Working quote", selectionState: "Open", purchaseState: "Not purchased",
      purchaser: "Owner", links: [link],
    }],
    doorHardware: {
      schedule: [{
        id: "DOOR-1", floor: "Ground", location: "Study", lever: "Example lever",
        function: "Passage", finish: "Nickel", rosette: "Round", hinges: "Existing",
        confirmation: "Unconfirmed", links: [link],
      }],
      plans: [], links: [link], excelSha256: "synthetic-hash",
    },
    contacts: [{ name: "Example Owner", company: "Example", role: "Owner", email: "owner@example.invalid", links: [link] }],
    meetings: [{ date: "2026-01-01", title: "Synthetic meeting", status: "Reviewed", links: [link] }],
    decisions: [{ id: "decision-1", category: "Lighting", title: "Synthetic decision", status: "Scoped", links: [link] }],
    liveBudget: { totals: { baseline: "10.00" }, rows: [{ code: "01", name: "Example cost code" }] },
    ownerBudget: { totals: { estimate: "10.00" } }, spending: { coreTotals: { documented: "10.00" } },
    purchases: { rows: [{ id: "invoice-1", amount: "10.00", transactionKind: "Invoice" }] },
    finance: [{ id: "invoice-1", amount: "10.00", status: "Not settlement" }],
    contractorForecast: { pending: [] },
    drawings: [], drawingSheets: [], drawingReviewNote: { status: "Source snapshot" },
    changeOrders: [{ id: "co-1", title: "Example change", amount: "10.00", execution: "Not verified", links: [link] }],
    timeline: [{ title: "Example event", date: "2026-01-01", status: "Planned", links: [link] }],
    issues: [{ title: "Example question", status: "Open", history: false, links: [link] }],
    contracts: [{ title: "Example draft", status: "Draft only", links: [link] }],
    documents: [{ title: "Example evidence", date: "2026-01-01", type: "PDF", section: "Sources", url: link.url }],
    sourceRegisters: [link], publication: { format: "Source snapshot" },
    appEdits: { selectionIds: [], doorIds: [], warnings: [], sourceExportsCurrent: true, doorWorkbookCurrent: true },
  };
  return {
    revision: 1, etag: '"revision-1"', importedAt: "2026-01-02T00:00:00Z", model,
    editable: { selection: ["product", "model", "finish", "price", "priceBasis", "selectionState"], door: ["finish", "confirmation", "note"] },
  };
}
async function waitFor(predicate) {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 5));
  }
  assert.fail("UI did not reach the expected state");
}
async function browser(options = {}) {
  let project = fixture();
  if (options.setup) options.setup(project);
  const calls = [];
  const dom = new JSDOM(html, { url: "https://app.example.invalid/", runScripts: "outside-only", pretendToBeVisual: true });
  const { window } = dom;
  window.HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  window.HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  window.fetch = async (url, request = {}) => {
    calls.push({ url, ...request });
    if (url === "/auth-options") return { ok: true, json: async () => ({ microsoft: !options.pending, google: !options.pending }) };
    if (options.reject && url === "/api/project") return { ok: false, status: 403, json: async () => ({ detail: "Not enrolled" }) };
    if (url === "/api/project") return { ok: true, json: async () => structuredClone(project) };
    if (url === "/api/identity") return { ok: true, json: async () => ({ id: "synthetic-owner", authorized: false }) };
    if (url === "/api/history") return { ok: true, json: async () => ({ entries: [{ revision: 1, action: "publish", at: "2026-01-02", basis: "Synthetic import" }] }) };
    if (url.startsWith("/api/edits/")) {
      if (options.conflict) return { ok: false, status: 409, json: async () => ({ detail: "Reload and review before saving." }) };
      assert.equal(request.headers["If-Match"], project.etag);
      assert.equal(request.headers["X-Requested-With"], "RemodelTracker");
      const body = JSON.parse(request.body);
      Object.assign(project.model.selections[0], body.values, { ownerEdit: { basis: body.basis } });
      project.revision++;
      project.etag = `"revision-${project.revision}"`;
      return { ok: true, json: async () => structuredClone(project) };
    }
    throw new Error("Unexpected synthetic request: " + url);
  };
  window.eval(script);
  await waitFor(() => options.pending
    ? window.document.getElementById("access-status").textContent.includes("configuration is pending")
    : options.reject
    ? window.document.getElementById("identity-panel").hidden === false
    : window.document.getElementById("workspace").hidden === false);
  return { dom, window, document: window.document, calls };
}

test("all project surfaces render without errors and preserve evidence links", async () => {
  const { dom, window, document } = await browser();
  const errors = [];
  window.addEventListener("error", event => errors.push(event.message));
  for (const link of [...document.querySelectorAll("#navigation a")]) {
    window.location.hash = link.hash;
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.ok(document.getElementById("content").children.length, link.hash);
    assert.ok(link.hasAttribute("aria-current"), link.hash);
  }
  assert.deepEqual(errors, []);
  window.location.hash = "#selections";
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.ok(document.querySelector('a[href="https://app.example.invalid/api/documents/test-document#page=1"]'));
  dom.window.close();
});

test("private strings are text, not executable markup", async () => {
  const { dom, window, document } = await browser({
    setup(project) { project.model.selections[0].product = '<img src=x onerror="window.pwned=1">'; },
  });
  window.location.hash = "#selections";
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.ok(document.getElementById("content").textContent.includes("<img src=x"));
  assert.equal(document.querySelector('img[src="x"]'), null);
  assert.equal(window.pwned, undefined);
  assert.equal(window.localStorage.length, 0);
  dom.window.close();
});

test("browser edits send the original ETag and a required basis", async () => {
  const { dom, window, document, calls } = await browser();
  window.location.hash = "#selections";
  await new Promise(resolve => setTimeout(resolve, 10));
  document.querySelector("[data-record-id] button").click();
  assert.ok(document.getElementById("editor").open);
  document.querySelector('[name="product"]').value = "Revised example product";
  document.getElementById("edit-form").dispatchEvent(new window.Event("submit", { cancelable: true }));
  assert.ok(document.getElementById("edit-error").textContent.includes("basis"));
  assert.equal(calls.filter(call => call.method === "POST").length, 0);
  document.getElementById("edit-basis").value = "Owner reviewed the new source quote";
  document.getElementById("edit-form").dispatchEvent(new window.Event("submit", { cancelable: true }));
  await waitFor(() => !document.getElementById("editor").open);
  const request = calls.find(call => call.method === "POST");
  assert.equal(request.url, "/api/edits/selection/selection-1");
  assert.deepEqual(JSON.parse(request.body).values, { product: "Revised example product" });
  assert.ok(document.getElementById("content").textContent.includes("Revised example product"));
  dom.window.close();
});

test("a stale save is shown as an error without closing or retrying", async () => {
  const { dom, window, document, calls } = await browser({ conflict: true });
  window.location.hash = "#selections";
  await new Promise(resolve => setTimeout(resolve, 10));
  document.querySelector("[data-record-id] button").click();
  document.querySelector('[name="finish"]').value = "Brass";
  document.getElementById("edit-basis").value = "Review quote";
  document.getElementById("edit-form").dispatchEvent(new window.Event("submit", { cancelable: true }));
  await waitFor(() => document.getElementById("edit-error").textContent.includes("Reload"));
  assert.ok(document.getElementById("editor").open);
  assert.equal(calls.filter(call => call.method === "POST").length, 1);
  dom.window.close();
});

test("an unenrolled identity sees no project data", async () => {
  const { dom, document } = await browser({ reject: true });
  assert.equal(document.getElementById("workspace").hidden, true);
  assert.equal(document.getElementById("content").children.length, 0);
  assert.ok(document.getElementById("access-status").textContent.includes("Not enrolled"));
  assert.ok(document.getElementById("identity").textContent.includes("synthetic-owner"));
  dom.window.close();
});

test("theme override respects both light and dark", () => {
  const script = fs.readFileSync(path.join(staticRoot, "theme.js"), "utf8");
  for (const theme of ["light", "dark"]) {
    const dom = new JSDOM("", { url: `https://app.example.invalid/?scoutTheme=${theme}`, runScripts: "outside-only" });
    dom.window.eval(script);
    assert.equal(dom.window.document.documentElement.dataset.theme, theme);
    dom.window.close();
  }
});

test("post-login session fragments are discarded without touching ordinary navigation", () => {
  const script = fs.readFileSync(path.join(staticRoot, "theme.js"), "utf8");
  for (const [fragment, expected] of [["#token=synthetic-session", ""], ["#selections", "#selections"]]) {
    const dom = new JSDOM("", {
      url: `https://app.example.invalid/?scoutTheme=light${fragment}`, runScripts: "outside-only",
    });
    dom.window.eval(script);
    assert.equal(dom.window.location.hash, expected);
    assert.equal(dom.window.location.search, "?scoutTheme=light");
    assert.equal(dom.window.sessionStorage.length, 0);
    assert.equal(dom.window.localStorage.length, 0);
    dom.window.close();
  }
});

test("global search includes nested budget cost codes", async () => {
  const { dom, window, document } = await browser();
  const search = document.getElementById("search");
  search.value = "Example cost code";
  search.dispatchEvent(new window.Event("input"));
  await waitFor(() => document.getElementById("title").textContent === "Search results");
  assert.ok(document.getElementById("content").textContent.includes("Example cost code"));
  dom.window.close();
});

test("bootstrap deployment hides unconfigured providers and never requests private records", async () => {
  const { dom, document, calls } = await browser({ pending: true });
  assert.equal(document.getElementById("workspace").hidden, true);
  assert.equal(document.getElementById("microsoft-signin").hidden, true);
  assert.equal(document.getElementById("google-signin").hidden, true);
  assert.equal(calls.some(call => call.url.startsWith("/api/")), false);
  dom.window.close();
});

# Remodel tracker

A private, editable project app. Application code belongs in this repository.
Project documents, contact details, financial records, source-specific rules,
and owner identities do not.

The app reads a verified publication of the existing archive, preserves its
source documents, and adds a separate owner-decision layer. No existing archive
file is moved, edited, or deleted. The original source production tools remain
outside GitHub because they mix reusable logic with private project facts.

## What is implemented

- Project overview, selections, door hardware, team, meetings, decisions,
  contractor and owner budgets, spending, invoices, drawings, change orders,
  timeline, coordination items, contracts, document library, and provenance.
- Search and filtering over imported records; authenticated document streaming,
  including PDF byte ranges and original source links.
- Browser editing of selection choices, reference prices, and door rows.
  Every save requires a basis, preserves prior values and identity, and uses an
  optimistic concurrency check. Restoring source values adds history rather than
  erasing it.
- Current selection CSV, door CSV/PDF, and owner-edit/history JSON exports.
  Original registers are clearly labeled source snapshots. A door edit disables
  the imported Excel takeoff/pivot as a current download until it is reconciled.
- A read-only archive inspection and explicit publisher, external local storage
  for development, managed-identity Blob Storage for Azure, and code-only packaging.

This is not a new financial ledger, mailbox importer, automatic procurement
approval system, or architectural drawing editor. Orders, payments, source
drawings, and component allocations still require evidence review. Source-model
and native-pivot production remain in the preserved local workflow; the web app
does not run desktop Excel in Azure or claim to regenerate that pivot.

## Storage and privacy

Keep the OneDrive archive as the preserved source and recovery copy. Publish
only the app's referenced documents into a private Azure Blob container, not the
entire archive, caches, tool folder, historical portals, or unrelated mail.
Referenced administrative evidence is included when the published app explicitly
links it. Publishing is an operator action; it is not automatic two-way sync.

The private store contains content-addressed documents, immutable source
snapshots, and one conditional workspace record containing the source pointer,
overrides, revision, and audit history. A failed or concurrent publication cannot
activate a mixed revision. Publishing new source evidence checks existing edits
against their expected original values; conflicts require explicit reconciliation.
Orphaned immutable uploads from an interrupted publish are not activated and are
not deleted automatically.

Azure encryption at rest, HTTPS, disabled public blob access and shared keys,
container-scoped managed-identity access, versioning, and 30-day soft deletion are
configured in `infra/main.bicep`. The storage endpoint is network-public but
authorization-private; it is not a private-endpoint/VNet deployment. No blob URL,
SAS, storage key, or document bytes are put in the public shell or GitHub.
Authenticated content is `no-store`; the app uses no localStorage, IndexedDB,
service worker, external analytics, fonts, or third-party front-end CDN.
Easy Auth post-login session fragments are discarded before application startup;
the browser's authenticated session cookie is used instead.

Audit history is transactionally preserved by the app, not an independently
immutable/WORM compliance log. An administrator with storage write access can
change it. Use storage versioning and the separate preserved archive for recovery.

## Development

Python 3.12+ and Node 22+ are sufficient:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
npm ci --no-audit --no-fund
.\.venv\Scripts\python.exe -m pytest
npm test
.\.venv\Scripts\ruff.exe check .
node --check .\app\static\app.js
.\.venv\Scripts\python.exe .\scripts\check_repository.py
```

Tests generate synthetic records in temporary private directories. They require
no owner documents, Azure subscription, OAuth credentials, or live mailbox.

The public shell can be started with
`.\.venv\Scripts\python.exe -m uvicorn app.main:app`.
Private APIs intentionally refuse local forged identity headers; there is no
development authentication bypass. For authenticated manual testing use a
dedicated App Service deployment with Easy Auth. API unit tests supply a
synthetic trusted gateway context in-process, not an HTTP-accessible bypass.

## Inspect and publish the archive

Do not copy the archive into this checkout, even temporarily. Enter its location
privately in your shell:

```powershell
$archive = Read-Host 'Absolute path to the preserved archive'
.\.venv\Scripts\python.exe -m app inspect --source "$archive"
```

Inspection verifies the completed publication's input and output hashes and
every linked local document, then prints counts only. This is a byte/link
integrity check, **not** a claim that every original email, drawing, image,
contract, or manual has been semantically reviewed.

For an external local store, choose a separate private folder outside both the
checkout and source archive:

```powershell
$privateStore = Read-Host 'Separate private application storage directory'
.\.venv\Scripts\python.exe -m app publish --source "$archive" --store "$privateStore"
$env:PRIVATE_STORE = $privateStore
```

For Azure, after deployment and explicit upload approval, sign in through the
Azure CLI using your own account. Give the publishing operator **Storage Blob
Data Contributor** on the content container; resource Contributor alone does
not grant blob data access. The app already has its own managed-identity role.

```powershell
az login
$storageUrl = Read-Host 'HTTPS storage account origin'
.\.venv\Scripts\python.exe -m app publish --source "$archive" --account-url "$storageUrl"
```

The publisher uses your CLI login; the web app uses its managed identity.
No connection string or storage key is required. No cloud resource is created
and no content is uploaded by installing, testing, packaging, or inspecting.
On the initial publication, all referenced documents must be uploaded
successfully before the app can use them.

## Microsoft and Google sign-in

Azure App Service Easy Auth validates provider tokens and maintains sessions.
The app only trusts platform-injected headers when App Service and Easy Auth
are enabled. Do not expose the Python service through a separate gateway that
bypasses Easy Auth. Never enable `WEBSITE_AUTH_ENABLED` by hand on a public
standalone server.

Configure `OWNER_EMAIL` privately to the sole permitted owner's address.
Each enabled provider must also have that owner's pinned immutable
`X-MS-CLIENT-PRINCIPAL-ID` in `OWNER_AAD_ID` or `OWNER_GOOGLE_ID`.
Both email and the provider-specific ID must match. A matching email alone
never grants access; the Microsoft issuer accepts personal Microsoft accounts,
not arbitrary workforce tenants.

To enroll the owner:

1. Deploy with the owner email but empty owner IDs. Provider credentials can also
   be omitted for an initial locked-down deployment: sign-in buttons stay hidden
   and private routes remain protected. Configure each provider in App Service
   Authentication, then set `MICROSOFT_LOGIN_ENABLED=true` or
   `GOOGLE_LOGIN_ENABLED=true` in App Service environment variables.
2. Sign in with the owner's Microsoft account. The access page shows **My sign-in
   identity**, obtained from `/api/identity`. Only the caller's own identity is
   returned, with no project content.
3. Store that `id` privately in `OWNER_AAD_ID` in App Service configuration.
   Never paste provider tokens or client secrets into a chat or commit them.
4. Repeat with the owner's Google account to set `OWNER_GOOGLE_ID`. The Google
   account must use the configured owner email; this can be a non-Gmail Google
   account. An unrelated Gmail account is not silently linked by email.
5. Confirm the owner can read/edit, another account receives 403, anonymous
   APIs receive 401, and direct anonymous blob access fails before uploading
   actual content.

All writes additionally require the canonical `APP_ORIGIN`, a same-origin
request header, JSON, and the workspace ETag. If using a custom domain, update
`APP_ORIGIN`, both providers' redirect registrations, and allowed redirects.

References: [App Service authentication](https://learn.microsoft.com/azure/app-service/overview-authentication-authorization),
[Microsoft personal accounts](https://learn.microsoft.com/azure/app-service/configure-authentication-provider-aad),
[Google registration](https://learn.microsoft.com/azure/app-service/configure-authentication-provider-google).

## Azure deployment

**Deployment is not performed automatically.** The B1 App Service plan has a
standing monthly cost, plus Blob capacity, operations, version history and
egress. Check current regional prices and approve the subscription, resource
group, location, and cost before provisioning.

Register a Microsoft application that supports **Personal Microsoft accounts
only**. Use the Web redirect URI
`https://<app-hostname>/.auth/login/aad/callback`.
Create a Google web OAuth client with redirect URI
`https://<app-hostname>/.auth/login/google/callback` and matching
authorized JavaScript origin. Request only identity scopes, not mail or Drive.
Google consent-screen testing must include the owner as a test user if applicable.
Use the actual hostname from the deployment's `appUrl` output; newer Azure app
hostnames can include a generated suffix. Update both registrations if needed.

Create a private Bicep parameter file **outside the repository** containing
`appName`, `storageName`, and `ownerEmail`. Provider client IDs and secrets
(`microsoftClientId`, `microsoftClientSecret`, `googleClientId`,
`googleClientSecret`) are optional during bootstrap.
Use secure parameter sources or Key Vault parameter references rather than
putting secret values on a shell command line. The owner IDs can initially be empty
for fail-closed owner enrollment. The deploying operator needs resource creation
and role-assignment permissions.

The infrastructure template is authoritative: applying it again with empty
provider or owner parameters disables their configured access. Preserve those
values in private parameter sources for future infrastructure changes. Normal
code-only ZIP deployments do not reapply or reset authentication configuration.

After explicit approval, use the Azure CLI:

```powershell
$group = Read-Host 'Approved resource group'
$location = Read-Host 'Approved Azure location'
$parameters = Read-Host 'Private ARM parameters JSON path'
az group create --name "$group" --location "$location"
az deployment group what-if --resource-group "$group" --template-file .\infra\main.bicep --parameters "@$parameters"
az deployment group create --resource-group "$group" --template-file .\infra\main.bicep --parameters "@$parameters"
$package = Join-Path $env:TEMP 'remodel-app-code.zip'
.\.venv\Scripts\python.exe .\scripts\package_app.py --output "$package"
$appName = Read-Host 'Deployed app name'
az webapp deploy --resource-group "$group" --name "$appName" --src-path "$package" --type zip
Remove-Item -LiteralPath "$package"
```

Code packaging has an explicit application-file allowlist; it never packages
`.env`, private stores, tests, archive content, or the whole working directory.
CI is synthetic-only and performs no deployment. Blob uploads happen separately
with the private publisher after owner authorization is verified.

## Source updates and recovery

Web edits do **not** modify the original OneDrive workbook, CLI edit registry, or
preserved documents. Download **Owner edits and audit history** to reconcile
changes into that workflow when required. Rebuild the original publication
there, including native Excel/component review, then publish again. Stale source
expectations block the new publication rather than silently dropping owner edits.
Restore/reconcile the relevant overrides deliberately before retrying.

Purchased product/model/finish/size changes are blocked. A selection location
explicitly derived from a numbered door cannot be edited independently.
Purchased products bound to doors require source reconciliation for door model,
function, or finish changes. Other door-location edits update the derived
selection location. Choices never manufacture order, payment, delivery,
installation, or approval evidence.

The existing archive has not been exhaustively re-reviewed as part of this code
migration. Existing incomplete-review warnings are preserved in source
provenance. Long historical manuals, drawings and emails need their own review;
hash validation and application import are not substitutes.

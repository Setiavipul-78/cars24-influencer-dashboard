/**
 * Cars24 Influencer Dashboard — Google Sheet → GitHub sync
 *
 * SETUP (one time):
 *   1. Open your Google Sheet → Extensions → Apps Script
 *   2. Paste this entire file, replacing any existing code
 *   3. Extensions → Apps Script → Project Settings → Script Properties
 *      Add property:  GITHUB_TOKEN  =  <your GitHub PAT>
 *      (GitHub → Settings → Developer settings → Personal access tokens → Fine-grained
 *       Repository: cars24-influencer-dashboard, Permission: Contents = Read & write)
 *   4. Click Run → pushSheetToGitHub → Authorize when prompted
 *   5. Triggers (clock icon) → Add trigger:
 *        Function: pushSheetToGitHub
 *        Event: From spreadsheet → On edit
 *      Add a second trigger:
 *        Function: pushSheetToGitHub
 *        Event: Time-driven → Day timer → 8:00–9:00 AM
 */

const GITHUB_OWNER  = 'Setiavipul-78';
const GITHUB_REPO   = 'cars24-influencer-dashboard';
const GITHUB_FILE   = 'sheet_data.json';
const GITHUB_BRANCH = 'main';
const SHEET_GID     = 1928933144;

const YT_SHEET_GID  = 1490331911;  // "pan india poa youtube" tab
const YT_GITHUB_FILE = 'yt_sheet_data.json';

function toStr(v) {
  if (v === null || v === undefined || v === '') return '';
  if (v instanceof Date) {
    return Utilities.formatDate(v, Session.getScriptTimeZone(), 'yyyy-MM-dd');
  }
  return v.toString().trim();
}

function pushSheetToGitHub() {
  const props = PropertiesService.getScriptProperties();
  const token = props.getProperty('GITHUB_TOKEN');
  if (!token) {
    Logger.log('❌  GITHUB_TOKEN not set — go to Project Settings → Script Properties and add it');
    return;
  }

  const ss    = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheets().find(s => s.getSheetId() === SHEET_GID) || ss.getActiveSheet();
  const range = sheet.getDataRange();
  const data  = range.getValues();

  if (data.length < 2) { Logger.log('Sheet has no data rows'); return; }

  const rawHeaders = data[0];
  const headers    = rawHeaders.map(h => h.toString().toLowerCase().trim()
                       .replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, ''));

  const rows = data.slice(1)
    .map(row => {
      const obj = {};
      headers.forEach((h, i) => { obj[h] = toStr(row[i]); });
      return obj;
    })
    .filter(r => headers.some(h => (h.includes('name') || h.includes('influencer')) && r[h]));

  const payload = JSON.stringify({ exportedAt: new Date().toISOString(), rows }, null, 2);
  const encoded = Utilities.base64Encode(Utilities.newBlob(payload).getBytes());

  const apiUrl  = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/contents/${GITHUB_FILE}`;
  const ghHdrs  = { Authorization: `token ${token}`, Accept: 'application/vnd.github.v3+json' };

  // Get current SHA so GitHub accepts the update
  let sha = null;
  try {
    const getResp = UrlFetchApp.fetch(apiUrl, { headers: ghHdrs, muteHttpExceptions: true });
    if (getResp.getResponseCode() === 200) sha = JSON.parse(getResp.getContentText()).sha;
  } catch (e) { /* file may not exist yet on first push */ }

  const body = {
    message: `chore: sync sheet data ${new Date().toISOString().slice(0, 10)}`,
    content:  encoded,
    branch:   GITHUB_BRANCH,
  };
  if (sha) body.sha = sha;

  const putResp = UrlFetchApp.fetch(apiUrl, {
    method:           'put',
    headers:          ghHdrs,
    payload:          JSON.stringify(body),
    muteHttpExceptions: true,
  });

  const code = putResp.getResponseCode();
  if (code === 200 || code === 201) {
    Logger.log(`✅  sheet_data.json pushed — ${rows.length} rows`);
  } else {
    Logger.log(`❌  GitHub push failed (HTTP ${code}): ${putResp.getContentText().slice(0, 300)}`);
  }

  // Also push YouTube POA tab → yt_sheet_data.json
  pushTabToGitHub(ss, YT_SHEET_GID, YT_GITHUB_FILE, token, ghHdrs);
}

function pushTabToGitHub(ss, gid, filename, token, ghHdrs) {
  const sheet = ss.getSheets().find(s => s.getSheetId() === gid);
  if (!sheet) { Logger.log(`⚠️  Sheet GID ${gid} not found — skipping ${filename}`); return; }

  const data = sheet.getDataRange().getValues();
  if (data.length < 2) { Logger.log(`Sheet ${gid} has no data rows`); return; }

  const rawHeaders = data[0];
  const headers    = rawHeaders.map(h => h.toString().toLowerCase().trim()
                       .replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, ''));

  const rows = data.slice(1)
    .map(row => {
      const obj = {};
      headers.forEach((h, i) => { obj[h] = toStr(row[i]); });
      return obj;
    })
    .filter(r => headers.some(h => (h.includes('name') || h.includes('creator')) && r[h]));

  const payload = JSON.stringify({ exportedAt: new Date().toISOString(), rows }, null, 2);
  const encoded = Utilities.base64Encode(Utilities.newBlob(payload).getBytes());

  const apiUrl = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/contents/${filename}`;

  let sha = null;
  try {
    const getResp = UrlFetchApp.fetch(apiUrl, { headers: ghHdrs, muteHttpExceptions: true });
    if (getResp.getResponseCode() === 200) sha = JSON.parse(getResp.getContentText()).sha;
  } catch (e) {}

  const body = {
    message: `chore: sync ${filename} ${new Date().toISOString().slice(0, 10)}`,
    content:  encoded,
    branch:   GITHUB_BRANCH,
  };
  if (sha) body.sha = sha;

  const putResp = UrlFetchApp.fetch(apiUrl, {
    method: 'put', headers: ghHdrs,
    payload: JSON.stringify(body),
    muteHttpExceptions: true,
  });
  const code = putResp.getResponseCode();
  Logger.log(code === 200 || code === 201
    ? `✅  ${filename} pushed — ${rows.length} rows`
    : `❌  ${filename} push failed (HTTP ${code}): ${putResp.getContentText().slice(0, 200)}`);
}

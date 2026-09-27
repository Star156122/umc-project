const { appendNewsHtml } = require("./stockNewsService");
const pool = require("./db");
const { createHash } = require("crypto");
const { promisify } = require("util");
const gunzip = promisify(require("zlib").gunzip);
const { loadDatabaseReportByAnalysisId } = require("./databaseReportService");
const digest = value => createHash("sha256").update(value).digest("hex");
function error(message, status) { return Object.assign(new Error(message), { status }); }

function parseReportContent(value) {
  if (value && typeof value === "object" && !Buffer.isBuffer(value)) {
    return value;
  }
  const text = Buffer.isBuffer(value) ? value.toString("utf8") : String(value || "");
  const normalized = text.replace(/^\uFEFF/, "").trim();
  try {
    return JSON.parse(normalized);
  } catch (_error) {
    // Recover only the immutable archive index from malformed legacy JSON.
    // The HTML itself is still loaded from and verified against the import tables.
    const sourceMatch = normalized.match(/"sourceArchiveSha256"\s*:\s*"([a-f0-9]{64})"/i);
    const fileMatch = normalized.match(/"report_file"\s*:\s*"([^"]+)"/);
    if (sourceMatch && fileMatch) {
      let reportFile = fileMatch[1];
      try {
        reportFile = JSON.parse(`"${reportFile}"`);
      } catch (_error) {
        reportFile = reportFile.replace(/\\"/g, '"');
      }
      return {
        sourceArchiveSha256: sourceMatch[1].toLowerCase(),
        report_file: reportFile,
      };
    }

    // Some legacy exports contain non-JSON bytes around an otherwise valid object.
    const start = normalized.indexOf("{");
    const end = normalized.lastIndexOf("}");
    if (start >= 0 && end > start) {
      try {
        return JSON.parse(normalized.slice(start, end + 1));
      } catch (_nestedError) {
        throw error("原始 HTML 報告資料損壞，請重新匯入原始檔案。", 500);
      }
    }
    throw error("原始 HTML 報告資料損壞，請重新匯入原始檔案。", 500);
  }
}

async function loadReportHtml(userId, analysisId) {
  // Resolve ownership first. Never use a caller-supplied path to read local files.
  const report = await loadDatabaseReportByAnalysisId(userId, analysisId);
  const [[row]] = await pool.execute("SELECT report_content FROM reports WHERE report_id = ?", [report.reportDbId]);
  if (!row || row.report_content == null) {
    throw error("找不到此份原始 HTML 報告。", 404);
  }
  const payload = parseReportContent(row.report_content);
  if (typeof payload.reportHtml === "string" && payload.reportHtml.trim()) {
    return { html: await appendNewsHtml(payload.reportHtml, report), analysisId: report.analysisId };
  }
  if (!payload.sourceArchiveSha256 || !payload.report_file) {
    throw error("這份舊回測未保存 HTML。請選擇有「原始報告」標記的歷史報告。", 404);
  }
  const source = payload.sourceArchiveSha256;
  const pathHash = digest(payload.report_file);
  let files, chunks;
  try {
    [files] = await pool.execute("SELECT content_sha, raw_bytes FROM report_import_files WHERE source_sha = ? AND path_sha = ?", [source, pathHash]);
    [chunks] = await pool.execute("SELECT content FROM report_import_chunks WHERE source_sha = ? AND path_sha = ? ORDER BY chunk_no", [source, pathHash]);
  } catch (err) {
    if (err.code === "ER_NO_SUCH_TABLE") throw error("尚未匯入原始 HTML 報告。", 404);
    throw err;
  }
  if (!files.length || !chunks.length) throw error("找不到此份原始 HTML 報告。", 404);
  const raw = await gunzip(Buffer.concat(chunks.map(chunk => chunk.content)), { maxOutputLength: 32 * 1024 * 1024 });
  if (raw.length !== Number(files[0].raw_bytes) || digest(raw) !== files[0].content_sha) {
    throw error("報告內容驗證失敗，請重新匯入原始檔案。", 500);
  }
  return { html: await appendNewsHtml(raw.toString("utf8"), report), analysisId: report.analysisId };
}
module.exports = { loadReportHtml };

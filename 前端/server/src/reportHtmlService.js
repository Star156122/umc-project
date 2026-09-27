const pool = require("./db");
const { createHash } = require("crypto");
const { promisify } = require("util");
const gunzip = promisify(require("zlib").gunzip);
const { loadDatabaseReportByAnalysisId } = require("./databaseReportService");
const digest = value => createHash("sha256").update(value).digest("hex");
function error(message, status) { return Object.assign(new Error(message), { status }); }

async function loadReportHtml(userId, analysisId) {
  // Resolve ownership first. Never use a caller-supplied path to read local files.
  const report = await loadDatabaseReportByAnalysisId(userId, analysisId);
  const [[row]] = await pool.execute("SELECT report_content FROM reports WHERE report_id = ?", [report.reportDbId]);
  const payload = typeof row.report_content === "string" ? JSON.parse(row.report_content) : row.report_content;
  if (typeof payload.reportHtml === "string" && payload.reportHtml.trim()) {
    return { html: payload.reportHtml, analysisId: report.analysisId };
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
  return { html: raw.toString("utf8"), analysisId: report.analysisId };
}
module.exports = { loadReportHtml };

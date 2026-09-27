const pool = require("./db");
const {
  databaseRowToYahooSymbol,
  getYahooStocksBySymbols,
  localizeStockNames,
  resolveStockForFavorite,
} = require("./marketService");

function normalizeCode(value) {
  return String(value || "").trim();
}

async function listFavoriteRows(userId) {
  const [rows] = await pool.execute(
    `SELECT
       fs.favorite_id,
       fs.stock_id,
       fs.added_at,
       s.stock_name,
       s.market
     FROM favorite_stocks fs
     INNER JOIN stocks s ON s.stock_id = fs.stock_id
     WHERE fs.user_id = ?
     ORDER BY fs.added_at DESC, fs.favorite_id DESC`,
    [userId]
  );
  return rows;
}

async function listFavoriteCodes(userId) {
  const [rows] = await pool.execute(
    `SELECT stock_id
     FROM favorite_stocks
     WHERE user_id = ?
     ORDER BY added_at DESC`,
    [userId]
  );
  return rows.map((row) => normalizeCode(row.stock_id));
}

async function getFavorites(userId) {
  const rows = await listFavoriteRows(userId);
  if (rows.length === 0) {
    return { stocks: [], updatedAt: new Date().toISOString() };
  }

  const symbols = rows.map(databaseRowToYahooSymbol);
  const overridesBySymbol = new Map(
    rows.map((row) => [
      databaseRowToYahooSymbol(row),
      {
        code: normalizeCode(row.stock_id),
        name: row.stock_name,
        market: String(row.market || "").toLowerCase() === "otc" ? "otc" : "tse",
      },
    ])
  );

  const rawQuotes = await getYahooStocksBySymbols(symbols, {
    includeUnavailable: true,
    overridesBySymbol,
  });
  const quotes = await localizeStockNames(rawQuotes);
  const quoteBySymbol = new Map(quotes.map((quote) => [quote.yahooSymbol, quote]));

  const stocks = rows.map((row) => {
    const symbol = databaseRowToYahooSymbol(row);
    const quote = quoteBySymbol.get(symbol) || {};
    return {
      ...quote,
      code: normalizeCode(row.stock_id),
      name: quote.name || row.stock_name,
      market: String(row.market || "").toLowerCase() === "otc" ? "otc" : "tse",
      yahooSymbol: symbol,
      favoriteId: row.favorite_id,
      addedAt: row.added_at,
      isFavorite: true,
    };
  });

  await Promise.all(
    stocks.map((stock) =>
      pool.execute(
        `UPDATE stocks
         SET stock_name = ?, updated_at = CURRENT_TIMESTAMP
         WHERE stock_id = ? AND stock_name <> ?`,
        [stock.name || stock.code, stock.code, stock.name || stock.code]
      )
    )
  );

  const updatedTimes = stocks.map((stock) => stock.updatedAt).filter(Boolean).sort();
  return {
    stocks,
    updatedAt: updatedTimes.at(-1) || new Date().toISOString(),
    source: "Yahoo Finance",
  };
}

async function addFavorite(userId, input) {
  const stock = await resolveStockForFavorite(input || {});
  const connection = await pool.getConnection();

  try {
    await connection.beginTransaction();

    await connection.execute(
      `INSERT INTO stocks (stock_id, stock_name, market, industry)
       VALUES (?, ?, ?, NULL)
       ON DUPLICATE KEY UPDATE
         stock_name = VALUES(stock_name),
         market = VALUES(market),
         updated_at = CURRENT_TIMESTAMP`,
      [stock.code, stock.name || stock.code, stock.market]
    );

    await connection.execute(
      `INSERT INTO favorite_stocks (user_id, stock_id)
       VALUES (?, ?)
       ON DUPLICATE KEY UPDATE added_at = added_at`,
      [userId, stock.code]
    );

    const [rows] = await connection.execute(
      `SELECT favorite_id, added_at
       FROM favorite_stocks
       WHERE user_id = ? AND stock_id = ?
       LIMIT 1`,
      [userId, stock.code]
    );

    await connection.commit();

    return {
      ...stock,
      favoriteId: rows[0]?.favorite_id || null,
      addedAt: rows[0]?.added_at || new Date().toISOString(),
      isFavorite: true,
    };
  } catch (error) {
    await connection.rollback();
    throw error;
  } finally {
    connection.release();
  }
}

async function removeFavorite(userId, code) {
  const cleanCode = normalizeCode(code);
  if (!/^\d{4,6}$/.test(cleanCode)) {
    const error = new Error("股票代號格式不正確");
    error.status = 400;
    throw error;
  }

  const [result] = await pool.execute(
    `DELETE FROM favorite_stocks
     WHERE user_id = ? AND stock_id = ?`,
    [userId, cleanCode]
  );

  return { removed: result.affectedRows > 0, code: cleanCode };
}

module.exports = {
  listFavoriteCodes,
  getFavorites,
  addFavorite,
  removeFavorite,
};

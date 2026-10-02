/** Diagnostic, non-ESOL proposal workbooks from PROPOSAL_TABLES.json.
 *  Run with the bundled @oai/artifact-tool node_modules junction in scripts/.
 */
import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [inputPath, outputDir] = process.argv.slice(2);
if (!inputPath || !outputDir) throw new Error("usage: build_proposed_workbooks.mjs PROPOSAL_TABLES.json OUT_DIR");
const payload = JSON.parse(await fs.readFile(inputPath, "utf8"));
if (payload.diagnostic_only !== true || payload.esol_payload !== false) throw new Error("not a diagnostic proposal payload");
const tables = payload.tables;
await fs.mkdir(outputDir, {recursive: true});

const config = [
  {
    file: "STERBRUST_UPDATE_EXISTING.xlsx", title: "Существующие товары: предложение обновления",
    sheet: "Existing", rows: tables.existing,
    columns: [
      ["Sterbrust ID", "sterbrust_product_id"], ["Модель", "model"], ["Название Sterbrust", "sterbrust_name"],
      ["Цена RUB", "price"], ["Валюта", "currency"], ["Наличие", "availability"],
      ["Предложенный источник", "selected_source_proposed"], ["ID у поставщика", "source_external_id"],
      ["Наблюдение UTC", "observed_at"], ["Статус", "selection_status"],
      ["URL карточки", "source_url"],
    ],
  },
  {
    file: "STERBRUST_CREATE_NEW_CANDIDATES.xlsx", title: "Новые товары: подтверждённые кандидаты",
    sheet: "New", rows: tables.new,
    columns: [
      ["NEW_GROUP_ID", "new_group_id"], ["NEW_CANDIDATE_ID", "new_candidate_id"], ["Источник", "source"],
      ["ID у источника", "external_id"], ["Модель", "model"],
      ["Исполнение (полная модель)", "execution"], ["Предлагаемое название", "proposed_name"],
      ["SECTION_ID", "section_id"], ["Путь раздела", "section_path"],
      ["Причина отсутствия в каталоге", "absence_evidence"],
      ["URL карточки", "source_url"], ["Источников", "source_offers"],
      ["Готовность", "readiness_state"], ["Характеристик", "characteristic_count"],
      ["Изображений", "image_count"], ["Документов", "document_count"],
      ["Остаток enrichment", "enrichment_blockers"],
    ],
  },
  {
    file: "STERBRUST_REVIEW.xlsx", title: "Товары, требующие проверки",
    sheet: "Review", rows: tables.review,
    columns: [
      ["Источник", "source"], ["ID у источника", "external_id"], ["Модель", "model"],
      ["Название", "name"], ["Статус", "export_status"], ["Причина", "export_reason"],
      ["H1 сохранённой detail-страницы (не подтверждён)", "observed_detail_titles"],
      ["Признаки конфликта", "match_conflicts"],
      ["Результат matching", "match_status"], ["Предлагаемый Sterbrust ID", "proposed_sterbrust_id"],
      ["Цена", "price"], ["Состояние цены", "price_state"], ["Валюта", "currency"],
      ["Наличие", "availability"], ["URL карточки", "source_url"],
      ["Evidence", "evidence_ref"],
      ["Detail REVIEW evidence", "detail_review_evidence"],
    ],
  },
];

if (tables.existing_price_availability) config.unshift({
  file: "STERBRUST_EXISTING_PRICE_AVAILABILITY.xlsx", title: "Подтверждённые товары: цена и наличие",
  sheet: "PriceAvailability", rows: tables.existing_price_availability,
  columns: [
    ["Sterbrust Product ID", "sterbrust_product_id"], ["Название Sterbrust", "sterbrust_name"],
    ["Поставщик", "source"], ["ID у поставщика", "external_id"], ["Товар поставщика", "name"],
    ["Модель / исполнение", "model"], ["Цена поставщика", "price"], ["Состояние цены", "price_state"],
    ["Валюта", "currency"], ["Наличие", "availability"], ["Наблюдение UTC", "observed_at"],
    ["Метод matching", "match_method"], ["Evidence", "evidence_ref"], ["URL карточки", "source_url"],
  ],
});

function excelColumn(index) {
  let value = index + 1, result = "";
  while (value) { value--; result = String.fromCharCode(65 + value % 26) + result; value = Math.floor(value / 26); }
  return result;
}

for (const spec of config) {
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add(spec.sheet);
  sheet.showGridLines = false;
  sheet.tabColor = "#18324F";
  const width = spec.columns.length;
  const lastCol = excelColumn(width - 1);
  sheet.getRange("A2").values = [[spec.title]];
  sheet.getRange("A2").format.font = {name: "Arial", size: 14, bold: true, color: "#18324F"};
  sheet.getRange("A3").values = [["Диагностический QA-файл. Не является ESOL import payload и не изменяет Sterbrust."]];
  sheet.getRange("A3").format.font = {name: "Arial", size: 10, italic: true, color: "#5B6672"};
  sheet.getRangeByIndexes(4, 0, 1, width).values = [spec.columns.map(([title]) => title)];
  const header = sheet.getRange(`A5:${lastCol}5`);
  header.format = {fill: "#18324F", font: {name: "Arial", size: 10, bold: true, color: "#FFFFFF"}};
  header.format.rowHeight = 27;
  header.format.verticalAlignment = "center";
  header.format.horizontalAlignment = "center";
  header.format.wrapText = true;
  const matrix = spec.rows.map(row => spec.columns.map(([, key]) => {
    const value = row[key];
    if (key === "source_offers") return Array.isArray(value) ? value.length : 0;
    if (key === "selection_status") return value === "PROPOSED_ONLY_SUPPLIERS_DISABLED" ? "Предложение, не применено" : String(value ?? "");
    if (key === "observed_at") return value ? new Date(value) : null;
    if (key === "match_conflicts") return Array.isArray(value) ? value.join("; ") : typeof value === "object" && value !== null ? JSON.stringify(value) : String(value ?? "");
    if (key === "enrichment_blockers") return Array.isArray(value) ? value.join("; ") : String(value ?? "");
    if (key === "observed_detail_titles") return Array.isArray(value) ? value.join("\n") : String(value ?? "");
    if (["characteristic_count", "image_count", "document_count"].includes(key)) return value == null ? null : Number(value);
    if (key === "price") return value == null || value === "" ? null : Number(value);
    if (key === "section_id") return value == null ? null : Number(value);
    if (value == null) return "";
    if (typeof value === "string" && /^[=+@-]/.test(value)) return "'" + value;
    return String(value);
  }));
  if (matrix.length) {
    sheet.getRangeByIndexes(5, 0, matrix.length, width).values = matrix;
    sheet.getRange(`A6:${lastCol}${5 + matrix.length}`).format.font = {name: "Arial", size: 10, color: "#17212B"};
    sheet.getRange(`A6:${lastCol}${5 + matrix.length}`).format.rowHeight = spec.sheet === "Review" ? 36 : 30;
  } else {
    sheet.getRange("A6").values = [["Подтверждённых записей для этого файла нет."]];
    sheet.getRange("A6").format.font = {name: "Arial", size: 10, italic: true, color: "#7A4E00"};
  }
  for (let index = 0; index < width; index++) {
    const [title, key] = spec.columns[index];
    const col = excelColumn(index);
    const chosen = ["new_candidate_id", "new_group_id"].includes(key) ? 34 :
                   key === "source_url" ? 64 : key === "name" || key === "sterbrust_name" || key === "proposed_name" ? 48 :
                   key === "section_path" || key === "export_reason" || key === "match_conflicts" ? 50 :
                   key === "model" ? 24 : key === "observed_at" ? 24 : key === "selection_status" ? 30 :
                   key === "absence_evidence" ? 42 :
                   ["evidence_ref", "detail_review_evidence"].includes(key) ? 60 :
                   key === "observed_detail_titles" ? 48 : key === "enrichment_blockers" ? 50 :
                   Math.max(16, Math.min(28, title.length + 4));
    sheet.getRange(`${col}5:${col}${Math.max(6, 5 + matrix.length)}`).format.columnWidth = chosen;
    if (key === "price") sheet.getRange(`${col}6:${col}${Math.max(6, 5 + matrix.length)}`).setNumberFormat("#,##0.00");
    if (key === "section_id") sheet.getRange(`${col}6:${col}${Math.max(6, 5 + matrix.length)}`).setNumberFormat("0");
    if (key === "observed_at") sheet.getRange(`${col}6:${col}${Math.max(6, 5 + matrix.length)}`).setNumberFormat("yyyy-mm-dd hh:mm");
    if (["name", "sterbrust_name", "proposed_name", "export_reason", "match_conflicts", "enrichment_blockers",
         "new_group_id", "new_candidate_id", "source_url", "evidence_ref", "absence_evidence",
         "observed_detail_titles", "detail_review_evidence", "model"].includes(key)) {
      sheet.getRange(`${col}6:${col}${Math.max(6, 5 + matrix.length)}`).format.wrapText = true;
    }
  }
  if (matrix.length) sheet.getRange(`A6:${lastCol}${5 + matrix.length}`).format.autofitRows();
  sheet.getRange(`A5:${lastCol}5`).format.autofitRows();
  sheet.freezePanes.freezeRows(5);
  sheet.freezePanes.freezeColumns(1);
  workbook.recalculate();
  const check = await workbook.inspect({kind: "region", sheetId: spec.sheet,
                                        range: `A2:${lastCol}${Math.min(9, Math.max(6, 5 + matrix.length))}`,
                                        maxChars: 2500});
  console.log(`${spec.file}: ${matrix.length} data rows; ${check.ndjson.slice(0, 500)}`);
  const errors = await workbook.inspect({kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
                                         options: {useRegex: true, maxResults: 50}, maxChars: 1500});
  if (errors.ndjson.includes("#REF!") || errors.ndjson.includes("#VALUE!")) throw new Error("formula error in workbook");
  const preview = await workbook.render({sheetName: spec.sheet, range: `A1:${lastCol}${Math.min(10, Math.max(6, 5 + matrix.length))}`,
                                         scale: 1, format: "png"});
  await fs.writeFile(path.join(outputDir, `${spec.sheet.toLowerCase()}_preview.png`),
                     new Uint8Array(await preview.arrayBuffer()));
  const blob = await SpreadsheetFile.exportXlsx(workbook);
  await blob.save(path.join(outputDir, spec.file));
}

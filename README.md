# Bank Statement PDF Parser

Extract transactions from bank statement PDFs directly in your browser. No installs, no servers, no uploads — your financial data never leaves your computer.

## What It Does

Copy-pasting from bank statement PDFs produces jumbled data where withdrawals and deposits get mixed together. This tool fixes that by using the actual position of text on the page to reconstruct the original table layout.

It automatically:
- Detects column headers (date, description, withdrawal, deposit, balance)
- Groups transactions into **Withdrawals** and **Deposits** tables
- Calculates totals for each table
- Lets you edit any cell to fix misparses
- Exports to **CSV** or **clipboard**

## Supported Banks

Works with Canadian banks including TD, RBC, BMO, Scotiabank, and others. Uses adaptive column detection rather than hardcoded formats, so it should work with most statement layouts that have selectable text.

## How to Use

1. **Download** `bank-statement-parser.html`
2. **Open** it in any modern browser (Chrome, Firefox, Safari, Edge)
3. **Drag and drop** your bank statement PDF onto the page (or click to browse)
4. **Review** the parsed Withdrawals and Deposits tables
5. **Edit** any cell by clicking on it if something parsed incorrectly
6. **Adjust columns** using the column mapping dropdowns if auto-detection got it wrong
7. **Export** — click "Download CSV" or "Copy" for either table

## Privacy

Everything runs client-side using [PDF.js](https://mozilla.github.io/pdf.js/). Your PDF is processed entirely in your browser. Nothing is sent to any server.

## Limitations

- Only works with PDFs that contain selectable text (not scanned images/photos)
- Complex multi-section statements may need manual column adjustment
- Very unusual layouts may require editing cells after parsing

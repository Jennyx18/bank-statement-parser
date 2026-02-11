# Bank Statement PDF Parser

Extract transactions from bank statement PDFs. Splits withdrawals and deposits into separate tables with totals. Everything stays on your computer.

## What It Does

Copy-pasting from bank statement PDFs produces jumbled data where withdrawals and deposits get mixed together. This tool fixes that by properly reading the PDF's table structure.

It automatically:
- Detects column headers (date, description, withdrawal, deposit, balance)
- Groups transactions into **Withdrawals** and **Deposits** tables
- Calculates totals for each table
- Lets you edit any cell to fix misparses
- Exports to **CSV** or **clipboard**

## Two Versions

### `bank_parser.py` (Recommended)

Uses [pdfplumber](https://github.com/jsvine/pdfplumber) for accurate table extraction. Much better at detecting columns, dates, and descriptions.

**Requirements:** Python 3 + pdfplumber

```bash
pip install pdfplumber
python3 bank_parser.py
```

This opens a browser UI at `http://127.0.0.1:8765`. Drag and drop your PDF, review the tables, export.

### `bank-statement-parser.html` (No Install)

A single HTML file that runs entirely in the browser using PDF.js. No Python needed — just open the file. Works well for simple statement layouts but may struggle with complex ones.

## Supported Banks

Works with Canadian banks including TD, RBC, BMO, Scotiabank, and others. Uses adaptive column detection rather than hardcoded formats, so it should work with most statement layouts that have selectable text.

## How to Use

1. **Run** `python3 bank_parser.py` (or open `bank-statement-parser.html`)
2. **Drag and drop** your bank statement PDF onto the page (or click to browse)
3. **Review** the parsed Withdrawals and Deposits tables
4. **Edit** any cell by clicking on it if something parsed incorrectly
5. **Adjust columns** using the mapping dropdowns if auto-detection got it wrong
6. **Export** — click "Download CSV" or "Copy" for either table

## Privacy

Your PDF is processed locally. The Python version runs a local-only server (`127.0.0.1`). The HTML version uses client-side PDF.js. Nothing is sent to the internet.

## Limitations

- Only works with PDFs that contain selectable text (not scanned images/photos)
- Complex multi-section statements may need manual column adjustment
- Very unusual layouts may require editing cells after parsing

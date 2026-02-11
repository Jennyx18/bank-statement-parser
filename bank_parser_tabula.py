#!/usr/bin/env python3
"""
Bank Statement PDF Parser (Tabula)
Run: python3 bank_parser_tabula.py
Opens a browser UI. Drag-and-drop your bank statement PDF.
Uses tabula-py (Java) for table extraction.
Everything stays local — nothing is uploaded anywhere.
Requires: Java installed, tabula-py (auto-installed on first run)
"""

import http.server
import json
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import webbrowser

# ── Auto-install tabula-py ──
if not shutil.which("java"):
    print("Error: Java is required but not found.")
    print("Install Java first: https://www.java.com/download/")
    sys.exit(1)

try:
    import tabula
    import pandas
except ImportError:
    print("Installing tabula-py (one-time setup)...")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "tabula-py"],
            stdout=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install",
             "--break-system-packages", "tabula-py"],
            stdout=subprocess.DEVNULL,
        )
    import tabula
    import pandas
    print("Done.\n")

PORT = 8765

# ── PDF Parsing ──

DATE_RE = re.compile(
    r'^(?:'
    r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[.\s]?\s*\d{1,2}(?:[,\s]+\d{2,4})?'
    r'|\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?'
    r'|\d{4}[/\-]\d{1,2}[/\-]\d{1,2}'
    r')$',
    re.IGNORECASE
)

HEADER_KEYWORDS = {
    'date': re.compile(r'date|posting\s*date|trans\.?\s*date', re.I),
    'description': re.compile(r'description|details|transaction|particulars|payee', re.I),
    'withdrawal': re.compile(r'withdrawal|debit|charges?|amount\s*deducted|dr', re.I),
    'deposit': re.compile(r'deposit|credit|amount\s*added|cr', re.I),
    'balance': re.compile(r'balance|closing|running', re.I),
}


def parse_amount(s):
    if not s or not isinstance(s, str):
        return None
    s = s.strip().replace('$', '').replace(',', '').replace(' ', '')
    if s.startswith('(') and s.endswith(')'):
        s = '-' + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


def is_date(s):
    return bool(DATE_RE.match(s.strip())) if s else False


def classify_columns(headers):
    mapping = {}
    for i, h in enumerate(headers):
        h_clean = str(h).strip()
        if not h_clean:
            continue
        for role, pat in HEADER_KEYWORDS.items():
            if pat.search(h_clean) and role not in mapping:
                mapping[role] = i
                break
    return mapping


def parse_pdf(file_bytes):
    """Parse a bank statement PDF using tabula and return withdrawals + deposits."""
    # Write to temp file (tabula needs a file path)
    tmp = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
    tmp.write(file_bytes)
    tmp.close()

    try:
        # Try lattice mode first (for PDFs with table borders/lines)
        dfs = tabula.read_pdf(tmp.name, pages='all', lattice=True,
                              multiple_tables=True, silent=True)

        # If lattice found nothing useful, try stream mode
        if not dfs or all(len(df) < 2 for df in dfs):
            dfs = tabula.read_pdf(tmp.name, pages='all', stream=True,
                                  multiple_tables=True, silent=True)

        if not dfs:
            return {'withdrawals': [], 'deposits': [], 'headers': [],
                    'column_mapping': {}, 'method': 'tabula',
                    'total_rows': 0,
                    'error': 'No tables found in PDF. It may be a scanned image.'}

        # Combine all dataframes
        # Pick the largest table (most likely the transaction table)
        best_df = max(dfs, key=lambda df: len(df))

        # Also merge any tables with matching column counts (multi-page statements)
        target_cols = len(best_df.columns)
        matching = [df for df in dfs if len(df.columns) == target_cols]
        if len(matching) > 1:
            best_df = pandas.concat(matching, ignore_index=True)

        # Convert to row lists
        headers = [str(c).strip() for c in best_df.columns.tolist()]
        rows = []
        for _, row in best_df.iterrows():
            rows.append([str(v).strip() if pandas.notna(v) else '' for v in row.tolist()])

        # Detect column mapping from headers
        col_mapping = classify_columns(headers)

        # If headers look like data (no keywords matched), check first row
        if len(col_mapping) < 2:
            # Tabula sometimes puts headers in the first data row
            if rows:
                first_row_mapping = classify_columns(rows[0])
                if len(first_row_mapping) >= 2:
                    headers = rows[0]
                    rows = rows[1:]
                    col_mapping = first_row_mapping

        # If still no mapping, use heuristic
        if len(col_mapping) < 2:
            num_cols = len(headers)
            if num_cols >= 5:
                col_mapping = {'date': 0, 'description': 1, 'withdrawal': 2, 'deposit': 3, 'balance': 4}
            elif num_cols == 4:
                col_mapping = {'date': 0, 'description': 1, 'withdrawal': 2, 'deposit': 3}
            elif num_cols == 3:
                col_mapping = {'date': 0, 'description': 1, 'withdrawal': 2}

        # Parse transactions
        withdrawals = []
        deposits = []
        last_date = ''

        for row in rows:
            while len(row) < len(headers):
                row.append('')

            date_val = row[col_mapping['date']].strip() if 'date' in col_mapping and col_mapping['date'] < len(row) else ''
            desc_val = row[col_mapping['description']].strip() if 'description' in col_mapping and col_mapping['description'] < len(row) else ''
            wd_val = row[col_mapping['withdrawal']].strip() if 'withdrawal' in col_mapping and col_mapping['withdrawal'] < len(row) else ''
            dp_val = row[col_mapping['deposit']].strip() if 'deposit' in col_mapping and col_mapping['deposit'] < len(row) else ''

            # Skip empty and header-like rows
            if not desc_val and not wd_val and not dp_val:
                continue
            row_text = ' '.join(c.strip() for c in row)
            if HEADER_KEYWORDS['date'].search(row_text) and HEADER_KEYWORDS['description'].search(row_text):
                continue
            if re.search(r'(opening|closing|total|statement|continued|page\s+\d)', desc_val, re.I):
                continue

            current_date = date_val if is_date(date_val) else last_date
            if is_date(date_val):
                last_date = date_val

            wd_amount = parse_amount(wd_val)
            dp_amount = parse_amount(dp_val)

            if wd_amount is None and dp_amount is None:
                if desc_val:
                    target = None
                    if withdrawals and deposits:
                        target = withdrawals if len(withdrawals) >= len(deposits) else deposits
                    elif withdrawals:
                        target = withdrawals
                    elif deposits:
                        target = deposits
                    if target:
                        target[-1]['description'] += ' ' + desc_val
                continue

            if wd_amount is not None and wd_amount > 0:
                withdrawals.append({'date': current_date, 'description': desc_val, 'amount': round(wd_amount, 2)})
            if dp_amount is not None and dp_amount > 0:
                deposits.append({'date': current_date, 'description': desc_val, 'amount': round(dp_amount, 2)})

        # Fallback for single amount column
        if not withdrawals and not deposits:
            for row in rows:
                while len(row) < len(headers):
                    row.append('')
                date_val = row[col_mapping.get('date', 0)].strip() if col_mapping.get('date', 0) < len(row) else ''
                desc_val = row[col_mapping.get('description', 1)].strip() if col_mapping.get('description', 1) < len(row) else ''
                if not desc_val:
                    continue
                current_date = date_val if is_date(date_val) else last_date
                if is_date(date_val):
                    last_date = date_val
                for ci in range(len(row)):
                    if ci in (col_mapping.get('date'), col_mapping.get('description')):
                        continue
                    amt = parse_amount(row[ci])
                    if amt is not None and amt != 0:
                        entry = {'date': current_date, 'description': desc_val, 'amount': round(abs(amt), 2)}
                        if row[ci].strip().startswith('-') or row[ci].strip().startswith('('):
                            withdrawals.append(entry)
                        else:
                            deposits.append(entry)
                        break

        return {
            'withdrawals': withdrawals,
            'deposits': deposits,
            'headers': headers,
            'column_mapping': col_mapping,
            'method': 'tabula',
            'total_rows': len(rows),
        }

    finally:
        os.unlink(tmp.name)


def reparse_with_mapping(file_bytes, col_mapping):
    """Re-parse with user-specified column mapping."""
    tmp = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
    tmp.write(file_bytes)
    tmp.close()

    try:
        dfs = tabula.read_pdf(tmp.name, pages='all', lattice=True,
                              multiple_tables=True, silent=True)
        if not dfs or all(len(df) < 2 for df in dfs):
            dfs = tabula.read_pdf(tmp.name, pages='all', stream=True,
                                  multiple_tables=True, silent=True)
        if not dfs:
            return {'withdrawals': [], 'deposits': [], 'error': 'No tables found.'}

        best_df = max(dfs, key=lambda df: len(df))
        target_cols = len(best_df.columns)
        matching = [df for df in dfs if len(df.columns) == target_cols]
        if len(matching) > 1:
            best_df = pandas.concat(matching, ignore_index=True)

        headers = [str(c).strip() for c in best_df.columns.tolist()]
        rows = []
        for _, row in best_df.iterrows():
            rows.append([str(v).strip() if pandas.notna(v) else '' for v in row.tolist()])

        # Check if first row is actually headers
        first_row_mapping = classify_columns(rows[0]) if rows else {}
        if len(first_row_mapping) >= 2:
            headers = rows[0]
            rows = rows[1:]

        withdrawals = []
        deposits = []
        last_date = ''

        for row in rows:
            while len(row) < len(headers):
                row.append('')

            date_val = row[col_mapping['date']].strip() if 'date' in col_mapping and col_mapping['date'] < len(row) else ''
            desc_val = row[col_mapping['description']].strip() if 'description' in col_mapping and col_mapping['description'] < len(row) else ''
            wd_val = row[col_mapping['withdrawal']].strip() if 'withdrawal' in col_mapping and col_mapping['withdrawal'] < len(row) else ''
            dp_val = row[col_mapping['deposit']].strip() if 'deposit' in col_mapping and col_mapping['deposit'] < len(row) else ''

            if not desc_val and not wd_val and not dp_val:
                continue
            row_text = ' '.join(c.strip() for c in row)
            if HEADER_KEYWORDS['date'].search(row_text) and HEADER_KEYWORDS['description'].search(row_text):
                continue
            if re.search(r'(opening|closing|total|statement|continued|page\s+\d)', desc_val, re.I):
                continue

            current_date = date_val if is_date(date_val) else last_date
            if is_date(date_val):
                last_date = date_val

            wd_amount = parse_amount(wd_val)
            dp_amount = parse_amount(dp_val)

            if wd_amount is None and dp_amount is None:
                if desc_val:
                    target = withdrawals if (withdrawals and (not deposits or len(withdrawals) >= len(deposits))) else deposits
                    if target:
                        target[-1]['description'] += ' ' + desc_val
                continue

            if wd_amount is not None and wd_amount > 0:
                withdrawals.append({'date': current_date, 'description': desc_val, 'amount': round(wd_amount, 2)})
            if dp_amount is not None and dp_amount > 0:
                deposits.append({'date': current_date, 'description': desc_val, 'amount': round(dp_amount, 2)})

        return {
            'withdrawals': withdrawals,
            'deposits': deposits,
            'headers': headers,
            'column_mapping': col_mapping,
            'method': 'tabula',
            'total_rows': len(rows),
        }

    finally:
        os.unlink(tmp.name)


# ── Web Server ──

HTML_PAGE = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bank Statement Parser</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #f5f7fa; --surface: #ffffff; --border: #d1d9e6;
    --text: #1a2332; --text-muted: #5a6b80;
    --accent: #2563eb; --accent-hover: #1d4ed8;
    --danger: #dc2626; --success: #16a34a;
    --withdrawal-bg: #fef2f2; --withdrawal-header: #dc2626;
    --deposit-bg: #f0fdf4; --deposit-header: #16a34a;
    --radius: 8px;
  }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--text); line-height: 1.5; min-height: 100vh; padding: 20px; }
  h1 { text-align: center; font-size: 1.6rem; margin-bottom: 4px; }
  .subtitle { text-align: center; color: var(--text-muted); font-size: 0.9rem; margin-bottom: 24px; }
  #drop-zone { border: 2px dashed var(--border); border-radius: var(--radius); padding: 48px 24px; text-align: center; background: var(--surface); cursor: pointer; transition: border-color 0.2s, background 0.2s; max-width: 600px; margin: 0 auto 24px; }
  #drop-zone.drag-over { border-color: var(--accent); background: #eff6ff; }
  #drop-zone p { color: var(--text-muted); margin-top: 8px; }
  #drop-zone .icon { font-size: 2.5rem; }
  #file-input { display: none; }
  #status { text-align: center; padding: 12px; margin-bottom: 16px; border-radius: var(--radius); display: none; }
  #status.info { display: block; background: #eff6ff; color: #1e40af; }
  #status.error { display: block; background: #fef2f2; color: #dc2626; }
  #status.success { display: block; background: #f0fdf4; color: #16a34a; }
  #column-mapping { display: none; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; margin-bottom: 24px; max-width: 800px; margin-left: auto; margin-right: auto; }
  #column-mapping h3 { margin-bottom: 12px; font-size: 1rem; }
  .mapping-row { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; flex-wrap: wrap; }
  .mapping-row label { min-width: 100px; font-weight: 600; font-size: 0.85rem; }
  .mapping-row select { padding: 6px 10px; border: 1px solid var(--border); border-radius: 4px; font-size: 0.85rem; flex: 1; max-width: 300px; }
  .mapping-actions { margin-top: 12px; display: flex; gap: 8px; }
  #tables-section { display: none; }
  .table-container { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); margin-bottom: 24px; overflow: hidden; }
  .table-header { display: flex; justify-content: space-between; align-items: center; padding: 12px 16px; flex-wrap: wrap; gap: 8px; }
  .table-header h2 { font-size: 1.1rem; display: flex; align-items: center; gap: 8px; }
  .table-header .count { font-weight: normal; color: var(--text-muted); font-size: 0.85rem; }
  .withdrawal-table .table-header { background: var(--withdrawal-bg); }
  .deposit-table .table-header { background: var(--deposit-bg); }
  .table-actions { display: flex; gap: 6px; flex-wrap: wrap; }
  .table-wrapper { overflow-x: auto; max-height: 500px; overflow-y: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
  thead th { position: sticky; top: 0; background: var(--bg); padding: 10px 12px; text-align: left; font-weight: 600; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.03em; color: var(--text-muted); border-bottom: 2px solid var(--border); z-index: 1; }
  tbody td { padding: 8px 12px; border-bottom: 1px solid #eef1f6; }
  tbody tr:hover { background: #f8fafc; }
  td[contenteditable] { outline: none; cursor: text; }
  td[contenteditable]:focus { background: #eff6ff; box-shadow: inset 0 0 0 2px var(--accent); }
  td.cost { text-align: right; font-variant-numeric: tabular-nums; }
  .row-delete { background: none; border: none; color: #aaa; cursor: pointer; font-size: 1.1rem; padding: 2px 6px; border-radius: 4px; }
  .row-delete:hover { color: var(--danger); background: var(--withdrawal-bg); }
  .btn { display: inline-flex; align-items: center; gap: 6px; padding: 7px 14px; border: 1px solid var(--border); border-radius: 6px; font-size: 0.82rem; font-weight: 500; cursor: pointer; background: var(--surface); color: var(--text); transition: background 0.15s, border-color 0.15s; white-space: nowrap; }
  .btn:hover { background: var(--bg); border-color: #b0bdd0; }
  .btn-primary { background: var(--accent); color: white; border-color: var(--accent); }
  .btn-primary:hover { background: var(--accent-hover); border-color: var(--accent-hover); }
  .btn-sm { padding: 5px 10px; font-size: 0.78rem; }
  .toast { position: fixed; bottom: 24px; right: 24px; background: #1a2332; color: white; padding: 10px 18px; border-radius: 6px; font-size: 0.85rem; opacity: 0; transform: translateY(10px); transition: all 0.3s; z-index: 1000; }
  .toast.show { opacity: 1; transform: translateY(0); }
  .privacy-note { text-align: center; color: var(--text-muted); font-size: 0.78rem; margin-top: 24px; padding: 12px; }
</style>
</head>
<body>
<h1>Bank Statement Parser</h1>
<p class="subtitle">Extract transactions from PDF bank statements. Your file is processed locally &mdash; nothing leaves your computer.</p>

<div id="drop-zone">
  <div class="icon">&#128196;</div>
  <p><strong>Drag &amp; drop a PDF here</strong> or click to browse</p>
  <p style="font-size:0.8rem;">Supports TD, RBC, BMO, Scotiabank, and more</p>
  <input type="file" id="file-input" accept=".pdf,application/pdf">
</div>

<div id="status"></div>

<div id="column-mapping">
  <h3>Column Mapping</h3>
  <p style="font-size:0.85rem; color:var(--text-muted); margin-bottom:12px;">
    Detected columns from your PDF. Adjust if the auto-detection is wrong, then click Apply.
  </p>
  <div id="mapping-fields"></div>
  <div class="mapping-actions">
    <button class="btn btn-primary" onclick="reparse()">Apply &amp; Re-parse</button>
  </div>
</div>

<div id="tables-section">
  <div class="table-container withdrawal-table">
    <div class="table-header">
      <h2 style="color:var(--withdrawal-header)">Withdrawals <span class="count" id="withdrawal-count"></span></h2>
      <div class="table-actions">
        <button class="btn btn-sm" onclick="addRow('withdrawals')">+ Add Row</button>
        <button class="btn btn-sm" onclick="copyTable('withdrawals')">Copy</button>
        <button class="btn btn-sm" onclick="downloadCSV('withdrawals')">Download CSV</button>
      </div>
    </div>
    <div class="table-wrapper">
      <table id="withdrawals-table">
        <thead><tr><th>Date</th><th>Description</th><th style="text-align:right">Amount</th><th style="width:36px"></th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <div class="table-container deposit-table">
    <div class="table-header">
      <h2 style="color:var(--deposit-header)">Deposits <span class="count" id="deposit-count"></span></h2>
      <div class="table-actions">
        <button class="btn btn-sm" onclick="addRow('deposits')">+ Add Row</button>
        <button class="btn btn-sm" onclick="copyTable('deposits')">Copy</button>
        <button class="btn btn-sm" onclick="downloadCSV('deposits')">Download CSV</button>
      </div>
    </div>
    <div class="table-wrapper">
      <table id="deposits-table">
        <thead><tr><th>Date</th><th>Description</th><th style="text-align:right">Amount</th><th style="width:36px"></th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>
<p class="privacy-note">&#128274; Your PDF is processed locally using Tabula + Java. Nothing is uploaded to the internet.</p>

<script>
let lastResult = null;

const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');
const statusEl = document.getElementById('status');
const mappingSection = document.getElementById('column-mapping');
const mappingFields = document.getElementById('mapping-fields');
const tablesSection = document.getElementById('tables-section');

dropZone.addEventListener('click', () => fileInput.click());
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  const file = e.dataTransfer.files[0];
  if (file && file.type === 'application/pdf') uploadFile(file);
  else showStatus('Please drop a PDF file.', 'error');
});
fileInput.addEventListener('change', e => { if (e.target.files[0]) uploadFile(e.target.files[0]); });

async function uploadFile(file) {
  showStatus('Parsing PDF with Tabula...', 'info');
  try {
    const formData = new FormData();
    formData.append('pdf', file);
    const resp = await fetch('/parse', { method: 'POST', body: formData });
    if (!resp.ok) throw new Error('Server error: ' + resp.status);
    const result = await resp.json();
    if (result.error) { showStatus(result.error, 'error'); return; }
    lastResult = result;
    showMappingUI(result.headers, result.column_mapping);
    renderResults(result);
  } catch (err) {
    showStatus('Error: ' + err.message, 'error');
  }
}

async function reparse() {
  const selects = mappingFields.querySelectorAll('select');
  const mapping = {};
  for (const sel of selects) {
    const v = parseInt(sel.value);
    if (v >= 0) mapping[sel.dataset.role] = v;
  }
  showStatus('Re-parsing with new column mapping...', 'info');
  try {
    const resp = await fetch('/reparse', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ column_mapping: mapping })
    });
    const result = await resp.json();
    if (result.error) { showStatus(result.error, 'error'); return; }
    lastResult = result;
    renderResults(result);
  } catch(err) {
    showStatus('Error: ' + err.message, 'error');
  }
}

function showMappingUI(headers, mapping) {
  mappingSection.style.display = 'block';
  const roles = ['date', 'description', 'withdrawal', 'deposit', 'balance'];
  const roleLabels = { date: 'Date', description: 'Description', withdrawal: 'Withdrawal', deposit: 'Deposit', balance: 'Balance' };
  mappingFields.innerHTML = '';
  for (const role of roles) {
    const div = document.createElement('div');
    div.className = 'mapping-row';
    const label = document.createElement('label');
    label.textContent = roleLabels[role];
    div.appendChild(label);
    const select = document.createElement('select');
    select.dataset.role = role;
    const noneOpt = document.createElement('option');
    noneOpt.value = '-1';
    noneOpt.textContent = '\u2014 Not present \u2014';
    select.appendChild(noneOpt);
    for (let i = 0; i < headers.length; i++) {
      const opt = document.createElement('option');
      opt.value = i;
      opt.textContent = headers[i] || ('Column ' + (i+1));
      if (mapping[role] === i) opt.selected = true;
      select.appendChild(opt);
    }
    div.appendChild(select);
    mappingFields.appendChild(div);
  }
}

function renderResults(result) {
  const wCount = result.withdrawals.length;
  const dCount = result.deposits.length;
  if (wCount + dCount === 0) {
    showStatus('No transactions found. Try adjusting column mappings above.', 'error');
  } else {
    showStatus('Found ' + wCount + ' withdrawal(s) and ' + dCount + ' deposit(s). Parsed ' + result.total_rows + ' rows.', 'success');
  }
  tablesSection.style.display = 'block';
  renderTable('withdrawals', result.withdrawals);
  renderTable('deposits', result.deposits);
}

function renderTable(type, data) {
  const tbody = document.querySelector('#' + type + '-table tbody');
  const countEl = document.getElementById((type === 'withdrawals' ? 'withdrawal' : 'deposit') + '-count');
  tbody.innerHTML = '';
  countEl.textContent = '(' + data.length + ')';
  let total = 0;
  for (let i = 0; i < data.length; i++) {
    const row = data[i];
    total += row.amount;
    const tr = document.createElement('tr');
    tr.innerHTML =
      '<td contenteditable="true" style="min-width:80px;white-space:nowrap">' + esc(row.date) + '</td>' +
      '<td contenteditable="true" style="min-width:200px">' + esc(row.description) + '</td>' +
      '<td contenteditable="true" class="cost" style="min-width:80px">' + row.amount.toFixed(2) + '</td>' +
      '<td><button class="row-delete" data-type="' + type + '" data-index="' + i + '" title="Delete row">&times;</button></td>';
    tbody.appendChild(tr);
  }
  if (data.length > 0) {
    const tr = document.createElement('tr');
    tr.style.fontWeight = '600';
    tr.style.borderTop = '2px solid var(--border)';
    tr.innerHTML = '<td></td><td style="text-align:right">Total</td><td class="cost">' + total.toFixed(2) + '</td><td></td>';
    tbody.appendChild(tr);
  }
  tbody.querySelectorAll('.row-delete').forEach(btn => {
    btn.addEventListener('click', function() {
      const t = this.dataset.type;
      const idx = parseInt(this.dataset.index);
      if (t === 'withdrawals') lastResult.withdrawals.splice(idx, 1);
      else lastResult.deposits.splice(idx, 1);
      renderResults(lastResult);
    });
  });
}

function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

function getTableData(type) {
  const tbody = document.querySelector('#' + type + '-table tbody');
  const rows = [];
  for (const tr of tbody.querySelectorAll('tr')) {
    const cells = tr.querySelectorAll('td[contenteditable]');
    if (cells.length < 3) continue;
    rows.push({ date: cells[0].textContent.trim(), description: cells[1].textContent.trim(), amount: cells[2].textContent.trim() });
  }
  return rows;
}

function toCSV(data) {
  const lines = ['Date,Description,Amount'];
  for (const r of data) lines.push(r.date + ',"' + r.description.replace(/"/g, '""') + '",' + r.amount);
  return lines.join('\n');
}

function downloadCSV(type) {
  const data = getTableData(type);
  if (!data.length) { showToast('No data'); return; }
  const blob = new Blob([toCSV(data)], { type: 'text/csv' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = type + '.csv';
  a.click();
  URL.revokeObjectURL(a.href);
  showToast(type + '.csv downloaded');
}

function copyTable(type) {
  const data = getTableData(type);
  if (!data.length) { showToast('No data'); return; }
  const tsv = data.map(r => r.date + '\t' + r.description + '\t' + r.amount).join('\n');
  navigator.clipboard.writeText(tsv).then(() => showToast('Copied to clipboard')).catch(() => {
    const ta = document.createElement('textarea'); ta.value = tsv;
    document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();
    showToast('Copied to clipboard');
  });
}

function addRow(type) {
  const entry = { date: '', description: '', amount: 0 };
  if (type === 'withdrawals') lastResult.withdrawals.push(entry);
  else lastResult.deposits.push(entry);
  renderResults(lastResult);
  const tbody = document.querySelector('#' + type + '-table tbody');
  const trs = tbody.querySelectorAll('tr');
  if (trs.length >= 2) { const c = trs[trs.length - 2].querySelector('td[contenteditable]'); if (c) c.focus(); }
}

function showStatus(msg, type) { statusEl.textContent = msg; statusEl.className = type; }
function showToast(msg) { const t = document.getElementById('toast'); t.textContent = msg; t.classList.add('show'); setTimeout(() => t.classList.remove('show'), 2000); }
</script>
</body>
</html>'''


class Handler(http.server.BaseHTTPRequestHandler):
    last_pdf_bytes = None

    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == '/parse':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            content_type = self.headers.get('Content-Type', '')
            if 'multipart/form-data' in content_type:
                boundary = content_type.split('boundary=')[1].strip()
                pdf_bytes = self._extract_file_from_multipart(body, boundary)
            else:
                pdf_bytes = body

            if not pdf_bytes:
                self._json_response({'error': 'No PDF data received'})
                return

            Handler.last_pdf_bytes = pdf_bytes
            try:
                result = parse_pdf(pdf_bytes)
                self._json_response(result)
            except Exception as e:
                self._json_response({'error': f'Parse error: {str(e)}'})

        elif self.path == '/reparse':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            params = json.loads(body)
            new_mapping = params.get('column_mapping', {})
            if Handler.last_pdf_bytes is None:
                self._json_response({'error': 'No PDF loaded. Upload a PDF first.'})
                return
            try:
                result = reparse_with_mapping(Handler.last_pdf_bytes, new_mapping)
                self._json_response(result)
            except Exception as e:
                self._json_response({'error': f'Re-parse error: {str(e)}'})
        else:
            self.send_response(404)
            self.end_headers()

    def _extract_file_from_multipart(self, body, boundary):
        boundary_bytes = boundary.encode('utf-8')
        parts = body.split(b'--' + boundary_bytes)
        for part in parts:
            if b'filename=' in part:
                header_end = part.find(b'\r\n\r\n')
                if header_end == -1:
                    continue
                content = part[header_end + 4:]
                if content.endswith(b'\r\n'):
                    content = content[:-2]
                if content.endswith(b'--'):
                    content = content[:-2]
                if content.endswith(b'\r\n'):
                    content = content[:-2]
                return content
        return None

    def _json_response(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))

    def log_message(self, format, *args):
        pass


def main():
    server = http.server.HTTPServer(('127.0.0.1', PORT), Handler)
    print(f"\nBank Statement Parser running at http://127.0.0.1:{PORT}")
    print("Press Ctrl+C to stop.\n")
    threading.Timer(0.5, lambda: webbrowser.open(f'http://127.0.0.1:{PORT}')).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == '__main__':
    main()

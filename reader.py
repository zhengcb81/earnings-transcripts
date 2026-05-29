#!/usr/bin/env python3
"""
美股电话会议纪要 - 本地Web阅读器
Usage: python3 reader.py [--port 8765]
Then open http://localhost:8765 in browser
"""

import json
import re
import argparse
import webbrowser
import threading
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

BASE_DIR = Path(__file__).parent
TRANSCRIPTS_DIR = BASE_DIR / "transcripts"
COMPANIES_FILE = BASE_DIR / "companies.txt"

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>美股电话会议纪要阅读器</title>
<style>
  :root {
    --bg: #0d1117; --bg2: #161b22; --bg3: #21262d; --border: #30363d;
    --text: #e6edf3; --text2: #8b949e; --accent: #58a6ff; --accent2: #3fb950;
    --warn: #d29922; --red: #f85149; --purple: #bc8cff;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; background: var(--bg); color: var(--text); display: flex; height: 100vh; overflow: hidden; }

  /* ── Sidebar ── */
  .sidebar { width: 280px; min-width: 280px; background: var(--bg2); border-right: 1px solid var(--border); display: flex; flex-direction: column; overflow: hidden; }
  .sidebar-header { padding: 16px; border-bottom: 1px solid var(--border); }
  .sidebar-header h1 { font-size: 15px; font-weight: 600; color: var(--accent); margin-bottom: 10px; display: flex; align-items: center; gap: 8px; }
  .sidebar-header h1::before { content: '📊'; }
  .search-box { width: 100%; padding: 8px 12px; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; color: var(--text); font-size: 13px; outline: none; }
  .search-box:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(88,166,255,0.15); }
  .search-box::placeholder { color: var(--text2); }
  .company-list { flex: 1; overflow-y: auto; padding: 8px; }
  .company-item { padding: 10px 12px; border-radius: 6px; cursor: pointer; margin-bottom: 2px; transition: background 0.15s; }
  .company-item:hover { background: var(--bg3); }
  .company-item.active { background: var(--bg3); border-left: 3px solid var(--accent); }
  .company-ticker { font-size: 14px; font-weight: 600; color: var(--accent); }
  .company-name { font-size: 12px; color: var(--text2); margin-top: 2px; }
  .company-count { font-size: 11px; color: var(--text2); float: right; background: var(--bg); padding: 2px 8px; border-radius: 10px; }

  /* ── Main ── */
  .main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
  .toolbar { padding: 12px 20px; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 12px; background: var(--bg2); flex-wrap: wrap; }
  .toolbar-title { font-size: 16px; font-weight: 600; flex: 1; }
  .quarter-tabs { display: flex; gap: 6px; flex-wrap: wrap; }
  .quarter-tab { padding: 5px 14px; border-radius: 20px; font-size: 12px; font-weight: 500; cursor: pointer; border: 1px solid var(--border); background: var(--bg); color: var(--text2); transition: all 0.15s; white-space: nowrap; }
  .quarter-tab:hover { border-color: var(--accent); color: var(--text); }
  .quarter-tab.active { background: var(--accent); color: #fff; border-color: var(--accent); }

  /* ── Content ── */
  .content-wrapper { flex: 1; overflow-y: auto; padding: 0; }
  .content { max-width: 860px; margin: 0 auto; padding: 24px 32px 80px; }
  .meta-bar { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 24px; padding: 14px 18px; background: var(--bg2); border-radius: 8px; border: 1px solid var(--border); }
  .meta-item { font-size: 12px; color: var(--text2); }
  .meta-item strong { color: var(--text); font-weight: 600; }
  .meta-item a { color: var(--accent); text-decoration: none; }
  .meta-item a:hover { text-decoration: underline; }

  .transcript-body { line-height: 1.8; font-size: 15px; }
  .transcript-body h2 { font-size: 18px; font-weight: 700; color: var(--accent); margin: 32px 0 12px; padding-bottom: 6px; border-bottom: 1px solid var(--border); }
  .transcript-body h3 { font-size: 16px; font-weight: 600; color: var(--purple); margin: 24px 0 8px; }
  .transcript-body p { margin-bottom: 14px; color: var(--text); }
  .transcript-body .highlight { background: rgba(210,153,34,0.12); padding: 12px 16px; border-radius: 6px; border-left: 3px solid var(--warn); margin: 16px 0; font-size: 14px; }
  .transcript-body .kpi { display: inline-block; background: var(--bg3); padding: 2px 8px; border-radius: 4px; font-family: 'SF Mono', 'Fira Code', monospace; font-size: 13px; color: var(--accent2); margin: 0 2px; }

  /* ── Split view ── */
  .split-view { max-width: 1200px; margin: 0 auto; padding: 24px 20px 80px; }
  .split-row { display: flex; gap: 16px; margin-bottom: 6px; padding: 14px 0; border-bottom: 1px solid var(--border); min-height: 40px; }
  .split-row:last-child { border-bottom: none; }
  .split-row .en-col { flex: 1; padding-right: 16px; border-right: 2px solid var(--border); }
  .split-row .zh-col { flex: 1; padding-left: 16px; }
  .split-row .en-text { font-size: 14px; line-height: 1.8; color: var(--text); }
  .split-row .zh-text { font-size: 14px; line-height: 1.8; color: #c9d1d9; }
  .split-row .pair-header { font-weight: 700; margin-bottom: 6px; font-size: 13px; }
  .split-row .pair-header.en-header { color: var(--accent); }
  .split-row .pair-header.zh-header { color: var(--purple); }
  .pair-kpi { display: inline-block; background: var(--bg3); padding: 1px 6px; border-radius: 3px; font-size: 12px; color: var(--accent2); }
  .pair-num { display: inline-block; width: 22px; height: 22px; line-height: 22px; text-align: center; border-radius: 50%; background: var(--bg3); color: var(--text2); font-size: 11px; margin-right: 6px; flex-shrink: 0; }

  /* ── Jump nav ── */
  .jump-nav { position: fixed; bottom: 0; left: 280px; right: 0; background: var(--bg2); border-top: 1px solid var(--border); padding: 8px 20px; display: flex; gap: 8px; align-items: center; z-index: 10; }
  .jump-nav span { font-size: 12px; color: var(--text2); margin-right: 8px; }
  .jump-btn { padding: 4px 12px; border-radius: 4px; font-size: 12px; cursor: pointer; border: 1px solid var(--border); background: var(--bg); color: var(--text2); transition: all 0.15s; }
  .jump-btn:hover { border-color: var(--accent); color: var(--text); }

  /* ── Welcome ── */
  .welcome { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; color: var(--text2); }
  .welcome-icon { font-size: 64px; margin-bottom: 16px; }
  .welcome h2 { font-size: 20px; color: var(--text); margin-bottom: 8px; }
  .welcome p { font-size: 14px; }

  /* ── Search highlight ── */
  mark { background: rgba(210,153,34,0.3); color: var(--text); padding: 1px 2px; border-radius: 2px; }

  /* ── Scrollbar ── */
  ::-webkit-scrollbar { width: 8px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--bg3); border-radius: 4px; }
  ::-webkit-scrollbar-thumb:hover { background: var(--border); }

  /* ── Print ── */
  @media print {
    .sidebar, .toolbar, .jump-nav { display: none; }
    .main { margin: 0; }
    .content { max-width: 100%; padding: 0; }
    body { background: #fff; color: #000; }
  }

  /* ── Responsive ── */
  @media (max-width: 768px) {
    .sidebar { width: 100%; min-width: 100%; position: absolute; z-index: 20; transform: translateX(-100%); transition: transform 0.3s; }
    .sidebar.open { transform: translateX(0); }
    .jump-nav { left: 0; }
    .menu-btn { display: block; }
  }
  .menu-btn { display: none; background: none; border: none; color: var(--text); font-size: 20px; cursor: pointer; }

  /* ── Font size control ── */
  .font-ctrl { display: flex; align-items: center; gap: 4px; }
  .font-ctrl button { width: 28px; height: 28px; border-radius: 4px; border: 1px solid var(--border); background: var(--bg); color: var(--text2); cursor: pointer; font-size: 14px; display: flex; align-items: center; justify-content: center; }
  .font-ctrl button:hover { border-color: var(--accent); color: var(--text); }
  .lang-ctrl { display: flex; gap: 4px; }
  .lang-btn { padding: 4px 12px; border-radius: 4px; font-size: 12px; cursor: pointer; border: 1px solid var(--border); background: var(--bg); color: var(--text2); transition: all 0.15s; }
  .lang-btn:hover { border-color: var(--accent); color: var(--text); }
  .lang-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }
</style>
</head>
<body>

<aside class="sidebar" id="sidebar">
  <div class="sidebar-header">
    <h1>电话会议纪要</h1>
    <input class="search-box" id="searchInput" placeholder="搜索公司或Ticker..." autocomplete="off">
  </div>
  <div class="company-list" id="companyList"></div>
</aside>

<main class="main">
  <div class="toolbar" id="toolbar">
    <button class="menu-btn" onclick="document.getElementById('sidebar').classList.toggle('open')">☰</button>
    <div class="toolbar-title" id="toolbarTitle">选择公司开始阅读</div>
    <div class="quarter-tabs" id="quarterTabs"></div>
    <div class="font-ctrl">
      <button onclick="changeFontSize(-1)" title="缩小字号">A-</button>
      <button onclick="changeFontSize(1)" title="放大字号">A+</button>
    </div>
    <div class="lang-ctrl" id="langCtrl" style="display:none">
      <button class="lang-btn active" id="btnEn" onclick="setLang('en')">English</button>
      <button class="lang-btn" id="btnBi" onclick="setLang('bi')">中英对照</button>
    </div>
  </div>
  <div class="content-wrapper" id="contentWrapper">
    <div class="welcome" id="welcomeScreen">
      <div class="welcome-icon">📊</div>
      <h2>美股电话会议纪要阅读器</h2>
      <p>从左侧选择公司开始阅读投资者电话会议纪要</p>
    </div>
    <div class="content" id="contentArea" style="display:none"></div>
  </div>
  <div class="jump-nav" id="jumpNav" style="display:none">
    <span>快速跳转:</span>
    <div id="jumpButtons"></div>
  </div>
</main>

<script>
// ── Data from server ──
const DATA = /*__DATA__*/{}/*__END__*/;

let currentCompany = null;
let currentQuarter = null;
let currentLang = 'en';
let fontSize = 15;

// ── Render sidebar ──
function renderSidebar(filter = '') {
  const list = document.getElementById('companyList');
  const lf = filter.toLowerCase();
  list.innerHTML = '';
  DATA.companies.forEach(c => {
    const transcripts = DATA.transcripts[c.ticker] || [];
    if (filter && !c.ticker.toLowerCase().includes(lf) && !c.name_cn.includes(lf) && !c.name_en.toLowerCase().includes(lf)) return;
    const div = document.createElement('div');
    div.className = 'company-item' + (currentCompany === c.ticker ? ' active' : '');
    div.innerHTML = `<span class="company-ticker">${c.ticker}</span><span class="company-count">${transcripts.length}期</span><div class="company-name">${c.name_cn} · ${c.name_en}</div>`;
    div.onclick = () => selectCompany(c.ticker);
    list.appendChild(div);
  });
}

// ── Select company ──
function selectCompany(ticker) {
  currentCompany = ticker;
  const transcripts = DATA.transcripts[ticker] || [];
  const company = DATA.companies.find(c => c.ticker === ticker);

  document.getElementById('toolbarTitle').textContent = `${company.name_en} (${company.name_cn})`;

  // Render quarter tabs
  const tabs = document.getElementById('quarterTabs');
  tabs.innerHTML = '';
  transcripts.forEach((t, i) => {
    const tab = document.createElement('div');
    tab.className = 'quarter-tab' + (i === 0 ? ' active' : '');
    tab.textContent = t.quarter;
    tab.onclick = () => selectQuarter(ticker, t.quarter);
    tabs.appendChild(tab);
  });

  renderSidebar(document.getElementById('searchInput').value);

  if (transcripts.length > 0) {
    selectQuarter(ticker, transcripts[0].quarter);
  }
}

// ── Select quarter ──
function selectQuarter(ticker, quarter) {
  currentQuarter = quarter;
  const transcripts = DATA.transcripts[ticker] || [];
  const t = transcripts.find(x => x.quarter === quarter);
  if (!t) return;

  // Update tabs
  document.querySelectorAll('.quarter-tab').forEach(tab => {
    tab.classList.toggle('active', tab.textContent === quarter);
  });

  // Parse and render content
  const content = document.getElementById('contentArea');
  const welcome = document.getElementById('welcomeScreen');
  welcome.style.display = 'none';
  content.style.display = 'block';

  // Show/hide lang toggle
  const langCtrl = document.getElementById('langCtrl');
  langCtrl.style.display = t.bilingual_data ? 'flex' : 'none';

  const meta = `<div class="meta-bar">
    <div class="meta-item"><strong>公司:</strong> ${t.company}</div>
    <div class="meta-item"><strong>季度:</strong> ${t.quarter}</div>
    <div class="meta-item"><strong>字数:</strong> ${t.char_count.toLocaleString()}</div>
    <div class="meta-item"><a href="${t.url}" target="_blank">🔗 原文链接</a></div>
  </div>`;

  if (currentLang === 'bi' && t.bilingual_data) {
    // Split-screen bilingual view
    content.innerHTML = meta + renderSplitView(t.bilingual_data);
  } else {
    content.innerHTML = meta + `<div class="transcript-body" id="transcriptBody">${formatContent(t.content)}</div>`;
  }
  content.style.fontSize = fontSize + 'px';

  // Jump nav
  renderJumpNav(t.content);
  document.getElementById('jumpNav').style.display = 'flex';

  // Scroll to top
  document.getElementById('contentWrapper').scrollTop = 0;

  // Highlight search if active
  const sf = document.getElementById('searchInput').value;
  if (sf) highlightInContent(sf);
}

// ── Format content ──
function formatContent(text) {
  const lines = text.split('\n');
  let html = '';
  let inBlock = false;

  for (let line of lines) {
    line = line.trim();
    if (!line) { if (inBlock) { html += '</p>'; inBlock = false; } continue; }

    // Section headers (lines that look like headers)
    if (/^(Revenue|Operating|Earnings|Gross Margin|Net Income|Free Cash|Guidance|Outlook|Q&A|Question.and.Answer|Call participants|Financial|Summary|Highlights)/i.test(line) && line.length < 80) {
      if (inBlock) { html += '</p>'; inBlock = false; }
      html += `<h3>${escHtml(line)}</h3>`;
      continue;
    }

    // Date/time lines
    if (/^\w+,\s+\w+\.\s+\d+,\s+\d{4}/.test(line) || /^\d{1,2}:\d{2}\s+(a\.m\.|p\.m\.)/i.test(line)) {
      if (inBlock) { html += '</p>'; inBlock = false; }
      html += `<p style="color:var(--text2);font-size:13px">${escHtml(line)}</p>`;
      continue;
    }

    // Participant lines
    if (/—\s/.test(line) && line.length < 100) {
      if (inBlock) { html += '</p>'; inBlock = false; }
      html += `<p style="color:var(--purple);font-size:14px">👤 ${escHtml(line)}</p>`;
      continue;
    }

    if (!inBlock) { html += '<p>'; inBlock = true; }
    // Escape HTML first, then apply KPI highlighting
    let safe = escHtml(line);
    safe = safe.replace(/(\$[\d,.]+[BbMmKk]?%?)/g, '<span class="kpi">$1</span>');
    safe = safe.replace(/(\d+(?:\.\d+)?%)/g, '<span class="kpi">$1</span>');
    html += safe + ' ';
  }
  if (inBlock) html += '</p>';
  return html;
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Jump nav (sections) ──
function renderJumpNav(text) {
  const nav = document.getElementById('jumpButtons');
  nav.innerHTML = '';
  const sections = ['Revenue', 'Operating', 'Earnings', 'Guidance', 'Q&A', 'Question'];
  sections.forEach(s => {
    if (text.toLowerCase().includes(s.toLowerCase())) {
      const btn = document.createElement('button');
      btn.className = 'jump-btn';
      btn.textContent = s;
      btn.onclick = () => {
        const body = document.getElementById('transcriptBody');
        const els = body.querySelectorAll('h3');
        for (const el of els) {
          if (el.textContent.toLowerCase().includes(s.toLowerCase())) {
            el.scrollIntoView({ behavior: 'smooth', block: 'start' });
            el.style.background = 'rgba(88,166,255,0.1)';
            setTimeout(() => el.style.background = '', 2000);
            break;
          }
        }
      };
      nav.appendChild(btn);
    }
  });
}

// ── Search ──
document.getElementById('searchInput').addEventListener('input', function() {
  renderSidebar(this.value);
  if (this.value && currentCompany) highlightInContent(this.value);
  if (!this.value) removeHighlight();
});

function highlightInContent(query) {
  removeHighlight();
  if (!query) return;
  const body = document.getElementById('transcriptBody');
  if (!body) return;
  const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  const re = new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')})`, 'gi');
  nodes.forEach(node => {
    if (node.parentElement.tagName === 'MARK') return;
    if (re.test(node.textContent)) {
      const span = document.createElement('span');
      span.innerHTML = node.textContent.replace(re, '<mark>$1</mark>');
      node.parentElement.replaceChild(span, node);
    }
  });
}

function removeHighlight() {
  document.querySelectorAll('mark').forEach(m => {
    const parent = m.parentElement;
    parent.replaceChild(document.createTextNode(m.textContent), m);
    parent.normalize();
  });
}

// ── Font size ──
function changeFontSize(delta) {
  fontSize = Math.max(12, Math.min(22, fontSize + delta));
  const body = document.getElementById('transcriptBody');
  if (body) body.style.fontSize = fontSize + 'px';
}

// ── Split view renderer ──
function renderSplitView(biData) {
  if (!biData || !biData.pairs) return '<p>无中英对照数据</p>';

  const pairs = biData.pairs;
  let html = '<div class="split-view">';

  pairs.forEach((pair, i) => {
    let en = escHtml(pair.en);
    let zh = escHtml(pair.zh);
    const firstLine = pair.en.trim().split('\n')[0];
    const isHeader = /^(Revenue|Operating|Earnings|Gross|Net|Free Cash|Guidance|Q&A|Question|Call participants|Highlights|Summary|Financial|Cash Flow|Capital|More Personal|Intelligent Cloud|Productivity|AI Business)/i.test(firstLine);
    const isParticipant = pair.en.includes('—') && pair.en.length < 120;
    const isDatetime = /^\w+,\s+\w+\.\s+\d+/.test(pair.en.trim()) || /^\d{1,2}:\d{2}/.test(pair.en.trim());

    // KPI highlight
    en = en.replace(/(\$[\d,.]+[BbMmKk]?%?)/g, '<span class="pair-kpi">$1</span>');
    en = en.replace(/(\d+(?:\.\d+)?%)/g, '<span class="pair-kpi">$1</span>');
    zh = zh.replace(/(\$[\d,.]+[BbMmKk]?%?)/g, '<span class="pair-kpi">$1</span>');
    zh = zh.replace(/(\d+(?:\.\d+)?%)/g, '<span class="pair-kpi">$1</span>');

    let icon = '';
    let headerClass = '';
    if (isDatetime) { icon = '📅 '; headerClass = 'pair-header en-header'; }
    else if (isParticipant) { icon = '👤 '; headerClass = 'pair-header en-header'; }
    else if (isHeader) { icon = '📋 '; headerClass = 'pair-header en-header'; }

    html += `<div class="split-row">`;
    // English column
    html += `<div class="en-col">`;
    if (headerClass) {
      html += `<div class="${headerClass}">${icon}<span class="pair-num">${i+1}</span>${en.split('\n')[0]}</div>`;
      if (en.split('\n').length > 1) html += `<div class="en-text">${en.split('\n').slice(1).join('<br>')}</div>`;
    } else {
      html += `<div class="en-text"><span class="pair-num">${i+1}</span>${en.replace(/\n/g, '<br>')}</div>`;
    }
    html += `</div>`;
    // Chinese column
    html += `<div class="zh-col">`;
    if (headerClass) {
      const zhHeader = zh.split('\n')[0];
      html += `<div class="pair-header zh-header">${icon}<span class="pair-num">${i+1}</span>${zhHeader}</div>`;
      if (zh.split('\n').length > 1) html += `<div class="zh-text">${zh.split('\n').slice(1).join('<br>')}</div>`;
    } else {
      html += `<div class="zh-text"><span class="pair-num">${i+1}</span>${zh.replace(/\n/g, '<br>')}</div>`;
    }
    html += `</div>`;
    html += `</div>`;
  });

  html += '</div>';
  return html;
}

// ── Language toggle ──
function setLang(lang) {
  currentLang = lang;
  document.getElementById('btnEn').classList.toggle('active', lang === 'en');
  document.getElementById('btnBi').classList.toggle('active', lang === 'bi');
  // Re-render current content
  if (currentCompany && currentQuarter) selectQuarter(currentCompany, currentQuarter);
}

// ── Keyboard shortcuts ──
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT') return;
  if (e.key === '/') { e.preventDefault(); document.getElementById('searchInput').focus(); }
  if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
    const tabs = document.querySelectorAll('.quarter-tab');
    const activeIdx = Array.from(tabs).findIndex(t => t.classList.contains('active'));
    const newIdx = e.key === 'ArrowLeft' ? activeIdx + 1 : activeIdx - 1;
    if (newIdx >= 0 && newIdx < tabs.length) tabs[newIdx].click();
  }
});

// ── Init ──
renderSidebar();
</script>
</body>
</html>"""


class ReaderHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/' or parsed.path == '/index.html':
            self.serve_index()
        elif parsed.path == '/api/data':
            self.serve_data()
        else:
            self.send_error(404)

    def serve_index(self):
        data = self.build_data()
        html = HTML_TEMPLATE.replace('/*__DATA__*/{}/*__END__*/', json.dumps(data, ensure_ascii=False))
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def serve_data(self):
        data = self.build_data()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def build_data(self):
        companies = load_companies()
        transcripts = {}

        for company in companies:
            ticker = company["ticker"]
            company_dir = TRANSCRIPTS_DIR / ticker
            if not company_dir.exists():
                transcripts[ticker] = []
                continue

            files = sorted(company_dir.glob("*earnings_call*.txt"), reverse=True)
            entries = []
            for f in files:
                content = f.read_text(encoding="utf-8")
                # Parse header
                meta = {}
                for line in content.split("\n"):
                    if line.startswith("Company:"):
                        meta["company"] = line.split(":", 1)[1].strip()
                    elif line.startswith("Quarter:"):
                        meta["quarter"] = line.split(":", 1)[1].strip()
                    elif line.startswith("URL:"):
                        meta["url"] = line.split(":", 1)[1].strip()
                    elif line.startswith("Characters:"):
                        try:
                            meta["char_count"] = int(line.split(":", 1)[1].strip())
                        except (ValueError, IndexError):
                            meta["char_count"] = 0

                # Extract body (after the header separator)
                parts = content.split("=" * 70)
                body = parts[-1].strip() if len(parts) > 1 else content

                entry = {
                    "quarter": meta.get("quarter", f.stem),
                    "company": meta.get("company", ticker),
                    "url": meta.get("url", ""),
                    "char_count": meta.get("char_count", len(body)),
                    "content": body,
                    "filename": f.name,
                }

                # Load bilingual version if available (JSON format)
                bilingual_path = f.parent / f.name.replace("_earnings_call", "_bilingual").replace(".txt", ".json")
                if not bilingual_path.exists():
                    # Also try .txt for old format
                    bilingual_path = f.parent / f.name.replace("_earnings_call", "_bilingual")
                if bilingual_path.exists():
                    bcontent = bilingual_path.read_text(encoding="utf-8")
                    if bilingual_path.suffix == ".json":
                        try:
                            entry["bilingual_data"] = json.loads(bcontent)
                        except (json.JSONDecodeError, OSError):
                            entry["bilingual_data"] = None
                    else:
                        # Old text format - skip
                        entry["bilingual_data"] = None
                else:
                    entry["bilingual_data"] = None

                entries.append(entry)

            transcripts[ticker] = entries

        return {"companies": companies, "transcripts": transcripts}

    def log_message(self, format, *args):
        pass  # Suppress HTTP logs


def load_companies():
    companies = []
    if COMPANIES_FILE.exists():
        for line in COMPANIES_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3:
                companies.append({
                    "ticker": parts[0],
                    "name_cn": parts[1],
                    "name_en": parts[2],
                    "exchange": parts[3] if len(parts) > 3 else "",
                })
    return companies


def main():
    parser = argparse.ArgumentParser(description="美股电话会议纪要阅读器")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    server = HTTPServer(("127.0.0.1", args.port), ReaderHandler)
    url = f"http://localhost:{args.port}"
    print(f"阅读器已启动: {url}")
    print(f"按 Ctrl+C 退出")

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
        server.server_close()


if __name__ == "__main__":
    main()

'use strict';
const TOKEN = window.MASHU_TOKEN;
const AVATAR = '/static/mashu.jpg';
const MAIN = 'assistant';
const $ = (s) => document.querySelector(s);

let messages = [];
let lastId = 0;
let state = { films: [], cameras: [], inbox: [], stages: { used: 0, max: 0 }, desks: { used: 0, max: 0 }, stage_names: [] };
const reports = {};
let live = {};   // 频道 → 正在进行的回复（SSE live 推送）
let current = load('mashu.channel', MAIN);
let view = load('mashu.view', 'chat');
if (['board', 'decisions', 'library', 'budget'].includes(view)) view = 'chat';   // 这几页顶部不显示了：上次停在那里就回聊天
let seen = loadJSON('mashu.seen', {});
let collapsed = loadJSON('mashu.collapsed', { _cands: true });
let query = '';
let candidates = null;
let decisionFilter = '待追认';
let focusEl = null;
const expanded = new Set();   // 直接展开处理的卡片
const docsCache = {};

function load(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } }
function loadJSON(k, d) { try { return JSON.parse(localStorage.getItem(k)) || d; } catch { return d; } }
function save(k, v) { try { localStorage.setItem(k, typeof v === 'string' ? v : JSON.stringify(v)); } catch { /* 隐私模式 */ } }

// ---------- 术语 ----------
const GATE = { prod: '生产发布 / 受保护分支', irreversible: '不可逆操作', preview: '测试环境', 'release-plan': '发布计划', budget: '追加用量' };
function roleName(r) { return r || ''; }
function statusName(s) { return (s.status === 'closed' && s.status_label) || ST[SEQ_ST[s.status]] || s.status_label || ''; }

// ---------- api ----------
async function api(path, body) {
  const res = await fetch(path, {
    method: body ? 'POST' : 'GET',
    headers: { 'X-Mashu-Token': TOKEN, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
function toast(text, action = null, label = '去处理') {
  let el = $('.toast');
  if (!el) { el = document.createElement('div'); el.className = 'toast'; document.body.appendChild(el); }
  el.innerHTML = `<span>${esc(text)}</span>${action ? `<button>${esc(label)}</button>` : ''}`;
  el.classList.toggle('action', !!action);
  if (action) el.querySelector('button').onclick = () => { el.classList.remove('show'); action(); };
  el.classList.add('show');
  clearTimeout(el._t); el._t = setTimeout(() => el.classList.remove('show'), action ? 6000 : 2800);
}

// ---------- 图标：统一的线条 SVG（Lucide 风格，1.75 描边，currentColor），界面里不用表情 ----------
const ICONS = {
  folder: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
  chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  mail: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
  board: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18M15 3v18"/>',
  monitor: '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',
  notebook: '<path d="M2 6h4M2 10h4M2 14h4M2 18h4"/><rect x="4" y="2" width="16" height="20" rx="2"/><path d="M16 2v20"/>',
  library: '<path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/>',
  wallet: '<path d="M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2a1 1 0 0 0-1-1"/><path d="M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4"/>',
  refresh: '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
  contrast: '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>',
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  'chev-r': '<path d="m9 18 6-6-6-6"/>',
  'chev-l': '<path d="m15 18-6-6 6-6"/>',
  'chev-d': '<path d="m6 9 6 6 6-6"/>',
  'arrow-r': '<path d="M5 12h14M12 5l7 7-7 7"/>',
  'arrow-ur': '<path d="M7 17 17 7M7 7h10v10"/>',
  'arrow-d': '<path d="M12 5v14M19 12l-7 7-7-7"/>',
  swap: '<path d="M8 3 4 7l4 4"/><path d="M4 7h16"/><path d="m16 21 4-4-4-4"/><path d="M20 17H4"/>',
  sparkle: '<path d="M12 3l1.9 5.8L20 10.5l-6.1 1.7L12 18l-1.9-5.8L4 10.5l6.1-1.7z"/>',
  bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
  panel: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M15 3v18"/>',
  clip: '<path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.09-3.09a2 2 0 0 0-2.82 0L6 21"/>',
  file: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/>',
  doc: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z"/><path d="M14 2v4a2 2 0 0 0 2 2h4M16 13H8M16 17H8M10 9H8"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
  archive: '<rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8M10 12h4"/>',
  film: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 3v18M17 3v18M3 7.5h4M3 12h18M3 16.5h4M17 7.5h4M17 16.5h4"/>',
  gear: '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
  pen: '<path d="M12 20h9"/><path d="M16.38 3.62a1 1 0 0 1 3 3L7.37 18.64a2 2 0 0 1-.86.5l-2.87.84a.5.5 0 0 1-.62-.62l.84-2.87a2 2 0 0 1 .5-.86z"/>',
  hand: '<path d="M18 11V6a2 2 0 0 0-4 0v5M14 10V4a2 2 0 0 0-4 0v6M10 10.5V6a2 2 0 0 0-4 0v8"/><path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15"/>',
  ask: '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22z"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3M12 17h.01"/>',
  star: '<path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01z"/>',
  alert: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4M12 17h.01"/>',
  clipboard: '<rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2M12 11h4M12 16h4M8 11h.01M8 16h.01"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  inbox: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
  flag: '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1zM4 22v-7"/>',
  'check-c': '<circle cx="12" cy="12" r="9"/><path d="m9 12 2 2 4-4"/>',
  'x-c': '<circle cx="12" cy="12" r="9"/><path d="m15 9-6 6M9 9l6 6"/>',
  brain: '<path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z"/><path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z"/><path d="M12 5v13"/>',
  edit: '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4z"/>',
  play: '<path d="M6 4l14 8-14 8z"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  restart: '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>',
  book: '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2zM22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>',
  ruler: '<path d="M21.3 15.3a2.4 2.4 0 0 1 0 3.4l-2.6 2.6a2.4 2.4 0 0 1-3.4 0L2.7 8.7a2.41 2.41 0 0 1 0-3.4l2.6-2.6a2.41 2.41 0 0 1 3.4 0z"/><path d="m14.5 12.5 2-2M11.5 9.5l2-2M8.5 6.5l2-2M17.5 15.5l2-2"/>',
  flask: '<path d="M10 2v7.53a2 2 0 0 1-.21.9L4.72 20.55A1 1 0 0 0 5.62 22h12.76a1 1 0 0 0 .9-1.45l-5.07-10.12a2 2 0 0 1-.21-.9V2M8.5 2h7M7 16h10"/>',
  flame: '<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.07-2.14-.22-4.05 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.15.43-2.29 1-3a2.5 2.5 0 0 0 2.5 2.5z"/>',
  ban: '<circle cx="12" cy="12" r="9"/><path d="m5.7 5.7 12.6 12.6"/>',
  dot: '<circle cx="12" cy="12" r="4" fill="currentColor" stroke="none"/>',
  circle: '<circle cx="12" cy="12" r="8"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
  terminal: '<path d="m4 17 6-6-6-6M12 19h8"/>',
  news: '<path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-2 2Zm0 0a2 2 0 0 1-2-2v-9c0-1.1.9-2 2-2h2M18 14h-8M15 18h-5M10 6h8v4h-8z"/>',
  wrench: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
  send: '<path d="M14.54 21.69a.5.5 0 0 0 .94-.03l6.5-19a.5.5 0 0 0-.64-.64l-19 6.5a.5.5 0 0 0-.03.94l7.93 3.18a2 2 0 0 1 1.11 1.11z"/><path d="m21.85 2.15-10.94 10.94"/>',
  copy: '<rect x="8" y="8" width="14" height="14" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
  hash: '<path d="M4 9h16M4 15h16M10 3 8 21M16 3l-2 18"/>',
  loader: '<path d="M21 12a9 9 0 1 1-6.22-8.56"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  pause: '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>',
};
function icon(name, size = 16, cls = '') {
  return `<svg class="ic${cls ? ' ' + cls : ''}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ICONS.circle}</svg>`;
}

// ---------- 渲染工具 ----------
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
// 聊天里的图片 / 文件：消息里带本机绝对路径（`![说明](/abs/x.png)`、`📎 [名](/abs/x.pdf)`），经 /api/blob 显示
const IMG_RX = /\.(png|jpe?g|gif|webp|svg|bmp)$/i;
function blobUrl(p) { return `/api/blob?token=${encodeURIComponent(TOKEN)}&path=${encodeURIComponent(p)}`; }
function unesc(s) { return String(s).replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, '&'); }
function baseName(p) { return String(p).split('/').pop(); }
function fileIcon(name) { return icon(IMG_RX.test(name) ? 'image' : /\.pdf$/i.test(name) ? 'doc' : /\.(zip|tar|gz|tgz|7z)$/i.test(name) ? 'archive' : /\.(mp4|mov|webm)$/i.test(name) ? 'film' : 'clip', 15); }
function imgHtml(p, alt) {
  return `<a class="img-wrap" data-img="${esc(p)}" data-name="${esc(alt || baseName(p))}" title="${esc(p)}"><img class="md-img" loading="lazy" src="${esc(blobUrl(p))}" alt="${esc(alt || baseName(p))}"></a>`;
}
function fileChip(p, name) {
  if (/\/$/.test(name || '')) return `<span class="md-file" data-reveal="${esc(p)}" title="在访达中打开：${esc(p)}"><span>${icon('folder', 15)}</span>${esc(name)}</span>`;   // 拖进来的文件夹
  if (IMG_RX.test(p)) return imgHtml(p, name);
  return `<a class="md-file" data-reveal="${esc(p)}" href="${esc(blobUrl(p))}" target="_blank" rel="noopener" title="${/\.(md|markdown)$/i.test(p) ? '点击在网页里查看' : '点击在访达中显示'}，⌘ 点击在浏览器打开：${esc(p)}"><span>${fileIcon(p)}</span>${esc(name || baseName(p))}</a>`;
}
// 正文里没写成 markdown 的图片路径（“截图在 /tmp/x.png”）也在气泡下面直接显示
function bareImages(text) {
  const linked = new Set([...String(text).matchAll(/\]\(([^)\s]+)\)/g)].map((m) => m[1]));
  const out = [];
  for (const m of String(text).matchAll(/(?:^|[\s`'"(（：:，,])((?:~\/|\/(?!\/))[^\s`'"()（）<>，。；;]*\.(?:png|jpe?g|gif|webp))(?=$|[\s`'"()（）<>，。；;:：,]|\.(?:\s|$))/gim)) {
    if (!linked.has(m[1]) && !out.includes(m[1])) out.push(m[1]);
  }
  return out.slice(0, 8);
}
function galleryHtml(text) {
  const list = bareImages(text);
  return list.length ? `<div class="att-gallery">${list.map((p) => imgHtml(p)).join('')}</div>` : '';
}
function mdLines(p) {
  let h = esc(p);
  h = h.replace(/!\[([^\]]*)\]\(((?:\/|~\/)[^)\s]+)\)/g, (m, alt, x) => imgHtml(unesc(x), unesc(alt)));
  h = h.replace(/(?:📎\s*)?\[([^\]]+)\]\(((?:\/|~\/)[^)\s]+)\)/g, (m, name, x) => fileChip(unesc(x), unesc(name)));
  h = h.replace(/`([^`\n]+)`/g, '<code>$1</code>');
  h = h.replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>');
  h = h.replace(/^#{1,6}\s+(.+)$/gm, '<b>$1</b>');
  h = h.replace(/^([ \t]*)[-*]\s+/gm, (m, sp) => `${'&nbsp;'.repeat(sp.length * 2)}• `);
  h = h.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  h = h.replace(/\[([^\]]+)\]\(([^)\s:]+\.(?:md|json))\)/g, '<a class="md-link" data-md-link="$2">$1</a>');
  h = h.replace(/^&gt;\s?(.*)$/gm, '<span class="quote">$1</span>');
  return h.replace(/\n/g, '<br>');
}
const MD_ROW = /^\s*\|.*\|\s*$/;
const MD_SEP = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/;
function mdCells(line) { return line.trim().replace(/^\||\|$/g, '').split('|').map((c) => mdLines(c.trim())); }
function mdTable(lines) {
  const [head, , ...body] = lines;
  return `<div class="md-table"><table><thead><tr>${mdCells(head).map((c) => `<th>${c}</th>`).join('')}</tr></thead>
    <tbody>${body.map((r) => `<tr>${mdCells(r).map((c) => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}
function md(text) {
  const parts = String(text ?? '').split(/```[\w-]*\n?/);
  return parts.map((p, i) => {
    if (i % 2) return `<pre><code>${esc(p.replace(/\n$/, ''))}</code></pre>`;
    // 表格：表头行 + 分隔行 + 若干数据行，其余照常
    const lines = p.split('\n');
    const out = [];
    let buf = [];
    for (let j = 0; j < lines.length; j++) {
      if (MD_ROW.test(lines[j]) && MD_SEP.test(lines[j + 1] || '')) {
        let k = j + 2;
        while (k < lines.length && MD_ROW.test(lines[k])) k++;
        if (buf.length) out.push(mdLines(buf.join('\n').replace(/\n+$/, '')));
        out.push(mdTable(lines.slice(j, k)));
        buf = [];
        j = k - 1;
        if (lines[k] === '') j = k;      // 表格后的空行不再多出一个换行
      } else buf.push(lines[j]);
    }
    if (buf.length) out.push(mdLines(buf.join('\n')));
    return out.join('');
  }).join('');
}
function hue(name) { let h = 0; for (const c of String(name)) h = (h * 31 + c.charCodeAt(0)) % 360; return h; }
function letterAv(name, cls = 'av', light = '') {
  const l = light ? `<span class="light ${light}"></span>` : '';
  if (!name) return `<div class="${cls} sys-av">${icon('gear', 15)}${l}</div>`;
  return `<div class="${cls}" style="background:hsl(${hue(name)} 42% 58%)" title="${esc(name)}">${esc([...name][0])}${l}</div>`;
}
function mashuAv(cls = 'av', light = '') {
  return `<div class="${cls}"><img src="${AVATAR}" alt="麻薯">${light ? `<span class="light ${light}"></span>` : ''}</div>`;
}
function camLight(c) {
  if (!c) return '';
  if (c.state === 'queued') return 'queued';
  if (!c.alive) return '';
  return c.busy ? 'shooting' : 'idle';
}
function camText(c, offText = '未运行') {
  if (!c) return offText;
  if (c.state === 'queued') return '排队';
  if (!c.alive) return '已退出';
  return c.busy ? '忙碌' : '空闲';
}
function strip(stages, big = false, defs = null) {
  if ((stages || []).length <= 1) return '';   // 直接做：没有阶段
  const cls = (s) => ({ 已完成: 'done', 跳过: 'skip', 进行中: 'doing', 待确认: 'wait' }[s] || '');
  return `<span class="strip${big ? ' big' : ''}">${(stages || []).map((s, i) =>
    `<i class="${cls(s)}" title="${esc(stageName(i + 1, defs))}：${esc(s || '未开始')}"></i>`).join('')}</span>`;
}
function stageName(id, defs = null) {
  const s = (defs || state.stage_names || [])[id - 1];
  if (!s) return `阶段 ${id}`;
  return s.name;
}
function timeText(ts) { return ts ? ts.slice(5, 16).replace('T', ' ') : ''; }
function filmOf(id) { return state.films.find((f) => f.id === id); }
function seqOf(ch) {
  const [fid, key] = ch.split('/');
  const f = filmOf(fid);
  return f && key ? [f, f.sequences.find((s) => s.key === key)] : [f, null];
}
function seqLabel(f, s) { return `${f.title} #${s.num ?? s.key}`; }
function pct(x) { return x == null ? '未知' : `${Math.round(x * 100)}%`; }
function num(n) { return n == null ? '—' : Number(n).toLocaleString(); }
const KIND = { ask: '请示', confirm: '申请进入下一阶段', gate: '申请审批', tell: '通报', report: '汇报', 'stage-done': '阶段完成', 'sequence-done': '功能完成' };
const MSG_TYPE = { ask: '请示', approve: '审批', fyi: '通报', done: '完成', blocked: '阻塞', handoff: '交接' };
const NOTIFY = ['tell', 'report', 'stage-done', 'sequence-done'];
const LEVEL = { producer: '子脑', assistant: '主脑', executive: '用户', crew: '执行者', 'self-iterate': '自迭代', external: '外部' };

// ---------- 频道 ----------
// 主脑、子脑之间的往来和例行通报只进邮箱，不出现在用户和麻薯的对话里（功能频道是执行者的工作记录，照常显示）
const MAIL_ONLY = ['event', 'digest', 'memo', 'forward', 'report'];
function inChat(m) {
  if (m.channel.includes('/')) return true;
  return !MAIL_ONLY.includes(m.kind) && !(m.kind === 'notice' && m.text.startsWith('（记入简报）'));
}
function channelMessages(ch) { return messages.filter((m) => m.channel === ch && inChat(m)); }
function unreadList(ch) {
  return messages.filter((m) => m.channel === ch && m.id > (seen[m.channel] || 0) && m.sender !== 'executive' && inChat(m) &&
    !['notice', 'forward', 'report'].includes(m.kind));
}
// 红点：点了跳到这几个频道里最早的一条未读
function unreadBadge(chs) {
  const us = chs.flatMap(unreadList);
  if (!us.length) return '';
  const m = us.reduce((x, y) => (y.id < x.id ? y : x));
  return `<span class="badge unread-b" data-go="${esc(m.channel)}" data-focus="m-${m.id}" title="未读，点击跳到第一条">${us.length}</span>`;
}
function markSeen() {
  const list = channelMessages(current);
  if (list.length) seen[current] = list[list.length - 1].id;
  save('mashu.seen', seen);
}
function go(ch, focus, fromHash) {
  if (ch !== current) { $('#input').value = drafts[ch] || ''; autosize(); closeSlash(); }
  if (!focus && !fromHash) { const u = unreadList(ch)[0]; if (u) focus = `m-${u.id}`; }   // 有未读就停在第一条未读
  const switched = ch !== current;
  current = ch; focusEl = focus || null;
  save('mashu.channel', current);
  if (switched) { detailData = null; setTimeout(pollDetail, 0); }
  if (focus && /^raised-/.test(focus)) {
    const rid = Number(focus.split('-')[1]);
    if (reports[rid] && reports[rid].status === 'open' && reports[rid].holder !== 'executive') expanded.add(rid);
  }
  delete docsCache[current];
  setView('chat', true);
  renderAll(!focus);
  if (!fromHash) pushHash();
  if (focus) flashTo(focus);
  else $('#input').focus();
}
function flashTo(id, tries = 0) {
  const el = document.getElementById(id);
  if (!el) { if (tries < 10) setTimeout(() => flashTo(id, tries + 1), 150); return; }
  el.scrollIntoView({ block: 'center' });
  el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash');
  const input = el.querySelector('input');
  if (input) input.focus({ preventScroll: true });
}
function hashOf() { return `#/${view}/${encodeURIComponent(current)}${focusEl ? '/' + focusEl : ''}`; }
function pushHash() { if (location.hash !== hashOf()) history.pushState(null, '', hashOf()); }
function readHash() {
  const m = location.hash.match(/^#\/(\w+)\/([^/]+)(?:\/(.+))?$/);
  return m ? { view: m[1], channel: decodeURIComponent(m[2]), focus: m[3] || null } : null;
}
window.addEventListener('popstate', () => {
  const h = readHash();
  if (!h) return;
  if (h.view !== 'chat') { current = h.channel; setView(h.view, true); renderAll(); return; }
  go(h.channel, h.focus, true);
});
function nextPending(afterId) {
  const list = state.inbox || [];
  if (!list.length) return toast('没有待您处理的事了');
  const i = list.findIndex((r) => r.id > (afterId || 0));
  const r = list[i >= 0 ? i : 0];
  go(r.film_id || MAIN, `card-${r.id}`);
}
function setView(v, noHash) {
  view = v; save('mashu.view', v);
  if (!noHash) pushHash();
  document.querySelectorAll('#tabs button[data-view]').forEach((b) => b.classList.toggle('active', b.dataset.view === v));
  requestAnimationFrame(moveTabInd);
  document.querySelectorAll('.view').forEach((s) => s.classList.toggle('show', s.id === `view-${v}`));
  if (typeof applyProjMode === 'function' && $('#wfDash')) applyProjMode();
  if (v === 'mail') renderMail();
  if (v === 'board') renderBoard();
  if (v === 'wall') renderWall();
  if (v === 'accounts') renderAccounts();
  if (v === 'decisions') renderDecisions();
  if (v === 'budget') renderBudget();
  if (v === 'library') renderLibrary();
  if (v === 'selfiter') renderSelfIter();
}

// ---------- 标签页角标 ----------
function renderTabs() {
  const n = (state.inbox || []).length;
  const d = state.decisions_pending || 0;
  const badge = (x, cls = '') => (x ? ` <span class="badge ${cls}">${x}</span>` : '');
  const tab = (v, ic, t, extra = '') => { const b = document.querySelector(`[data-view="${v}"]`); if (!b) return; const h = `${icon(ic, 16)}<span>${t}</span>${extra}`; if (b._html !== h) { b.innerHTML = h; b._html = h; } };
  tab('chat', 'chat', '聊天', badge(n, 'gold'));
  tab('mail', 'mail', '邮箱', badge(mailPending(), 'gold'));
  tab('board', 'board', '看板');
  tab('wall', 'monitor', '会话监控');
  tab('accounts', 'wallet', '模型池');
  tab('decisions', 'notebook', '决策记录', badge(d, 'dim'));
  tab('library', 'library', '资料库');
  tab('budget', 'wallet', '用量');
  tab('selfiter', 'refresh', '自迭代', badge(state.self_iterate_pending || 0, 'gold'));
  moveTabInd();
}
function moveTabInd() {
  const tabs = $('#tabs');
  let ind = tabs.querySelector('.tab-ind');
  if (!ind) { ind = document.createElement('i'); ind.className = 'tab-ind'; tabs.prepend(ind); }
  const b = tabs.querySelector('button.active');
  if (!b) { ind.style.opacity = 0; return; }
  ind.style.opacity = 1; ind.style.left = `${b.offsetLeft - tabs.scrollLeft}px`; ind.style.width = `${b.offsetWidth}px`;
}

// ---------- 左侧：状态 ----------
// 统一的执行状态（侧栏只用直白说法，一眼能看懂）
// 会话状态：空闲 / 执行中 / 阻塞 / 待回复 / 待审批 / 等待后台任务 / 已完成（待查看带小点）/ 已归档（手点归档）
const ST = {
  approve: '待审批', answer: '待回复', relay: '待回复', busy: '执行中', queued: '排队中', waiting: '等后台', peer: '等会话',
  idle: '空闲', blocked: '阻塞', done: '已完成', archived: '已归档', off: '空闲', rest: '空闲',
  stuck: '可能卡住', apierr: 'API 报错',
};
const ST_TIP = { relay: '在等子脑回复', queued: '排队等空位', waiting: '等待后台任务完成', peer: '发了点对点消息，在等对方会话回复', off: '还没启动', rest: '会话在休息，有事会自动叫醒',
  stuck: '忙碌中但很久没有任何进展，可能卡住了', apierr: '调用接口报错，麻薯会自动让它继续' };
const SEQ_ST = { stuck: 'stuck', apierr: 'apierr', sign: 'answer', asking: 'relay', queued: 'queued', shooting: 'busy', waiting: 'waiting', peer: 'peer', idle: 'idle', blocked: 'blocked',
  done: 'done', closed: 'done', archived: 'archived', stopped: 'off' };
// 左侧筛选胶囊：每类对应哪些状态（已归档只在「全部」底下的折叠组里）
const FILTERS = [['all', '全部', null], ['need', '需要我', ['approve', 'answer', 'blocked']], ['busy', '执行中', ['busy', 'relay', 'queued', 'waiting', 'peer']],
  ['idle', '空闲', ['idle', 'off', 'rest']], ['done', '已完成', ['done']]];
let sideFilter = null;   // 只看某类
function inFilter(k, f = sideFilter) { const x = FILTERS.find((y) => y[0] === f); return !x || !x[2] || x[2].includes(k); }
// 侧栏功能排序：需要我 → 执行中 → 等后台 / 等会话回复 → 已完成 → 空闲（已归档另收进折叠组）
const ST_RANK = { approve: 0, answer: 0, blocked: 1, relay: 2, busy: 2, queued: 3, waiting: 3, peer: 3, done: 4, idle: 5, off: 6, rest: 6, archived: 7 };
const unseenDone = (s) => s.seen === false && SEQ_ST[s.status] === 'done';   // 老服务没有 seen 字段时不显示小点
function needOf(list) {
  if (!list.length) return null;
  return list.some((r) => r.gate || r.kind === 'confirm') ? 'approve' : 'answer';
}
function camState(c, offKey = 'rest') {
  if (!c) return offKey;
  if (c.state === 'queued') return 'queued';
  if (!c.alive) return offKey;
  if (c.health === 'stuck' || c.health === 'apierr') return c.health;
  return c.busy ? 'busy' : (c.bg ? 'waiting' : 'idle');
}
function inboxOf(fid, key) {
  return (state.inbox || []).filter((r) => (fid === null ? !r.film_id : r.film_id === fid) && (key === undefined || r.seq_key === key));
}
function brainState(f) {
  const need = needOf(inboxOf(f.id));
  if (need) return need;
  if (f.reports) return 'relay';
  const cs = camState(f.producer);
  if (cs === 'idle' && f.waits && f.waits.length) return 'peer';
  return cs;
}
function seqStateKey(f, s) { return needOf(inboxOf(f.id, s.key)) || SEQ_ST[s.status] || 'off'; }
function pill(k) { return `<span class="pill st-${k}"${ST_TIP[k] ? ` title="${ST_TIP[k]}"` : ''}>${ST[k]}</span>`; }
function sdot(k) { return `<span class="sdot st-${k}"></span>`; }
function ctxLvl(p) { return p >= 0.75 ? 'hi' : p >= 0.6 ? 'mid' : ''; }
// 会话的上下文占用（75% 会被要求交接）：侧栏每个在运行的会话都显示
function ctxChip(c) {
  if (!c || !c.alive || c.context_pct == null) return '';
  const p = c.context_pct;
  return `<span class="ctx ${ctxLvl(p)}" title="上下文 ${pct(p)}${c.model ? ` · ${esc(c.model)}` : ''}${c.effort ? ` · 思考 ${esc(c.effort)}` : ''}">${Math.round(p * 100)}%</span>`;
}
// 正在干活的会话现在在做什么（SSE live 推送）
function toolName(t) { return String(t || '').split('__').pop(); }
function liveText(ch) {
  const l = live[ch];
  if (!l) return '';
  if (l.waiting) return '等待接收';
  if (l.health) return `⚠️ ${l.health_note || (l.health === 'stuck' ? '可能卡住' : 'API 报错')}`;
  const base = l.tool ? `正在用 ${toolName(l.tool)}` : (l.text ? '正在写回复' : (l.tools_total ? `思考中 · 已执行 ${l.tools_total} 步` : '思考中'));
  return l.stalled_min >= STALL_HINT_MIN ? `${base} · ${l.stalled_min} 分钟没动静` : base;
}
// 卡住 / API 报错时，侧栏圆点和状态显示真实原因，而不是一直「执行中」
const STALL_HINT_MIN = 3;       // 屏幕和记录这么多分钟没变化就在状态里提示（15 分钟才算「可能卡住」并提醒）
function liveKey(ch) { const l = live[ch]; return l && l.health ? l.health : 'busy'; }
function liveBusy(ch) { const l = live[ch]; return !!(l && !l.waiting); }
function ageMinutes(since) { return since ? Math.max(0, Math.floor((Date.now() / 1000 - since) / 60)) : 0; }
function ageCompact(mins) {
  if (!mins || mins < 1) return '';
  if (mins < 60) return `${mins}分`;
  const h = Math.floor(mins / 60), r = mins % 60;
  return h >= 24 ? `${Math.floor(h / 24)}天` : (r ? `${h}h${r}m` : `${h}h`);
}
function insertDetailDur(detail, dur) {
  if (!detail || !dur || detail.includes('分钟') || detail.includes('已超时')) return detail;
  for (const mk of [' · 在等 ', ' · 等 ', ' 在等它回复']) {
    const idx = detail.indexOf(mk);
    if (idx > 0) {
      const cut = mk === ' 在等它回复' ? detail.lastIndexOf(' · ', idx) : idx;
      if (cut > 0) return `${detail.slice(0, cut)} · ${dur}${detail.slice(cut)}`;
    }
  }
  return `${detail} · ${dur}`;
}
function waitSuffixes(waits, waitedBy, k, short = false) {
  const parts = [];
  if (k !== 'peer' && waits && waits.length) {
    const liveW = waits.filter((w) => !w.timed_out);
    const src = liveW.length ? liveW : waits;
    const lbls = [...new Set(src.map((w) => (w.num != null ? `#${w.num}` : w.to)))].join('、');
    parts.push(liveW.length ? `等 ${lbls}` : `等 ${lbls} 超时`);
  }
  if (waitedBy && waitedBy.length) {
    const activeB = waitedBy.filter((w) => !w.timed_out);
    if (activeB.length) {
      const lbls = [...new Set(activeB.map((w) => w.from))].join('、');
      parts.push(short ? `${lbls} 等回复` : `${lbls} 在等它回复`);
    }
  }
  return parts.join(' · ');
}
function formatStatusDetail(rawDetail, since, k, ch, waits, waitedBy) {
  const mins = ageMinutes(since);
  if (ch && liveBusy(ch) && !['approve', 'answer'].includes(k)) {
    const lt = liveText(ch);
    let out = mins >= 1 && !lt.includes('分钟') ? `${lt} · 已跑 ${mins} 分钟` : lt;
    const suf = waitSuffixes(waits, waitedBy, 'busy');
    if (suf) out = `${out} · ${suf}`;
    return out;
  }
  let det = rawDetail || '';
  if (!det) return ST[k] || '';
  if (mins >= 1 && !det.includes('分钟') && !det.includes('已超时')) {
    if (k === 'idle' && det.startsWith('空闲')) det = det.replace(/^空闲/, `空闲 ${mins} 分钟`);
    else if (k === 'busy') det = insertDetailDur(det, `已跑 ${mins} 分钟`);
    else if (['peer', 'waiting', 'queued', 'stuck', 'apierr', 'approve', 'answer', 'relay'].includes(k)) {
      det = insertDetailDur(det, `${mins} 分钟`);
    }
  }
  return det;
}
// 左栏极简详情：去掉与状态胶囊重复的前缀，把时长拆到行尾右对齐，悬停仍看完整原文
function compactSideDetail(rawDetail, since, k, ch, waits, waitedBy) {
  const mins = ageMinutes(since);
  const full = formatStatusDetail(rawDetail, since, k, ch, waits, waitedBy);
  const age = ['busy', 'peer', 'waiting', 'queued', 'stuck', 'apierr', 'approve', 'answer', 'relay', 'idle'].includes(k) ? ageCompact(mins) : '';
  if (ch && liveBusy(ch) && !['approve', 'answer'].includes(k)) {
    let text = liveText(ch);
    const suf = waitSuffixes(waits, waitedBy, 'busy', true);
    if (suf) text = `${text} · ${suf}`;
    return { text, age: text.includes('分钟') ? '' : ageCompact(mins), full };
  }
  let text = String(rawDetail || '').trim();
  if (!text) return { text: '', age, full };
  text = text.replace(/\s*在等它回复/g, ' 等回复');
  if (k === 'blocked') text = text.replace(/^阻塞[：:]\s*/, '');
  else if (k === 'queued') text = text.replace(/^排队[：:]\s*/, '').replace(/已满\s*/, '满 ').replace(/^本项目已有\s*/, '本项目 ');
  else if (k === 'waiting') text = text.replace(/^后台[：:]\s*/, '');
  else if (k === 'done') text = text.replace(/^已完成(?:\s*[·・]\s*)?/, '');
  else if (k === 'idle') text = text.replace(/^空闲(?:\s*\d+\s*分钟)?(?:\s*[·・]\s*)?/, '');
  else if (k === 'relay') text = text.replace(/^等子脑回复\s*/, '等子脑 ');
  else if (k === 'approve') text = text.replace(/^等用户批准开工\s*/, '批准开工 ').replace(/^等用户确认\s*/, '确认 ').replace(/^等用户审批\s*/, '审批 ');
  else if (k === 'answer') text = text.replace(/^等用户回复\s*/, '等你回 ');
  else if (k === 'off' || k === 'rest') { if (text === '未开始' || text === '空闲') text = ''; }
  text = text.replace(/^待处理上报\s*/, '待处理 ');
  if (text === ST[k]) text = '';
  return { text, age: text.includes('分钟') || text.includes('已超时') ? '' : age, full };
}
function detailTone(k, isLive) {
  if (k === 'blocked' || k === 'stuck' || k === 'apierr') return 'bad';
  if (isLive || k === 'busy' || k === 'approve' || k === 'answer') return 'em';
  return '';
}
function workingLine() { return `<div class="working">工作中 <span class="dots"><i></i><i></i><i></i></span></div>`; }
// 分段进度条：只有分阶段的工作流功能才有，直接做的不画
function segBar(s) {
  const st = s.stages || [];
  if (st.length <= 1) return '';
  const cls = (x) => ({ 已完成: 'done', 跳过: 'skip', 进行中: 'doing', 待确认: 'wait' }[x] || '');
  return `<span class="segbar">${st.map((x, i) => `<i class="${cls(x)}" title="${esc(stageName(i + 1, s.stage_defs))}：${esc(x || '未开始')}"></i>`).join('')}</span>`;
}
function resetAt(v) {
  if (v == null || v === '') return null;
  if (typeof v === 'number') return v > 1e12 ? v : v * 1000;
  if (/^\d+$/.test(v)) return Number(v) * (v.length > 12 ? 1 : 1000);
  const t = Date.parse(v);
  return Number.isNaN(t) ? null : t;
}
function resetText(v) {
  const t = resetAt(v);
  if (!t) return '';
  const m = Math.max(0, Math.round((t - Date.now()) / 60000));
  if (m < 60) return `${m} 分钟后重置`;
  if (m < 24 * 60) return `${Math.floor(m / 60)} 小时 ${m % 60} 分后重置`;
  const d = new Date(t);
  return `${'日一二三四五六'[d.getDay()] ? '周' + '日一二三四五六'[d.getDay()] : ''} ${d.toTimeString().slice(0, 5)} 重置`;
}
function resetShort(v) {
  const t = resetAt(v);
  if (!t) return '';
  const m = Math.max(0, Math.round((t - Date.now()) / 60000));
  if (m < 60) return `${m}分`;
  if (m < 24 * 60) return `${Math.floor(m / 60)}小时${m % 60}分`;
  return `${Math.floor(m / 1440)}天${Math.floor((m % 1440) / 60)}小时`;
}
function resetCompact(v) {
  const t = resetAt(v);
  if (!t) return '';
  const m = Math.max(0, Math.round((t - Date.now()) / 60000));
  if (m < 60) return `${m}分`;
  if (m < 24 * 60) {
    const h = Math.floor(m / 60);
    const r = m % 60;
    return r ? `${h}h${r}分` : `${h}h`;
  }
  const d = Math.floor(m / 1440);
  const h = Math.floor((m % 1440) / 60);
  return h ? `${d}天${h}h` : `${d}天`;
}
function quotaStaleTag(at) {
  if (!at) return '';
  const age = Math.round((Date.now() / 1000 - at) / 60);
  if (age < 15) return '';
  const s = age >= 60 ? `${Math.floor(age / 60)}h前` : `${age}分前`;
  return `<i class="q-rst" title="${age} 分钟前的数据">·${s}</i>`;
}
// 账号池：左下角显示默认号（Gemini 是本机当前号）的数字，悬停列出所有号
function acctTip(data) {
  const list = (data && data.accounts) || [];
  if (!list.length) return '';
  return list.map((a) => {
    const q = a.quota || {};
    const v = q.billing === 'usage' ? '按量' : q.remaining != null ? `剩 ${Math.round(q.remaining * 100)}%` : [q.five_hour && q.five_hour.used_percentage != null ? `5h ${Math.round(q.five_hour.used_percentage)}%` : '',
      q.seven_day && q.seven_day.used_percentage != null ? `周 ${Math.round(q.seven_day.used_percentage)}%` : ''].filter(Boolean).join(' ') || '额度未知';
    return `${a.id === data.account ? '▶ ' : '   '}${acctName(a)}：${v}${a.disabled ? '（禁用）' : a.status !== 'ok' ? `（${a.status_cn}）` : ''}`;
  }).join('\n');
}
function acctBadge(data) {
  const list = (data && data.accounts) || [];
  return list.length > 1 ? `<i class="q-n" title="${esc(acctTip(data))}">${list.length}号</i>` : '';
}
function renderRateLimitRow(name, data) {
  if (!data) return '';
  if (data.billing === 'usage') return `<div class="q-plat-row"><span class="q-plat-name">${name}${acctBadge(data)}</span><span class="q-plat-vals">按量</span></div>`;
  if (data.installed === false) {
    return `<div class="q-plat-row q-dim" title="${name} 未安装"><span class="q-plat-name">${name}</span><span class="q-plat-vals">未安装</span></div>`;
  }
  const f5 = data.five_hour, fw = data.seven_day;
  const p5 = f5 && f5.used_percentage != null ? Math.round(Number(f5.used_percentage)) : null;
  const pw = fw && fw.used_percentage != null ? Math.round(Number(fw.used_percentage)) : null;
  if (p5 == null && pw == null) {
    return `<div class="q-plat-row" title="${name} 等待会话报上额度"><span class="q-plat-name">${name}</span><span class="q-plat-vals q-dim">等会话报上来</span></div>`;
  }
  const item = (label, p, r) => {
    if (p == null) return '';
    const cls = p >= 90 ? 'hi' : p >= 70 ? 'mid' : '';
    const rTxt = r ? `<i class="q-rst">·${resetCompact(r)}</i>` : '';
    const tDesc = r ? `，${resetText(r)}` : '';
    return `<span class="q-item ${cls}" title="${name} ${label}额度已用 ${p}%${tDesc}">${label} <b>${p}%</b>${rTxt}</span>`;
  };
  return `<div class="q-plat-row" ${data.accounts ? `title="${esc(acctTip(data))}"` : ''}>
    <span class="q-plat-name">${name}${acctBadge(data)}</span>
    <span class="q-plat-vals">
      ${item('5h', p5, f5 && f5.resets_at)}
      ${item('周', pw, fw && fw.resets_at)}
      ${quotaStaleTag(data.at)}
    </span>
  </div>`;
}
function renderOpenCodeRow(data) {
  if (!data) return '';
  if (data.installed === false) {
    return `<div class="q-plat-row q-dim" title="OpenCode 未安装"><span class="q-plat-name">OpenCode</span><span class="q-plat-vals">未安装</span></div>`;
  }
  if (data.today == null && data.week == null) {
    return `<div class="q-plat-row" title="OpenCode 等待花费数据"><span class="q-plat-name">OpenCode</span><span class="q-plat-vals q-dim">等数据</span></div>`;
  }
  const tStr = `$${Number(data.today || 0).toFixed(2)}`;
  const wStr = `$${Number(data.week || 0).toFixed(2)}`;
  return `<div class="q-plat-row" title="OpenCode 今日花费 ${tStr}，本周花费 ${wStr}">
    <span class="q-plat-name">OpenCode</span>
    <span class="q-plat-vals">
      <span class="q-item" title="今日花费">今日 <b>${tStr}</b></span>
      <span class="q-item" title="本周花费">本周 <b>${wStr}</b></span>
      ${quotaStaleTag(data.at)}
    </span>
  </div>`;
}
function renderGeminiRow(data) {
  if (!data) return '';
  if (data.installed === false) {
    return `<div class="q-plat-row q-dim" title="Gemini (agy) 未安装"><span class="q-plat-name">Gemini</span><span class="q-plat-vals">未安装</span></div>`;
  }
  if (data.available && data.remaining != null) {
    const r = Math.round(data.remaining * 100), cls = r <= 10 ? 'hi' : r <= 30 ? 'mid' : '';
    return `<div class="q-plat-row" title="${esc(acctTip(data))}">
      <span class="q-plat-name">Gemini${acctBadge(data)}</span>
      <span class="q-plat-vals">
        <span class="q-item ${cls}" title="各模型里剩得最少的${data.low_model ? `：${esc(data.low_model)}` : ''}${data.reset ? `，${resetText(data.reset)}` : ''}">剩 <b>${r}%</b>${data.reset ? `<i class="q-rst">·${resetCompact(data.reset)}</i>` : ''}</span>
        ${quotaStaleTag(data.at)}
      </span></div>`;
  }
  const reason = data.reason || 'agy CLI 未提供额度查询接口';
  return `<div class="q-plat-row" title="Gemini：${esc(reason)}">
    <span class="q-plat-name">Gemini</span>
    <span class="q-plat-vals q-dim">无额度数据</span>
  </div>`;
}
function quotaHtml() {
  const L = state.limits;
  if (!L || (!L.claude && !L.codex && !L.opencode && !L.gemini && !L.five_hour && !L.seven_day)) {
    return '<div class="quota"><div class="q-stale">额度：等受管会话的状态栏报上来</div></div>';
  }
  const claude = L.claude || (L.five_hour || L.seven_day ? L : null);
  const rows = [
    renderRateLimitRow('Claude', claude),
    renderRateLimitRow('Codex', L.codex),
    renderOpenCodeRow(L.opencode),
    renderGeminiRow(L.gemini),
  ].filter(Boolean).join('');
  return `<div class="quota">${rows}</div>`;
}

// ---------- 按账号分组（#8）：项目有 .mashu/accounts.json 时，侧栏、项目页功能列表、工作流面板按账号分组 / 筛选；没有就完全照旧 ----------
let acctSel = loadJSON('mashu.acct', {});          // 项目 id → 工作流面板选中的账号 id（缺省 = 全部）
const ACCT_C = ['var(--blue)', 'var(--purple)', 'var(--green)', 'var(--amber)', 'var(--red)'];
function acctsOf(f) { const a = f && f.accounts; return a && (a.accounts || []).length ? a : null; }
function acctOfSeq(f, s) { return s.account || (acctsOf(f) || {}).default || ''; }
function acctDot(f, id) { const i = acctsOf(f).accounts.findIndex((a) => a.id === id); return `<i class="acct-dot" style="--ac:${i < 0 ? 'var(--faint)' : ACCT_C[i % ACCT_C.length]}"></i>`; }
function curAcct(f) { const a = acctsOf(f); const v = a && acctSel[f.id]; return v && a.accounts.some((x) => x.id === v) ? v : ''; }
function acctGroups(f, list, sOf = (x) => x) { return acctsOf(f).accounts.map((a) => [a, list.filter((x) => acctOfSeq(f, sOf(x)) === a.id)]); }

function renderStatusBoard() {
  const keys = state.films.flatMap((f) => f.sequences.map((s) => seqStateKey(f, s)));
  const html = FILTERS.map(([id, label]) => {
    const n = keys.filter((k) => inFilter(k, id)).length;
    const on = (sideFilter || 'all') === id;
    return `<button class="fcap f-${id} ${on ? 'on' : ''} ${n ? '' : 'zero'}" data-sfilter="${id}" title="只看「${label}」的功能">${label}<b>${n}</b></button>`;
  }).join('');
  const el = $('#statusBoard');
  if (el._html !== html) { el.innerHTML = html; el._html = html; }
}

function renderSide() {
  renderStatusBoard();
  const a = state.assistant;
  const q = query.trim().toLowerCase();
  const hit = (...xs) => !q || xs.join(' ').toLowerCase().includes(q);
  const pass = (k) => inFilter(k);
  // 麻薯：主脑（主对话，管项目）+ 每个项目一个子脑
  const aState = needOf(inboxOf(null)) || camState(a, 'off');
  const aLive = liveBusy(MAIN);
  const aDet = compactSideDetail(a?.detail || state.assistant_detail || '', a?.detail_since ?? state.assistant_detail_since, aState, MAIN);
  const aTone = detailTone(aState, aLive);
  const boxHtml = `<div class="mashu-box">
    <div class="row main-row ${current === MAIN ? 'active' : ''}" data-go="${MAIN}" title="麻薯主脑：主对话">
      ${mashuAv('av')}
      <div class="meta"><div class="t">麻薯</div>
        <div class="s st-line ${aTone}" title="${esc(aDet.full)}">${sdot(aLive ? liveKey(MAIN) : aState)}<span class="st-t">${esc(aDet.text || ST[aState] || '')}</span>${aDet.age ? `<span class="st-age">${esc(aDet.age)}</span>` : ''}${ctxChip(a)}</div></div>
      ${unreadBadge([MAIN])}</div>
    <button class="ghost-btn" data-theme-toggle title="切换主题"></button>
  </div>`;
  if ($('#assistantItem')._html !== boxHtml) { $('#assistantItem').innerHTML = boxHtml; $('#assistantItem')._html = boxHtml; applyTheme(); }

  const html = [];
  const archAll = [];      // 各项目的已归档功能：统一收到侧栏最下面一个小列表，不占注意力
  for (const f of state.films) {
    const all = f.sequences.map((s) => {
      const k = seqStateKey(f, s);
      return [s, liveBusy(`${f.id}/${s.key}`) && ST_RANK[k] > 2 ? 'busy' : k, k];
    });
    const seqs = all.filter(([s, , k]) => pass(k) && hit(f.title, f.id, s.title, s.key, s.crew?.name || ''))
      .sort((x, y) => (ST_RANK[x[1]] ?? 9) - (ST_RANK[y[1]] ?? 9));
    const bk = brainState(f);
    if ((q || sideFilter) && !seqs.length && !(pass(bk) && hit(f.title, f.id))) continue;
    const open = !collapsed[f.id] || !!sideFilter;
    const nRun = all.filter(([, k]) => ['busy', 'relay', 'queued', 'waiting', 'peer'].includes(k)).length;
    const nLive = all.filter(([, , k]) => k !== 'archived').length;   // 已归档的不计数、不算未读
    const subChs = all.filter(([, , k]) => k !== 'archived').map(([s]) => `${f.id}/${s.key}`);
    const fLive = liveBusy(f.id);
    const fDet = compactSideDetail(f.detail || f.producer?.detail || '', f.detail_since ?? f.producer?.detail_since, bk, f.id, f.waits);
    const fTone = detailTone(bk, fLive);
    const inFilm = current === f.id || current.startsWith(f.id + '/');
    html.push(`<div class="film-card ${inFilm ? 'cur' : ''} ${open && (nLive || acctsOf(f)) ? 'open' : ''}">`);   // 一个项目一张卡：子脑标题 + 它的功能会话
    html.push(`<div class="film-head ${current === f.id ? 'active' : ''}" data-go="${esc(f.id)}" title="麻薯在 ${esc(f.title)} 的子脑 · ${esc(f.path)}">
      <div class="fh-top">
        <span class="caret ${open ? 'open' : ''}" data-toggle="${esc(f.id)}" title="${open ? '收起' : '展开'}功能">${icon('chev-r', 12)}</span>
        ${sdot(fLive ? liveKey(f.id) : bk)}<span class="ft">${esc(f.title)}</span>
        <span class="fc">${nLive || ''}${nRun ? ` · ${nRun} 在跑` : ''}</span>
        ${unreadBadge(open ? [f.id] : [f.id, ...subChs])}${ctxChip(f.producer)}${fLive && !['busy', 'stuck', 'apierr'].includes(bk) ? pill(liveKey(f.id)) : pill(bk)}
        <button class="mini-btn" data-pitch="${esc(f.id)}" title="新功能">${icon('plus', 13)}</button>
      </div>
      ${fDet.text ? `<div class="fh-detail ${fTone}" title="${esc(fDet.full)}"><span class="st-dt">${esc(fDet.text)}</span>${fDet.age ? `<span class="st-age">${esc(fDet.age)}</span>` : ''}</div>` : ''}</div>`);
    if (!open) { html.push('</div>'); continue; }
    const seqRow = ([s, , k]) => {
      const ch = `${f.id}/${s.key}`;
      const c = s.crew;
      const who = (c || s.last)?.name || '';
      const busy = k === 'busy' || liveBusy(ch);
      const fresh = k === 'done' && unseenDone(s);
      const sDet = compactSideDetail(s.detail || '', s.detail_since, k, ch, s.waits, s.waited_by);
      const sTone = detailTone(k, busy);
      const fallback = who ? `「${who}」` : (s.num != null ? `#${s.num}` : '');
      const subTxt = sDet.text || fallback;
      const rowTip = `${f.title} #${s.num ?? ''} ${s.title}${who ? ` · 「${who}」` : ''}${sDet.full ? `\n${sDet.full}` : ''}`;
      const act = ['done', 'blocked', 'idle', 'off'].includes(k) ? `<button class="row-act" data-cam="archive" data-ref="${esc(ch)}" title="归档：收进「已归档」">${icon('archive', 13)}</button>`
        : k === 'archived' ? `<button class="row-act" data-cam="unarchive" data-ref="${esc(ch)}" title="取消归档">${icon('undo', 13)}</button>` : '';
      html.push(`<div class="row seq-row ${current === ch ? 'active' : ''} ${busy ? 'is-busy' : ''} ${fresh ? 'fresh' : ''}" data-go="${esc(ch)}" title="${esc(rowTip)}">
        ${fresh ? '<span class="fresh-dot" title="已完成，还没看结果"></span>' : ''}
        <div class="meta"><div class="t-line"><span class="t">${esc(s.title)}</span>${busy && !['busy', 'stuck', 'apierr'].includes(k) ? pill(liveKey(ch)) : pill(k)}${k === 'archived' ? '' : unreadBadge([ch])}${act}</div>
          <div class="st-detail ${sDet.text ? sTone : 'dim'}" title="${esc(sDet.full || fallback)}">${busy ? '<span class="dots"><i></i><i></i><i></i></span>' : ''}<span class="st-dt">${esc(subTxt)}</span>${segBar(s)}${ctxChip(c)}${sDet.age ? `<span class="st-age">${esc(sDet.age)}</span>` : ''}</div></div></div>`);
    };
    const arch = seqs.filter((x) => x[1] === 'archived').sort((x, y) => (y[0].archived_at || 0) - (x[0].archived_at || 0));   // 归档越晚越靠上
    const cur = seqs.filter((x) => x[1] !== 'archived');
    if (acctsOf(f)) {        // 按账号分组：组标题 + 计数，可收起
      for (const [a, items] of acctGroups(f, cur, (x) => x[0])) {
        if (!items.length && (q || sideFilter)) continue;
        const ak = `acct:${f.id}:${a.id}`;
        const aOpen = !collapsed[ak] || !!q;
        html.push(`<div class="acct-head" data-toggle="${esc(ak)}" title="${esc(a.account ? `账号「${a.account}」` : a.name)} · ${aOpen ? '收起' : '展开'}">
          <span class="caret ${aOpen ? 'open' : ''}">${icon('chev-r', 11)}</span>${acctDot(f, a.id)}<span>${esc(a.name)}</span><b>${items.length}</b></div>`);
        if (aOpen) items.forEach(seqRow);
      }
    } else cur.forEach(seqRow);
    if (arch.length) archAll.push([f, arch, seqRow]);
    if (!f.sequences.length) html.push(`<div class="cand seq-empty"><span>还没有功能</span><button data-pitch="${esc(f.id)}">${icon('plus', 12)}新功能</button></div>`);
    html.push('</div>');
  }
  if (!state.films.length) html.push(`<div class="cand"><span>还没有项目。点上面的 ＋ 登记，或在主对话里让麻薯登记。</span></div>`);
  else if (sideFilter && !html.length) html.push(`<div class="cand"><span>没有「${(FILTERS.find((x) => x[0] === sideFilter) || [])[1] || ''}」的功能</span></div>`);
  const nArch = archAll.reduce((n, [, xs]) => n + xs.length, 0);
  if (nArch) {
    const aOpen = !!collapsed['archOpen:_all'] || !!q;
    html.push(`<div class="group-title arch-title" data-toggle="archOpen:_all" title="${aOpen ? '收起' : '展开'}已归档的功能"><span>${icon('archive', 11)}已归档 (${nArch})</span><span class="caret ${aOpen ? 'open' : ''}">${icon('chev-r', 12)}</span></div>`);
    if (aOpen) {
      html.push('<div class="arch-all">');
      for (const [f, xs, row] of archAll) { html.push(`<div class="arch-film">${esc(f.title)}</div>`); xs.forEach(row); }
      html.push('</div>');
    }
  }
  html.push(`<div class="group-title" data-toggle="_cands"><span>未登记的文件夹${candidates ? ` (${candidates.length})` : ''}</span><span class="caret ${collapsed._cands ? '' : 'open'}">${icon('chev-r', 12)}</span></div>`);
  if (!collapsed._cands) {
    if (!candidates) { html.push('<div class="cand"><span>扫描中…</span></div>'); loadCandidates(); } else {
      for (const c of candidates.slice(0, 80)) {
        html.push(`<div class="cand"><span title="${esc(c.path)}">${esc(c.name)}${c.git ? '' : ' <small>(非 git)</small>'}</span>
          <button data-greenlight="${esc(c.path)}">登记</button></div>`);
      }
      if (!candidates.length) html.push('<div class="cand"><span>没有未登记的文件夹</span></div>');
    }
  }
  const setH = (sel, h) => { const e = $(sel); if (e._html !== h) { e.innerHTML = h; e._html = h; } };
  setH('#slate', html.join(''));
  const byAcct = state.stages.by_account || [];
  const execTxt = byAcct.length ? byAcct.map((x) => `${esc(x.label)} ${x.used}/${state.stages.max}`).join(' · ') : `0（每号≤${state.stages.max}）`;
  setH('#sideFoot', `${quotaHtml()}<div class="run-stat"><span title="执行者并发槽：每个账号（Claude、每个 Codex 号…）各自最多同时 ${state.stages.max} 个，总数不限">执行 ${execTxt}</span>
    ${state.reads && state.reads.used ? `<span title="只读阶段（分阶段工作流里的方案等）并发槽">只读 ${state.reads.used}/${state.reads.max}</span>` : ''}
    <span title="同时在岗的子脑">子脑 ${state.desks.used}/${state.desks.max}</span></div>`);
  const tr = $('#tabsRight');
  const tmuxCmd = `tmux attach -t ${state.tmux || 'mashu-studio'}`;
  const stale = !!state.server?.stale;
  if (tr._cmd !== tmuxCmd + stale) {
    tr._cmd = tmuxCmd + stale;
    tr.innerHTML = `<button class="ghost-btn restart-btn ${stale ? 'stale' : ''}" data-restart title="${stale ? '麻薯的代码改过了，重启网页服务后生效（各会话不受影响）' : '重启网页服务和调度器（各会话不受影响）'}">${icon('restart', 14)}<span>${stale ? '有更新，重启生效' : '重启服务'}</span></button>
      <span class="tmux" data-copy="${esc(tmuxCmd)}" title="点击复制">${icon('terminal', 13)}${esc(tmuxCmd)}</span>
      <button class="ghost-btn bell" data-feed title="动态：各项目的进展和交互">${icon('bell', 16)}<span id="feedBadge"></span></button>
      <button class="ghost-btn" data-palette title="跳转 / 命令（⌘K）"><span class="kbd">⌘K</span></button>
      <button class="ghost-btn" data-panel-toggle title="收起 / 展开右侧栏（⌘\\）">${icon('panel', 16)}</button>`;
  }
}
async function loadCandidates() {
  if (loadCandidates.busy) return;
  loadCandidates.busy = true;
  try { candidates = (await api('/api/candidates')).candidates; } catch (e) { candidates = []; toast(e.message); }
  loadCandidates.busy = false;
  renderSide();
}

// ---------- 频道头 ----------
// 会话控制：预设 / CLI / 模型 / 思考 / 账号 / Code Mode（按对话记住，空闲时在原窗格 --resume 重开生效）+ 交接 / 新会话
// 功能批准开工时定的执行配置（state.json 的 exec）：执行者对话的「默认」先看它，再看角色默认预设
function featExec(ch) {
  if (!ch || ch === MAIN || !ch.includes('/')) return null;
  const s = seqOf(ch)[1];
  return (s && s.exec) || null;
}
function presetsList() { return state.presets || []; }
function presetById(id) { return id ? presetsList().find((x) => x.id === id) || null : null; }
function rolePresetId(role) { return (state.role_presets || {})[role] || ''; }
function rolePreset(role) { return presetById(rolePresetId(role)); }
function roleDefaultExec(role) {
  const pr = rolePreset(role);
  if (pr) {
    return { cli: pr.cli || 'claude', model: pr.model || '', effort: pr.effort || '',
      account: pr.cli === 'codex' ? (pr.account || '') : '', code: pr.cli === 'claude' ? !!pr.code_mode : false,
      preset: pr.id, preset_name: pr.name };
  }
  const d = state.pref_defaults || {};
  const cli = (d.providers || {})[role] || 'claude';
  const pl = platOf(cli);
  return { cli, model: (d[pl.default_key] || {})[role] || '', effort: (d.efforts || {})[role] || '',
    account: '', code: cli === 'claude' && (d.codes || {})[role] === 'on', preset: '', preset_name: '' };
}
function execSame(a, b) {
  const ac = a.cli || 'claude', bc = b.cli || 'claude';
  return ac === bc && (a.model || '') === (b.model || '') && (a.effort || '') === (b.effort || '')
    && (ac === 'codex' ? (a.account || '') : '') === (bc === 'codex' ? (b.account || '') : '')
    && (ac === 'claude' ? !!a.code : false) === (bc === 'claude' ? !!b.code : false);
}
function baseExecOf(ch, role) {
  let cur = roleDefaultExec(role);
  const ex = featExec(ch);
  if (ex) {
    const epr = presetById(ex.preset);
    if (epr) {
      cur = { cli: epr.cli || 'claude', model: epr.model || '', effort: epr.effort || '',
        account: epr.cli === 'codex' ? (epr.account || '') : '', code: epr.cli === 'claude' ? !!epr.code_mode : false,
        preset: epr.id, preset_name: epr.name };
    }
    if (ex.cli && ex.cli !== cur.cli) {
      cur = { cli: ex.cli, model: '', effort: cur.effort, account: '', code: false, preset: '', preset_name: '' };
    }
    if (ex.model) { cur.model = ex.model; if (!epr || cur.model !== (epr.model || '')) { cur.preset = ''; cur.preset_name = ''; } }
    if (ex.effort) { cur.effort = ex.effort; if (!epr || cur.effort !== (epr.effort || '')) { cur.preset = ''; cur.preset_name = ''; } }
    if (cur.cli === 'codex' && ex.account) { cur.account = ex.account; if (!epr || cur.account !== (epr.account || '')) { cur.preset = ''; cur.preset_name = ''; } }
    if (cur.cli === 'claude' && 'code_mode' in ex) { cur.code = !!ex.code_mode; if (!epr || cur.code !== !!epr.code_mode) { cur.preset = ''; cur.preset_name = ''; } }
  }
  const p = (state.prefs || {})[ch] || {};
  const cpr = presetById(p.preset);
  if (cpr) {
    cur = { cli: cpr.cli || 'claude', model: cpr.model || '', effort: cpr.effort || '',
      account: cpr.cli === 'codex' ? (cpr.account || '') : '', code: cpr.cli === 'claude' ? !!cpr.code_mode : false,
      preset: cpr.id, preset_name: cpr.name };
  }
  return cur;
}
function effectiveExec(ch, role) {
  const base = baseExecOf(ch, role);
  const p = (state.prefs || {})[ch] || {};
  let cli = base.cli, model = base.model, effort = base.effort, account = base.account, code = base.code;
  let preset = base.preset, preset_name = base.preset_name;
  if (p.provider && p.provider !== cli) {
    cli = p.provider; model = ''; account = ''; code = false; preset = ''; preset_name = '';
  }
  const mk = platOf(cli).model_key;
  if (mk in p) model = p[mk] || '';
  if ('effort' in p) effort = p.effort || '';
  if (cli === 'codex' && 'xacct' in p) account = p.xacct || '';
  if (cli === 'claude' && (p.code === 'on' || p.code === 'off')) code = p.code === 'on';
  const cur = { cli, model, effort, account: cli === 'codex' ? account : '', code: cli === 'claude' ? code : false };
  const customized = !execSame(cur, base);
  return { ...cur, preset, preset_name, base, customized };
}
function presetSelectOpts(selectedId, defaultPreset, isCustom) {
  const defLabel = defaultPreset ? `默认：${defaultPreset.name}` : '默认：Claude 默认';
  return `<option value="" ${!isCustom && !selectedId ? 'selected' : ''}>${esc(defLabel)}</option>`
    + (isCustom ? '<option value="__custom__" selected disabled>自定义</option>' : '')
    + presetsList().map((x) => `<option value="${esc(x.id)}" ${!isCustom && selectedId === x.id ? 'selected' : ''}>${esc(x.name)}</option>`).join('');
}
function presetSummary(pr) {
  const parts = [PROV_CN[pr.cli] || pr.cli, pr.model || 'CLI 默认', `思考 ${pr.effort || '默认'}`];
  if (pr.cli === 'codex') {
    const a = pr.account ? cliAccts('codex').find((x) => x.id === pr.account) : null;
    parts.push(`账号 ${pr.account ? (acctName(a) || pr.account) : '自动'}`);
  }
  if (pr.cli === 'claude' && pr.code_mode) parts.push('Code 开');
  return parts.join(' · ');
}
const PROV_CN = { claude: 'Claude', gemini: 'Gemini', codex: 'Codex', opencode: 'OpenCode' };
const ACCT_PREF = { codex: 'xacct' };      // 对话偏好里指定模型池号的键（Gemini 只留单号，不能指定：#157）
// 平台表（后端 platforms_view）：能不能选、不能选的原因、模型列表、对话偏好里的模型键
function plats() {
  return state.platforms || [
    { id: 'claude', label: 'Claude', ok: true, model_key: 'model', default_key: 'models', models: state.model_choices || [] },
    { id: 'gemini', label: 'Gemini', ok: true, model_key: 'gmodel', default_key: 'gmodels', models: state.gemini_model_choices || [] }];
}
function platOf(id) { return plats().find((x) => x.id === id) || plats()[0]; }
function modelsFor(prov, aid) {
  const a = cliAccts(prov).find((x) => x.id === aid) || cliAccts(prov).find((x) => !aid && x.default);
  return (a && a.kind === 'api' && a.models && a.models.length) ? a.models : (platOf(prov).models || []);
}
function provOpts(cur, withDefault) {
  return plats().map((x) => `<option value="${x.id}" ${x.id === cur ? 'selected' : ''} ${x.ok || x.id === cur ? '' : 'disabled'} title="${esc(x.ok ? (x.version ? `${x.label} ${x.version}` : x.label) : x.reason)}">${esc(x.label)}${x.ok ? '' : '（不可用）'}</option>`).join('');
}
function chRole(ch) { return ch === MAIN ? 'assistant' : (seqOf(ch)[1] ? 'crew' : 'producer'); }
// 这个对话下次新会话用哪家
function chProvider(ch) { return effectiveExec(ch, chRole(ch)).cli; }
function shortModel(x) { return String(x || '').replace(/^claude-/, '').replace(/-(\d+)-(\d+)(?:-\d{8})?$/, ' $1.$2').replace(/^\w/, (c) => c.toUpperCase()); }
function sessCtl(ch, role, cam, buttons) {
  const p = (state.prefs || {})[ch] || {};
  const eff = effectiveExec(ch, role);
  const opts = (list, cur, now, none) => `<option value="">${now ? esc(now) : none}</option>`
    + (cur && !list.includes(cur) ? `<option value="${esc(cur)}" selected>${esc(cur)}</option>` : '')
    + list.map((x) => `<option value="${esc(x)}" ${x === cur ? 'selected' : ''}>${esc(x)}</option>`).join('');
  const ctx = cam && cam.alive && cam.context_pct != null ? cam.context_pct : null;
  const prov = eff.cli, pl = platOf(prov), camProv = cam && cam.alive ? cam.provider || 'claude' : '';
  const nowModel = cam && (cam.provider || 'claude') === prov ? cam.model : '';
  const ex = featExec(ch);
  const defPr = (ex && presetById(ex.preset)) || rolePreset(role);
  const showCustom = eff.customized || (!p.preset && ex && !ex.preset && !execSame(eff, roleDefaultExec(role)));
  const full = `${eff.preset_name && !showCustom ? `预设：${eff.preset_name} · ` : ''}${PROV_CN[prov] || prov} · ${eff.model || nowModel || '默认模型'} · 思考 ${eff.effort || (cam && cam.effort) || '默认'}`;
  return `<div class="sess-ctl">
    <div class="sess-pill" title="${esc(full)}">
      <label class="sel preset" title="模型池预设：选了自动填好其余几格；单独改任意一格后显示「自定义」">
        <select data-pref="preset" data-ch="${esc(ch)}">${presetSelectOpts(p.preset || '', defPr, showCustom)}</select></label>
      <span class="sp-sep">·</span>
      <label class="sel prov" title="用哪家 CLI（Claude / Gemini / Codex / OpenCode）。只对新会话（点「新会话」或交接后）生效，恢复会话沿用原来那家；灰掉的是现在不能用的（悬停看原因）${camProv ? `；现在的会话：${PROV_CN[camProv] || camProv}` : ''}">
        <select data-pref="provider" data-ch="${esc(ch)}">${provOpts(prov)}</select></label>
      ${camProv && camProv !== prov ? `<span class="sp-pend" title="现在的会话还是 ${PROV_CN[camProv]}，点「新会话」或交接后换成 ${PROV_CN[prov]}">待新会话</span>` : ''}
      <span class="sp-sep">·</span>
      <label class="sel" title="${esc(full)}
${esc(pl.label)} 模型${nowModel ? `（现在：${esc(nowModel)}）` : ''}。第一项 = CLI 默认。切换时会话在原窗格重开，对话不丢${prov === 'opencode' && !(pl.models || []).length ? '。还没有 OpenCode 模型列表：到「模型池」点刷新' : ''}">
        <select data-pref="${esc(pl.model_key)}" data-ch="${esc(ch)}">${opts(modelsFor(prov, eff.account), eff.model, prov === 'claude' ? shortModel(nowModel) : nowModel, '默认模型')}</select></label>
      <span class="sp-sep">·</span>
      <label class="sel eff" title="思考程度${cam && cam.effort ? `（现在：${esc(cam.effort)}）` : ''}。Codex / OpenCode 会换成这个模型支持的最接近一档。切换时会话在原窗格重开，对话不丢">
        <select data-pref="effort" data-ch="${esc(ch)}">${opts(state.effort_choices || [], eff.effort, cam && cam.effort, '默认思考')}</select></label>
      ${ACCT_PREF[prov] && cliAccts(prov).length ? `<span class="sp-sep">·</span>
      <label class="sel acct" title="用模型池里哪个号：第一项 = 自动（状态正常、额度剩得最多的）。只对新会话（新会话 / 交接）生效，会话中途不换号${cam && cam.alive && cam.account ? `；现在的会话用的是 ${esc(acctName(cliAccts().find((a) => a.id === cam.account)) || cam.account)}` : ''}">
        <select data-pref="${ACCT_PREF[prov]}" data-ch="${esc(ch)}">${acctOpts(prov, eff.account || '')}</select></label>` : ''}
      ${prov !== 'claude' ? '' : `<span class="sp-sep">·</span>
      <label class="sel" title="Claude Code 实验性 Code Mode（用 JavaScript 一次跑多个工具）。开了以后 Chrome 浏览器、computer use 用不了；只对新会话（新会话 / 交接）生效，恢复会话沿用原模式">
        <select data-pref="code" data-ch="${esc(ch)}">
          <option value="off" ${!eff.code ? 'selected' : ''}>Code 关</option><option value="on" ${eff.code ? 'selected' : ''}>Code 开</option></select></label>`}
      ${ctx != null ? `<span class="sp-ctx ${ctxLvl(ctx)}" title="上下文 ${pct(ctx)}（75% 会被要求交接）"><i style="--p:${Math.round(ctx * 100)}"></i>上下文 ${Math.round(ctx * 100)}%</span>` : ''}
    </div>
    <button class="btn ${detailOn ? 'on' : ''}" data-detail title="详细：像 Claude Code 终端一样显示工具参数、子代理、后台任务、待办、思考和状态栏">${icon('terminal', 15)}<span class="bt">${detailOn ? '详细' : '简洁'}</span></button>
    ${buttons ? `<button class="btn" data-sess="handoff" title="交接：让它整理交接文档（handoff.md），然后在原窗格换新会话接着做" ${cam && cam.alive ? '' : 'disabled'}>${icon('swap', 15)}<span class="bt">交接</span></button>
      <button class="btn" data-sess="new-session" title="新会话：选择预设并新开一个会话，不带上一段对话、不读交接">${icon('sparkle', 15)}<span class="bt">新会话</span></button>` : ''}</div>`;
}
function headStatus(ch, k, extra = '', detail = '') {
  const isLive = liveBusy(ch);
  const txt = detail || (isLive ? liveText(ch) : ST[k]);
  const tone = detailTone(k, isLive);
  return `<div class="s">${sdot(isLive ? liveKey(ch) : k)}<span class="st-t ${tone}" title="${esc(txt)}">${esc(txt)}</span>${extra}</div>`;
}
function renderProjectBoard(project) {
  let board = $('#projectBoard');
  if (!project) { board?.remove(); return; }
  if (!board) {
    board = document.createElement('details');
    board.id = 'projectBoard';
    $('#chatHead').after(board);
  }
  if (board.dataset.film !== project.id) board.open = false;
  board.dataset.film = project.id;
  const entries = project.board || [];
  const html = `<summary>协作板 <small>最近 ${entries.length} 条</small></summary><ul>${entries.length ? entries.slice().reverse().map(item =>
    `<li><b>${esc(item.author)}</b><time>${esc(item.ts.slice(5, 16).replace('T', ' '))}</time><p>${esc(item.text)}</p></li>`).join('') : '<li>暂无进展</li>'}</ul>`;
  if (board._html !== html) { board.innerHTML = html; board._html = html; }
}

function renderHead() {
  renderProjectBoard(isFilmCh(current) ? filmOf(current) : null);
  const real = $('#chatHead');
  // 内容没变不重画；正在选模型 / 思考程度时也不重画，免得下拉框被关掉
  const el = { set innerHTML(h) { if (real._html !== h && !(real.contains(document.activeElement) && document.activeElement.tagName === 'SELECT')) { real.innerHTML = h; real._html = h; } } };
  const input = $('#input');
  if (current === MAIN) {
    const a = state.assistant;
    const ak = needOf(inboxOf(null)) || camState(a, 'off');
    const aDet = formatStatusDetail(a?.detail || state.assistant_detail || '', a?.detail_since ?? state.assistant_detail_since, ak, MAIN);
    el.innerHTML = `${mashuAv('av')}
      <div class="meta"><div class="t">麻薯 · 主脑</div>
        ${headStatus(MAIN, ak, '<span class="sub">· 主对话 · 项目管理</span>', aDet)}</div>
      <div class="head-acts">${sessCtl(MAIN, 'assistant', a, true)}</div>`;
    input.placeholder = '和麻薯说点什么… Enter 发送，Shift+Enter 换行';
    return;
  }
  const [f, s] = seqOf(current);
  if (!f) { el.innerHTML = '<div class="meta"><div class="t">频道不存在</div></div>'; return; }
  if (!s) {
    const p = f.producer;
    const bk = brainState(f);
    const fDet = formatStatusDetail(f.detail || p?.detail || '', f.detail_since ?? p?.detail_since, bk, f.id, f.waits);
    el.innerHTML = `${mashuAv('av')}
      <div class="meta"><div class="t"><a class="crumb" data-go="${MAIN}">麻薯</a><span class="crumb-sep">${icon('chev-r', 14)}</span>${esc(f.title)}</div>
        ${headStatus(f.id, bk, `<span class="sub">· 子脑</span><span class="path">${esc(f.path)}</span>`, fDet)}</div>
      <div class="seg proj-mode" role="tablist"><button class="${projMode[f.id] === 'wf' ? '' : 'active'}" data-projmode="chat">${icon('chat', 14)}对话</button><button class="${projMode[f.id] === 'wf' ? 'active' : ''}" data-projmode="wf">${icon('board', 14)}工作流</button></div>
      <div class="head-acts">${sessCtl(f.id, 'producer', p, true)}
      ${p ? `<button class="btn" data-cam="producer-off" data-film="${esc(f.id)}" title="关掉子脑会话，有事会自动叫醒">${icon('moon', 15)}<span class="bt">休息</span></button>` :
        `<button class="btn" data-cam="producer-call" data-film="${esc(f.id)}" title="叫醒子脑">${icon('sun', 15)}<span class="bt">叫醒</span></button>`}
      <button class="btn primary" data-pitch="${esc(f.id)}" title="给这个项目开新功能">${icon('plus', 15)}<span class="bt">新功能</span></button></div>`;
    input.placeholder = `和 ${f.title} 的子脑说… Enter 发送`;
    return;
  }
  const c = s.crew;
  const ref = `${f.id}/${s.key}`;
  const who = (c || s.last)?.name || '';
  const k = seqStateKey(f, s);
  const sDet = formatStatusDetail(s.detail || '', s.detail_since, k, ref, s.waits, s.waited_by);
  el.innerHTML = `<div class="av seq-av" style="background:hsl(${hue(s.key)} 42% 58%)">${esc(who ? [...who][0] : String(s.num ?? '·'))}</div>
    <div class="meta"><div class="t">${esc(seqLabel(f, s))} · ${esc(s.title)}</div>
      ${headStatus(ref, k, `${s.stages.length > 1 && s.stage ? `<span class="sub">· 阶段 ${s.stage.id} ${esc(stageName(s.stage.id, s.stage_defs))} · ${esc(s.stage.status)}</span>${behaviorLinks(((s.stage_defs || [])[s.stage.id - 1] || {}).behavior, f.id)}` : ''}
      ${segBar(s)}
      ${s.stages.length > 1 && s.playbook && s.playbook.name ? `<span class="tag">工作流 ${esc(s.playbook.name)} v${esc(s.playbook.version)}</span>` : ''}
      ${c ? `<span class="sub">${esc(roleName(c.crew_role))}「${esc(c.name)}」</span>` : ''}`, sDet)}</div>
    <div class="head-acts">${sessCtl(ref, 'crew', c, false)}
    ${k !== 'archived' ? `<button class="btn ${k === 'done' ? 'primary' : ''}" data-cam="archive" data-ref="${esc(ref)}" title="归档：左栏收进「已归档」，之后有新进展会自动回来">${icon('archive', 14)}<span class="bt">归档</span></button>` : ''}
    ${k === 'archived' ? `<button class="btn" data-cam="unarchive" data-ref="${esc(ref)}" title="移回左栏列表">${icon('undo', 14)}<span class="bt">取消归档</span></button>` : ''}
    <button class="btn" data-cam="action" data-ref="${esc(ref)}" title="启动执行者">${icon('play', 14)}<span class="bt">启动</span></button>
    <button class="btn danger" data-cam="cut" data-ref="${esc(ref)}" title="结束这个执行者会话">${icon('stop', 14)}<span class="bt">停止</span></button>
    <button class="btn" data-cam="retake" data-ref="${esc(ref)}" title="换一个新执行者重来">${icon('restart', 14)}<span class="bt">重启</span></button></div>`;
  input.placeholder = `直接对这个功能的执行者说…（没有在运行的会自动启动）`;
}

// ---------- 消息 ----------
function whoOf(m) {
  if (m.sender === 'executive') return ['我', ''];
  if (m.sender === 'assistant') return ['麻薯', '主脑'];
  if (m.sender === 'producer') { const f = filmOf(m.film_id); return ['麻薯', `${f ? f.title : m.film_id} 子脑`]; }
  if (m.sender === 'crew') {
    const c = state.cameras.find((x) => x.id === m.camera_id);
    return [`「${m.camera_name || ''}」`, roleName(c?.crew_role) || '执行者'];
  }
  return ['调度器', '系统'];
}
function avOf(m) {
  if (m.sender === 'assistant' || m.sender === 'producer') return mashuAv();
  if (m.sender === 'system') return letterAv('');
  return letterAv(m.camera_name || m.sender);
}
function cardAv(r) {
  if (r.source_role === 'assistant') return mashuAv('av sm');
  if (!r.source_name) return letterAv('', 'av sm');
  return letterAv(r.source_name, 'av sm');
}
function clock(t) { return t ? new Date(t * 1000).toTimeString().slice(0, 5) : ''; }
function waited(r) {
  const min = Math.max(0, Math.round((Date.now() / 1000 - (r.holder_since || r.created_at)) / 60));
  return min < 1 ? '刚刚' : `已等 ${min} 分钟`;
}
function holderName(r) {
  if (r.holder === 'producer') return '麻薯';
  return LEVEL[r.holder] || r.holder;
}
function closedLine(r) {
  if (r.status === 'superseded' || r.superseded_by) {
    const why = r.superseded_by ? `已被 #${r.superseded_by} 取代` : (r.stale_reason || '已被取代');
    return `<div class="done-line muted">${icon('swap', 13)}${esc(why)}${r.closed_at ? ` · ${clock(r.closed_at)}` : ''}</div>`;
  }
  if (r.status === 'muted') return `<div class="done-line muted">${icon('moon', 13)}已静默，不处理${r.stale_reason ? `（${esc(r.stale_reason)}）` : ''} · ${clock(r.closed_at)}</div>`;
  if (r.status === 'closed' && !NOTIFY.includes(r.kind)) {
    const by = LEVEL[r.answered_by] || r.answered_by || '';
    return `<div class="done-line muted">${icon('check', 13)}${by ? esc(by) : ''}已结案 · ${clock(r.closed_at)}${r.answer ? `：${esc(r.answer)}` : ''}</div>`;
  }
  if (NOTIFY.includes(r.kind)) return `<div class="done-line muted">${icon('check', 13)}已送达主对话</div>`;
  const by = LEVEL[r.answered_by] || r.answered_by;
  const ok = (r.options || [])[0] === r.answer;
  if (r.gate) {
    if (ok) return `<div class="done-line ok">${icon('pen', 13)}${r.answered_by === 'executive' ? '您' : esc(by)}已审批 · ${clock(r.closed_at)}${r.approval ? ` · 有效至 ${clock(r.approval.expires_at)}` : ''}${r.approval && r.approval.payload ? ` · 绑定 @${esc(r.approval.payload.slice(0, 8))}` : ''}</div>`;
    return `<div class="done-line bad">${icon('x', 13)}${esc(by)}驳回 · ${esc(r.answer)}</div>`;
  }
  if (r.kind === 'confirm') {
    return ok ? `<div class="done-line ok">${icon('check', 13)}${esc(by)}批准进入 ${esc(r.stage_label || '')} · ${clock(r.closed_at)}</div>`
      : `<div class="done-line bad">${icon('x', 13)}${esc(by)}没有批准 · ${esc(r.answer)}</div>`;
  }
  return `<div class="done-line ok">${icon('check', 13)}${esc(by)}批复 · ${clock(r.closed_at)}：${esc(r.answer)}</div>`;
}
// 开工审批卡：第一格选预设，后面是 CLI / 模型 / 思考 / 账号 / Code Mode；选预设自动填好，改一格变「自定义」
function kickModelOpts(prov, cur, aid) {
  const list = modelsFor(prov, aid);
  return '<option value="">默认模型</option>' + (cur && !list.includes(cur) ? `<option value="${esc(cur)}" selected>${esc(cur)}</option>` : '')
    + list.map((x) => `<option value="${esc(x)}" ${x === cur ? 'selected' : ''}>${esc(x)}</option>`).join('');
}
const kickSel = {};       // 卡片上改过但还没批的执行配置：重画消息时保留
function execMatchesPreset(ex, pr) {
  if (!pr) return false;
  return execSame({ cli: ex.cli || 'claude', model: ex.model || '', effort: ex.effort || '',
    account: ex.account || '', code: !!ex.code_mode }, { cli: pr.cli || 'claude', model: pr.model || '',
    effort: pr.effort || '', account: pr.account || '', code: !!pr.code_mode });
}
function kickExecHtml(r) {
  const defPr = rolePreset('crew');
  const defEx = roleDefaultExec('crew');
  const raw = kickSel[r.id] || (r.meta || {}).exec || { preset: defPr ? defPr.id : '', cli: defEx.cli, model: defEx.model, effort: defEx.effort, account: defEx.account, code_mode: defEx.code };
  const ex = { preset: raw.preset || '', cli: raw.cli || 'claude', model: raw.model || '', effort: raw.effort || '',
    account: raw.account || '', code_mode: !!raw.code_mode };
  const matchedPr = ex.preset ? presetById(ex.preset) : null;
  const isDefault = !ex.preset && execSame({ ...ex, code: ex.code_mode }, defEx);
  const isCustom = !isDefault && !(matchedPr && execMatchesPreset(ex, matchedPr));
  const selPresetId = isDefault ? '' : (matchedPr && !isCustom ? matchedPr.id : '');
  return `<div class="kick-exec" data-kick="${r.id}" title="批准后执行者按这里的配置启动；第一格选预设，改任意一格后显示自定义">
    <span class="ke-l">${icon('terminal', 13)}执行者</span>
    <select data-kick-preset="${r.id}" title="模型池预设">${presetSelectOpts(selPresetId, defPr, isCustom)}</select>
    <select data-kick-cli="${r.id}">${provOpts(ex.cli)}</select>
    <select data-kick-model="${r.id}">${kickModelOpts(ex.cli, ex.model, ex.account)}</select>
    <select data-kick-effort="${r.id}"><option value="">默认思考</option>${(state.effort_choices || []).map((x) => `<option value="${esc(x)}" ${x === ex.effort ? 'selected' : ''}>思考 ${esc(x)}</option>`).join('')}</select>
    <select data-kick-acct="${r.id}" title="模型池里的号（自动 = 开工时挑额度剩得最多的）" ${ACCT_PREF[ex.cli] && cliAccts(ex.cli).length ? '' : 'hidden'}>${acctOpts(ex.cli, ex.account)}</select>
    <select data-kick-code="${r.id}" title="Claude Code 实验性 Code Mode" ${ex.cli === 'claude' ? '' : 'hidden'}><option value="off" ${!ex.code_mode ? 'selected' : ''}>Code 关</option><option value="on" ${ex.code_mode ? 'selected' : ''}>Code 开</option></select>
  </div>`;
}
function kickExecOf(rid, el) {
  const box = (el && el.querySelector(`[data-kick="${rid}"]`)) || document.querySelector(`[data-kick="${rid}"]`);
  if (!box) return null;
  const prEl = box.querySelector('[data-kick-preset]');
  const cli = box.querySelector('[data-kick-cli]').value;
  const ac = box.querySelector('[data-kick-acct]');
  const cd = box.querySelector('[data-kick-code]');
  const pval = prEl ? prEl.value : '';
  const defPr = rolePreset('crew');
  const out = { cli, model: box.querySelector('[data-kick-model]').value, effort: box.querySelector('[data-kick-effort]').value,
    account: ac && !ac.hidden ? ac.value : '' };
  if (cli === 'claude' && cd && !cd.hidden && cd.value === 'on') out.code_mode = true;
  if (pval && pval !== '__custom__') out.preset = pval;
  else if (pval === '' && defPr && execMatchesPreset(out, defPr)) out.preset = defPr.id;
  return out;
}
document.addEventListener('change', (ev) => {
  const s = ev.target.closest('select[data-kick-preset],select[data-kick-cli],select[data-kick-model],select[data-kick-effort],select[data-kick-acct],select[data-kick-code]');
  if (!s) return;
  const box = s.closest('[data-kick]');
  const prEl = box.querySelector('[data-kick-preset]');
  const cliEl = box.querySelector('[data-kick-cli]');
  const mEl = box.querySelector('[data-kick-model]');
  const eEl = box.querySelector('[data-kick-effort]');
  const aEl = box.querySelector('[data-kick-acct]');
  const cEl = box.querySelector('[data-kick-code]');
  if (s.dataset.kickPreset !== undefined) {
    const pr = s.value ? presetById(s.value) : null;
    const target = pr ? { cli: pr.cli || 'claude', model: pr.model || '', effort: pr.effort || '', account: pr.account || '', code: !!pr.code_mode } : roleDefaultExec('crew');
    if (cliEl) cliEl.value = target.cli;
    if (aEl) { aEl.innerHTML = acctOpts(target.cli, target.account); aEl.hidden = !(ACCT_PREF[target.cli] && cliAccts(target.cli).length); }
    if (mEl) mEl.innerHTML = kickModelOpts(target.cli, target.model, target.account);
    if (eEl) eEl.value = target.effort;
    if (cEl) { cEl.value = target.code ? 'on' : 'off'; cEl.hidden = target.cli !== 'claude'; }
    // 移除临时「自定义」项
    prEl?.querySelector('option[value="__custom__"]')?.remove();
  } else {
    if (s.dataset.kickCli) {
      if (mEl) mEl.innerHTML = kickModelOpts(s.value, '');
      if (aEl) { aEl.innerHTML = acctOpts(s.value, ''); aEl.hidden = !(ACCT_PREF[s.value] && cliAccts(s.value).length); }
      if (cEl) { cEl.value = 'off'; cEl.hidden = s.value !== 'claude'; }
    }
    if (s.dataset.kickAcct) {
      const prov = cliEl.value;
      if (mEl) mEl.innerHTML = kickModelOpts(prov, modelsFor(prov, s.value).includes(mEl.value) ? mEl.value : '', s.value);
    }
    // 任意一格改了，预设格切到「自定义」
    if (prEl) {
      let opt = prEl.querySelector('option[value="__custom__"]');
      if (!opt) {
        opt = document.createElement('option');
        opt.value = '__custom__'; opt.textContent = '自定义'; opt.disabled = true;
        prEl.insertBefore(opt, prEl.children[1] || null);
      }
      prEl.value = '__custom__';
    }
  }
  kickSel[box.dataset.kick] = kickExecOf(box.dataset.kick, box.parentElement);
});
function muteBtn(r) { return `<button class="btn ghost mute" data-mute="${r.id}" title="静默：不处理这件事、不叫醒任何人，维持现状">${icon('moon', 13)}静默</button>`; }
async function muteReport(rid) {
  try { await api('/api/mute', { report_id: Number(rid) }); expanded.delete(Number(rid)); await refresh(); if (view === 'mail') await loadMail(); toast(`#${rid} 已静默`); }
  catch (e) { toast(e.message); }
}
function cardActions(r) {
  const opts = r.options || [];
  const m = r.meta || {};
  if (m.external && m.web_only) {   // 外部卡片要在外部网页完成（例如带 2FA 的发布批准）：只给链接
    return `<div class="acts">${m.url ? `<a class="btn primary" href="${esc(m.url)}" target="_blank" rel="noopener">${icon('arrow-r', 14)}去${esc(m.source || '外部')}网页处理</a>` : ''}
      ${muteBtn(r)}<span class="hint-inline">这件只能在${esc(m.source || '外部')}网页完成，麻薯不代答</span></div>`;
  }
  const twoWay = (r.gate || r.kind === 'confirm') && opts.length >= 2;
  if (twoWay) {
    const yes = `${icon(r.gate ? 'pen' : 'check', 14)}${esc(opts[0])}`;
    if (r.gate === 'irreversible') {
      return `<div class="acts danger-zone"><input data-confirm-input="${r.id}" placeholder="不可逆：输入项目 id「${esc(r.film_id || '')}」确认">
        <button class="btn danger" data-answer="${r.id}" data-text="${esc(opts[0])}" data-need="${esc(r.film_id || '')}">${yes}</button>
        <button class="btn" data-reject="${r.id}" data-label="${esc(opts[1])}">${esc(opts[1])}…</button></div>
        <div class="acts reject" id="rej-${r.id}" hidden><input data-reject-input="${r.id}" placeholder="理由（可选，会转给提出的人）">
        <button class="btn danger" data-reject-go="${r.id}" data-label="${esc(opts[1])}">确认${esc(opts[1])}</button></div>`;
    }
    return `${r.gate === 'kickoff' ? kickExecHtml(r) : ''}<div class="acts">
      <button class="btn primary" data-answer="${r.id}" data-text="${esc(opts[0])}">${yes}</button>
      <button class="btn danger" data-reject="${r.id}" data-label="${esc(opts[1])}">${esc(opts[1])}…</button>
      ${muteBtn(r)}<span class="hint-inline">${r.gate ? `审批后 ${esc(String(state.approval_ttl_hours || 2))} 小时内有效` : ''}</span></div>
      <div class="acts reject" id="rej-${r.id}" hidden>
        <input data-reject-input="${r.id}" placeholder="理由（可选，会转给提出的人）">
        <button class="btn danger" data-reject-go="${r.id}" data-label="${esc(opts[1])}">确认${esc(opts[1])}</button></div>`;
  }
  const btns = opts.map((o) => `<button class="btn opt ${m.recommend === o ? 'rec' : ''}" data-answer="${r.id}" data-text="${esc(o)}">${m.recommend === o ? icon('star', 13) : ''}${esc(o)}</button>`).join('');
  return `<div class="acts">${btns}<input placeholder="${btns ? '或者直接写几句…' : '写下您的回复…'}" data-reply-input="${r.id}">
    <button class="btn primary" data-reply="${r.id}">回复</button>${muteBtn(r)}</div>`;
}
// 卡片 / 折叠块的展开状态：重画消息时保留
const openDetails = new Set();
document.addEventListener('toggle', (ev) => {
  const k = ev.target.dataset?.dkey;
  if (k) { if (ev.target.open) openDetails.add(k); else openDetails.delete(k); }
}, true);
function detailsOpen(k) { return openDetails.has(k) ? 'open' : ''; }
function goLink(attrs, label = '查看') { return `<a class="card-go" ${attrs}>${label}${icon('arrow-r', 13)}</a>`; }
function renderCard(m) {
  const r = reports[m.report_id];
  const primary = m.kind === 'card';
  const domId = primary ? `card-${m.report_id}` : `raised-${m.report_id}`;
  if (!r) return `<div class="card" id="${domId}"><div class="card-main"><div class="ct muted">#${m.report_id} 加载中…</div></div></div>`;
  const notify = NOTIFY.includes(r.kind);
  const open = r.status === 'open';
  const superseded = r.status === 'superseded' || !!r.superseded_by;
  const staleLabel = superseded ? (r.superseded_by ? `已被 #${r.superseded_by} 取代` : (r.stale_reason || '已被取代')) : (r.stale_reason ? `已过时：${r.stale_reason}` : '');
  const isStale = superseded || (open && !!r.stale_reason);
  const mtType = r.msg_type || (r.gate || r.kind === 'confirm' ? 'approve' : (notify ? 'fyi' : 'ask'));
  const mtLabel = r.msg_type_label || MSG_TYPE[mtType] || mtType;
  const mine = open && !notify && !isStale && r.holder === 'executive';
  const canAct = open && !notify && (mine || expanded.has(r.id) || (r.holder === 'executive' && isStale));
  const seqCh = r.film_id ? (r.seq_key ? `${r.film_id}/${r.seq_key}` : r.film_id) : '';
  const cardMsg = messages.find((x) => x.kind === 'card' && x.report_id === r.id);
  const kindCls = r.gate ? 'gate' : (r.kind === 'confirm' ? 'confirm' : (notify ? 'note' : 'ask'));
  let link = '';
  if (primary && r.seq_key) link = goLink(`data-go="${esc(seqCh)}" data-focus="raised-${r.id}" title="去这个功能"`);
  else if (!primary && cardMsg) link = goLink(`data-go="${esc(cardMsg.channel)}" data-focus="card-${r.id}" title="${cardMsg.channel === MAIN ? '主对话' : '子脑对话'}里的卡片"`);
  const stTag = superseded
    ? `<span class="tag k-stale">${esc(staleLabel)}</span>`
    : (r.stale_reason && open ? `<span class="tag k-stale">已过时</span>` : (notify ? '<span class="tag k-note">通知</span>' : (open ? '' : '<span class="tag k-done">已回复</span>')));
  const typeTag = `<span class="tag mt-${esc(mtType)}">${esc(mtLabel)}</span>`;
  const kindText = KIND[r.kind] || r.kind;
  const kindTag = kindText !== mtLabel ? `<span class="tag k-${kindCls}">${esc(kindText)}</span>` : '';
  const head = `<div class="ch">${stTag}${typeTag}${kindTag}
      <span class="origin">${esc(r.origin)}</span><span class="rid">#${r.id}</span><span class="time">${timeText(m.ts)}</span></div>`;
  // 通知类（阶段完成、通报……）：一行标题，正文折叠
  if (notify) {
    const title = r.summary || firstLine(r.text, 80);
    const more = String(r.text || '').trim() !== String(title).trim();
    return `<div class="card compact ${kindCls} ${open ? '' : 'closed'}" id="${domId}">${cardAv(r)}
      <div class="card-main">${head}<div class="ct-title">${esc(title)}</div>
        ${more ? `<details class="ct-more" data-dkey="c${r.id}" ${detailsOpen(`c${r.id}`)}><summary>展开全文</summary><div class="ct">${md(r.text)}</div></details>` : ''}</div>${link}</div>`;
  }
  const chips = [];
  if (r.gate) chips.push(`<span class="chip warn">${icon('pen', 12)}${esc(GATE[r.gate] || r.gate_label || r.gate)}</span>`);
  if (r.kind === 'confirm' && r.stage_label) chips.push(`<span class="chip">${icon('arrow-r', 12)}申请进入 ${esc(r.stage_label)}</span>`);
  if (r.seq_key && primary) chips.push(`<button class="chip link" data-go="${esc(seqCh)}">${icon('clipboard', 12)}功能状态</button>`);
  for (const d of r.docs || []) chips.push(`<button class="chip link" data-doc="${esc(seqCh)}" data-path="${esc(d.path)}">${icon('doc', 12)}${esc(d.label)}</button>`);
  const mt = r.meta || {};
  if (mt.external) chips.push(mt.url ? `<a class="chip link" href="${esc(mt.url)}" target="_blank" rel="noopener">${icon('arrow-ur', 12)}${esc(mt.source || '外部')} ${esc(mt.ref || '')}</a>`
    : `<span class="chip">${esc(mt.source || '外部')} ${esc(mt.ref || '')}</span>`);
  if (mt.reversible === false) chips.push('<span class="chip bad">不可逆</span>');
  if (mt.impact) chips.push(`<span class="chip">影响：${esc(mt.impact)}</span>`);
  for (const a of mt.anomalies || []) chips.push(`<span class="chip bad">${icon('alert', 12)}${esc(a)}</span>`);
  if (mt.head && !mt.external) chips.push(`<span class="chip mono" title="审批绑定这个提交，代码变了要重新审批">@ ${esc(mt.head.slice(0, 8))}</span>`);
  if (mt.diff && !mt.external) chips.push(`<span class="chip mono">${mt.diff.commits} 提交 · ${mt.diff.files} 文件 · +${mt.diff.insertions} −${mt.diff.deletions}</span>`);
  const acBox = mt.ac ? `<details class="ac-box" data-dkey="ac${r.id}" ${detailsOpen(`ac${r.id}`)}><summary>验收标准 ${mt.ac.passed}/${mt.ac.total} 通过</summary>${mt.ac.items.map((i) =>
    `<div class="ac-row ${i.passes ? 'ok' : 'no'}">${icon(i.passes ? 'check' : 'x', 12)}<b>${esc(i.id)}</b> ${esc(i.text)}</div>`).join('')}</details>` : '';
  const recLine = mt.recommend ? `<div class="rec-line">${icon('star', 13)}推荐：<b>${esc(mt.recommend)}</b>${mt.why ? ` —— ${esc(mt.why)}` : ''}${mt.recommend_by ? `<span class="k">（${esc(mt.recommend_by)}）</span>` : ''}</div>` : '';
  const route = (r.route || []).filter((x) => x.action === 'pass').map((x) =>
    `<div class="op"><b>${esc(x.who)}</b>${x.auto ? '<span class="tag">超时上交</span>' : ''}<span>${esc(x.note || '（无意见）')}</span></div>`).join('');
  let foot = '';
  if (canAct) foot = cardActions(r);
  else if (open && !notify) foot = `<div class="done-line wait">${icon('clock', 13)}在${esc(holderName(r))}手上 · ${waited(r)}
      <button class="btn small" data-expand="${r.id}">直接批复</button></div>`;
  else if (!open) foot = closedLine(r);
  if (isStale) {
    const sumTitle = r.summary || firstLine(r.text, 70);
    const tailWhy = superseded ? '' : (r.stale_reason || '');
    const metaHead = `<div class="ch"><span class="origin">${esc(r.origin)}</span><span class="time">${timeText(m.ts)}</span></div>`;
    return `<div class="card stale ${kindCls} ${open ? '' : 'closed'}" id="${domId}">${cardAv(r)}
      <div class="card-main">
        <details class="card-stale-fold" data-dkey="st${r.id}" ${detailsOpen(`st${r.id}`)}>
          <summary>${stTag}${typeTag}<span class="rid">#${r.id}</span><span class="stale-sum">${esc(sumTitle)}</span>${tailWhy ? `<span class="stale-why">${esc(tailWhy)}</span>` : ''}</summary>
          <div class="stale-body">${metaHead}<div class="ct">${md(r.text)}</div>
          ${chips.length ? `<div class="chips">${chips.join('')}</div>` : ''}
          ${acBox}${recLine}${route ? `<div class="route"><div class="route-h">各级意见</div>${route}</div>` : ''}${foot}</div>
        </details>
      </div>${link}</div>`;
  }
  return `<div class="card ${kindCls} ${mine ? 'need' : ''} ${open ? '' : 'closed'}" id="${domId}">${cardAv(r)}
    <div class="card-main">${head}
    <div class="ct">${md(r.text)}</div>
    ${chips.length ? `<div class="chips">${chips.join('')}</div>` : ''}
    ${acBox}${recLine}${route ? `<div class="route"><div class="route-h">各级意见</div>${route}</div>` : ''}${foot}
    ${open && canAct && mt.if_no_reply ? `<div class="hint-inline" style="margin-top:6px">不回复：${esc(mt.if_no_reply)}</div>` : ''}</div>${link}</div>`;
}
function whoLine(name, role, ts, extra = '') {
  return `<div class="who"><b>${esc(name)}</b>${role ? `<span class="sep">·</span><span class="role">${esc(role)}</span>` : ''}${ts ? `<span class="sep">·</span><span class="ts">${timeText(ts)}</span>` : ''}${extra}</div>`;
}
function renderReportMsg(m) {
  const r = reports[m.report_id];
  const to = m.target === 'assistant' ? '麻薯 · 主脑' : '麻薯';
  const st = !r ? '' : (r.status !== 'open' ? `<span class="tag">已结案</span>` : `<span class="tag k-ask">在${esc(LEVEL[r.holder])}手上</span>`);
  return `<div class="msg small">${letterAv('', 'av sm')}<div class="body">
    ${whoLine('调度器', '', m.ts, `<span class="tag">上报 #${m.report_id} → ${to}</span>${st}`)}
    <details class="bubble" data-dkey="r${m.id}" ${detailsOpen(`r${m.id}`)}><summary>${esc((r && r.summary) || m.text.split('\n')[1] || '')}</summary>${md(m.text)}</details></div></div>`;
}
function renderMsg(m) {
  switch (m.kind) {
    case 'notice': return `<div class="notice"><span>${md(m.text)}</span></div>`;
    case 'handover': return `<div class="handover">${esc(m.text)}</div>`;
    case 'event': return renderEvent(m);
    case 'card': case 'raised': return renderCard(m);
    case 'brief': return `<div class="card brief"><div class="card-main"><div class="ch"><span class="tag gold">需求</span></div><div class="ct">${md(m.text)}</div></div></div>`;
    case 'report': return renderReportMsg(m);
    case 'digest': return `<div class="msg small">${letterAv('', 'av sm')}<div class="body">${whoLine('调度器', '', m.ts, '<span class="tag gold">简报数据 → 主脑</span>')}
      <details class="bubble" data-dkey="d${m.id}" ${detailsOpen(`d${m.id}`)}><summary>麻薯据此整理简报（点开看原始数据）</summary>${md(m.text)}</details></div></div>`;
    default: break;
  }
  if (m.sender === 'executive') {
    const to = m.target === MAIN || m.target.startsWith('producer:') ? '' : `→ 执行者`;
    return `<div class="msg me"><div class="body"><div class="who">${copyBtn(m)}${to ? `<span class="tag">${to}</span>` : ''}<b>我</b><span class="sep">·</span><span class="ts">${timeText(m.ts)}</span></div>
      <div class="bubble">${md(m.text)}</div>${galleryHtml(m.text)}</div></div>`;
  }
  const [name, role] = whoOf(m);
  const tag = { memo: '主脑指示', note: '条子', answer: '批复', forward: '转呈主脑' }[m.kind];
  let text = m.text;
  let quick = '';
  // 选项行不一定在最后（比如联网搜索后会在末尾附 Sources），取最后一个选项行，按钮放在气泡下面
  const qm = (m.sender === 'assistant' || m.sender === 'producer') && m.kind === 'reply'
    ? [...text.matchAll(/^[ \t]*(?:选项|快捷回复)[:：][ \t]*(.*\S.*)$/gm)].pop() : null;
  if (qm) {
    text = (text.slice(0, qm.index).replace(/\s+$/, '') + '\n\n' + text.slice(qm.index + qm[0].length).replace(/^\s+/, '')).trim();
    const live = !messages.some((x) => x.id > m.id && x.channel === m.channel && x.sender === 'executive');
    const opts = qm[1].split(/\s*[|｜]\s*/).filter(Boolean).slice(0, 6);
    quick = `<div class="quick">${opts.map((o) => `<button class="btn opt" ${live ? '' : 'disabled'} data-quick="${esc(o)}">${esc(o)}</button>`).join('')}</div>`;
  }
  return `<div class="msg ${m.kind === 'forward' ? 'small' : ''} ${m.sender}">${avOf(m)}<div class="body">
    ${whoLine(name, role, m.ts, `${tag ? `<span class="tag gold">${tag}</span>` : ''}${copyBtn(m)}`)}
    <div class="bubble">${md(text)}</div>${galleryHtml(text)}${quick}</div></div>`;
}
function copyBtn(m) { return `<button class="msg-copy" data-copymsg="${m.id}" title="复制这条">${icon('copy', 12)}复制</button>`; }
function renderEvent(m) {
  const i = m.text.indexOf('|');
  const type = i > 0 ? m.text.slice(0, i) : 'info';
  const text = i > 0 ? m.text.slice(i + 1) : m.text;
  const f = filmOf(m.film_id);
  const s = f && m.seq_key ? f.sequences.find((x) => x.key === m.seq_key) : null;
  const ch = f ? (s ? `${f.id}/${s.key}` : f.id) : '';
  const [ic, label] = { stage: ['check-c', '阶段完成'], done: ['flag', '已完成'], warn: ['alert', '注意'] }[type] || ['bell', '动态'];
  return `<div class="card compact event ev-${esc(type)}"><div class="av ev-av">${icon(ic, 17)}</div>
    <div class="card-main"><div class="ch"><span class="tag k-ev">${label}</span><span class="origin">${f ? `${esc(f.title)}${s ? ` #${esc(s.num ?? '')} ${esc(s.title)}` : ''}` : ''}</span><span class="time">${timeText(m.ts)}</span></div>
      <div class="ct-title">${esc(text)}</div></div>${ch ? goLink(`data-go="${esc(ch)}"`) : ''}</div>`;
}
function renderPendingBar() {
  const bar = $('#pendingBar');
  let list = state.inbox || [];
  if (current !== MAIN) list = list.filter((r) => current === r.film_id || current === `${r.film_id}/${r.seq_key}`);
  if (!list.length) { bar.hidden = true; return; }
  bar.hidden = false;
  const inSeq = current.includes('/');
  const focusOf = (r) => (inSeq ? `raised-${r.id}` : `card-${r.id}`);
  const goOf = (r) => (inSeq ? current : (r.film_id || MAIN));
  bar.innerHTML = `<span class="pb-title">${icon('inbox', 15)}待您处理 <b>${list.length}</b> 件</span>
    <div class="pb-list">${list.slice(0, 5).map((r) => `<button class="pb-item ${r.gate ? 'gate' : ''}" data-go="${esc(goOf(r))}" data-focus="${focusOf(r)}">${current === MAIN && r.film_id ? `<span class="pb-film">${esc((filmOf(r.film_id) || {}).title || r.film_id)}</span>` : ''}
      <span class="tag k-${r.gate ? 'gate' : (r.kind === 'confirm' ? 'confirm' : 'ask')}">${esc(KIND[r.kind] || r.kind)}</span>${esc(r.summary || '')}</button>`).join('')}</div>
    <button class="btn small" data-next="0">逐件处理${icon('arrow-d', 13)}</button>`;
}
function renderMessages(stick) {
  const box = $('#messages');
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 140;
  const list = channelMessages(current);
  if (!list.length) {
    box.innerHTML = current === MAIN
      ? '<div class="empty">这里是主对话，麻薯在这里管项目。<br>麻薯主导所有项目；各项目的阶段完成、会话异常、需要你帮忙的事都会出现在这里。<br>跟麻薯说说您想做什么，比如“把 ~/workspace/xxx 登记成项目，加一个导出功能”。</div>'
      : '<div class="empty">还没有消息</div>';
  } else {
    const html = list.map(renderMsg).join('');
    if (html === box._html && !stick) return renderLive();   // 没变化就不动 DOM，免得打断输入
    // 保留卡片里正在写的内容、展开的理由框和焦点
    const keep = {};
    box.querySelectorAll('[data-reply-input],[data-reject-input]').forEach((i) => { if (i.value) keep[i.outerHTML.match(/data-(reply|reject)-input="\d+"/)[0]] = i.value; });
    const opened = [...box.querySelectorAll('.acts.reject:not([hidden])')].map((x) => x.id);
    const act = document.activeElement;
    const actKey = act && box.contains(act) ? (act.outerHTML.match(/data-(reply|reject)-input="\d+"/) || [])[0] : null;
    const caret = actKey ? act.selectionStart : 0;
    const top = box.scrollTop;
    const prevMax = box._ch === current ? box._max || 0 : Infinity;
    box.innerHTML = html;
    box._html = html;
    list.forEach((m, i) => { if (box.children[i] && !box.children[i].id) box.children[i].id = `m-${m.id}`; });   // 红点跳转定位用
    list.forEach((m, i) => { if (m.id > prevMax) box.children[i]?.classList.add('enter'); });
    box._ch = current; box._max = list[list.length - 1].id;
    for (const [k, v] of Object.entries(keep)) { const i = box.querySelector(`[${k}]`); if (i) i.value = v; }
    for (const id of opened) { const x = document.getElementById(id); if (x) x.hidden = false; }
    if (actKey) { const i = box.querySelector(`[${actKey}]`); if (i) { i.focus({ preventScroll: true }); i.setSelectionRange(caret, caret); } }
    if (!(stick || nearBottom)) box.scrollTop = top;
  }
  renderLive();
  if (stick || nearBottom) box.scrollTop = box.scrollHeight;
}

// 这一轮用过的工具：live 只推当前工具，网页自己记下切换过的，画成列表（前一个算做完）
const liveTools = {};   // 频道 → [{name, done}]
function trackLive() {
  for (const ch of Object.keys(liveTools)) if (!liveBusy(ch)) delete liveTools[ch];
  for (const [ch, l] of Object.entries(live)) {
    if (l.waiting) continue;
    const list = liveTools[ch] || (liveTools[ch] = []);
    const last = list[list.length - 1];
    const t = toolName(l.tool);
    if (t && (!last || last.done || last.name !== t)) { if (last) last.done = true; list.push({ name: t, done: false }); }
    else if (!t && last && !last.done) last.done = true;
  }
}
function liveHtml(l) {
  if (!l) return '';
  const m = { sender: l.role, film_id: l.film_id, camera_name: l.name };
  const [name] = l.role === 'crew' ? [`「${l.name || ''}」`] : whoOf(m);
  const st = liveText(current);
  const mine = channelMessages(current).filter((x) => x.sender === 'executive');
  const got = mine.length ? firstLine(mine[mine.length - 1].text, 80) : '';
  const hasServerTools = Array.isArray(l.tools) && l.tools.length > 0;
  const shown = detailOn ? [] : (hasServerTools
    ? l.tools.slice(-8).map((t) => ({ name: toolName(t.name), detail: t.detail || '', state: t.state || 'done' }))
    : (liveTools[current] || []).slice(-8).map((t) => ({ name: t.name, detail: '', state: t.done ? 'done' : 'doing' })));
  const totalTools = hasServerTools ? Math.max(l.tools_total || 0, shown.length) : (liveTools[current] || []).length;
  const lvIcon = (s) => s === 'doing' ? icon('loader', 14, 'spin') : (s === 'error' ? icon('x', 14) : icon('check', 14));
  const lvSt = (s) => s === 'doing' ? '进行中' : (s === 'error' ? '出错' : '已完成');
  return `<div class="msg ${l.role} live" id="liveMsg">${avOf(m)}<div class="body">
    <div class="who"><b>${esc(name)}</b><span class="sep">·</span><span>${l.waiting ? '排队中' : '正在执行'}</span>${l.waiting ? '' : `<span class="sep">·</span><a class="term-link" data-screen title="看它的终端画面">看终端</a>`}</div>
    <div class="bubble live-card">
      ${healthBanner(l)}
      <div class="lv-title"><span>${esc(st)}</span><span class="dots"><i></i><i></i><i></i></span>
        ${l.waiting ? '' : `<button class="live-stop" data-interrupt title="打断这一轮，会话不关">${icon('stop', 11)}打断 <span class="kbd">Esc</span></button>`}</div>
      ${got ? `<div class="lv-got">收到：${esc(got)}</div>` : ''}
      ${shown.length ? `<div class="lv-tools">${totalTools > shown.length ? `<div class="lv-more">前面还用过 ${totalTools - shown.length} 次工具</div>` : ''}${shown.map((t) =>
        `<div class="lv-tool ${t.state}">${lvIcon(t.state)}<b>${esc(t.name)}</b>${t.detail ? `<code>${esc(t.detail)}</code>` : ''}<span>${lvSt(t.state)}</span></div>`).join('')}</div>` : ''}
      ${l.text ? `<div class="lv-text">${md(l.text)}</div>` : ''}
    </div></div></div>`;
}
function healthBanner(l) {
  if (l.waiting) return '';
  const since = l.health_at ? `，从 ${new Date(l.health_at * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })} 开始` : '';
  if (l.health === 'apierr') return `<div class="lv-health bad">${icon('x', 14)}<div><b>${esc(l.health_note || 'API 报错')}</b>
    <span>多半是网络或接口不稳定${since}。麻薯会自动让它继续（最多 3 次）；一直不好就点「看终端」确认，或打断后再发一句「继续」。</span></div></div>`;
  if (l.health === 'stuck') return `<div class="lv-health bad">${icon('x', 14)}<div><b>${esc(l.health_note || '可能卡住了')}</b>
    <span>屏幕和对话记录都没有变化${since}。如果它在跑渲染、构建这类长命令可以等；否则点「看终端」确认，或打断后再发一句「继续」。</span></div></div>`;
  if (l.stalled_min >= STALL_HINT_MIN) return `<div class="lv-health warn">${icon('loader', 14, 'spin')}<div><b>已经 ${l.stalled_min} 分钟没有新动静</b>
    <span>可能在跑长命令，也可能在等接口；到 15 分钟还没动静会标「可能卡住」并提醒你。</span></div></div>`;
  return '';
}
function renderLive() {
  renderComposer();
  const box = $('#messages');
  const html = liveHtml(live[current]);
  const el = box.querySelector('#liveMsg');
  if ((el ? el._html : '') === html) return;
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 140;
  el?.remove();
  if (html) {
    box.querySelector('.empty')?.remove();
    box.insertAdjacentHTML('beforeend', html);
    box.lastElementChild._html = html;
  }
  renderDetail();
  if (nearBottom) box.scrollTop = box.scrollHeight;
}

// ---------- 「详细」模式：Claude Code 终端里能看到的信息（/api/session，每 2 秒拉一次） ----------
let detailOn = load('mashu.detail', '1') !== '0';
let detailData = null, detailFor = null, detailTimer = null;
function pollDetail() {
  clearTimeout(detailTimer);
  const cam = camOfChannel(current);
  const next = () => { detailTimer = setTimeout(pollDetail, document.hidden ? 8000 : 2000); };
  if (!detailOn || !cam || !cam.alive) { detailData = null; detailFor = null; renderDetail(); return next(); }
  const id = cam.id;
  api(`/api/session?camera=${id}`).then((d) => {
    if (camOfChannel(current)?.id !== id) return;
    detailData = d; detailFor = id; renderDetail();
  }).catch(() => {}).finally(next);
}
function fmtTok(n) { n = +n || 0; return n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n); }
function fmtDur(sec) {
  sec = Math.max(0, Math.round(sec || 0));
  return sec < 60 ? `${sec}s` : sec < 3600 ? `${Math.floor(sec / 60)}m ${sec % 60}s` : `${Math.floor(sec / 3600)}h ${Math.floor(sec % 3600 / 60)}m`;
}
function resetIn(ts) { if (!ts) return ''; const s = ts - Date.now() / 1000; return s > 0 ? `${fmtDur(s).replace(/ \d+s$/, '')}后重置` : ''; }
function detailHtml(d) {
  const st = d.status || {}, now = Date.now() / 1000;
  const m = st.model || {}, ws = st.workspace || {}, cost = st.cost || {}, cw = st.context_window || {};
  const L = d.limits || st.rate_limits || {};
  const cwd = (ws.current_dir || st.cwd || '').replace(/^\/Users\/[^/]+/, '~');
  const chip = (k, v, t = '') => v === '' || v == null ? '' : `<span class="sd-chip" ${t ? `title="${esc(t)}"` : ''}><i>${esc(k)}</i>${esc(v)}</span>`;
  const lim = (k, x) => x && x.used_percentage != null ? chip(k, `${Math.round(x.used_percentage)}%`, resetIn(x.resets_at)) : '';
  const limChips = d.provider === 'gemini'
    ? [L.remaining != null ? chip('额度', `剩 ${Math.round(L.remaining * 100)}%`,
        [L.low_model ? `最低 ${L.low_model}` : '', L.reset ? resetText(L.reset) : ''].filter(Boolean).join(' · ')) : '']
    : (d.provider === 'opencode'
      ? [L.today != null ? chip('今日花费', `$${(+L.today).toFixed(2)}`, L.week != null ? `本周 $${(+L.week).toFixed(2)}` : '') : '',
        L.week != null ? chip('本周花费', `$${(+L.week).toFixed(2)}`) : '']
      : L.billing === 'usage' ? [chip('额度', '按量')] : [lim('5h', L.five_hour), lim('7d', L.seven_day)]);
  const stat = [
    chip('模型', m.display_name || m.id || ''), chip('思考', (st.effort || {}).level || ''), d.code_mode ? chip('Code Mode', '开') : '',
    chip('目录', cwd, ws.project_dir || ''),
    cw.used_percentage != null ? chip('上下文', `${Math.round(cw.used_percentage)}%`, cw.context_window_size ? `窗口 ${fmtTok(cw.context_window_size)}` : '') : '',
    cost.total_cost_usd != null ? chip('花费', `$${(+cost.total_cost_usd).toFixed(2)}`) : '',
    cost.total_duration_ms != null ? chip('时长', fmtDur(cost.total_duration_ms / 1000), cost.total_api_duration_ms != null ? `API ${fmtDur(cost.total_api_duration_ms / 1000)}` : '') : '',
    cost.total_lines_added != null ? chip('改动', `+${cost.total_lines_added} / -${cost.total_lines_removed || 0} 行`) : '',
    ...limChips,
    chip('输出风格', (st.output_style || {}).name && st.output_style.name !== 'default' ? st.output_style.name : ''),
    chip('版本', st.version || ''),
  ].join('');
  const turn = d.busy && d.turn_start ? `<div class="sd-turn">${icon('loader', 13, 'spin')}这一轮 ${fmtDur(now - d.turn_start)} · ↓ ${fmtTok(d.turn_out)} tokens</div>` : '';
  const stIcon = (s) => s === 'doing' || s === 'running' ? icon('loader', 13, 'spin') : s === 'error' || s === 'failed' || s === 'killed' ? icon('x', 13) : icon('check', 13);
  const tools = (d.tools || []).slice(-10);      // 只显示最近 10 次
  const toolsHtml = tools.length ? `<div class="sd-sec"><div class="sd-h">工具 · 这一轮 ${d.tools_total} 次${d.tools_total > tools.length ? `（显示最近 ${tools.length}）` : ''}</div>
    ${tools.map((t) => `<div class="sd-row ${t.state}">${stIcon(t.state)}<b>${esc(toolName(t.name))}</b><code>${esc(t.detail || '')}</code><span class="sd-t">${t.start ? fmtDur((t.end || now) - t.start) : ''}</span></div>`).join('')}</div>` : '';
  const ag = d.agents || [];
  const agHtml = ag.length ? `<div class="sd-sec"><div class="sd-h">子代理 · ${ag.filter((a) => a.state === 'running').length} 个在跑</div>
    ${ag.map((a) => `<div class="sd-agent ${a.state === 'running' ? 'doing' : a.state}">${stIcon(a.state === 'running' ? 'running' : a.state)}
      <b>${esc(a.type || a.tool)}</b><span class="sd-desc">${esc(a.desc || '')}</span>
      <span class="sd-t">${a.start ? fmtDur((a.end || now) - a.start) : ''}${a.tokens ? ` · ${fmtTok(a.tokens)} tokens` : ''}${a.bg ? ' · 后台' : ''}</span>
      ${a.state === 'running' && a.tool ? `<div class="sd-sub">↳ ${esc(a.tool)}</div>` : ''}</div>`).join('')}</div>` : '';
  const sh = d.shells || [];
  const shHtml = sh.length ? `<div class="sd-sec"><div class="sd-h">后台命令</div>
    ${sh.map((a) => `<div class="sd-row ${a.state === 'running' ? 'doing' : a.state}">${stIcon(a.state === 'running' ? 'running' : a.state)}<b>${esc(a.tool)}</b><code>${esc(a.desc || '')}</code><span class="sd-t">${a.start ? fmtDur((a.end || now) - a.start) : ''}</span></div>`).join('')}</div>` : '';
  const td = d.todos || [];
  const tdHtml = td.length ? `<div class="sd-sec"><div class="sd-h">待办</div>
    ${td.map((x) => `<div class="sd-todo ${esc(x.state)}">${x.state === 'completed' ? '☑' : x.state === 'in_progress' ? '◐' : '☐'} ${esc(x.text)}</div>`).join('')}</div>` : '';
  const th = d.thinking ? `<details class="sd-sec sd-think"><summary class="sd-h">思考片段</summary><div class="sd-think-t">${esc(d.thinking)}</div></details>` : '';
  return `<div class="sess-detail" id="sessDetail"><div class="sd-stat">${stat || '<span class="sd-chip">状态栏还没报上来</span>'}</div>${turn}${agHtml}${shHtml}${tdHtml}${toolsHtml}${th}</div>`;
}
function renderDetail() {
  const box = $('#messages');
  if (!box) return;
  let el = box.querySelector('#sessDetail');
  const cam = camOfChannel(current);
  const html = detailOn && detailData && cam && cam.id === detailFor ? detailHtml(detailData) : '';
  if (!html) { el?.remove(); return; }
  const open = el?.querySelector('details')?.open;
  if (!el || el._html !== html) {
    el?.remove();
    box.insertAdjacentHTML('beforeend', html);
    el = box.lastElementChild; el._html = html;
    if (open) el.querySelector('details')?.setAttribute('open', '');
  } else if (el !== box.lastElementChild) box.appendChild(el);
}


// ---------- 右侧：麻薯需要你 + 主脑/子脑的收件 · 发件 ----------
// 需要你的事分三类：要你审批/确认（闸门、进入下一阶段）、要你帮忙（登录、密钥、付款、线下……）、在问你（其余请示）。
const HELP_RX = /登录|登陆|账号|帐号|密钥|密码|令牌|token|api[ _-]?key|付款|支付|充值|购买|订阅|线下|手动|扫码|验证码|二维码|实名|签字|login|password|payment/i;
function needCat(r) {
  if (r.gate || r.kind === 'confirm') return 'approve';
  if (parsed(r.meta, {}).human) return 'help';
  return HELP_RX.test(`${r.summary || ''}\n${r.text || ''}`) ? 'help' : 'ask';
}
const NEED_CATS = [
  ['approve', '要你审批 / 确认', 'pen'],
  ['help', '要你帮忙', 'hand'],
  ['ask', '在问你', 'ask'],
];
function parsed(v, d) { if (typeof v !== 'string') return v || d; try { return JSON.parse(v) || d; } catch { return d; } }
function needRow(r, showWhere) {
  const opts = parsed(r.options, []);
  const m = parsed(r.meta, {});
  const go = `data-go="${esc(r.film_id || MAIN)}" data-focus="card-${r.id}"`;
  let acts = '';
  if ((r.gate || r.kind === 'confirm') && opts.length >= 2 && r.gate !== 'irreversible') {
    acts = `<button class="btn small primary" data-answer="${r.id}" data-text="${esc(opts[0])}">${icon('check', 13)}${esc(opts[0])}</button>`;
  } else if (!r.gate && r.kind !== 'confirm' && opts.length && opts.length <= 4) {
    acts = opts.map((o) => `<button class="btn small ${m.recommend === o ? 'rec' : ''}" data-answer="${r.id}" data-text="${esc(o)}">${m.recommend === o ? icon('star', 12) : ''}${esc(o)}</button>`).join('');
  }
  const mtType = r.msg_type || (r.gate || r.kind === 'confirm' ? 'approve' : 'ask');
  const mtChip = `<span class="tag mt-${esc(mtType)}">${esc(r.msg_type_label || MSG_TYPE[mtType] || mtType)}</span>`;
  const chip = r.gate ? `<span class="tag k-gate">${esc(GATE[r.gate] || r.gate)}</span>` : (r.kind === 'confirm' ? '<span class="tag k-confirm">进下一阶段</span>' : '');
  const staleChip = r.stale_reason ? `<span class="tag k-stale" title="${esc(r.stale_reason)}">已过时</span>` : '';
  return `<div class="need-item${r.stale_reason ? ' is-stale' : ''}${renderPanel.seen && !renderPanel.seen.has(r.id) ? ' enter' : ''}">
    <div class="need-main" ${go}><div class="need-subj">${staleChip}${mtChip}${chip}${esc(r.summary || r.text)}</div>
      <div class="need-o">#${r.id}${showWhere ? ` · ${esc(r.origin || '')}` : ''} · ${esc(waited(r))}${r.stale_reason ? ` · ${esc(r.stale_reason)}` : ''}</div></div>
    <div class="need-acts">${acts}<button class="btn small" ${go}>${r.gate === 'irreversible' ? '去审批' : (acts ? '详情' : '去回复')}${icon('arrow-ur', 12)}</button></div></div>`;
}
function needsHtml(list, showWhere) {
  if (!list.length) return `<h3>麻薯需要你</h3><div class="need-all-clear">${icon('check-c', 16)}没有等你的事，麻薯在自己推进</div>`;
  const fresh = list.filter((r) => !r.stale_reason && !r.superseded_by && r.status !== 'superseded');
  const stale = list.filter((r) => r.stale_reason || r.superseded_by || r.status === 'superseded');
  const groups = {};
  for (const r of fresh) (groups[needCat(r)] = groups[needCat(r)] || []).push(r);
  const counts = NEED_CATS.filter(([k]) => groups[k]).map(([k, t]) => `<span class="need-count c-${k}">${t} <b>${groups[k].length}</b></span>`).join('');
  const staleBox = stale.length ? `<details class="need-stale-fold" data-dkey="need-stale" ${detailsOpen('need-stale')}><summary>已过时 / 被取代 ${stale.length} 件（默认折叠）</summary>${stale.map((r) => needRow(r, showWhere)).join('')}</details>` : '';
  if (!fresh.length) return `<h3>麻薯需要你</h3><div class="need-all-clear">${icon('check-c', 16)}没有活跃待处理事项</div>${staleBox}`;
  return `<h3>麻薯需要你 <span class="h3-sub">${fresh.length} 件</span></h3><div class="need-counts">${counts}</div>
    ${NEED_CATS.filter(([k]) => groups[k]).map(([k, t, ic]) => `<div class="need-group c-${k}"><div class="need-h">${icon(ic, 13)}${t}</div>
      ${groups[k].map((r) => needRow(r, showWhere)).join('')}</div>`).join('')}
    ${fresh.length > 1 ? `<button class="btn small" data-next="0">逐件处理${icon('arrow-d', 13)}</button>` : ''}${staleBox}`;
}

// 主脑 / 子脑本次会话的往来（右侧小列表）：只列当前会话开机以来收到、发出的，按时间倒序，不看历史（历史去「邮箱」）
const panelMail = { box: null, data: null, at: 0, busy: false };
async function loadPanelMail(box) {
  if (panelMail.busy) return;
  panelMail.busy = true;
  try {
    const d = await api(`/api/mail?box=${encodeURIComponent(box)}`);
    for (const r of d.reports || []) reports[r.id] = r;
    Object.assign(panelMail, { box, data: d, at: Date.now() });
  } catch (e) { console.error(e); panelMail.at = Date.now(); }
  panelMail.busy = false;
  renderPanel();
}
function panelMailRow(x) {
  const r = x.report_id ? reports[x.report_id] : null;
  let st = '';
  if (x.pending) st = '<span class="pm-st pend"><i class="pdot"></i>待处理</span>';
  else if (r && r.status === 'open') st = r.holder === 'digest' ? '<span class="pm-st">进简报</span>' : `<span class="pm-st open">在${esc(holderName(r))}手上</span>`;
  else if (r && r.status !== 'open') st = '<span class="pm-st">已结</span>';
  const who = x.dir === 'in' ? `← ${x.frm === '调度器' ? '系统' : x.frm}` : `→ ${x.to}`;
  return `<div class="pm-row ${x.pending ? 'pending' : ''}" data-pmail="${esc(x.key)}" data-pdir="${x.dir}" title="${esc(x.subject)}${x.where ? ' · ' + esc(x.where) : ''}">
    <div class="pm-l1"><span class="pm-dir ${x.dir}">${x.dir === 'in' ? '收' : '发'}</span><span class="tag">${esc(x.kind_label)}</span>
      <span class="pm-meta">${esc(who)}</span>${st}<span class="time">${timeText(x.ts).slice(6)}</span></div>
    <div class="pm-subj">${esc(x.subject)}</div></div>`;
}
function brainMailHtml(box, cam) {
  const who = box === 'assistant' ? '主脑' : '子脑';
  const since = cam && cam.alive ? cam.started_at || 0 : 0;
  if (!since) return `<h3>${who}本次会话往来</h3><div class="kv"><span class="k">${who}没在运行</span></div>`;
  const fresh = panelMail.box === box && Date.now() - panelMail.at < 3000;
  if (!fresh && !panelMail.busy) setTimeout(() => loadPanelMail(box), 0);
  const d = panelMail.box === box ? panelMail.data : null;
  if (!d) return `<h3>${who}本次会话往来</h3><div class="kv"><span class="k">加载中…</span></div>`;
  const cur = (x) => x.ts && new Date(x.ts).getTime() / 1000 >= since - 5;
  const list = [...d.inbox.filter(cur).map((x) => ({ ...x, dir: 'in' })), ...d.outbox.filter(cur).map((x) => ({ ...x, dir: 'out' }))]
    .sort((x, y) => (y.ts || '').localeCompare(x.ts || ''));
  const nIn = list.filter((x) => x.dir === 'in').length;
  return `<h3>${who}本次会话往来 <span class="h3-sub">收 ${nIn} · 发 ${list.length - nIn} · ${new Date(since * 1000).toTimeString().slice(0, 5)} 起</span></h3>
    <div class="pm-list">${list.map(panelMailRow).join('') || '<div class="kv"><span class="k">这次会话还没有收发</span></div>'}</div>`;
}

// ---------- 右侧面板 ----------
function renderPanel() {
  const real = $('#panel');
  const p = { innerHTML: '' };
  panelInto(p);
  if (real._html !== p.innerHTML) {
    const top = real.scrollTop;
    real.innerHTML = p.innerHTML; real._html = p.innerHTML; real.scrollTop = top;
  }
  renderPanel.seen = new Set((state.inbox || []).map((r) => r.id));
}
function panelInto(p) {
  if (current === MAIN) {
    const live = state.films.reduce((n, f) => n + f.sequences.filter((s) => s.crew?.alive).length, 0);
    p.innerHTML = `${needsHtml(state.inbox || [], true)}${brainMailHtml('assistant', state.assistant)}
      <h3>麻薯的记忆</h3><div class="chips">
        ${['index.md', '用户.md', '方针.md', '在办.md', '复盘.md', '守则.md'].map((x) => `<button class="chip link" data-open-lib="memory" data-path="${x}">${icon('brain', 12)}${x === 'index.md' ? '索引' : x.replace('.md', '')}</button>`).join('')}</div>
      <h3>决策记录</h3><div class="kv">主脑和子脑先斩后奏的决定：<b>${state.decisions_pending || 0}</b> 条待追认
      <div style="margin-top:6px"><button class="btn" data-view-go="decisions">去看看</button></div></div>
      <h3>简报</h3><div class="kv">每天 9:00、18:00 麻薯会发简报；阶段完成这类例行通报都汇进去，不单独打扰您。
        <div style="margin-top:6px"><button class="btn" data-cam="digest-now">现在就要一份</button></div></div>
      <h3>今天</h3>
      <div class="kv"><span class="k">项目</span><div class="v">${state.films.length} 个项目，${state.films.reduce((n, f) => n + f.sequences.length, 0)} 个功能</div></div>
      <div class="kv"><span class="k">并发槽</span><div class="v">${live} 个执行者在运行 · 每个账号上限 ${state.stages.max}（总数不限）</div></div>`;
    return;
  }
  const [f, s] = seqOf(current);
  if (!f) { p.innerHTML = ''; return; }
  if (!s) {
    const b = f.budget || {};
    const fk = `film:${f.id}`;
    p.innerHTML = `${needsHtml(inboxOf(f.id), false)}${brainMailHtml(`producer:${f.id}`, f.producer)}<h3>资料库</h3><div class="chips">
        <button class="chip link" data-open-lib="${esc(fk)}" data-path="README.md">${icon('book', 12)}项目说明</button>
        <button class="chip link" data-open-lib="${esc(fk)}" data-path="playbook.json">${icon('refresh', 12)}工作流 v${esc(f.playbook.version)}</button>
        <button class="chip link" data-open-lib="${esc(fk)}" data-path="rules.md">${icon('ruler', 12)}项目规则</button>
        <button class="chip link" data-open-lib="${esc(fk)}" data-path="producer-notes.md">${icon('notebook', 12)}项目笔记</button>
        <button class="chip link" data-open-lib="${esc(fk)}" data-path="check.sh">${icon('flask', 12)}检查脚本</button>
        <button class="chip link" data-open-lib="${esc(fk)}" data-path="hotspots.txt">${icon('flame', 12)}热点文件</button></div>
      <div class="kv"><span class="k">工作流</span><div class="v">${esc(f.playbook.name)}：${esc((f.playbook.stages || []).join(' → '))}</div></div>
      ${f.check_configured ? '' : `<div class="kv warn-box">${icon('alert', 13)}还没配置自动检查脚本，完成阶段时没有机器把关。<button class="chip link" data-open-lib="film:${esc(f.id)}" data-path="check.sh">看 check.sh</button></div>`}
      ${(f.trust || []).length ? `<div class="kv"><span class="k">放权</span>${f.trust.map((t) => `<div>${esc(t.category.split(':')[1] || t.category)}：${t.delegated ? '已交给麻薯' : `连续原样批准 ${t.streak} 次`}</div>`).join('')}</div>` : ''}
      <h3>项目</h3>
      <div class="kv"><span class="k">工程文件夹</span><div class="v path">${esc(f.path)}</div></div>
      <div class="kv"><span class="k">子脑</span><div class="v">${camText(f.producer, '休息中')}${f.producer ? ` · 上下文 ${pct(f.producer.context_pct)}` : ''}</div></div>
      <div class="kv"><span class="k">今日 token</span><div class="v">${num(b.today_tokens)}${b.today_cost != null ? ` · $${b.today_cost.toFixed(2)}` : ''}</div></div>
      <div class="kv"><span class="k">同时运行上限</span><div class="v">${f.max_stages} 个功能（共用同一个文件夹）</div></div>
      <h3>功能</h3>${(acctsOf(f) ? acctGroups(f, f.sequences) : [[null, f.sequences]]).map(([a, list]) => (a ? `<div class="acct-sub">${acctDot(f, a.id)}${esc(a.name)}<b>${list.length}</b></div>` : '')
        + list.map((x) => `<div class="inbox-item" data-go="${esc(f.id)}/${esc(x.key)}">
        <div class="o">#${esc(x.num)} · ${esc(statusName(x))}</div>${strip(x.stages)} ${esc(x.title)}</div>`).join('')).join('') || '<div class="kv"><span class="k">还没有</span></div>'}
      <div style="margin-top:10px"><button class="btn primary" data-pitch="${esc(f.id)}">＋ 新功能</button></div>`;
    return;
  }
  const h = s.handoff || {};
  const c = s.crew || s.last;
  const ctx = c?.context_pct;
  const ENTRY = { producer: '子脑批', assistant: '主脑批', executive: '麻薯批' };
  const stages = (s.stages || []).map((st, i) => {
    const d = (s.stage_defs || [])[i] || {};
    return `<div class="kv" style="margin:3px 0"><span class="dot ${({ 进行中: 'shooting', 待确认: 'sign', 已完成: 'on' })[st] || ''}"></span>${i + 1}. ${esc(stageName(i + 1, s.stage_defs))}
      <span class="k">· ${esc(roleName(d.role))} · ${esc(st || '未开始')}</span>${d.entry ? ` <span class="tag">${ENTRY[d.entry]}</span>` : ''}${s.stage && s.stage.id === i + 1 ? behaviorLinks(d.behavior, f.id) : ''}</div>`;
  }).join('');
  const kv = (k, v) => (v ? `<div class="kv"><span class="k">${k}</span><div class="v">${md(v)}</div></div>` : '');
  p.innerHTML = `<h3>功能状态 <span class="h3-sub">${esc((s.playbook || {}).name || '')} v${esc((s.playbook || {}).version || '')}</span></h3>${stages}
    ${s.blocker ? kv(`${icon('ban', 12)}阻塞`, s.blocker) : ''}
    ${kv('做到', h.progress)}${kv('下一步', h.next)}${kv('待确认', h.confirm)}${kv('事实', h.context)}${kv('注意', h.risk)}
    ${s.reports.length ? `<h3>未结上报单</h3>${s.reports.map((r) => `<div class="inbox-item" data-go="${esc(current)}" data-focus="raised-${r.id}"><div class="o">#${r.id} · ${esc(KIND[r.kind] || r.kind)} · 在${LEVEL[r.holder]}手上</div>${esc(r.summary)}</div>`).join('')}` : ''}
    <h3>执行者</h3>
    ${c ? `<div class="kv">${esc(roleName(c.crew_role))}「${esc(c.name)}」· ${s.crew ? camText(c) : '已结束'}</div>
      <div class="kv"><span class="k">上下文 ${pct(ctx)}</span><div class="meter"><i class="${ctx > 0.75 ? 'bad' : ctx > 0.6 ? 'warn' : ''}" style="width:${Math.min(100, (ctx || 0) * 100)}%"></i></div></div>
      <div class="kv"><span class="k">token</span><div class="v">${num(c.tokens)}</div></div>` : '<div class="kv"><span class="k">还没有执行者</span></div>'}
    <h3>验收标准 <span class="h3-sub">${(s.ac || []).filter((i) => i.passes).length}/${(s.ac || []).length}</span></h3>
    ${(s.ac || []).map((i) => `<div class="ac-row ${i.passes ? 'ok' : 'no'}" title="${esc(i.evidence || '')}">${icon(i.passes ? 'check' : 'x', 12)}<b>${esc(i.id)}</b> ${esc(i.text)}</div>`).join('') || '<div class="kv"><span class="k">方案阶段写</span></div>'}
    <h3>改动范围</h3><div class="kv">${(s.claims || []).map((g) => `<code>${esc(g)}</code>`).join(' ') || '<span class="k">还没声明</span>'}
      ${(s.claims_extra || []).length ? `<div class="k" style="margin-top:4px">范围外写过：${s.claims_extra.map((g) => `<code>${esc(g)}</code>`).join(' ')}</div>` : ''}</div>
    ${(s.commits || []).length ? `<div class="kv"><span class="k">提交</span><div class="v">${s.commits.map((x) => `<code>${esc(x)}</code>`).join(' ')}</div></div>` : ''}
    ${(s.checks || []).map((ck) => `<div class="kv"><span class="k">阶段 ${ck.stage} 自动检查</span> ${ck.unconfigured ? '<span class="ck">未配置</span>' : (ck.ok ? `<span class="ck ok">${icon('check-c', 13)}通过</span>` : `<span class="ck bad">${icon('x-c', 13)}失败</span>`)}</div>`).join('')}
    <h3>资料</h3>${docsHtml(current)}
    <h3>工作目录</h3><div class="kv"><span class="k">与本项目其他功能共用</span><div class="v path">${esc(s.set_dir)}</div></div>
    <div class="kv"><span class="k">排队优先级 ${s.priority}</span><div><button class="btn" data-prio="${esc(current)}" data-v="${s.priority + 1}">提前</button>
      <button class="btn" data-prio="${esc(current)}" data-v="${s.priority - 1}">靠后</button></div></div>
    ${s.summary ? `<h3>需求</h3><div class="kv">${md(s.summary)}</div>` : ''}`;
}

function docsHtml(ref) {
  if (!(ref in docsCache)) {
    docsCache[ref] = null;
    api(`/api/docs?ref=${encodeURIComponent(ref)}`).then((d) => { docsCache[ref] = d.docs; if (current === ref) renderPanel(); })
      .catch(() => { docsCache[ref] = []; });
    return '<div class="kv"><span class="k">加载中…</span></div>';
  }
  const list = docsCache[ref];
  if (!list) return '<div class="kv"><span class="k">加载中…</span></div>';
  if (!list.length) return '<div class="kv"><span class="k">还没有文档（方案阶段写好后会出现在这里）</span></div>';
  return `<div class="chips">${list.map((d) => `<button class="chip link" data-doc="${esc(ref)}" data-path="${esc(d.path)}">${icon('doc', 12)}${esc(d.label)}</button>`).join('')}</div>`;
}

// ---------- 看板 ----------
function renderBoard() {
  const el = $('#view-board');
  const lanes = [];
  for (const f of state.films) {
    const groups = {};
    for (const s of f.sequences) {
      const k = (s.stage_defs || []).map((d) => d.name).join('|');
      (groups[k] = groups[k] || { defs: s.stage_defs, pb: s.playbook, seqs: [] }).seqs.push(s);
    }
    if (!f.sequences.length) groups._ = { defs: [{ name: '直接做', plain: '' }], pb: null, seqs: [] };
    for (const g of Object.values(groups)) {
      const n = g.defs.length;
      const head = (n <= 1 ? '<div class="col-h">进行中<small>直接做，不分阶段</small></div>'
        : g.defs.map((d, i) => `<div class="col-h">${i + 1}. ${esc(stageName(i + 1, g.defs))}<small>${esc(d.plain || '')}${d.role ? ' · ' + esc(roleName(d.role)) : ''}</small></div>`).join(''))
        + `<div class="col-h">${icon('check-c', 14)}已完成<small></small></div>`;
      const cells = [...Array(n + 1).keys()].map((c) => {
        const items = g.seqs.filter((s) => (s.stage ? s.stage.id === c + 1 : c === n));
        return `<div class="cell">${items.map((s) => `<div class="bcard" data-go="${esc(f.id)}/${esc(s.key)}">
          <div class="bt">${esc(s.num)} · ${esc(s.title)}</div>
          <div class="bs"><span class="dot ${s.status}"></span>${esc(statusName(s))}${s.crew ? ` · ${esc(s.crew.name)}` : ''}${s.reports.length ? ` · ${icon('inbox', 12)}` : ''}</div></div>`).join('')}</div>`;
      }).join('');
      lanes.push(`<div class="lane"><div class="lane-h">${sdot(brainState(f))}${esc(f.title)}${n > 1 ? `<span class="tag gold">工作流 ${esc((g.pb || {}).name || '')} v${esc((g.pb || {}).version || '')}</span>` : ''}</div>
        <div class="board" style="grid-template-columns:repeat(${n + 1}, minmax(130px, 1fr))">${head}${cells}</div></div>`);
    }
  }
  el.innerHTML = `<h2>看板</h2><p class="hint">每条泳道是一个项目的功能。默认直接做，只分进行中 / 已完成；按专门工作流分阶段的功能，列是工作流的各阶段。点卡片进入功能对话。</p>
    <div style="overflow-x:auto">${lanes.join('') || '<div class="empty">还没有项目</div>'}</div>`;
}

// ---------- 项目页「工作流」视图（#7）：每个工作流一个标签，阶段流水线看板 + 工作流自己配置的专属字段 ----------
let projMode = loadJSON('mashu.projmode', {});     // 项目 id → 'wf'（工作流）/ 缺省（对话）
let wfTab = loadJSON('mashu.wftab', {});           // 项目 id → 选中的工作流 id
const wfArch = new Set();                          // 展开了「已归档」的工作流
const dash = {};                                   // 项目 id → { data, at, sig, busy }
function isFilmCh(ch) { return ch !== MAIN && !ch.includes('/') && !!filmOf(ch); }
function wfOn() { return view === 'chat' && isFilmCh(current) && projMode[current] === 'wf'; }
function setProjMode(fid, mode) {
  if (mode === 'wf') projMode[fid] = 'wf'; else delete projMode[fid];
  save('mashu.projmode', projMode);
  renderHead(); applyProjMode(true);
}
function dashSig(f) { return f.sequences.map((s) => `${s.key}:${s.status}:${s.stage ? s.stage.id : '-'}:${s.updated}`).join('|'); }
async function loadDash(fid, force) {
  const f = filmOf(fid); const d = dash[fid] || (dash[fid] = {});
  if (!f || d.busy) return;
  const sig = dashSig(f);
  if (!force && d.data && d.sig === sig && Date.now() - d.at < 60000) return;
  if (!force && d.data && Date.now() - d.at < 3000) return;
  d.busy = true;
  try { d.data = await api(`/api/dashboard?film=${encodeURIComponent(fid)}${force ? '&fresh=1' : ''}`); d.sig = sig; d.at = Date.now(); d.err = ''; }
  catch (e) { d.err = e.message || String(e); d.at = Date.now(); }
  finally { d.busy = false; }
  if (wfOn() && current === fid) renderDash();
}
function applyProjMode(force) {
  const on = wfOn();
  $('#view-chat').classList.toggle('wf-mode', on);
  $('#wfDash').hidden = !on;
  if (!on) return;
  renderDash();
  loadDash(current, force);
}
function ago(ts) {
  if (!ts) return '';
  const m = Math.max(0, (Date.now() / 1000 - ts) / 60);
  return m < 60 ? `${Math.max(1, Math.round(m))} 分钟` : m < 60 * 24 ? `${Math.round(m / 60)} 小时` : `${Math.round(m / 1440)} 天`;
}
function dayText(ts) { const d = new Date(ts * 1000); return `${d.getMonth() + 1}-${String(d.getDate()).padStart(2, '0')}`; }
function weekStart() { const d = new Date(); d.setHours(0, 0, 0, 0); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return d.getTime() / 1000; }
function wfField(x) {
  const v = x.value;
  if (x.type === 'image' || v == null || v === '' || (Array.isArray(v) && !v.length)) return '';
  const lab = `<span class="wf-l">${esc(x.label)}</span>`;
  if (x.type === 'count') {
    const tot = Number(x.total) || 0;
    const bar = tot ? `<i class="wf-bar"><b style="width:${Math.min(100, Math.round(v / tot * 100))}%"></b></i>` : '';
    return `<div class="wf-f">${lab}<span class="wf-v num">${esc(v)}${tot ? `<small>/${esc(tot)}</small>` : ''}${x.unit ? ` <small>${esc(x.unit)}</small>` : ''}</span>${bar}</div>`;
  }
  if (x.type === 'link') {
    const href = x.href || (x.file ? blobUrl(x.file) : '');
    if (!href) return '';
    const name = x.href ? x.value.replace(/^https?:\/\/(www\.)?/, '') : baseName(x.value);
    return `<div class="wf-f">${lab}<a class="wf-link" ${x.href ? '' : `data-reveal="${esc(x.file || x.value)}"`} href="${esc(href)}" target="_blank" rel="noopener" title="${esc(x.value)}">${icon(x.href ? 'arrow-ur' : fileIconName(x.value), 12)}<span>${esc(name)}</span></a></div>`;
  }
  if (x.type === 'metrics') {
    const items = (x.items || []).filter((i) => i.value != null);
    return `<div class="wf-f">${lab}<span class="wf-metrics">${items.map((i) => `<span><b>${esc(num(i.value))}</b>${esc(i.label)}</span>`).join('')}</span></div>`;
  }
  if (x.as === 'badge') return `<div class="wf-f">${lab}<span class="wf-badge ${esc(x.tone || '')}">${esc(v)}</span></div>`;
  return `<div class="wf-f">${lab}<span class="wf-v" title="${esc(v)}">${esc(v)}</span></div>`;
}
function fileIconName(p) { return IMG_RX.test(p) ? 'image' : /\.(mp4|mov|webm)$/i.test(p) ? 'film' : /\.zip$/i.test(p) ? 'archive' : 'doc'; }
function wfCard(f, s, fd, w, compact) {
  const k = seqStateKey(f, s);
  const need = needOf(inboxOf(f.id, s.key));
  const done = SEQ_ST[s.status] === 'done' || !!(fd && fd.done_at);
  const fields = (fd && fd.fields) || [];
  const cover = fields.find((x) => x.type === 'image' && x.value);
  const title = fields.find((x) => x.as === 'title' && x.value);
  const rest = fields.filter((x) => x !== cover && x !== title);
  const who = (s.crew || s.last || {}).name || '';
  const when = fd ? (done ? (fd.done_at ? `完成于 ${dayText(fd.done_at)}` : '') : (fd.since ? `这一步 ${ago(fd.since)}` : '')) : '';
  const ac = (s.ac || []).length ? `<span class="wf-ac" title="验收标准通过数">${icon('check-c', 12)}${s.ac.filter((a) => a.passes).length}/${s.ac.length}</span>` : '';
  const entryWait = fd && !done && w.stages[fd.col - 1] && (s.stages[fd.col - 1] || '未开始') === '未开始' && w.stages[fd.col - 1].entry;
  return `<div class="wcard${need ? ' need' : ''}${k === 'blocked' ? ' blocked' : ''}${compact ? ' compact' : ''}" data-go="${esc(f.id)}/${esc(s.key)}" title="打开 #${esc(s.num)} 的功能对话">
    ${cover && !compact ? `<div class="wc-cover"><img loading="lazy" src="${esc(blobUrl(cover.value))}" alt="${esc(cover.name || '')}"></div>` : ''}
    <div class="wc-body">
      <div class="wc-top"><span class="wc-num">#${esc(s.num ?? s.key)}</span><span class="wc-t">${esc(s.title)}</span>${need ? `<span class="wc-need">${icon('hand', 12)}等你</span>` : ''}</div>
      ${title ? `<div class="wc-title" title="${esc(title.value)}">${esc(title.value)}</div>` : ''}
      <div class="wc-meta">${sdot(k)}<span>${esc(entryWait && !need ? '等确认进入' : (ST[k] || statusName(s)))}</span>${who ? `<span>· ${esc(who)}</span>` : ''}${when ? `<span class="wc-when">${icon('clock', 11)}${esc(when)}</span>` : ''}${ac}</div>
      ${s.blocker && !done ? `<div class="wc-block">${icon('alert', 12)}<span>${esc(s.blocker)}</span></div>` : ''}
      ${!compact && rest.length ? `<div class="wc-fields">${rest.map(wfField).join('')}</div>` : ''}
    </div></div>`;
}
function renderDash() {
  const el = $('#wfDash');
  const f = filmOf(current);
  if (!f) return;
  const d = dash[f.id] || {};
  const put = (h) => { if (el._html !== h) { const top = el.scrollTop; el.innerHTML = h; el._html = h; el.scrollTop = top; } };
  if (!d.data) return put(`<div class="wf-loading">${d.err ? `工作流面板没加载出来：${esc(d.err)}<br><small>服务端还没有 /api/dashboard 的话，点右上角「重启服务」。</small>` : '正在读工作流…'}</div>`);
  const ws = d.data.workflows || [];
  if (!ws.length) return put('<div class="wf-loading">这个项目还没有工作流</div>');
  const seqs = Object.fromEntries(f.sequences.map((s) => [s.key, s]));
  const feats = d.data.features || {};
  const A = acctsOf(f);        // 按账号分组（#8）：选了账号就只看这个账号的功能和工作流
  const acct = curAcct(f);
  const accOf = (s) => (feats[s.key] || {}).account || acctOfSeq(f, s);
  const wfIn = (w) => {        // 工作流按 accounts.json 的 workflows 映射过滤；没写映射的：项目默认工作流，或用过它的功能里有这个账号的
    if (!acct) return true;
    const m = (A.workflows || {})[w.id];
    return Array.isArray(m) ? m.includes(acct) : !!w.default || w.features.some((k) => seqs[k] && accOf(seqs[k]) === acct);
  };
  const live = (w) => w.features.map((key) => seqs[key]).filter((s) => s && (!acct || accOf(s) === acct));
  const vis = ws.filter(wfIn);
  const sel = vis.find((w) => w.id === wfTab[f.id]) || vis[0] || ws[0];
  const acctBar = A ? `<div class="acct-bar"><span class="acct-l">账号</span>${[{ id: '', name: '全部' }, ...A.accounts].map((a) => {
    const list = f.sequences.filter((s) => s.status !== 'archived' && (!a.id || accOf(s) === a.id));
    const waiting = list.filter((s) => needOf(inboxOf(f.id, s.key))).length;
    const active = list.filter((s) => SEQ_ST[s.status] !== 'done').length;
    return `<button class="acct-b${a.id === acct ? ' on' : ''}" data-acct="${esc(a.id)}" title="${esc(a.id ? (a.account ? `账号「${a.account}」` : a.name) : '所有账号，看板按账号分段')}">${a.id ? acctDot(f, a.id) : ''}<span>${esc(a.name)}</span>${waiting ? `<span class="badge gold" title="等你处理">${waiting}</span>` : active ? `<span class="wf-cnt" title="进行中">${active}</span>` : ''}</button>`;
  }).join('')}</div>` : '';
  const tabs = vis.map((w) => {
    const list = live(w).filter((s) => s.status !== 'archived');
    const waiting = list.filter((s) => needOf(inboxOf(f.id, s.key))).length;
    const active = list.filter((s) => SEQ_ST[s.status] !== 'done').length;
    return `<button class="wf-tab${w === sel ? ' active' : ''}" data-wf-tab="${esc(w.id)}" title="${esc(w.summary || '')}">
      <span>${esc(w.name)}</span>${waiting ? `<span class="badge gold" title="等你处理">${waiting}</span>` : active ? `<span class="wf-cnt" title="进行中">${active}</span>` : ''}</button>`;
  }).join('');
  const all = live(sel);
  const shown = all.filter((s) => s.status !== 'archived');
  const arch = all.filter((s) => s.status === 'archived');
  const doneNow = (s) => SEQ_ST[s.status] === 'done';
  const wk = weekStart();
  const kpi = [
    ['进行中', shown.filter((s) => !doneNow(s)).length, 'busy'],
    ['等你处理', shown.filter((s) => needOf(inboxOf(f.id, s.key))).length, 'approve'],
    ['阻塞', shown.filter((s) => !doneNow(s) && (s.status === 'blocked' || s.blocker)).length, 'blocked'],
    ['本周完成', all.filter((s) => doneNow(s) || s.status === 'archived').filter((s) => ((feats[s.key] || {}).done_at || 0) >= wk).length, 'idle'],
  ];
  const kpis = kpi.map(([t, n, c]) => `<div class="wf-kpi st-${c}${n ? '' : ' zero'}"><b>${n}</b><span>${t}</span></div>`).join('')
    + ((acct && sel.overview_by ? sel.overview_by[acct] : sel.overview) || []).map((o) => `<div class="wf-kpi extra${o.value ? '' : ' zero'}" title="${esc(o.hint || '')}"><b>${o.value == null ? '—' : esc(num(o.value))}${o.unit && o.value != null ? `<small>${esc(o.unit)}</small>` : ''}</b><span>${esc(o.label)}</span></div>`).join('');
  const n = sel.stages.length;
  const direct = n <= 1;
  const cols = [...sel.stages.map((st, i) => ({
    name: direct ? '进行中' : st.name, idx: direct ? '' : String(i + 1), role: direct ? '' : st.role, entry: st.entry, hint: st.hint || (direct ? '' : st.plain), col: i + 1,
  })), { name: '已完成', idx: '', role: '', entry: null, hint: sel.done_hint || '', col: n + 1, done: true }];
  const ENTRY = { executive: '要你确认才进', assistant: '主脑确认才进', producer: '子脑确认才进' };
  const widths = [];
  const board = cols.map((c) => {
    const items = shown.filter((s) => ((feats[s.key] || {}).col || (doneNow(s) ? n + 1 : (s.stage ? s.stage.id : 1))) === c.col)
      .sort((a, b) => (needOf(inboxOf(f.id, b.key)) ? 1 : 0) - (needOf(inboxOf(f.id, a.key)) ? 1 : 0) || ((feats[b.key] || {}).since || 0) - ((feats[a.key] || {}).since || 0));
    widths.push(items.length ? 'minmax(190px, 1fr)' : 'minmax(104px, .5fr)');     // 空列窄一点，整条流水线尽量一屏看完
    return `<section class="wf-col${c.done ? ' done' : ''}${items.length ? '' : ' vacant'}">
      <header class="wf-ch">
        <div class="wf-cn">${c.idx ? `<span class="wf-idx">${c.idx}</span>` : c.done ? icon('check-c', 14) : ''}<b>${esc(c.name)}</b><span class="wf-cc">${items.length}</span></div>
        <div class="wf-tags">${c.role ? `<span class="wf-role">${esc(c.role)}</span>` : ''}${c.entry && ENTRY[c.entry] ? `<span class="wf-gate${c.entry === 'executive' ? ' you' : ''}">${icon('hand', 11)}${ENTRY[c.entry]}</span>` : ''}</div>
        ${c.hint ? `<div class="wf-hint" title="这一步看什么">${esc(c.hint)}</div>` : ''}
      </header>
      <div class="wf-cards">${(A && !acct ? acctGroups(f, items, (s) => ({ account: accOf(s) })).filter(([, xs]) => xs.length)
    .map(([a, xs]) => `<div class="wf-acct">${acctDot(f, a.id)}<span>${esc(a.name)}</span><b>${xs.length}</b></div>${xs.map((s) => wfCard(f, s, feats[s.key], sel)).join('')}`).join('')
    : items.map((s) => wfCard(f, s, feats[s.key], sel)).join('')) || '<div class="wf-empty">—</div>'}</div></section>`;
  }).join('');
  const src = { project: '项目专用', mashu: '麻薯通用', feature: '功能自带' }[sel.source] || '';
  put(`${acctBar}<div class="wf-tabs">${tabs}<button class="wf-refresh" data-wf-refresh title="重新读取">${icon('refresh', 14)}</button></div>
    <div class="wf-head"><div class="wf-ht"><b>${esc(sel.name)}</b>${sel.version ? `<span class="tag">v${esc(sel.version)}</span>` : ''}${src ? `<span class="tag">${src}</span>` : ''}${sel.default ? '<span class="tag gold">项目默认</span>' : ''}</div>
      ${sel.summary ? `<div class="wf-sum">${esc(sel.summary)}</div>` : ''}
      ${sel.config_error ? `<div class="wf-err">${icon('alert', 13)}面板配置有误，专属字段先不显示：${esc(sel.config_error)}</div>` : ''}</div>
    <div class="wf-kpis">${kpis}</div>
    <div class="wf-board" style="grid-template-columns:${widths.join(' ')}">${board}</div>
    ${arch.length ? `<div class="wf-arch"><button class="wf-arch-h" data-wf-arch="${esc(sel.id)}">${icon(wfArch.has(sel.id) ? 'chev-d' : 'chev-r', 14)}已归档 ${arch.length}</button>
      ${wfArch.has(sel.id) ? `<div class="wf-arch-list">${arch.map((s) => wfCard(f, s, feats[s.key], sel, true)).join('')}</div>` : ''}</div>` : ''}
    ${all.length ? '' : acct ? `<div class="wf-none">「${esc(A.accounts.find((a) => a.id === acct).name)}」在这个工作流还没有功能。开功能时用 <code>--account ${esc(acct)}</code>，卡片就会出现在这里。</div>` : `<div class="wf-none">这个工作流还没有功能。${sel.default ? '在对话里让子脑开功能，' : `开功能时用 <code>--playbook ${esc(sel.id)}</code>，`}卡片就会出现在这里。</div>`}`);
}
$('#chatHead').addEventListener('click', (ev) => {
  const t = ev.target.closest('[data-projmode]');
  if (!t || !isFilmCh(current)) return;
  ev.stopPropagation();
  setProjMode(current, t.dataset.projmode);
});
$('#wfDash').addEventListener('click', (ev) => {
  if (ev.target.closest('a.wf-link')) { if (!ev.target.closest('[data-reveal]')) ev.stopPropagation(); return; }     // 打开文件 / 链接，不进功能对话（本机文件交给全局的「在访达中显示」）
  const t = ev.target.closest('[data-wf-tab],[data-wf-arch],[data-wf-refresh],[data-acct]');
  if (!t) return;
  ev.stopPropagation();
  if ('acct' in t.dataset) { if (t.dataset.acct) acctSel[current] = t.dataset.acct; else delete acctSel[current]; save('mashu.acct', acctSel); return renderDash(); }
  if (t.dataset.wfTab) { wfTab[current] = t.dataset.wfTab; save('mashu.wftab', wfTab); return renderDash(); }
  if (t.dataset.wfArch) { wfArch.has(t.dataset.wfArch) ? wfArch.delete(t.dataset.wfArch) : wfArch.add(t.dataset.wfArch); return renderDash(); }
  if ('wfRefresh' in t.dataset) return loadDash(current, true);
});

// ---------- 邮箱 ----------
// 信箱：我 / 主脑 / 各子脑。收件 = 发给它的上报、指示、转呈、动态；发件 = 它发出的、批复和转呈过的。待处理的可以直接在这里批复。
let mailBox = load('mashu.mailbox', 'executive');
let mailTab = load('mashu.mailtab', 'in');
let mailPendingOnly = false;
let mailBoxes = [];
let mailData = null;
const mailOpen = new Set();
function mailPending() { return mailBoxes.reduce((n, b) => n + (b.pending || 0), 0); }
async function loadMail() {
  if (loadMail.busy) { loadMail.again = true; return; }
  loadMail.busy = true;
  try {
    const [idx, d] = await Promise.all([api('/api/mail'), view === 'mail' ? api(`/api/mail?box=${encodeURIComponent(mailBox)}`) : null]);
    mailBoxes = idx.boxes;
    if (d) {
      for (const r of d.reports) reports[r.id] = r;
      mailData = d;
    }
  } catch (e) { console.error(e); }
  loadMail.busy = false;
  if (loadMail.again) { loadMail.again = false; return loadMail(); }
  renderTabs();
  if (view === 'mail') drawMail();
}
function renderMail() { if (mailData && mailData.box === mailBox) drawMail(); loadMail(); }
function mailRow(x) {
  const r = x.report_id ? reports[x.report_id] : null;
  const isOpen = mailOpen.has(x.key);
  const st = x.pending ? '<span class="pill st-answer">待处理</span>'
    : (r && r.status === 'open' ? `<span class="pill st-relay">在${esc(holderName(r))}手上</span>` : '');
  let body = '';
  if (isOpen) {
    if (r) {
      if (r.status === 'open') expanded.add(r.id);       // 邮箱里打开就能直接批复
      body = renderCard({ kind: 'card', report_id: r.id, ts: x.ts });
    } else {
      body = `<div class="card note"><div class="ct">${md(x.text || '')}</div>
        ${x.channel ? `<div class="chips"><button class="chip link" data-go="${esc(x.channel)}">去对话${icon('arrow-ur', 12)}</button></div>` : ''}</div>`;
    }
  }
  return `<div class="mail-item ${x.pending ? 'pending' : ''} ${isOpen ? 'open' : ''}">
    <div class="mail-row" data-mail="${esc(x.key)}">
      <span class="tag ${x.type === 'report' ? 'gold' : ''}">${esc(x.kind_label)}</span>
      <span class="mail-who">${esc(mailTab === 'in' ? x.frm : '→ ' + x.to)}</span>
      <span class="mail-subj">${esc(x.subject)}<span class="mail-where">${esc(x.where || '')}</span></span>
      ${st}<span class="time">${timeText(x.ts)}</span></div>
    ${body ? `<div class="mail-body">${body}</div>` : ''}</div>`;
}
function drawMail() {
  const el = $('#view-mail');
  const d = mailData && mailData.box === mailBox ? mailData : null;
  let list = d ? (mailTab === 'in' ? d.inbox : d.outbox) : [];
  if (mailPendingOnly) list = list.filter((x) => x.pending);
  const nIn = d ? d.inbox.filter((x) => x.pending).length : 0;
  const html = `<div class="mail">
    <div class="mail-boxes">${mailBoxes.map((b) => `<button class="mail-box ${b.id === mailBox ? 'active' : ''}" data-mbox="${esc(b.id)}">
      ${b.id === 'executive' ? '<span class="av sm me-av">我</span>' : (b.id === 'assistant' ? mashuAv('av sm') : mashuAv('av sm'))}
      <span class="mb-t">${esc(b.label)}<small>${esc(b.sub)}</small></span>${b.pending ? `<span class="badge gold">${b.pending}</span>` : ''}</button>`).join('')}</div>
    <div class="mail-main">
      <div class="mail-head"><b>${esc(d ? d.label : '')}</b>
        <div class="seg"><button class="${mailTab === 'in' ? 'active' : ''}" data-mtab="in">收件${nIn ? ` · ${nIn} 待处理` : ''}</button><button class="${mailTab === 'out' ? 'active' : ''}" data-mtab="out">发件</button></div>
        <label class="hint mail-only"><input type="checkbox" data-mpend ${mailPendingOnly ? 'checked' : ''}> 只看待处理</label></div>
      <div class="mail-list">${!d ? '<div class="empty">加载中…</div>' : (list.map(mailRow).join('') || `<div class="empty">${mailPendingOnly ? '没有待处理的' : '空的'}</div>`)}</div>
    </div></div>`;
  if (html === el._html) return;
  // 保留正在写的批复和焦点
  const keep = {};
  el.querySelectorAll('[data-reply-input],[data-reject-input],[data-confirm-input]').forEach((i) => { if (i.value) keep[i.outerHTML.match(/data-(reply|reject|confirm)-input="\d+"/)[0]] = i.value; });
  const opened = [...el.querySelectorAll('.acts.reject:not([hidden])')].map((x) => x.id);
  const act = document.activeElement;
  const actKey = act && el.contains(act) ? (act.outerHTML.match(/data-(reply|reject|confirm)-input="\d+"/) || [])[0] : null;
  const list0 = el.querySelector('.mail-list');
  const top = list0 ? list0.scrollTop : 0;
  el.innerHTML = html;
  el._html = html;
  for (const [k, v] of Object.entries(keep)) { const i = el.querySelector(`[${k}]`); if (i) i.value = v; }
  for (const id of opened) { const x = el.querySelector(`#${id}`); if (x) x.hidden = false; }
  if (actKey) el.querySelector(`[${actKey}]`)?.focus({ preventScroll: true });
  const list1 = el.querySelector('.mail-list');
  if (list1) list1.scrollTop = top;
}

// ---------- 会话监控 ----------
let wallTimer = null;
let zoomCam = null;
// 默认设置已移到模型池页；会话监控页只留一行跳转
const ROLE_CN = { assistant: '主脑', producer: '子脑', crew: '执行者' };
function defaultsHtml() {
  return `<div class="defaults-move card-lite"><span>${icon('gear', 15)}默认平台、模型、预设与模型列表刷新已移到 <b>模型池</b></span>
    <button class="btn small primary" data-view-go="accounts">前往模型池 →</button></div>`;
}
document.addEventListener('click', async (ev) => {
  const b = ev.target.closest('[data-gmrefresh],[data-ocrefresh]');
  if (!b) return;
  b.disabled = true;
  try {
    const d = await api('/api/camera', { action: b.dataset.ocrefresh !== undefined ? 'opencode-models' : 'gemini-models' });
    toast(d.result);
    await refresh();
    if (view === 'accounts') renderAccounts();
    if (view === 'wall') renderWall();
  } catch (e) { toast(e.message); } finally { b.disabled = false; }
});
// ---------- 模型池（#4 + #14）：预设管理、角色默认 + 各家账号与额度 ----------
let acctJob = loadJSON('mashu.acctjob', null);
       // 正在进行的网页登录：{ id, platform }
function cliAccts(prov) { return (state.cli_accounts || []).filter((a) => !prov || a.platform === prov); }
function acctName(a) { return a ? (a.label || a.email || a.id) + (a.kind === 'api' ? ' · API' : '') : ''; }
function acctQuota(a) {
  const q = a.quota || {};
  if (a.kind === 'api') return '<span>按量</span>';
  if (a.platform === 'gemini') {
    if (q.remaining == null) return '<span class="q-dim">额度未知</span>';
    const r = Math.round(q.remaining * 100), cls = r <= 10 ? 'hi' : r <= 30 ? 'mid' : '';
    const models = (q.models || []).map((m) => `${m.name} 剩 ${Math.round(m.remaining * 100)}%${m.reset ? `（${resetText(m.reset)}）` : ''}`).join('\n');
    return `<span class="q-item ${cls}" title="${esc(models || '')}">最低剩 <b>${r}%</b>${q.low_model ? ` · ${esc(q.low_model)}` : ''}</span>`;
  }
  const it = (label, w) => {
    if (!w || w.used_percentage == null) return '';
    const p = Math.round(Number(w.used_percentage)), cls = p >= 90 ? 'hi' : p >= 70 ? 'mid' : '';
    return `<span class="q-item ${cls}" title="已用 ${p}%${w.resets_at ? `，${resetText(w.resets_at)}` : ''}">${label} <b>${p}%</b></span>`;
  };
  const h = it('5h', q.five_hour) + it('周', q.seven_day);
  return h || '<span class="q-dim" title="用这个号跑过 Codex 会话后才有">等会话报上来</span>';
}
function acctRow(a) {
  const st = a.disabled ? 'off' : a.status;
  const tags = [a.default ? '<span class="ac-tag def">默认</span>' : '', a.active ? '<span class="ac-tag act" title="本机 agy 现在登录的是这个号（钥匙串）">本机当前</span>' : '',
    a.source === 'local' ? '<span class="ac-tag" title="本机自己的登录：Codex 和 ~/.codex 共用，不复制">本机</span>' : ''].join('');
  const btn = (act, label, title, cls = '') => `<button class="btn small ${cls}" data-acct-act="${act}" data-acct="${esc(a.id)}" title="${esc(title)}">${label}</button>`;
  return `<div class="ac-row ${a.disabled ? 'dim' : ''}">
    <span class="ac-id">${esc(a.id)}</span>
    <span class="ac-name" title="${esc(a.base_url || a.email || '')}">${esc(acctName(a))}${a.plan ? ` <i class="ac-plan">${esc(a.plan)}</i>` : ''}${tags}</span>
    <span class="ac-st acs-${esc(st || '')}" title="${esc(a.error || '')}">${esc(a.disabled ? '已禁用' : a.status_cn || a.status || '')}${a.error ? ' ⓘ' : ''}</span>
    <span class="ac-q">${acctQuota(a)}</span>
    <span class="ac-t" title="最近续期 / 体检">${a.last_refresh ? `续期 ${ago(a.last_refresh)}前` : ''}${a.last_check ? ` · 体检 ${ago(a.last_check)}前` : ''}</span>
    <span class="ac-acts">
      ${a.default || a.platform === 'gemini' ? '' : btn('default', '设为默认', '没指定号、额度又差不多时优先用它；左下角显示默认号的额度')}
      ${btn('check', '体检', a.platform === 'gemini' ? '用 RT 续期并查一次额度' : '读登录状态；池里的号超过 7 天没续期会用 RT 续一次')}
      ${a.platform === 'codex' && a.source !== 'local' && a.kind !== 'api' ? btn('relogin', '重新登录', '在这个号目录里重新跑 codex login（设备码）') : ''}
      ${a.platform === 'gemini' ? '' : btn(a.disabled ? 'enable' : 'disable', a.disabled ? '启用' : '禁用', a.disabled ? '重新参与自动选号' : '自动选号不再选它（指定了也会改为自动）')}
      ${btn('remove', '删除', a.source === 'local' && a.platform === 'codex' ? '只取消登记，不动 ~/.codex 里你自己的登录' : '删掉这个号的登录文件和记录', 'danger')}
    </span></div>`;
}
function acctJobHtml() {
  if (!acctJob || !acctJob.data) return '';
  const j = acctJob.data;
  const done = j.state === 'done', bad = j.state === 'failed' || j.state === 'cancelled';
  return `<div class="ac-job ${done ? 'ok' : bad ? 'bad' : ''}">
    <b>${j.platform === 'codex' ? 'Codex' : 'Gemini'} 登录</b>
    ${done ? `<span>${icon('check', 13)}已加好：${esc(j.result || '')}</span>` : bad ? `<span>${icon('x', 13)}${esc(j.error || '没完成')}</span>`
      : j.url ? `<span>在浏览器打开 <a href="${esc(j.url)}" target="_blank" rel="noopener">${esc(j.url.length > 60 ? j.url.slice(0, 60) + '…' : j.url)}</a>${j.code ? `，输入一次性码 <code class="ac-code">${esc(j.code)}</code>（15 分钟内有效）` : ''}，用要加的账号登录；完成后这里会自动变成「已加好」</span>`
      : '<span>正在准备登录链接…</span>'}
    ${done || bad ? '<button class="btn small" data-acct-jobclose>知道了</button>' : '<button class="btn small" data-acct-jobcancel>取消</button>'}
    ${j.output && !done && !j.url ? `<pre class="ac-out">${esc(j.output.slice(-600))}</pre>` : ''}</div>`;
}
// 账号池页（顶部「账号池」标签）：各家的账号、额度、加号 / 导入、体检和选号规则
function vendCard(id, title, sub, body, acts = '') {
  return `<section class="vd-card" id="vd-${id}"><div class="vd-h"><b>${title}</b>${sub ? `<span class="hint-inline">${sub}</span>` : ''}</div>${body}${acts ? `<div class="ac-adds">${acts}</div>` : ''}</section>`;
}
function quotaBars(rows) {
  return `<div class="vd-q">${rows.map(([label, pctUsed, tip]) => {
    if (pctUsed == null) return `<div class="vd-qr"><span>${esc(label)}</span><span class="q-dim vd-unk">${esc(tip || '未知')}</span></div>`;
    const p = Math.max(0, Math.min(100, Math.round(pctUsed))), cls = p >= 90 ? 'hi' : p >= 70 ? 'mid' : '';
    return `<div class="vd-qr" title="${esc(tip || '')}"><span>${esc(label)}</span><i class="vd-bar ${cls}"><i style="width:${p}%"></i></i><b class="${cls}">已用 ${p}%</b><span class="q-dim">${esc(tip || '')}</span></div>`;
  }).join('')}</div>`;
}
function presetsSectionHtml() {
  const ps = presetsList();
  const rp = state.role_presets || {};
  const byId = Object.fromEntries(ps.map((p) => [p.id, p]));
  const rolesOf = (pid) => Object.keys(ROLE_CN).filter((r) => rp[r] === pid).map((r) => ROLE_CN[r]);
  const rows = ps.map((pr, idx) => {
    const rs = rolesOf(pr.id);
    const fb = pr.fallback ? byId[pr.fallback] : null;
    const pl = platOf(pr.cli);
    return `<div class="pr-row">
      <div class="pr-ord">
        <button class="btn small ghost" data-preset-move="up" data-preset-id="${esc(pr.id)}" ${idx === 0 ? 'disabled' : ''} title="上移">↑</button>
        <button class="btn small ghost" data-preset-move="down" data-preset-id="${esc(pr.id)}" ${idx === ps.length - 1 ? 'disabled' : ''} title="下移">↓</button>
      </div>
      <div class="pr-main">
        <div class="pr-name"><b>${esc(pr.name)}</b><span class="ac-id">${esc(pr.id)}</span>${!pl.ok ? `<span class="ac-tag bad" title="${esc(pl.reason || '')}">CLI 不可用</span>` : ''}${rs.map((r) => `<span class="ac-tag def">${esc(r)}默认</span>`).join('')}</div>
        <div class="pr-meta"><span>${esc(presetSummary(pr))}</span>${fb ? `<span class="pr-fb">备选 → <b>${esc(fb.name)}</b></span>` : ''}</div>
      </div>
      <div class="pr-acts">
        <button class="btn small" data-preset-edit="${esc(pr.id)}">编辑</button>
        <button class="btn small danger" data-preset-del="${esc(pr.id)}">删除</button>
      </div>
    </div>`;
  }).join('');
  return `<section class="vd-card pr-card" id="vd-presets">
    <div class="vd-h pr-head">
      <div><b>预设</b><span class="hint-inline">把 CLI + 模型 + 思考 + 账号 + Code Mode 打包成一个预设，在 ⚙ / 新会话 / 新功能 / 开工卡里一键选用；可指定备选预设在异常或额度不足时自动切换</span></div>
      <button class="btn small primary" data-preset-new>${icon('plus', 13)}新建预设</button>
    </div>
    <div class="pr-list">${rows || '<div class="hint">还没有预设：点右上角「新建预设」创建一个</div>'}</div>
  </section>`;
}
function roleDefaultsSectionHtml() {
  const ps = presetsList();
  const rp = state.role_presets || {};
  const oc = platOf('opencode');
  const nOver = Object.keys(state.prefs || {}).filter((k) => Object.keys(state.prefs[k] || {}).length).length;
  const pctLim = state.preset_fallback_pct ?? 95;
  const roleSel = (role) => `<label class="rp-item"><span>${ROLE_CN[role]}</span><select data-role-preset="${role}">
    <option value="">（CLI 默认）</option>
    ${ps.map((p) => `<option value="${esc(p.id)}" ${rp[role] === p.id ? 'selected' : ''}>${esc(p.name)}（${esc(PROV_CN[p.cli] || p.cli)}${p.model ? ` · ${esc(p.model)}` : ''}）</option>`).join('')}
  </select></label>`;
  return `<section class="vd-card rp-card" id="vd-roles">
    <div class="vd-h"><b>角色默认</b><span class="hint-inline">各角色默认使用的预设；对话里单独改过的优先。开新会话时 CLI 不可用、号异常或额度 ≥ ${esc(String(pctLim))}% 自动切换备选预设</span></div>
    <div class="rp-grid">${Object.keys(ROLE_CN).map(roleSel).join('')}</div>
    <div class="df-acts">
      <label><input type="checkbox" id="dfClear"> 同时清除各对话单独设置${nOver ? `（现有 ${nOver} 个）` : ''}</label>
      <button class="btn primary" data-rolesave>${icon('check', 14)}保存并切换</button>
      <span class="hint-inline">空闲会话会在原窗格重开（对话不丢），忙的这一轮做完再切</span>
    </div>
    <div class="rp-refresh hint">
      模型列表：
      <button class="btn small" data-gmrefresh>${icon('swap', 13)}刷新 Gemini 模型列表</button>（${state.gemini_models_cached ? '已缓存 agy models' : '内置兜底列表'}）
      <button class="btn small" data-ocrefresh>${icon('swap', 13)}刷新 OpenCode 模型列表</button>（${(oc.models || []).length} 个）
    </div>
  </section>`;
}
function openPresetModal(editId = null) {
  const ps = presetsList();
  const cur = editId ? presetById(editId) : null;
  const pr = cur || { id: '', name: '', cli: 'claude', model: '', effort: '', account: '', code_mode: false, fallback: '' };
  const fbOpts = `<option value="">无备选</option>` + ps.filter((x) => x.id !== pr.id).map((x) => `<option value="${esc(x.id)}" ${x.id === pr.fallback ? 'selected' : ''}>${esc(x.name)}（${esc(PROV_CN[x.cli] || x.cli)}${x.model ? ` · ${esc(x.model)}` : ''}）</option>`).join('');
  openModal(`<h2>${cur ? '编辑预设' : '新建预设'}</h2><div class="form api-form">
    <label>预设名称<input id="prName" value="${esc(pr.name)}" placeholder="例如 Claude Opus · high 或 Codex 主力" autocomplete="off"></label>
    <label>CLI<select id="prCli">${provOpts(pr.cli)}</select></label>
    <label>模型<select id="prModel">${kickModelOpts(pr.cli, pr.model, pr.account)}</select></label>
    <label>思考程度<select id="prEffort"><option value="">默认</option>${(state.effort_choices || []).map((x) => `<option value="${esc(x)}" ${x === pr.effort ? 'selected' : ''}>${esc(x)}</option>`).join('')}</select></label>
    <label id="prAcctWrap" ${ACCT_PREF[pr.cli] && cliAccts(pr.cli).length ? '' : 'hidden'}>账号<select id="prAcct">${acctOpts(pr.cli, pr.account)}</select></label>
    <label id="prCodeWrap" ${pr.cli === 'claude' ? '' : 'hidden'}>Code Mode<select id="prCode"><option value="off" ${!pr.code_mode ? 'selected' : ''}>关闭</option><option value="on" ${pr.code_mode ? 'selected' : ''}>开启（开了用不了 Chrome 浏览器）</option></select></label>
    <label>备选预设<select id="prFallback">${fbOpts}</select></label>
    <p class="hint">CLI 不可用、指定号异常、或已知额度已用 ≥ ${esc(String(state.preset_fallback_pct ?? 95))}% 时，开新会话自动换备选（最多 3 跳，不改对话偏好）。</p>
    <div class="api-actions"><button class="btn" data-close>取消</button><button class="btn primary" id="prSave">保存</button></div>
  </div>`);
  $('#modalBox').classList.add('api-dialog');
  const syncCli = () => {
    const cli = $('#prCli').value;
    const hasAcct = !!(ACCT_PREF[cli] && cliAccts(cli).length);
    $('#prAcctWrap').hidden = !hasAcct;
    if (hasAcct) $('#prAcct').innerHTML = acctOpts(cli, '');
    $('#prModel').innerHTML = kickModelOpts(cli, '', hasAcct ? $('#prAcct').value : '');
    $('#prCodeWrap').hidden = cli !== 'claude';
    if (cli !== 'claude') $('#prCode').value = 'off';
  };
  $('#prCli').onchange = syncCli;
  $('#prAcct').onchange = () => {
    const cli = $('#prCli').value, aid = $('#prAcct').value, m = $('#prModel');
    m.innerHTML = kickModelOpts(cli, modelsFor(cli, aid).includes(m.value) ? m.value : '', aid);
  };
  $('#prSave').onclick = async () => {
    const btn = $('#prSave'); btn.disabled = true;
    const cli = $('#prCli').value;
    try {
      const d = await api('/api/presets', {
        action: 'save',
        id: pr.id || '',
        name: $('#prName').value.trim(),
        cli,
        model: $('#prModel').value,
        effort: $('#prEffort').value,
        account: !$('#prAcctWrap').hidden ? $('#prAcct').value : '',
        code_mode: cli === 'claude' && $('#prCode').value === 'on',
        fallback: $('#prFallback').value,
      });
      closeModal();
      if (d.result) toast(d.result);
      await acctRefresh();
    } catch (e) { toast(e.message); } finally { btn.disabled = false; }
  };
  $('#prName').focus();
}
function acctPageHtml() {
  const L = state.limits || {}, V = state.vendors || {}, cl = L.claude || {}, oc = L.opencode || {};
  const ce = V.claude || {};
  const rst = (w) => (w && w.resets_at ? resetText(w.resets_at) : '');
  const claude = vendCard('claude', 'Claude', '用本机 claude 的登录（Claude 订阅）；麻薯不管 Claude 账号，要换号在终端里运行 claude 再 /login',
    `<div class="ac-row one"><span class="ac-id">本机</span><span class="ac-name">${esc(ce.email || '（没读到登录信息）')}${ce.org_type ? ` <i class="ac-plan">${esc(ce.org_type.replace(/^claude_/, ''))}</i>` : ''}</span>
      <span class="ac-st acs-ok">${ce.email ? '已登录' : ''}</span></div>
     ${quotaBars([['5 小时', cl.five_hour ? cl.five_hour.used_percentage : null, cl.five_hour ? rst(cl.five_hour) : '等受管会话的状态栏报上来'],
                  ['本周', cl.seven_day ? cl.seven_day.used_percentage : null, cl.seven_day ? rst(cl.seven_day) : '等受管会话的状态栏报上来']])}`);
  const cx = cliAccts('codex');
  const codex = vendCard('codex', 'Codex', '每个号一个独立的 CODEX_HOME，几个执行者可以同时用不同的号；本机号和 ~/.codex 共用登录（不复制，避免 RT 轮换掉登录）',
    (cx.length ? cx.map(acctRow).join('') : '<div class="hint">还没有 Codex 号：先「登记本机登录」，再加别的号</div>')
    + cx.filter((a) => a.quota && a.kind !== 'api').map((a) => `<div class="vd-sub">${esc(acctName(a))}</div>` + quotaBars([
      ['5 小时', a.quota.five_hour ? a.quota.five_hour.used_percentage : null, rst(a.quota.five_hour)],
      ['本周', a.quota.seven_day ? a.quota.seven_day.used_percentage : null, rst(a.quota.seven_day)]])).join(''),
    `<button class="btn small primary" data-acct-add="codex-api">${icon('plus', 13)}加 API 号</button>
     <button class="btn small primary" data-acct-add="codex-device" title="打开 OpenAI 的设备登录页，输入一次性码（码在点了以后才生成，15 分钟有效）">${icon('plus', 13)}加号（设备码）</button>
     <button class="btn small" data-acct-add="codex-browser" title="在本机浏览器里打开 OpenAI 登录页（codex login）">${icon('plus', 13)}加号（本机浏览器）</button>
     <button class="btn small" data-acct-add="codex-local" title="把 ~/.codex 里你自己的登录登记成默认号（只登记，不复制）">登记本机登录</button>`);
  const gm = cliAccts('gemini'), g = gm[0], gq = (g && g.quota) || {};
  const gemini = vendCard('gemini', 'Gemini（agy）', '只管本机 agy 当前登录的这一个号：后台用 RT 续期、查额度；不加第二个号、不切号（Google 在封第三方工具走 Antigravity 登录、多号轮换的号）。要换号就自己在终端运行 agy 重新登录，麻薯会自动跟上',
    (g ? acctRow(g) : '<div class="hint">还没登记：点「登记 / 同步本机 agy 登录」</div>')
    + ((gq.models || []).length ? `<details class="vd-models"><summary>各模型剩余额度（${gq.models.length} 个，最低的是 ${esc(gq.low_model || '')}）</summary>${quotaBars(gq.models.map((m) => [m.name, (1 - m.remaining) * 100, m.reset ? resetText(m.reset) : '']))}</details>` : ''),
    `<button class="btn small" data-acct-add="gemini-local" title="把本机 agy 现在的登录（钥匙串）登记进来；已经登记过的会换成现在这个号">登记 / 同步本机 agy 登录</button>`);
  const opencode = vendCard('opencode', 'OpenCode', '按量付费，没有账号额度；提供方和密钥在 OpenCode 自己的配置里',
    oc.today != null || oc.week != null ? `<div class="vd-q"><div class="vd-qr"><span>今日花费</span><b class="vd-unk">$${Number(oc.today || 0).toFixed(2)}</b></div><div class="vd-qr"><span>本周花费</span><b class="vd-unk">$${Number(oc.week || 0).toFixed(2)}</b></div></div>`
      : `<div class="hint">${V.opencode && V.opencode.installed === false ? '没装 OpenCode' : '还没有花费数据'}</div>`);
  const rules = vendCard('rules', '预设、选号与体检', '',
    `<ul class="vd-rules"><li><b>预设与备选</b>：优先级为「对话单改字段 &gt; 对话选的预设 &gt; 功能开工配置 &gt; 角色默认预设 &gt; CLI 默认」。开新会话时若 CLI 不可用、指定号异常或已知额度 ≥ ${esc(String(state.preset_fallback_pct ?? 95))}%，自动按备选链切换（最多 3 跳，不改对话偏好，恢复会话不换）。</li>
      <li><b>Codex 选号</b>：预设/对话指定的号 &gt; 自动（状态正常、没禁用、额度剩得最多；一样多时默认号优先）。API 号仅手动选择或设为默认时使用。只在新会话开工时选，会话中途不换号。</li>
      <li><b>体检</b>：后台每 10 分钟（Gemini）/ 30 分钟（Codex）一次：Gemini 快过期前用 RT 续期并查额度；Codex 读登录状态，麻薯自己持有的号超过 7 天没续期就续一次（本机号让 Codex 自己续）。</li>
      <li><b>凭据</b>：统一存在 <code>state/studio.json</code>（0600）与各号运行时目录里，页面和日志里不显示。</li></ul>`,
    `<button class="btn small" data-acct-checkall title="马上给所有号体检一次（续期 + 查额度）">${icon('refresh', 13)}全部体检</button>`);
  return `<div id="acctPage"><h2>模型池</h2><p class="hint">管理模型预设、各角色默认预设，以及各家 CLI 的账号与额度。</p>
    ${presetsSectionHtml()}
    ${roleDefaultsSectionHtml()}
    <div id="acctJob">${acctJobHtml()}</div>
    <div class="vd-grid">${claude}${codex}${gemini}${opencode}</div>${rules}</div>`;
}
let acctPageLast = '';
function renderAccounts() {
  const el = $('#view-accounts');
  if (!el) return;
  acctPageLast = acctPageHtml();
  el.innerHTML = acctPageLast;
}
async function acctApi(body) {
  const d = await api('/api/accounts', body);
  if (d.result) toast(d.result);
  return d;
}
async function acctRefresh() {
  await refresh();
  if (view === 'accounts') renderAccounts();
}
async function acctPoll() {
  if (!acctJob) return;
  try {
    const d = await api('/api/accounts', { action: 'job', job: acctJob.id });
    acctJob.data = d.job;
  } catch (e) { acctJob.data = { platform: acctJob.platform, state: 'failed', error: e.message }; }
  localStorage.setItem('mashu.acctjob', JSON.stringify({ id: acctJob.id, platform: acctJob.platform }));
  const box = $('#acctJob');
  if (box) box.innerHTML = acctJobHtml();
  if (['done', 'failed', 'cancelled'].includes(acctJob.data.state)) { localStorage.removeItem('mashu.acctjob'); if (acctJob.data.state === 'done') acctRefresh(); return; }
  setTimeout(acctPoll, 2000);
}
if (acctJob) setTimeout(acctPoll, 1500);
async function acctStartJob(body, platform) {
  const d = await acctApi(body);
  acctJob = { id: d.job, platform, data: { platform, state: 'starting' } };
  const box = $('#acctJob');
  if (box) box.innerHTML = acctJobHtml();
  setTimeout(acctPoll, 800);
}
document.addEventListener('click', async (ev) => {
  const pn = ev.target.closest('[data-preset-new]');
  if (pn) return openPresetModal(null);
  const pe = ev.target.closest('[data-preset-edit]');
  if (pe) return openPresetModal(pe.dataset.presetEdit);
  const pm = ev.target.closest('[data-preset-move]');
  if (pm) {
    pm.disabled = true;
    try { const d = await api('/api/presets', { action: 'move', id: pm.dataset.presetId, direction: pm.dataset.presetMove }); if (d.result) toast(d.result); await acctRefresh(); }
    catch (e) { toast(e.message); } finally { pm.disabled = false; }
    return;
  }
  const pd = ev.target.closest('[data-preset-del]');
  if (pd) {
    const pid = pd.dataset.presetDel, pr = presetById(pid);
    if (!pr) return;
    const refs = [];
    for (const r of Object.keys(ROLE_CN)) if ((state.role_presets || {})[r] === pid) refs.push(`${ROLE_CN[r]}默认`);
    for (const x of presetsList()) if (x.fallback === pid) refs.push(`预设「${x.name}」的备选`);
    const msg = refs.length ? `确定删除预设「${pr.name}」？\n\n以下引用会一并解除：${refs.join('、')}` : `确定删除预设「${pr.name}」？`;
    if (!window.confirm(msg)) return;
    pd.disabled = true;
    try { const d = await api('/api/presets', { action: 'delete', id: pid }); if (d.result) toast(d.result); await acctRefresh(); }
    catch (e) { toast(e.message); } finally { pd.disabled = false; }
    return;
  }
  const rs = ev.target.closest('[data-rolesave]');
  if (rs) {
    const role_presets = {};
    document.querySelectorAll('select[data-role-preset]').forEach((x) => { role_presets[x.dataset.rolePreset] = x.value; });
    rs.disabled = true;
    try {
      const d = await api('/api/presets', { action: 'set-roles', role_presets, clear: !!$('#dfClear')?.checked });
      if (d.result) toast(d.result);
      await acctRefresh();
    } catch (e) { toast(e.message); } finally { rs.disabled = false; }
    return;
  }
});
document.addEventListener('click', async (ev) => {
  const add = ev.target.closest('[data-acct-add]'), act = ev.target.closest('[data-acct-act]');
  const jc = ev.target.closest('[data-acct-jobcancel]'), jx = ev.target.closest('[data-acct-jobclose]'), ca = ev.target.closest('[data-acct-checkall]');
  if (!add && !act && !jc && !jx && !ca) return;
  const b = add || act || jc || jx || ca;
  if (jx) { acctJob = null; localStorage.removeItem('mashu.acctjob'); $('#acctJob').innerHTML = ''; return; }
  b.disabled = true;
  try {
    if (jc) { await acctApi({ action: 'cancel', job: acctJob && acctJob.id }); return; }
    if (ca) { b.textContent = '体检中…'; await acctApi({ action: 'check-all' }); await acctRefresh(); return; }
    if (add) {
      const k = add.dataset.acctAdd;
      if (k === 'codex-api') {
        openModal(`<h2>加 Codex API 号</h2><div class="form api-form"><label>名称<input id="apiName" autocomplete="off"></label><label>请求地址<input id="apiBase" placeholder="https://example.com/v1" autocomplete="off"></label><label>API key<input id="apiKey" type="password" autocomplete="off"></label><p class="hint">保存前会检查模型列表；使用此号按量计费。</p><div class="api-actions"><button class="btn" data-close>取消</button><button class="btn primary" id="apiSave">测试并保存</button></div></div>`);
        $('#modalBox').classList.add('api-dialog');
        $('#apiSave').onclick = async () => {
          const button = $('#apiSave'); button.disabled = true;
          try { await acctApi({ action: 'add-codex-api', name: $('#apiName').value, base_url: $('#apiBase').value, api_key: $('#apiKey').value }); closeModal(); await acctRefresh(); }
          catch (e) { toast(e.message); } finally { button.disabled = false; }
        };
        return;
      }
      if (k === 'codex-local' || k === 'gemini-local') { await acctApi({ action: 'import-local', platform: k.split('-')[0] }); await acctRefresh(); return; }
      if (k === 'codex-device' || k === 'codex-browser') return await acctStartJob({ action: 'add-codex', device: k === 'codex-device' }, 'codex');
    }
    if (act) {
      const a = act.dataset.acctAct, id = act.dataset.acct;
      if (a === 'remove' && !b.dataset.sure) { b.dataset.sure = '1'; b.textContent = '再点一次确认删除'; return; }
      if (a === 'relogin') return await acctStartJob({ action: 'relogin', id }, cliAccts().find((x) => x.id === id)?.platform || 'codex');
      if (a === 'check') b.textContent = '体检中…';
      await acctApi({ action: a, id });
      await acctRefresh();
    }
  } catch (e) { toast(e.message); } finally { b.disabled = false; }
});
// 对话 ⚙ / 开工卡里的「账号」下拉：只在这个平台的池里有号时出现
function acctOpts(prov, cur) {
  const list = cliAccts(prov);
  return `<option value="">自动选号</option>` + list.map((a) => `<option value="${esc(a.id)}" ${a.id === cur ? 'selected' : ''} ${a.disabled || a.status !== 'ok' ? 'disabled' : ''}>${esc(acctName(a))}${a.disabled ? '（已禁用）' : a.status !== 'ok' ? `（${esc(a.status_cn)}）` : ''}</option>`).join('');
}
function renderWall() {
  const el = $('#view-wall');
  const cams = state.cameras.filter((c) => c.state !== 'queued');
  el.innerHTML = `<div class="wall-head"><h2>会话监控</h2>
    <div class="cfg-acts">
      <button class="btn small" data-cfg-export title="导出 state/studio.json（可选是否带密钥）">${icon('arrow-d', 13)}导出配置</button>
      <button class="btn small" data-cfg-import title="导入配置包（先显示差异，确认后备份并写入）">${icon('upload', 13)}导入配置</button>
    </div></div>${defaultsHtml()}
    <p class="hint">所有在运行会话的实时终端，2 秒刷新。要接管：终端里 <code>tmux attach -t ${esc(state.tmux)}</code>，麻薯在 <code>desk</code> 窗口，每个项目一个 <code>film-&lt;id&gt;</code> 窗口（Ctrl-b w 选窗口，Ctrl-b z 放大窗格，Ctrl-b d 退出不停止）。</p>
    <div class="wall">${cams.map((c) => `<div class="tile"><div class="th" data-zoom="${c.id}">
      ${c.role === 'assistant' ? mashuAv('av sm', camLight(c)) : letterAv(c.name, 'av sm', camLight(c))}
      <span class="meta">${esc(c.title)}</span><span class="tag">${pct(c.context_pct)}</span></div>
      <pre class="screen" id="scr-${c.id}"></pre></div>`).join('') || '<div class="empty">没有在运行的会话</div>'}</div>`;
  renderWall.ready = !!state.platforms;
  refreshScreens();
}

function openExportModal() {
  openModal(`<h2>导出主配置</h2>
    <p class="hint">将 <code>state/studio.json</code> 里的设置、角色默认、账号清单、provider 与网页口令打包导出。</p>
    <div class="form cfg-form">
      <label class="cfg-chk"><input type="checkbox" id="cfgExpSecrets">
        <span><b>带密钥导出（口令加密）</b><small>包含账号凭据、API key、网页口令与本机 CLI 登录；不勾选则密钥一律替换成「已省略」，可直接发给同事。</small></span>
      </label>
      <div id="cfgExpSecretBox" hidden class="cfg-sec-box">
        <input type="password" id="cfgExpPass1" placeholder="设置加密口令（不进日志）" autocomplete="new-password">
        <input type="password" id="cfgExpPass2" placeholder="再输入一次确认口令" autocomplete="new-password">
        <div class="cfg-warn">${icon('alert', 14)}<span>Codex / Gemini 的 refresh_token 会轮换，导入新电脑后旧电脑上这些号要停用，否则两边会互相顶掉登录；导入新电脑后账号默认标为「已禁用」。</span></div>
      </div>
      <div class="cfg-m-acts"><button class="btn" data-close>取消</button><button class="btn primary" id="cfgExpGo">${icon('arrow-d', 14)}导出并下载</button></div>
    </div>`);
  const chk = $('#cfgExpSecrets'), box = $('#cfgExpSecretBox');
  chk.onchange = () => { box.hidden = !chk.checked; };
  $('#cfgExpGo').onclick = async () => {
    const ws = chk.checked, p1 = $('#cfgExpPass1').value, p2 = $('#cfgExpPass2').value;
    if (ws) {
      if (!p1) return toast('请输入加密口令');
      if (p1 !== p2) return toast('两次输入的口令不一致');
    }
    const btn = $('#cfgExpGo');
    btn.disabled = true;
    try {
      const d = await api('/api/config/export', { with_secrets: ws, passphrase: ws ? p1 : '' });
      const blob = new Blob([JSON.stringify(d.bundle, null, 2) + '\n'], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = d.filename || 'mashu-config.json';
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
      closeModal();
      toast(ws ? '已加密导出（导入新电脑后记得停用旧电脑上的同号）' : '已导出配置（密钥已省略）');
    } catch (e) { toast(e.message); } finally { btn.disabled = false; }
  };
}

function openImportModal() {
  openModal(`<h2>导入主配置</h2>
    <p class="hint">先选择配置包并预览差异，确认后再备份当前 <code>state/studio.json</code> 并写入。「已省略」的项不会覆盖本机已有值。</p>
    <div class="form cfg-form">
      <input type="file" id="cfgImpFile" accept=".json,application/json">
      <div id="cfgImpFileInfo" class="hint" hidden></div>
      <div id="cfgImpPassBox" hidden><input type="password" id="cfgImpPass" placeholder="输入解密口令" autocomplete="current-password"></div>
      <div id="cfgImpPreview" class="cfg-diff" hidden></div>
      <div class="cfg-m-acts">
        <button class="btn" data-close>取消</button>
        <button class="btn" id="cfgImpCheck" disabled>预览差异</button>
        <button class="btn primary" id="cfgImpApply" hidden>${icon('check', 14)}确认导入</button>
      </div>
    </div>`);
  $('#modalBox').classList.add('wide');
  let parsed = null;
  const resetPreview = () => { $('#cfgImpPreview').hidden = true; $('#cfgImpPreview').innerHTML = ''; $('#cfgImpApply').hidden = true; };
  $('#cfgImpPass').oninput = resetPreview;
  $('#cfgImpFile').onchange = async (ev) => {
    resetPreview();
    const f = ev.target.files && ev.target.files[0];
    if (!f) { parsed = null; $('#cfgImpCheck').disabled = true; $('#cfgImpFileInfo').hidden = true; return; }
    try {
      parsed = JSON.parse(await f.text());
    } catch {
      parsed = null;
      $('#cfgImpCheck').disabled = true;
      return toast('不是合法的 JSON 文件');
    }
    const enc = !!(parsed && parsed.encrypted);
    $('#cfgImpFileInfo').hidden = false;
    $('#cfgImpFileInfo').textContent = `${f.name} · ${enc ? '加密配置包（需口令解密）' : '明文配置包'}`;
    $('#cfgImpPassBox').hidden = !enc;
    $('#cfgImpCheck').disabled = false;
  };
  $('#cfgImpCheck').onclick = async () => {
    if (!parsed) return;
    const pw = parsed.encrypted ? $('#cfgImpPass').value : '';
    if (parsed.encrypted && !pw) return toast('请输入解密口令');
    const btn = $('#cfgImpCheck');
    btn.disabled = true;
    try {
      const d = await api('/api/config/import', { bundle: parsed, passphrase: pw, dry_run: true });
      const p = d.preview || {}, ch = p.changes || [], c = p.counts || {};
      const badge = { add: ['新增', 'ok'], update: ['覆盖', 'warn'], skip: ['跳过', 'dim'], write: ['本机CLI', 'warn'] };
      const rows = ch.map((x) => {
        const [lbl, cls] = badge[x.action] || [x.action, ''];
        return `<div class="cfg-diff-row"><span class="tag ${cls}">${esc(lbl)}</span><span class="cfg-diff-sec">${esc(x.section)}</span><span>${esc(x.summary)}</span></div>`;
      }).join('') || '<div class="hint">和当前配置一致，没有需要改动的内容。</div>';
      const warns = (p.warnings || []).map((w) => `<div class="cfg-warn">${icon('alert', 14)}<span>${esc(w)}</span></div>`).join('');
      $('#cfgImpPreview').innerHTML = `<div class="cfg-diff-sum">差异汇总：新增 ${c.add || 0} · 覆盖 ${c.update || 0} · 跳过 ${c.skip || 0}${c.write ? ` · 本机CLI ${c.write}` : ''}</div><div class="cfg-diff-list">${rows}</div>${warns}`;
      $('#cfgImpPreview').hidden = false;
      $('#cfgImpApply').hidden = false;
    } catch (e) { toast(e.message); } finally { btn.disabled = false; }
  };
  $('#cfgImpApply').onclick = async () => {
    if (!parsed) return;
    const pw = parsed.encrypted ? $('#cfgImpPass').value : '';
    const btn = $('#cfgImpApply');
    btn.disabled = true;
    try {
      const res = await api('/api/config/import', { bundle: parsed, passphrase: pw, confirm: true });
      const c = (res.preview && res.preview.counts) || {};
      closeModal();
      toast(`已导入主配置（新增 ${c.add || 0}，覆盖 ${c.update || 0}，写入前已备份）`);
      await refresh();
      renderWall();
    } catch (e) { toast(e.message); } finally { btn.disabled = false; }
  };
}
document.addEventListener('click', (ev) => {
  if (ev.target.closest('[data-cfg-export]')) return openExportModal();
  if (ev.target.closest('[data-cfg-import]')) return openImportModal();
});
async function refreshScreens() {
  clearTimeout(wallTimer);
  if (view !== 'wall' && !zoomCam) return;
  const ids = zoomCam ? [zoomCam] : state.cameras.filter((c) => c.state !== 'queued').map((c) => c.id);
  await Promise.all(ids.map(async (id) => {
    try {
      const d = await api(`/api/screen?camera=${id}`);
      const el = document.getElementById(zoomCam ? 'zoomScreen' : `scr-${id}`);
      if (el) { el.innerHTML = d.alive ? d.html : '<span style="color:#777">（会话已关闭）</span>'; if (!zoomCam) el.scrollTop = el.scrollHeight; }
    } catch { /* 忽略 */ }
  }));
  wallTimer = setTimeout(refreshScreens, 2000);
}
function zoom(id) {
  const c = state.cameras.find((x) => x.id === Number(id));
  if (!c) return;
  zoomCam = c.id;
  openModal(`<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px"><b style="flex:1">${esc(c.title)}</b>
    <code>tmux attach -t ${esc(state.tmux)}</code><button class="btn" data-close>关闭</button></div><pre class="screen" id="zoomScreen"></pre>`);
  refreshScreens();
}

// ---------- 决策记录 ----------
async function renderDecisions() {
  const el = $('#view-decisions');
  let list = [];
  try { list = (await api('/api/decisions')).decisions; } catch (e) { toast(e.message); }
  const shown = decisionFilter === '全部' ? list : list.filter((d) => d.status === decisionFilter);
  el.innerHTML = `<h2>决策记录</h2><p class="hint">主脑和子脑自己拍板的事：日常决定直接「已执行」，觉得不对随时推翻；麻薯拿不准的才挂「待追认」。推翻 = 告诉麻薯安排补救（不会自动回滚）。</p>
    <div class="seg">${['待追认', '已执行', '已推翻', '已追认', '全部'].map((x) => `<button class="${x === decisionFilter ? 'active' : ''}" data-dfilter="${x}">${x}</button>`).join('')}</div>
    ${shown.map((d) => {
      const f = filmOf(d.film_id);
      return `<div class="dec"><div class="dh"><span class="tag ${d.status === '待追认' ? 'gold' : ''}">${esc(d.status)}</span>
        <b>${d.actor === 'assistant' ? '麻薯 · 主脑' : (d.actor === 'producer' ? '麻薯 · 子脑' : esc(d.actor_name))}</b>
        <span>${f ? esc(f.title) : ''}${d.seq_key ? ` #${esc(d.seq_key.split('-')[0])}` : ''}</span><span style="margin-left:auto">${timeText(d.ts)}</span></div>
        <div class="dt">${md(d.summary)}</div>${d.why ? `<div class="hint" style="margin:0">理由：${esc(d.why)}</div>` : ''}
        ${d.review_note ? `<div class="hint" style="margin:4px 0 0">我：${esc(d.review_note)}</div>` : ''}
        ${['待追认', '已执行'].includes(d.status) ? `<div class="acts" style="display:flex;gap:8px;margin-top:8px">
          ${d.status === '待追认' ? `<button class="btn primary" data-decide="${d.id}" data-status="已追认">追认</button>` : ''}
          <input class="dnote" data-dnote="${d.id}" placeholder="推翻的原因（可选）">
          <button class="btn danger" data-decide="${d.id}" data-status="已推翻">推翻</button></div>` : ''}</div>`;
    }).join('') || '<div class="empty">没有记录</div>'}`;
}

// ---------- 资料库 ----------
let libSel = loadJSON('mashu.lib', null);
let libOpen = loadJSON('mashu.libOpen', {});     // 资料库各分区（麻薯 / 各项目）展开状态
let libData = null;
const ENTRY_LABEL = { producer: '子脑批准', assistant: '主脑批准', executive: '麻薯批准' };
function fileLabel(path) {
  const map = { 'README.md': '项目说明', 'playbook.json': '工作流', 'rules.md': '项目规则', 'producer-notes.md': '项目笔记',
    'check.sh': '检查脚本', 'hotspots.txt': '热点文件' };
  const parts = path.split('/');
  if ((parts[0] === 'features' || parts[0] === 'scenes') && parts.length > 2) {
    const docs = { 'state.json': '功能状态', 'sequence.json': '功能状态', 'acceptance.json': '验收标准', 'requirement.md': '需求',
      'design.md': '设计', 'review.md': '审查记录', 'release.md': '发布计划' };
    return `#${parts[1].split('-')[0]} · ${docs[parts[parts.length - 1]] || parts.slice(2).join('/')}`;
  }
  return map[path] || path.replace(/\.(md|json)$/, '');
}
// 行为模板按归属分：'' = 麻薯通用（playbooks/behaviors/），项目 id = 该项目 .mashu/playbooks/behaviors/
function libScope(root) { return /^(film|filmpb|filmbh):/.test(root || '') ? root.split(':')[1] : ''; }
function bhRoot(scope) { return scope ? `filmbh:${scope}` : 'behaviors'; }
function behaviorName(id, scope = '') {
  const b = libData && libData.behaviors && (libData.behaviors[scope] || {})[id];
  return (b ? b.title : ((state.behaviors || {})[scope] || {})[id]) || id;
}
function behaviorLinks(v, scope = '') {      // 阶段的 behavior 字段（一个 id 或列表）→ 跳到资料库里该项目「行为模板」的链接
  const ids = (typeof v === 'string' ? [v] : (Array.isArray(v) ? v : [])).filter(Boolean);
  return ids.map((id) => `<button class="chip link bh-link" data-open-lib="${esc(bhRoot(scope))}" data-path="${esc(id)}.md" title="行为模板 ${esc(id)}">${icon('book', 12)}${esc(behaviorName(id, scope))}</button>`).join('');
}
function renderPlaybook(pb, scope = '') {
  const stages = (pb.stages || []).map((d) => `<div class="pb-stage">
      <div class="pb-n">${d.id}</div>
      <div class="pb-body"><div class="pb-t">${esc(d.name)} <span class="k">${esc(d.plain || '')}</span>
        <span class="tag">${esc(roleName(d.role))}</span>${d.check ? '<span class="tag k-note">自动检查</span>' : ''}${d.require_ac ? '<span class="tag k-note">验收全过</span>' : ''}${d.writes === false ? '<span class="tag">只读</span>' : ''}${d.entry ? `<span class="tag k-${d.entry === 'executive' ? 'gate' : 'confirm'}">进入需${ENTRY_LABEL[d.entry]}</span>` : ''}</div>
        ${behaviorLinks(d.behavior, scope) ? `<div class="pb-do pb-bh"><b>行为模板</b>${behaviorLinks(d.behavior, scope)}</div>` : ''}
        <div class="pb-do"><b>做什么</b>${md(d.do || '')}</div><div class="pb-do"><b>退出条件</b>${md(d.exit || '')}</div></div></div>`).join('');
  const log = (pb.changelog || []).slice().reverse().map((c) => `<div class="op"><b>v${esc(c.version)}</b><span>${esc(c.ts || '')} · ${esc(c.by || '')}：${esc(c.note || '')}</span></div>`).join('');
  return `<div class="pb-head"><h2>${esc(pb.name || pb.id)} <span class="tag gold">v${esc(pb.version)}</span></h2>
      ${pb.summary ? `<p class="hint">${esc(pb.summary)}</p>` : ''}${pb.based_on ? `<p class="hint">基于模板「${esc(pb.based_on)}」</p>` : ''}</div>
    <div class="pb-flow">${(pb.stages || []).map((d) => `<span>${esc(d.name)}</span>`).join('<i>→</i>')}</div>
    ${stages}${log ? `<h3>变更记录</h3><div class="route">${log}</div>` : ''}`;
}
async function renderLibrary() {
  const el = $('#view-library');
  try { libData = await api('/api/library'); } catch (e) { return toast(e.message); }
  if (!libSel) libSel = { root: 'memory', path: 'index.md' };
  const label = (g, f) => (g.key === 'playbooks' || g.key.startsWith('filmpb:') ? f.path.replace('.json', '')
    : (g.key === 'memory' || g.key.startsWith('filmdocs:') ? f.path.replace(/\.md$/, '') : (f.title || fileLabel(f.path))));
  const group = (g) => `<div class="lib-group"><div class="lib-gt">${esc(g.title)}</div>
    ${g.files.map((f) => `<button class="lib-item ${libSel.root === g.key && libSel.path === f.path ? 'active' : ''}" data-lib-root="${esc(g.key)}" data-lib-path="${esc(f.path)}" title="${esc(f.path)}">
      ${esc(label(g, f))}</button>`).join('') || '<div class="hint">（空）</div>'}</div>`;
  // 按归属分区：麻薯一块、每个项目一块（各自文件夹里的东西），可折叠；当前打开的文件所在分区总是展开
  const cur = (libData.groups.find((g) => g.key === libSel.root) || {}).owner;
  const owners = libData.owners || [{ key: '', title: '资料' }];
  const nav = owners.map((o) => {
    const gs = libData.groups.filter((g) => (g.owner || '') === o.key);
    const open = libOpen[o.key] ?? (o.key === 'mashu' || o.key === cur);
    return `<div class="lib-owner ${open ? 'open' : ''}"><button class="lib-ot" data-lib-owner="${esc(o.key)}" title="${esc(o.path || '')}">
      <span class="caret ${open ? 'open' : ''}">${icon('chev-r', 11)}</span><b>${esc(o.title)}</b><span class="k">${gs.reduce((n, g) => n + g.files.length, 0)}</span></button>
      <div class="lib-ob">${o.path ? `<div class="lib-op">${esc(o.path.replace(/^\/Users\/[^/]+/, '~'))}</div>` : ''}${gs.map(group).join('')}</div></div>`;
  }).join('');
  el.innerHTML = `<div class="lib"><nav class="lib-nav">${nav}</nav><article class="lib-view" id="libView"><div class="hint">加载中…</div></article></div>`;
  showLibFile();
}
async function showLibFile() {
  const v = $('#libView');
  if (!v || !libSel) return;
  try {
    const d = await api(`/api/file?root=${encodeURIComponent(libSel.root)}&path=${encodeURIComponent(libSel.path)}`);
    let body;
    if (d.path.endsWith('.json')) {
      let obj = null;
      try { obj = JSON.parse(d.text); } catch { /* 原样显示 */ }
      body = obj && obj.stages ? renderPlaybook(obj, libScope(libSel.root)) : `<pre><code>${esc(d.text)}</code></pre>`;
    } else body = `<div class="doc-body plain">${md(d.text)}</div>`;
    const grp = libData.groups.find((g) => g.key === libSel.root) || {};
    const where = [((libData.owners || []).find((o) => o.key === grp.owner) || {}).title, grp.title].filter(Boolean).join(' · ');
    const bh = (libSel.root === 'behaviors' || libSel.root.startsWith('filmbh:')) && d.path !== 'README.md'
      ? ((libData.behaviors || {})[libScope(libSel.root)] || {})[d.path.replace(/\.md$/, '')] : null;
    const used = bh ? `<div class="bh-used"><b>被这些工作流阶段使用</b>${bh.used.map((u) => `<button class="chip link" data-open-lib="${esc(u.root)}" data-path="${esc(u.path)}">${icon('refresh', 12)}${esc(u.name)} · ${esc(u.stage)}. ${esc(u.stage_name)}</button>`).join('') || '<span class="k">还没有工作流阶段引用</span>'}</div>` : '';
    v.innerHTML = `<div class="hint path">${esc(where)} / ${esc(d.path)}</div>${used}${body}
      <p class="hint" style="margin-top:14px">资料由主脑、子脑和执行者维护。想改什么，在主对话里告诉麻薯。</p>`;
  } catch (e) { v.innerHTML = `<div class="hint">${esc(e.message)}</div>`; }
}
function openLib(root, path) { libSel = { root, path }; save('mashu.lib', libSel); setView('library'); }

// ---------- 用量 ----------
async function renderBudget() {
  const el = $('#view-budget');
  let d;
  try { d = await api('/api/budget'); } catch (e) { toast(e.message); return; }
  const cost = (x) => (x == null ? '未知' : `$${Number(x).toFixed(2)}`);
  const titleOf = (id) => (filmOf(id) ? filmOf(id).title : id);
  el.innerHTML = `<h2>用量</h2>
    <p class="hint">token 来自每轮结束时读取的 transcript usage。${d.priced ? '' : '还没在 state/studio.json 里配置模型单价（prices），所以费用显示“未知”。'}</p>
    <h3>今天 · 按项目</h3>
    <table><tr><th>项目</th><th>角色</th><th class="num">输入</th><th class="num">输出</th><th class="num">缓存读</th><th class="num">缓存写</th><th class="num">费用</th></tr>
    ${d.by_film.map((r) => `<tr><td>${esc(titleOf(r.film_id))}</td><td>${esc(LEVEL[r.role] || r.role)}</td><td class="num">${num(r.tin)}</td><td class="num">${num(r.tout)}</td><td class="num">${num(r.cr)}</td><td class="num">${num(r.cw)}</td><td class="num">${cost(r.cost)}</td></tr>`).join('') || '<tr><td colspan="7" class="hint">今天还没有用量</td></tr>'}</table>
    <h3>按天</h3>
    <table><tr><th>日期</th><th class="num">输入</th><th class="num">输出</th><th class="num">缓存读</th><th class="num">缓存写</th><th class="num">费用</th></tr>
    ${d.by_day.map((r) => `<tr><td>${esc(r.day)}</td><td class="num">${num(r.tin)}</td><td class="num">${num(r.tout)}</td><td class="num">${num(r.cr)}</td><td class="num">${num(r.cw)}</td><td class="num">${cost(r.cost)}</td></tr>`).join('')}</table>
    <h3>会话（最近 60 个）</h3>
    <table><tr><th>会话名</th><th>身份</th><th>项目 / 功能</th><th>状态</th><th class="num">上下文</th><th class="num">累计 token</th><th class="num">费用</th></tr>
    ${d.cameras.map((c) => `<tr><td>${esc(c.name)}</td><td>${esc(roleName(c.crew_role) || LEVEL[c.role])}</td><td>${esc(c.film_id ? titleOf(c.film_id) : '—')} ${esc(c.seq_key || '')}</td><td>${esc(c.state)}</td>
      <td class="num">${pct(c.context_pct)}</td><td class="num">${num((c.tokens_in || 0) + (c.tokens_out || 0) + (c.cache_read || 0) + (c.cache_write || 0))}</td><td class="num">${cost(c.cost)}</td></tr>`).join('')}</table>`;
}

// ---------- 自迭代 ----------
let siDay = '';
async function renderSelfIter() {
  const el = $('#view-selfiter');
  let d;
  try { d = await api('/api/self-iterate' + (siDay ? `?day=${encodeURIComponent(siDay)}` : '')); } catch (e) { toast(e.message); return; }
  const ST_CLS = { 待定: 'gold', 采纳: 'si-ok', 不采纳: '', 已覆盖: 'si-ok', 过时: '' };
  const RUN = { ok: '完成', failed: '失败', skipped: '没活动，跳过', running: '进行中' };
  const item = (x) => `<div class="dec"><div class="dh"><span class="tag ${ST_CLS[x.status] || ''}">${esc(x.status)}</span>
      <b>#${x.id}</b>${x.section === 'suggestion' ? `<span class="tag">${esc(x.area)}</span>` : ''}${x.priority ? `<span class="tag">优先级 ${esc(x.priority)}</span>` : ''}
      <span class="si-target">${esc(x.target || '')}</span></div>
      <div class="dt"><b>${esc(x.title)}</b></div>
      ${x.evidence ? `<div class="si-row"><span>依据</span><div>${md(x.evidence)}</div></div>` : ''}
      ${x.change ? `<div class="si-row"><span>改法</span><div>${md(x.change)}</div></div>` : ''}
      ${x.note || x.decided_by ? `<div class="hint" style="margin:4px 0 0">${esc(x.decided_by || '')}${x.note ? `：${esc(x.note)}` : ''}</div>` : ''}</div>`;
  const items = d.items || [];
  const sugg = items.filter((x) => x.section === 'suggestion');
  const keep = items.filter((x) => x.section !== 'suggestion');
  const rep = d.report || {};
  el.innerHTML = `<h2>自迭代</h2>
    <p class="hint">每天凌晨麻薯用便宜模型复盘最近 24 小时：会话 review、改进建议、可沉淀的记忆和工作流。主脑用 <code>mashu self-iterate adopt / reject / cover / outdated &lt;编号&gt;</code> 标记（采纳 / 不采纳 / 已被别的改动覆盖 / 过时），网页只读。</p>
    ${d.days.length ? `<div class="seg">${d.days.map((x) => `<button class="${x.day === d.day ? 'active' : ''}" data-siday="${esc(x.day)}">${esc(x.day.slice(5))}</button>`).join('')}</div>` : ''}
    ${(d.runs || []).map((r) => `<div class="hint si-run">运行 #${r.id} · ${r.trigger === 'tick' ? '定时' : '手动'} · ${esc(r.model || '')} · <b>${esc(RUN[r.status] || r.status)}</b>
      ${r.tokens_in != null ? ` · 输入 ${num(r.tokens_in)} / 输出 ${num(r.tokens_out)}` : ''}${r.cost != null ? ` · $${Number(r.cost).toFixed(4)}` : ''}
      ${r.error ? ` · ${esc(r.error)}` : ''}</div>`).join('') || '<div class="empty">还没有自迭代报告</div>'}
    ${d.run_id ? `<h3>总结</h3><div class="dec"><div class="dt">${md(rep.summary || '（无）')}</div></div>
      <h3>改进建议（${sugg.length}）</h3>${sugg.map(item).join('') || '<div class="hint">没有建议</div>'}
      <h3>可沉淀项（${keep.length}）</h3>${keep.map((x) => item({ ...x, area: '' })).join('') || '<div class="hint">没有</div>'}
      ${(rep.followups || []).length ? `<h3>已采纳改动的效果（${rep.followups.length}）</h3>${rep.followups.map((x) => `<div class="dec"><div class="dh"><span class="tag ${x.status === '生效' ? 'si-ok' : 'gold'}">${esc(x.status)}</span><b>${esc(x.item)}</b></div>${x.evidence ? `<div class="dt">${md(x.evidence)}</div>` : ''}</div>`).join('')}` : ''}
      <h3>会话 review（${(rep.sessions || []).length}）</h3>
      <table><tr><th>会话</th><th>角色</th><th>项目</th><th>目的</th><th>链路</th><th>效果</th><th>说明</th></tr>
      ${(rep.sessions || []).map((s) => `<tr><td>${esc(s.session)}</td><td>${esc(s.role)}</td><td>${esc(s.project)}</td><td>${esc(s.purpose)}</td><td>${esc(s.chain)}</td><td>${esc(s.effect)}</td><td>${esc(s.comment)}</td></tr>`).join('')}</table>` : ''}`;
  el.querySelectorAll('[data-siday]').forEach((b) => { b.onclick = () => { siDay = b.dataset.siday; renderSelfIter(); }; });
}

// ---------- 弹窗 ----------
function openModal(html) { $('#modalBox').innerHTML = html; $('#modal').hidden = false; }
function closeModal() { $('#modal').hidden = true; zoomCam = null; $('#modalBox').innerHTML = ''; $('#modalBox').classList.remove('wide', 'api-dialog'); if (view === 'wall') refreshScreens(); }
function greenlightModal() {
  openModal(`<h2>登记项目：把一个工程文件夹交给麻薯电脑秘书</h2>
    <div class="form"><input id="glPath" placeholder="工程文件夹路径，例如 ~/workspace/my-app"><input id="glTitle" placeholder="项目名（可选，默认用文件夹名）">
    <div style="display:flex;gap:8px;justify-content:flex-end"><button class="btn" data-close>取消</button><button class="btn primary" id="glGo">登记</button></div></div>
    <p class="hint" style="margin-top:14px">登记后麻薯会为它开一个子脑和对应的对话；执行者直接在这个文件夹里开发，不建分支。</p>`);
  $('#glPath').focus();
}
// 通用预设+执行配置选择器（用于新功能弹窗、新会话弹框）：第一格选预设，选了自动填好其余几格，改一格变「自定义」
function execPickerHtml(prefix, initEx, defPr) {
  const ex = { preset: initEx.preset || '', cli: initEx.cli || 'claude', model: initEx.model || '',
    effort: initEx.effort || '', account: initEx.account || '', code_mode: !!initEx.code_mode };
  const defEx = defPr ? { cli: defPr.cli || 'claude', model: defPr.model || '', effort: defPr.effort || '',
    account: defPr.account || '', code: !!defPr.code_mode } : { cli: 'claude', model: '', effort: '', account: '', code: false };
  const matchedPr = ex.preset ? presetById(ex.preset) : null;
  const isDefault = !ex.preset && execSame({ ...ex, code: ex.code_mode }, defEx);
  const isCustom = !isDefault && !(matchedPr && execMatchesPreset(ex, matchedPr));
  const selPid = isDefault ? '' : (matchedPr && !isCustom ? matchedPr.id : '');
  const hasAcct = !!(ACCT_PREF[ex.cli] && cliAccts(ex.cli).length);
  return `<div class="exec-picker" id="${prefix}Picker">
    <label>预设<select id="${prefix}Preset">${presetSelectOpts(selPid, defPr, isCustom)}</select></label>
    <div class="ep-grid">
      <label>CLI<select id="${prefix}Cli">${provOpts(ex.cli)}</select></label>
      <label>模型<select id="${prefix}Model">${kickModelOpts(ex.cli, ex.model, ex.account)}</select></label>
      <label>思考<select id="${prefix}Effort"><option value="">默认思考</option>${(state.effort_choices || []).map((x) => `<option value="${esc(x)}" ${x === ex.effort ? 'selected' : ''}>${esc(x)}</option>`).join('')}</select></label>
      <label id="${prefix}AcctWrap" ${hasAcct ? '' : 'hidden'}>账号<select id="${prefix}Acct">${acctOpts(ex.cli, ex.account)}</select></label>
      <label id="${prefix}CodeWrap" ${ex.cli === 'claude' ? '' : 'hidden'}>Code Mode<select id="${prefix}Code"><option value="off" ${!ex.code_mode ? 'selected' : ''}>关</option><option value="on" ${ex.code_mode ? 'selected' : ''}>开</option></select></label>
    </div>
  </div>`;
}
function wireExecPicker(prefix, defPr) {
  const prEl = $(`#${prefix}Preset`), cliEl = $(`#${prefix}Cli`), mEl = $(`#${prefix}Model`);
  const eEl = $(`#${prefix}Effort`), aEl = $(`#${prefix}Acct`), aWrap = $(`#${prefix}AcctWrap`);
  const cEl = $(`#${prefix}Code`), cWrap = $(`#${prefix}CodeWrap`);
  const markCustom = () => {
    let opt = prEl.querySelector('option[value="__custom__"]');
    if (!opt) {
      opt = document.createElement('option');
      opt.value = '__custom__'; opt.textContent = '自定义'; opt.disabled = true;
      prEl.insertBefore(opt, prEl.children[1] || null);
    }
    prEl.value = '__custom__';
  };
  prEl.onchange = () => {
    const pr = prEl.value ? presetById(prEl.value) : defPr;
    const t = pr ? { cli: pr.cli || 'claude', model: pr.model || '', effort: pr.effort || '', account: pr.account || '', code: !!pr.code_mode }
      : { cli: 'claude', model: '', effort: '', account: '', code: false };
    cliEl.value = t.cli;
    const hasAcct = !!(ACCT_PREF[t.cli] && cliAccts(t.cli).length);
    aWrap.hidden = !hasAcct;
    if (hasAcct) aEl.innerHTML = acctOpts(t.cli, t.account);
    else { aEl.innerHTML = '<option value="">自动选号</option>'; aEl.value = ''; }
    mEl.innerHTML = kickModelOpts(t.cli, t.model, hasAcct ? t.account : '');
    eEl.value = t.effort;
    cWrap.hidden = t.cli !== 'claude';
    cEl.value = t.code ? 'on' : 'off';
    prEl.querySelector('option[value="__custom__"]')?.remove();
  };
  cliEl.onchange = () => {
    const cli = cliEl.value;
    const hasAcct = !!(ACCT_PREF[cli] && cliAccts(cli).length);
    aWrap.hidden = !hasAcct;
    if (hasAcct) aEl.innerHTML = acctOpts(cli, '');
    else { aEl.innerHTML = '<option value="">自动选号</option>'; aEl.value = ''; }
    mEl.innerHTML = kickModelOpts(cli, '', '');
    cWrap.hidden = cli !== 'claude';
    cEl.value = 'off';
    markCustom();
  };
  aEl.onchange = () => {
    const cli = cliEl.value;
    mEl.innerHTML = kickModelOpts(cli, modelsFor(cli, aEl.value).includes(mEl.value) ? mEl.value : '', aEl.value);
    markCustom();
  };
  mEl.onchange = markCustom;
  eEl.onchange = markCustom;
  cEl.onchange = markCustom;
}
function readExecPicker(prefix, defPr) {
  const prEl = $(`#${prefix}Preset`), cli = $(`#${prefix}Cli`).value;
  const aWrap = $(`#${prefix}AcctWrap`), cWrap = $(`#${prefix}CodeWrap`);
  const pval = prEl.value;
  const out = {
    cli,
    model: $(`#${prefix}Model`).value,
    effort: $(`#${prefix}Effort`).value,
    account: !aWrap.hidden ? $(`#${prefix}Acct`).value : '',
  };
  if (cli === 'claude' && !cWrap.hidden && $(`#${prefix}Code`).value === 'on') out.code_mode = true;
  if (pval && pval !== '__custom__') out.preset = pval;
  else if (pval === '') {
    out.use_default = true;
    if (defPr && execMatchesPreset(out, defPr)) out.preset = defPr.id;
  }
  return out;
}
function pitchModal(fid) {
  const f = filmOf(fid);
  const defPr = rolePreset('crew'), defEx = roleDefaultExec('crew');
  openModal(`<h2>${esc(f.title)} · 新功能</h2><div class="form">
    <input id="ptSlug" placeholder="英文短名，例如 export-csv"><input id="ptTitle" placeholder="中文标题（可选）">
    <textarea id="ptBrief" placeholder="需求：要做什么、为什么、已知信息"></textarea>
    ${execPickerHtml('pt', { ...defEx, preset: '', code_mode: defEx.code }, defPr)}
    ${(f.playbook.stages || []).length > 1 ? `<label class="hint" style="display:flex;gap:6px;align-items:center;margin:0"><input type="checkbox" id="ptFlow" style="width:auto">按项目工作流「${esc(f.playbook.name)}」分阶段：${esc(f.playbook.stages.join(' → '))}</label>` : ''}
    <div style="display:flex;gap:8px;justify-content:flex-end"><button class="btn" data-close>取消</button><button class="btn primary" id="ptGo" data-film="${esc(fid)}">创建</button></div></div>
    <p class="hint" style="margin-top:14px">默认直接做：一个执行者从头做到尾，不分阶段。一般更推荐直接跟麻薯说，她会交给这个项目的子脑安排。</p>`);
  wireExecPicker('pt', defPr);
  $('#ptSlug').focus();
}

// ---------- 动作 ----------
async function send() {
  const box = $('#input');
  const text = box.value.trim();
  const cmd = SLASH.find((x) => x.c === text.split(/\s/)[0] && (!x.when || x.when()));
  if (cmd && !attach.length) { box.value = ''; autosize(); setDraft(''); closeSlash(); return cmd.run(text.split(/\s+/)[1] || ''); }
  if (!text && !attach.length) return;
  if (attach.some((a) => a.status === 'up')) { toast('等附件传完…'); await Promise.all(attach.map((a) => a.job)); }
  const files = attach.filter((a) => a.status === 'ok')
    .map((a) => { const n = a.name.replace(/[[\]()]/g, '_'); return a.folder ? `📎 [${n}/](${a.path})` : a.image ? `![${n}](${a.path})` : `📎 [${n}](${a.path})`; }).join('\n');
  const full = [text, files].filter(Boolean).join('\n\n');
  if (!full) return;
  const sent = attach;
  attach = []; renderTray();
  box.value = ''; autosize(); setDraft(''); closeSlash();
  try { await api('/api/send', { channel: current, text: full }); await refresh(); } catch (e) { box.value = text; attach = sent; renderTray(); toast(e.message); }
}
async function sendText(text) {
  try { await api('/api/send', { channel: current, text }); await refresh(); } catch (e) { toast(e.message); }
}
async function answer(rid, text, el = null) {
  if (!text) return toast('先写点什么');
  const rr0 = reports[rid] || {};
  const opts0 = rr0.options || [];
  // 发布类批准点「拒绝」要再确认：误点后只能重新发起，发布流程可能要再走一次验证码（自迭代 11-27 #42）
  if (opts0.includes(text) && text !== opts0[0] && /拒绝|驳回|不批准|不同意|打回/.test(text)
      && (rr0.gate || /发布|上线|部署|release|deploy/i.test(`${rr0.summary || ''}`))
      && !window.confirm(`确定选「${text}」？\n\n${rr0.summary || ''}\n\n选了之后只能让对方重新发起。`)) return;
  // 文字回复以逗号、冒号等结尾，多半是没写完就按了发送（自迭代 11-27 #37，#1143/req/4 只发出半句）
  if (!opts0.includes(text) && /[，、：；（,:;(]\s*$/.test(text)
      && !window.confirm(`这条回复好像没写完（结尾是「${text.trim().slice(-6)}」），仍然发送？`)) return;
  const card = el || document.getElementById(`card-${rid}`) || document.getElementById(`raised-${rid}`);
  card?.classList.add('busy');
  card?.querySelectorAll('button,input').forEach((x) => { x.disabled = true; });
  try {
    const rr = reports[rid];
    const exec = rr && rr.gate === 'kickoff' && (rr.options || [])[0] === text ? kickExecOf(rid, card) : null;
    await api('/api/answer', { report_id: Number(rid), text, ...(exec ? { exec } : {}) });
    expanded.delete(Number(rid));
    await refresh();
    if (view === 'mail') await loadMail();
    const left = (state.inbox || []).length;
    toast(`#${rid} 已${(reports[rid]?.options || [])[0] === text && reports[rid]?.gate ? '审批' : '回复'}${left ? `，还有 ${left} 件待处理` : '，待办清空'}`);
  } catch (e) {
    card?.classList.remove('busy');
    card?.querySelectorAll('button,input').forEach((x) => { x.disabled = false; });
    toast(e.message);
  }
}
async function openDoc(ref, path) {
  let list = [];
  try { list = (await api(`/api/docs?ref=${encodeURIComponent(ref)}`)).docs; } catch (e) { return toast(e.message); }
  const show = async (p) => {
    let text = '';
    try { text = (await api(`/api/docs?ref=${encodeURIComponent(ref)}&path=${encodeURIComponent(p)}`)).text; } catch (e) { text = e.message; }
    const [f, s] = seqOf(ref);
    openModal(`<div class="doc-head"><b>${f && s ? esc(seqLabel(f, s)) : ''}</b>
        <div class="doc-tabs">${list.map((d) => `<button class="${d.path === p ? 'active' : ''}" data-doc="${esc(ref)}" data-path="${esc(d.path)}">${esc(d.label)}</button>`).join('')}</div>
        <button class="btn" data-close>关闭</button></div>
      <div class="doc-body">${md(text)}</div><div class="hint path">${esc(p)}</div>`);
  };
  show(path || (list[0] && list[0].path) || '');
}
async function camAction(btn) {
  btn.disabled = true;
  try {
    const d = await api('/api/camera', { action: btn.dataset.cam, ref: btn.dataset.ref, film: btn.dataset.film });
    toast(d.result); await refresh();
  } catch (e) { toast(e.message); } finally { btn.disabled = false; }
}
function autosize() { const t = $('#input'); t.style.height = 'auto'; t.style.height = Math.min(200, t.scrollHeight) + 'px'; }

// ---------- 聊天框：附件（粘贴 / 拖进来 / 附件按钮）、打断、斜杠命令、草稿 ----------
let attach = [];   // {id, name, size, mime, image, path, url, status: up|ok|err, job}
let attachSeq = 0;
let drafts = loadJSON('mashu.drafts', {});
function setDraft(v) { if (v) drafts[current] = v; else delete drafts[current]; save('mashu.drafts', drafts); }
function fmtSize(n) { return n < 1024 ? `${n}B` : n < 1048576 ? `${(n / 1024).toFixed(0)}KB` : `${(n / 1048576).toFixed(1)}MB`; }
function chWho(ch) {
  if (ch === MAIN) return '麻薯 · 主脑';
  const [f, s] = seqOf(ch);
  if (!f) return ch;
  return s ? `${seqLabel(f, s)} 的执行者` : `${f.title} 的子脑`;
}
function renderTray() {
  const t = $('#attachTray');
  t.hidden = !attach.length;
  t.innerHTML = attach.map((a) => `<div class="att st-${a.status}" title="${esc(a.path || a.name)}">
    ${a.image && a.url ? `<img src="${esc(a.url)}" alt="">` : `<span class="att-ic">${a.folder ? icon('folder', 15) : fileIcon(a.name)}</span>`}
    <span class="att-t"><span class="att-n">${esc(a.name)}${a.folder ? '/' : ''}</span><span class="att-s">${a.status === 'up' ? (a.folder ? `上传中 ${a.done}/${a.count}…` : '上传中…') : a.status === 'err' ? '上传失败' : a.folder ? `${a.count} 个文件 · ${fmtSize(a.size)}` : fmtSize(a.size)}</span></span>
    <button class="att-x" data-unattach="${a.id}" title="移除">${icon('x', 14)}</button></div>`).join('');
}
function addFiles(files) {
  for (const file of files) {
    if (file.size > 50e6) { toast(`${file.name} 太大（上限 50MB）`); continue; }
    const image = (file.type || '').startsWith('image/');
    let name = file.name || 'file';
    if (/^image\.(png|jpe?g|gif|webp)$/i.test(name) || !file.name) name = `截图-${new Date().toTimeString().slice(0, 8).replace(/:/g, '')}.${(file.type.split('/')[1] || 'png').replace('jpeg', 'jpg')}`;
    const a = { id: ++attachSeq, name, size: file.size, mime: file.type, image, status: 'up', url: image ? URL.createObjectURL(file) : '' };
    attach.push(a);
    a.job = fetch('/api/upload', {
      method: 'POST', body: file,
      headers: { 'X-Mashu-Token': TOKEN, 'X-File-Name': encodeURIComponent(name), 'Content-Type': file.type || 'application/octet-stream' },
    }).then(async (r) => {
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || r.statusText);
      Object.assign(a, { path: d.path, image: d.image, status: 'ok' });
    }).catch((e) => { a.status = 'err'; toast(`${a.name} 上传失败：${e.message}`); }).finally(renderTray);
  }
  renderTray();
  if (view !== 'chat') setView('chat');
  $('#input').focus();
}
function busyHere() { const l = live[current]; return !!(l && !l.waiting); }
function renderComposer() {
  const b = busyHere();
  $('#stopBtn').hidden = !b;
  $('.composer').classList.toggle('busy', b);
}
async function interruptNow() {
  if (!busyHere()) return toast('现在没有在忙的');
  try { const d = await api('/api/camera', { action: 'interrupt', channel: current }); toast(`${d.result}，可以直接说下一句`); await refresh(); $('#input').focus(); } catch (e) { toast(e.message); }
}
async function camApi(body) {
  try { const d = await api('/api/camera', body); toast(d.result); await refresh(); } catch (e) { toast(e.message); }
}
function camOfChannel(ch) {
  if (ch === MAIN) return state.assistant;
  const [f, s] = seqOf(ch);
  return s ? s.crew : f?.producer;
}
// 斜杠命令：输入框打 / 弹出，↑↓ 选，Enter / Tab 执行
const SLASH = [
  { c: '/stop', d: '打断它这一轮（同 Esc），会话不关', run: interruptNow },
  { c: '/attach', d: '添加图片 / 文件（也可以直接粘贴、拖文件或文件夹进来）', run: () => $('#fileInput').click() },
  { c: '/screen', d: '看它的终端画面', run: () => { const c = camOfChannel(current); return c && c.alive ? zoom(c.id) : toast('这个对话现在没有运行中的会话'); } },
  { c: '/handoff', d: '让它整理交接文档，然后在原窗格换新会话接着做', run: () => camApi({ action: 'handoff', channel: current }) },
  { c: '/new', d: '直接新开一个会话（不带上一段对话）', run: () => camApi({ action: 'new-session', channel: current }) },
  { c: '/provider', d: '切换平台：/provider codex（claude / gemini / codex / opencode / default），新会话生效', run: (x) => setPref('provider', x) },
  { c: '/model', d: '切换模型：/model opus（按这个对话的平台：Claude 是 fable / opus / sonnet / haiku，Codex / OpenCode 用它们的模型名；default）', run: (x) => setPref('model', x) },
  { c: '/effort', d: '切换思考程度：/effort high（low / medium / high / xhigh / max / default）', run: (x) => setPref('effort', x) },
  { c: '/compact', d: '压缩它的上下文（Claude Code 的 /compact，要它空闲时）', run: () => camApi({ action: 'slash', cmd: '/compact', channel: current }) },
  { c: '/feature', d: '给这个项目开一个新功能', when: () => current !== MAIN, run: () => pitchModal(current.split('/')[0]) },
  { c: '/stopall', d: '打断所有正在忙的会话', run: () => camApi({ action: 'interrupt-all' }) },
  { c: '/digest', d: '现在要一份简报', run: () => camApi({ action: 'digest-now' }) },
];
const slash = { items: [], sel: 0 };
function drawSlash() {
  const v = $('#input').value;
  const menu = $('#slashMenu');
  if (!/^\/\S*$/.test(v)) return closeSlash();
  const prev = slash.items[slash.sel]?.c;
  slash.items = SLASH.filter((x) => (!x.when || x.when()) && x.c.startsWith(v.toLowerCase()));
  if (!slash.items.length) return closeSlash();
  slash.sel = Math.max(0, slash.items.findIndex((x) => x.c === prev));
  menu.hidden = false;
  menu.innerHTML = slash.items.map((x, i) => `<div class="sl-item ${i === slash.sel ? 'sel' : ''}" data-slash="${i}"><b>${esc(x.c)}</b><span>${esc(x.d)}</span></div>`).join('')
    + `<div class="sl-foot">对 <b>${esc(chWho(current))}</b> · ↑↓ 选 · Enter 执行 · Esc 关</div>`;
}
function closeSlash() { const m = $('#slashMenu'); if (m) { m.hidden = true; slash.items = []; } }
function runSlash(i) {
  const x = slash.items[i];
  closeSlash();
  if (!x) return;
  if (x.c === '/model' || x.c === '/effort' || x.c === '/provider') { $('#input').value = x.c + ' '; autosize(); return; }   // 这两个要带参数
  $('#input').value = ''; autosize(); setDraft('');
  x.run('');
}
async function setPref(kind, v) {
  const pl = platOf(chProvider(current));
  if (kind === 'model') kind = pl.model_key;
  const list = { [pl.model_key]: pl.models, effort: state.effort_choices, provider: state.provider_choices }[kind] || [];
  if (!v) return toast(`用法：/${kind} ${list[0] || ''}（可选：${list.join(' / ')} / default）`);
  return camApi({ action: 'pref', channel: current, [kind]: v === 'default' ? '' : v });
}
async function sessAction(btn) {
  const kind = btn.dataset.sess;
  if (kind === 'new-session') {
    const ch = current, role = chRole(ch);
    const eff = effectiveExec(ch, role), defPr = rolePreset(role);
    const p = (state.prefs || {})[ch] || {};
    const initEx = { preset: eff.customized ? '' : (p.preset || ''), cli: eff.cli, model: eff.model, effort: eff.effort, account: eff.account, code_mode: eff.code };
    openModal(`<h2>新会话</h2><div class="form api-form"><p>新会话不会带上当前对话上下文。</p>${execPickerHtml('ns', initEx, defPr)}<div class="api-actions"><button class="btn" data-close>取消</button><button class="btn primary" id="newSessionGo">开始新会话</button></div></div>`);
    $('#modalBox').classList.add('api-dialog');
    wireExecPicker('ns', defPr);
    $('#newSessionGo').onclick = async () => {
      const button = $('#newSessionGo'); button.disabled = true;
      try {
        const d = await api('/api/camera', { action: 'new-session', channel: ch, exec: readExecPicker('ns', defPr) });
        closeModal();
        if (d.result) toast(d.result);
        await refresh();
      } catch (e) { toast(e.message); button.disabled = false; }
    };
    return;
  }
  btn.disabled = true;
  await camApi({ action: kind, channel: current });
  btn.disabled = false;
}
// 拖文件进窗口：切到聊天、遮罩提示发给谁，松手就加进附件
let dragDepth = 0;
function hasFiles(ev) { return [...(ev.dataTransfer?.types || [])].includes('Files'); }
document.addEventListener('dragenter', (ev) => {
  if (!hasFiles(ev)) return;
  ev.preventDefault();
  if (dragDepth++ === 0) {
    if (view !== 'chat') setView('chat');
    $('#dropWho').textContent = chWho(current);
    $('#dropZone').hidden = false;
  }
});
document.addEventListener('dragover', (ev) => { if (hasFiles(ev)) { ev.preventDefault(); ev.dataTransfer.dropEffect = 'copy'; } });
document.addEventListener('dragleave', (ev) => { if (hasFiles(ev) && --dragDepth <= 0) { dragDepth = 0; $('#dropZone').hidden = true; } });
document.addEventListener('drop', (ev) => {
  if (!hasFiles(ev)) return;
  ev.preventDefault();            // 别让浏览器直接打开文件
  dragDepth = 0; $('#dropZone').hidden = true;
  const entries = [...(ev.dataTransfer.items || [])].map((it) => it.webkitGetAsEntry?.()).filter(Boolean);
  if (!entries.some((e) => e.isDirectory)) return addFiles([...ev.dataTransfer.files]);
  addFiles(entries.filter((e) => e.isFile).map((e) => ev.dataTransfer.files[entries.indexOf(e)]).filter(Boolean));
  entries.filter((e) => e.isDirectory).forEach(addFolder);
});
// 拖进来的文件夹：遍历出全部文件（跳过隐藏文件、node_modules 等），按原目录结构传到 uploads/<日期>/<组>/，消息里带这个文件夹的路径
const SKIP_DIR = /^(\.|node_modules$|__pycache__$|venv$|\.venv$)/;
function walkDir(dir, pre, out) {
  return new Promise((done) => {
    const rd = dir.createReader(), all = [];
    const more = () => rd.readEntries(async (list) => {
      if (list.length) { all.push(...list); return more(); }
      for (const e of all) {
        if (SKIP_DIR.test(e.name) || out.length > 2000) continue;
        if (e.isDirectory) await walkDir(e, `${pre}${e.name}/`, out);
        else out.push(await new Promise((ok) => e.file((f) => ok({ f, rel: pre + e.name }), () => ok(null))));
      }
      done(out);
    }, () => done(out));
    more();
  });
}
async function addFolder(entry) {
  const a = { id: ++attachSeq, name: entry.name, folder: true, size: 0, count: 0, done: 0, status: 'up' };
  attach.push(a); renderTray();
  if (view !== 'chat') setView('chat');
  let files = (await walkDir(entry, '', [])).filter(Boolean);
  const big = files.filter((x) => x.f.size > 50e6);
  files = files.filter((x) => x.f.size <= 50e6);
  const total = files.reduce((n, x) => n + x.f.size, 0);
  if (!files.length || files.length > 2000 || total > 500e6) {
    attach = attach.filter((x) => x !== a); renderTray();
    return toast(!files.length ? `${entry.name} 里没有可传的文件` : `${entry.name} 太大（上限 2000 个文件、500MB），建议直接在消息里写文件夹路径`);
  }
  const group = `${new Date().toTimeString().slice(0, 8).replace(/:/g, '')}-${Math.random().toString(16).slice(2, 6)}-${entry.name}`;
  Object.assign(a, { size: total, count: files.length });
  renderTray();
  let i = 0, fail = 0;
  const one = async () => {
    while (i < files.length) {
      const x = files[i++];
      try {
        const r = await fetch('/api/upload', { method: 'POST', body: x.f, headers: { 'X-Mashu-Token': TOKEN, 'X-File-Name': encodeURIComponent(x.f.name),
          'X-Upload-Dir': encodeURIComponent(group), 'X-Rel-Path': encodeURIComponent(x.rel), 'Content-Type': x.f.type || 'application/octet-stream' } });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.error || r.statusText);
        a.path = a.path || d.dir;
      } catch (e) { fail++; }
      a.done++; renderTray();
    }
  };
  a.job = Promise.all([1, 2, 3, 4].map(one)).then(() => {
    a.status = a.path && fail < files.length ? 'ok' : 'err';
    if (fail || big.length) toast(`${entry.name}：${fail ? `${fail} 个文件没传上` : ''}${fail && big.length ? '，' : ''}${big.length ? `${big.length} 个超过 50MB 的跳过了` : ''}`);
    renderTray();
  });
}
function lightbox(p) {
  openModal(`<div class="lightbox"><img src="${esc(blobUrl(p))}" alt="${esc(baseName(p))}">
    <div class="lb-bar"><span class="path">${esc(p)}</span>
      <button class="btn" data-copy="${esc(p)}">复制路径</button>
      <button class="btn" data-reveal="${esc(p)}">在访达中显示</button>
      <a class="btn" href="${esc(blobUrl(p))}" target="_blank" rel="noopener">新标签打开</a>
      <button class="btn" data-close>关闭</button></div></div>`);
  $('#modalBox').classList.add('wide');
}
// 聊天里的 .md 附件：点开直接在网页里排版显示，图片（绝对路径或相对 md 所在目录）经 /api/blob 显示
async function mdViewer(p) {
  let text;
  try {
    const r = await fetch(blobUrl(p));
    if (!r.ok) throw new Error(`打不开：${baseName(p)}`);
    text = await r.text();
  } catch (e) { return toast(e.message); }
  const dir = p.replace(/[^/]*$/, '');
  text = text.replace(/!\[([^\]]*)\]\((?!\/|~\/|[a-z]+:)([^)\s]+)\)/gi, (m, alt, rel) => `![${alt}](${dir}${rel.replace(/^\.\//, '')})`);
  openModal(`<div class="md-viewer"><div class="lb-bar"><span class="path">${esc(p)}</span>
      <button class="btn" data-copy="${esc(p)}">复制路径</button>
      <button class="btn" data-reveal="${esc(p)}">在访达中显示</button>
      <button class="btn" data-close>关闭</button></div>
    <div class="md-viewer-body">${md(text)}</div></div>`);
  $('#modalBox').classList.remove('wide');
}
document.addEventListener('error', (ev) => {        // 图片打不开（文件不在 / 不让看）：换成文字
  if (ev.target.classList?.contains('md-img')) ev.target.closest('.img-wrap')?.classList.add('broken');
}, true);

document.addEventListener('click', async (ev) => {
  if (ev.target.closest('summary')) return;
  const lo = ev.target.closest('[data-lib-owner]');
  if (lo) {
    const box = lo.parentElement;
    libOpen[lo.dataset.libOwner] = box.classList.toggle('open');
    lo.querySelector('.caret')?.classList.toggle('open', box.classList.contains('open'));
    save('mashu.libOpen', libOpen);
    return;
  }
  const lib = ev.target.closest('[data-lib-root]');
  if (lib) { libSel = { root: lib.dataset.libRoot, path: lib.dataset.libPath }; save('mashu.lib', libSel);
    document.querySelectorAll('.lib-item').forEach((x) => x.classList.toggle('active', x === lib)); return showLibFile(); }
  const ml = ev.target.closest('[data-md-link]');
  if (ml && view === 'library' && libSel) {
    const dir = libSel.path.includes('/') ? libSel.path.replace(/[^/]+$/, '') : '';
    libSel = { root: libSel.root, path: dir + ml.dataset.mdLink }; save('mashu.lib', libSel);
    return renderLibrary();
  }
  const ol = ev.target.closest('[data-open-lib]');
  if (ol) return openLib(ol.dataset.openLib, ol.dataset.path);
  const t = ev.target.closest('[data-pmail],[data-mail],[data-mbox],[data-mtab],[data-quick],[data-go],[data-toggle],[data-cam],[data-answer],[data-reply],[data-reject],[data-reject-go],[data-expand],[data-doc],[data-next],[data-pitch],[data-greenlight],[data-zoom],[data-close],[data-decide],[data-dfilter],[data-sfilter],[data-prio],[data-view-go],#glGo,#ptGo,#greenlightBtn');
  if (!t) { if (ev.target.id === 'modal') closeModal(); return; }
  if (t.dataset.toggle) { ev.stopPropagation(); collapsed[t.dataset.toggle] = !collapsed[t.dataset.toggle]; save('mashu.collapsed', collapsed); renderSide(); return; }
  if (t.dataset.sfilter) { const v = t.dataset.sfilter === 'all' ? null : t.dataset.sfilter; sideFilter = sideFilter === v ? null : v; renderSide(); return; }
  if (t.dataset.pmbox || t.dataset.pmail) {
    const box = t.dataset.pmbox || panelMail.box;
    if (mailBox !== box) { mailBox = box; save('mashu.mailbox', box); mailData = null; mailOpen.clear(); }
    if (t.dataset.pdir) { mailTab = t.dataset.pdir; save('mashu.mailtab', mailTab); }
    if (t.dataset.pmail) mailOpen.add(t.dataset.pmail);
    return setView('mail');
  }
  if (t.dataset.mail) { mailOpen.has(t.dataset.mail) ? mailOpen.delete(t.dataset.mail) : mailOpen.add(t.dataset.mail); return drawMail(); }
  if (t.dataset.mbox) { mailBox = t.dataset.mbox; save('mashu.mailbox', mailBox); mailOpen.clear(); mailData = null; drawMail(); return loadMail(); }
  if (t.dataset.mtab) { mailTab = t.dataset.mtab; save('mashu.mailtab', mailTab); return drawMail(); }
  if (t.dataset.go) { if (view !== 'chat') setView('chat'); return go(t.dataset.go, t.dataset.focus); }
  if (t.dataset.doc) return openDoc(t.dataset.doc, t.dataset.path);
  if (t.dataset.quick) {
    t.disabled = true;
    try { await api('/api/send', { channel: current, text: t.dataset.quick }); await refresh(); } catch (e) { toast(e.message); }
    return;
  }
  if (t.dataset.next !== undefined) return nextPending(Number(t.dataset.next) || 0);
  if (t.dataset.expand && view === 'mail') { expanded.add(Number(t.dataset.expand)); return drawMail(); }
  if (t.dataset.expand) { expanded.add(Number(t.dataset.expand)); renderMessages(); return flashTo(document.getElementById(`raised-${t.dataset.expand}`) ? `raised-${t.dataset.expand}` : `card-${t.dataset.expand}`); }
  if (t.dataset.reject) {
    const box = (t.closest('.card') || document).querySelector(`#rej-${t.dataset.reject}`);
    box.hidden = !box.hidden;
    if (!box.hidden) box.querySelector('input').focus();
    return;
  }
  if (t.dataset.rejectGo) {
    const why = (t.closest('.card') || document).querySelector(`[data-reject-input="${t.dataset.rejectGo}"]`).value.trim();
    return answer(t.dataset.rejectGo, why ? `${t.dataset.label}：${why}` : t.dataset.label, t.closest('.card'));
  }
  if (t.dataset.cam) return camAction(t);
  if (t.dataset.answer) {
    if (t.dataset.need !== undefined) {
      const v = (t.closest('.card') || document).querySelector(`[data-confirm-input="${t.dataset.answer}"]`)?.value.trim();
      if (v !== t.dataset.need) return toast(`不可逆操作：先输入项目 id「${t.dataset.need}」`);
    }
    return answer(t.dataset.answer, t.dataset.text, t.closest('.card'));
  }
  if (t.dataset.reply) { const i = (t.closest('.card') || document).querySelector(`[data-reply-input="${t.dataset.reply}"]`); return answer(t.dataset.reply, i.value.trim(), t.closest('.card')); }
  if (t.dataset.pitch) return pitchModal(t.dataset.pitch);
  if (t.dataset.zoom) return zoom(t.dataset.zoom);
  if ('close' in t.dataset) return closeModal();
  if (t.dataset.viewGo) return setView(t.dataset.viewGo);
  if (t.dataset.dfilter) { decisionFilter = t.dataset.dfilter; return renderDecisions(); }
  if (t.dataset.prio) { try { await api('/api/priority', { ref: t.dataset.prio, value: Number(t.dataset.v) }); await refresh(); } catch (e) { toast(e.message); } return; }
  if (t.dataset.decide) {
    const note = document.querySelector(`[data-dnote="${t.dataset.decide}"]`)?.value || '';
    try { await api('/api/decision', { id: Number(t.dataset.decide), status: t.dataset.status, note }); toast(t.dataset.status); renderDecisions(); refresh(); } catch (e) { toast(e.message); }
    return;
  }
  if (t.dataset.greenlight) {
    try { const d = await api('/api/greenlight', { path: t.dataset.greenlight }); toast('已登记，麻薯接手了'); candidates = null; await refresh(); go(d.film); } catch (e) { toast(e.message); }
    return;
  }
  if (t.id === 'greenlightBtn') return greenlightModal();
  if (t.id === 'glGo') {
    try { const d = await api('/api/greenlight', { path: $('#glPath').value.trim(), title: $('#glTitle').value.trim() }); closeModal(); candidates = null; toast('已登记，麻薯接手了'); await refresh(); go(d.film); } catch (e) { toast(e.message); }
    return;
  }
  if (t.id === 'ptGo') {
    try {
      const ex = readExecPicker('pt', rolePreset('crew'));
      const d = await api('/api/pitch', { film: t.dataset.film, slug: $('#ptSlug').value.trim(), title: $('#ptTitle').value.trim(), brief: $('#ptBrief').value.trim(), workflow: !!$('#ptFlow')?.checked, exec: ex });
      closeModal(); toast(`方案执行者「${d.crew}」排队中`); await refresh(); go(`${t.dataset.film}/${d.key}`);
    } catch (e) { toast(e.message); }
  }
});
document.addEventListener('keydown', (ev) => {
  if (ev.key === 'Escape' && !$('#modal').hidden) closeModal();
  if (ev.target.dataset?.replyInput && ev.key === 'Enter' && !ev.isComposing) answer(ev.target.dataset.replyInput, ev.target.value.trim(), ev.target.closest('.card'));
  if (ev.target.dataset?.rejectInput && ev.key === 'Enter' && !ev.isComposing) (ev.target.closest('.card') || document).querySelector(`[data-reject-go="${ev.target.dataset.rejectInput}"]`).click();
});
document.addEventListener('change', (ev) => {
  if (ev.target.dataset && 'mpend' in ev.target.dataset) { mailPendingOnly = ev.target.checked; drawMail(); }
  if (ev.target.dataset?.pref) {
    const t = ev.target;
    if (t.dataset.pref === 'preset' && t.value === '__custom__') return;
    t.blur();
    camApi({ action: 'pref', channel: t.dataset.ch, [t.dataset.pref]: t.value });
  }
});
$('#input').addEventListener('keydown', (ev) => {
  if (ev.isComposing) return;
  if (!$('#slashMenu').hidden && slash.items.length) {
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') { ev.preventDefault(); slash.sel = (slash.sel + (ev.key === 'ArrowDown' ? 1 : -1) + slash.items.length) % slash.items.length; return drawSlash(); }
    if (ev.key === 'Tab' || (ev.key === 'Enter' && !ev.shiftKey)) { ev.preventDefault(); return runSlash(slash.sel); }
    if (ev.key === 'Escape') { ev.preventDefault(); return closeSlash(); }
  }
  if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); return send(); }
  if (ev.key === 'Escape' && busyHere()) { ev.preventDefault(); return interruptNow(); }
  if (ev.key === 'ArrowUp' && !ev.target.value) {     // 空输入框按 ↑：拿回我在这个对话说的上一句
    const mine = channelMessages(current).filter((m) => m.sender === 'executive');
    if (mine.length) { ev.preventDefault(); ev.target.value = mine[mine.length - 1].text; autosize(); setDraft(ev.target.value); }
  }
});
$('#input').addEventListener('input', () => { autosize(); setDraft($('#input').value); drawSlash(); });
$('#input').addEventListener('paste', (ev) => {
  const files = [...(ev.clipboardData?.files || [])];
  if (!files.length) return;
  ev.preventDefault();
  addFiles(files);
});
$('#attachBtn').addEventListener('click', () => $('#fileInput').click());
$('#imageBtn').addEventListener('click', () => { const i = $('#fileInput'); i.accept = 'image/*'; i.click(); i.accept = ''; });
$('#fileInput').addEventListener('change', (ev) => { addFiles([...ev.target.files]); ev.target.value = ''; });
$('#stopBtn').addEventListener('click', interruptNow);
$('#sendBtn').addEventListener('click', send);
// 消息列表离底部远时，右下角出现「跳到最新」箭头，点一下直接到底（不做滚动动画）
function syncToBottom() {
  const box = $('#messages'), btn = $('#toBottom');
  const far = box.offsetParent && box.scrollHeight - box.scrollTop - box.clientHeight > 300;
  btn.hidden = !far;
  if (far) btn.style.bottom = `${box.offsetParent.clientHeight - (box.offsetTop + box.clientHeight) + 14}px`;
}
$('#messages').addEventListener('scroll', syncToBottom, { passive: true });
window.addEventListener('resize', syncToBottom);
new MutationObserver(syncToBottom).observe($('#messages'), { childList: true });
$('#toBottom').addEventListener('click', () => { const box = $('#messages'); box.scrollTop = box.scrollHeight; syncToBottom(); });
$('#search').addEventListener('input', (ev) => { query = ev.target.value; renderSide(); });
document.querySelectorAll('#tabs button').forEach((b) => b.addEventListener('click', () => setView(b.dataset.view)));


// ---------- 主题、命令面板、快捷键 ----------
let themePref = load('mashu.theme', 'auto');
const darkMQ = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
function applyTheme() {
  const dark = themePref === 'dark' || (themePref === 'auto' && darkMQ && darkMQ.matches);
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  const b = $('[data-theme-toggle]');
  if (b) { b.innerHTML = icon({ auto: 'contrast', light: 'sun', dark: 'moon' }[themePref], 16); b.title = `主题：${{ auto: '跟随系统', light: '浅色', dark: '深色' }[themePref]}（点击切换）`; }
}
darkMQ?.addEventListener?.('change', applyTheme);
function cycleTheme() { themePref = { auto: 'light', light: 'dark', dark: 'auto' }[themePref]; save('mashu.theme', themePref); applyTheme(); toast(`主题：${{ auto: '跟随系统', light: '浅色', dark: '深色' }[themePref]}`); }
async function restartServer() {
  try { await api('/api/restart-server', {}); } catch (e) { return toast(e.message); }
  toast('正在重启网页服务，几秒后自动刷新…');
  const t0 = Date.now();
  const poll = async () => {      // 等新服务起来再刷新
    try { if (Date.now() - t0 > 2500 && (await fetch('/', { cache: 'no-store' })).ok) return location.reload(); } catch (e) {}
    if (Date.now() - t0 < 60000) setTimeout(poll, 1000); else toast('重启超时，请手动刷新页面');
  };
  setTimeout(poll, 1500);
}
function togglePanel(force) {
  const off = force ?? !document.body.classList.contains('panel-off');
  document.body.classList.toggle('panel-off', off); save('mashu.paneloff', off ? '1' : '');
  setTimeout(moveTabInd, 300);
}
if (load('mashu.paneloff', '')) document.body.classList.add('panel-off');
// 右侧栏滑动：拖左边缘调宽度，拖到很窄松手就收起；收起后从右边缘往左拖、或点边上的小把手展开
let panelW = Math.min(560, Math.max(260, Number(load('mashu.pw', '340')) || 340));
document.documentElement.style.setProperty('--pw', `${panelW}px`);
(() => {
  const g = $('#panelGrip');
  const setW = (w) => document.documentElement.style.setProperty('--pw', `${w}px`);
  let drag = null;
  g.addEventListener('pointerdown', (ev) => {
    if (ev.button !== 0) return;
    ev.preventDefault();
    drag = { x: ev.clientX, moved: false };
    g.setPointerCapture(ev.pointerId);
  });
  g.addEventListener('pointermove', (ev) => {
    if (!drag) return;
    if (!drag.moved && Math.abs(ev.clientX - drag.x) < 5) return;
    if (!drag.moved) { drag.moved = true; document.body.classList.add('dragging-panel'); document.body.classList.remove('panel-off'); }
    setW(Math.max(0, Math.min(560, window.innerWidth - ev.clientX)));
  });
  const end = (ev) => {
    if (!drag) return;
    const d = drag; drag = null;
    document.body.classList.remove('dragging-panel');
    if (!d.moved) return togglePanel();
    const w = window.innerWidth - ev.clientX;
    if (w < 200) { setW(panelW); togglePanel(true); } else {
      panelW = Math.max(260, Math.min(560, w)); setW(panelW); save('mashu.pw', String(panelW)); togglePanel(false);
    }
    setTimeout(moveTabInd, 300);
  };
  g.addEventListener('pointerup', end);
  g.addEventListener('pointercancel', end);
})();

// 顶部只留用户常看的几页（用户 2026-11-26：看板、决策记录、资料库、用量不显示）；这几页的视图代码还在，聊天里的文件链接、右栏「去看看」照样能打开
const VIEWS = [['chat', 'chat', '聊天'], ['mail', 'mail', '邮箱'], ['wall', 'monitor', '会话监控'], ['accounts', 'wallet', '模型池'], ['selfiter', 'refresh', '自迭代']];
let pal = { items: [], sel: 0 };
function paletteItems() {
  const out = [];
  const inbox = state.inbox || [];
  for (const r of inbox) {
    const cat = { approve: '要你审批', help: '要你帮忙', ask: '在问你' }[needCat(r)];
    out.push({ sec: '等你处理', ic: { approve: 'pen', help: 'hand', ask: 'ask' }[needCat(r)], t: r.summary || r.text, s: `${cat} · #${r.id}`, run: () => go(r.film_id || MAIN, `card-${r.id}`) });
  }
  out.push({ sec: '对话', ic: 'user', t: '麻薯 · 主脑', s: '主对话', run: () => go(MAIN) });
  for (const f of state.films) {
    out.push({ sec: '对话', ic: 'brain', t: `${f.title} · 子脑`, s: f.id, run: () => go(f.id) });
    for (const x of f.sequences) out.push({ sec: '功能', ic: 'hash', t: `${f.title} #${x.num} ${x.title}`, s: statusName(x), run: () => go(`${f.id}/${x.key}`) });
  }
  VIEWS.forEach(([v, ic, t], i) => out.push({ sec: '页面', ic, t, s: `⌥${i + 1}`, run: () => setView(v) }));
  for (const f of state.films) out.push({ sec: '操作', ic: 'plus', t: `给 ${f.title} 开新功能`, s: '', run: () => pitchModal(f.id) });
  out.push({ sec: '操作', ic: 'plus', t: '登记一个项目', s: '', run: () => greenlightModal() });
  if (inbox.length) out.push({ sec: '操作', ic: 'arrow-d', t: '逐件处理等你的事', s: '⌘J', run: () => nextPending(0) });
  out.push({ sec: '操作', ic: 'contrast', t: '切换主题（跟随系统 / 浅色 / 深色）', s: '', run: cycleTheme });
  out.push({ sec: '操作', ic: 'panel', t: '收起 / 展开右侧栏', s: '⌘\\', run: () => togglePanel() });
  if (window.Notification && Notification.permission === 'default') out.push({ sec: '操作', ic: 'bell', t: '开启桌面提醒（有事等你时通知）', s: '', run: () => Notification.requestPermission().then((p) => toast(p === 'granted' ? '已开启桌面提醒' : '没有开启桌面提醒')) });
  if (Object.values(live).some((l) => !l.waiting)) out.push({ sec: '操作', ic: 'stop', t: '打断所有正在忙的会话', s: '/stopall', run: () => camApi({ action: 'interrupt-all' }) });
  out.push({ sec: '操作', ic: 'news', t: '现在要一份简报', s: '', run: () => api('/api/camera', { action: 'digest-now' }).then((d) => toast(d.result)).catch((e) => toast(e.message)) });
  return out;
}
function openPalette() {
  const el = $('#palette');
  el.hidden = false;
  const inp = el.querySelector('.pal-in');
  inp.value = ''; drawPalette(); inp.focus();
}
function closePalette() { $('#palette').hidden = true; }
function drawPalette() {
  const q = $('#palette .pal-in').value.trim().toLowerCase();
  const words = q.split(/\s+/).filter(Boolean);
  pal.items = paletteItems().filter((x) => words.every((w) => `${x.t} ${x.s} ${x.sec}`.toLowerCase().includes(w))).slice(0, 60);
  pal.sel = Math.min(pal.sel, Math.max(0, pal.items.length - 1));
  if (!q) pal.sel = 0;
  let sec = '';
  $('#palette .pal-list').innerHTML = pal.items.map((x, i) => {
    const h = x.sec !== sec ? `<div class="pal-sec">${esc((sec = x.sec))}</div>` : '';
    return `${h}<div class="pal-item ${i === pal.sel ? 'sel' : ''}" data-pal="${i}"><span class="pi-ic">${icon(x.ic, 16)}</span><span class="pi-t">${esc(x.t)}</span><span class="pi-s">${esc(x.s)}</span></div>`;
  }).join('') || '<div class="pal-sec">没有匹配的</div>';
  $('#palette .pal-item.sel')?.scrollIntoView({ block: 'nearest' });
}
function runPalette(i) { const x = pal.items[i]; closePalette(); if (x) x.run(); }
$('#palette').addEventListener('input', () => { pal.sel = 0; drawPalette(); });
$('#palette').addEventListener('mousemove', (ev) => {
  const it = ev.target.closest('[data-pal]');
  if (it && Number(it.dataset.pal) !== pal.sel) { pal.sel = Number(it.dataset.pal); drawPalette(); }
});
$('#palette').addEventListener('click', (ev) => {
  const it = ev.target.closest('[data-pal]');
  if (it) return runPalette(Number(it.dataset.pal));
  if (ev.target.id === 'palette') closePalette();
});
$('#palette').addEventListener('keydown', (ev) => {
  if (ev.key === 'ArrowDown') { ev.preventDefault(); pal.sel = Math.min(pal.items.length - 1, pal.sel + 1); drawPalette(); }
  else if (ev.key === 'ArrowUp') { ev.preventDefault(); pal.sel = Math.max(0, pal.sel - 1); drawPalette(); }
  else if (ev.key === 'Enter' && !ev.isComposing) { ev.preventDefault(); runPalette(pal.sel); }
  else if (ev.key === 'Escape') { ev.preventDefault(); closePalette(); }
});
document.addEventListener('keydown', (ev) => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(ev.target.tagName) || ev.target.isContentEditable;
  const mod = ev.metaKey || ev.ctrlKey;
  if (mod && ev.key.toLowerCase() === 'k') { ev.preventDefault(); return $('#palette').hidden ? openPalette() : closePalette(); }
  if (mod && ev.key === '\\') { ev.preventDefault(); return togglePanel(); }
  if (mod && ev.key.toLowerCase() === 'j') { ev.preventDefault(); return nextPending(focusEl && /^card-/.test(focusEl) ? Number(focusEl.split('-')[1]) : 0); }
  if (ev.altKey && /^Digit[1-8]$/.test(ev.code) && VIEWS[Number(ev.code.slice(5)) - 1]) { ev.preventDefault(); return setView(VIEWS[Number(ev.code.slice(5)) - 1][0]); }
  if (!typing && !mod && ev.key === '/') { ev.preventDefault(); if (view !== 'chat') setView('chat'); return $('#input').focus(); }
});
document.addEventListener('click', (ev) => {
  if (ev.target.closest('[data-theme-toggle]')) return cycleTheme();
  if (ev.target.closest('[data-restart]')) return restartServer();
  if (ev.target.closest('[data-panel-toggle]')) return togglePanel();
  if (ev.target.closest('[data-palette]')) return openPalette();
  const cp = ev.target.closest('[data-copy]');
  if (cp) navigator.clipboard?.writeText(cp.dataset.copy).then(() => toast('已复制'), () => {});
  const cm = ev.target.closest('[data-copymsg]');
  if (cm) { const m = messages.find((x) => x.id === Number(cm.dataset.copymsg)); if (m) navigator.clipboard?.writeText(m.text).then(() => toast('已复制'), () => {}); }
  if (ev.target.closest('[data-interrupt]')) return interruptNow();
  if (ev.target.closest('[data-screen]')) { const c = camOfChannel(current); return c && c.alive ? zoom(c.id) : toast('这个对话现在没有运行中的会话'); }
  const mu = ev.target.closest('[data-mute]');
  if (mu) return muteReport(mu.dataset.mute);
  if (ev.target.closest('[data-detail]')) { detailOn = !detailOn; save('mashu.detail', detailOn ? '1' : '0'); detailData = null; renderHead(); renderLive(); return pollDetail(); }
  const ss = ev.target.closest('[data-sess]');
  if (ss) return sessAction(ss);
  const ua = ev.target.closest('[data-unattach]');
  if (ua) { attach = attach.filter((a) => a.id !== Number(ua.dataset.unattach)); return renderTray(); }
  const rv = ev.target.closest('[data-reveal]');
  if (rv && /\.(md|markdown)$/i.test(rv.dataset.reveal) && !rv.closest('#modalBox') && !(ev.metaKey || ev.ctrlKey)) {
    ev.preventDefault(); ev.stopPropagation();
    return mdViewer(rv.dataset.reveal);
  }
  if (rv && !(ev.metaKey || ev.ctrlKey)) {     // 本机文件：在访达里打开所在文件夹；⌘ 点击照旧在浏览器打开
    ev.preventDefault(); ev.stopPropagation();
    return api('/api/reveal', { path: rv.dataset.reveal }).catch((e) => toast(e.message));
  }
  const im = ev.target.closest('[data-img]');
  if (im && !im.classList.contains('broken')) return lightbox(im.dataset.img);
  const sl = ev.target.closest('[data-slash]');
  if (sl) return runSlash(Number(sl.dataset.slash));
});
window.addEventListener('resize', () => moveTabInd());
$('#tabs').addEventListener('scroll', () => moveTabInd());
applyTheme();


// ---------- 动态：右上角铃铛 + 弹出通知 ----------
let feedSeen = Number(load('mashu.feedseen', '0')) || 0;
let feedReady = false;
function chLabel(ch) {
  if (!ch || ch === MAIN) return '主对话';
  const [f, x] = seqOf(ch);
  if (!f) return ch;
  return x ? `${f.title} #${x.num ?? ''} ${x.title}` : `${f.title}`;
}
function firstLine(t, n = 90) {
  const l = String(t || '').split('\n').map((x) => x.replace(/^[#>*\-\s]+/, '').trim()).find(Boolean) || '';
  return l.length > n ? l.slice(0, n) + '…' : l;
}
function feedItem(m) {
  const ch = m.channel;
  if (m.kind === 'event') {
    const i = m.text.indexOf('|');
    const type = i > 0 ? m.text.slice(0, i) : 'info';
    const f = filmOf(m.film_id);
    const x = f && m.seq_key ? f.sequences.find((y) => y.key === m.seq_key) : null;
    const where = f ? (x ? `${f.title} #${x.num ?? ''} ${x.title}` : f.title) : '全局';
    return { id: m.id, ic: { stage: 'check-c', done: 'flag', warn: 'alert' }[type] || 'bell', cls: type, t: where, d: m.text.slice(i + 1),
      ch: f ? (x ? `${f.id}/${x.key}` : f.id) : MAIN, ts: m.ts };
  }
  if (m.kind === 'card') {
    const r = reports[m.report_id];
    return { id: m.id, ic: 'pen', cls: 'need', t: `需要你 · ${chLabel(ch)}`, d: (r && r.summary) || firstLine(m.text), ch, focus: `card-${m.report_id}`, ts: m.ts };
  }
  if (m.kind === 'raised') return { id: m.id, ic: 'mail', cls: 'raised', t: `${m.camera_name ? `「${m.camera_name}」` : ''}提出 · ${chLabel(ch)}`, d: firstLine(m.text), ch, focus: `raised-${m.report_id}`, ts: m.ts };
  if (m.kind === 'answer') return { id: m.id, ic: 'check', cls: 'answer', t: `批复 · ${chLabel(ch)}`, d: firstLine(m.text.replace(/^\[[^\]]*\]\s*/, '')), ch, ts: m.ts };
  if (m.kind === 'handover' && /交接|阶段 \d/.test(m.text)) return { id: m.id, ic: 'swap', cls: 'handover', t: chLabel(ch), d: m.text.replace(/—/g, '').trim(), ch, ts: m.ts };
  if (m.kind === 'reply' && m.sender !== 'executive' && m.sender !== 'system') {
    const [name, role] = whoOf(m);
    return { id: m.id, ic: m.sender === 'crew' ? 'wrench' : 'chat', cls: 'reply', t: `${name}${role ? ' · ' + role : ''}`, d: firstLine(m.text), ch, ts: m.ts };
  }
  return null;
}
function feedItems(limit = 60) {
  const out = [];
  for (let i = messages.length - 1; i >= 0 && out.length < limit; i--) { const it = feedItem(messages[i]); if (it) out.push(it); }
  return out;
}
function renderFeedBadge() {
  const n = feedItems(99).filter((x) => x.id > feedSeen).length;
  const b = $('#feedBadge');
  if (b) { b.className = n ? 'badge feed-n' : ''; b.textContent = n ? (n > 99 ? '99+' : n) : ''; }
}
function feedRow(x) {
  return `<div class="feed-row c-${esc(x.cls)} ${x.id > feedSeen ? 'unseen' : ''}" data-go="${esc(x.ch)}"${x.focus ? ` data-focus="${esc(x.focus)}"` : ''}>
    <span class="fr-ic">${icon(x.ic, 15)}</span><div class="fr-b"><div class="fr-t"><span>${esc(x.t)}</span><span class="time">${timeText(x.ts).slice(6)}</span></div>
    <div class="fr-d">${esc(x.d)}</div></div></div>`;
}
function openFeed() {
  let pop = $('#feedPop');
  if (pop) { pop.remove(); return; }
  pop = document.createElement('div');
  pop.id = 'feedPop'; pop.className = 'feed-pop';
  const items = feedItems(60);
  pop.innerHTML = `<div class="fp-h"><b>动态</b><span>各项目的进展和交互</span><button class="ghost-btn" data-feed-read>全部已读</button></div>
    <div class="fp-list">${items.map(feedRow).join('') || '<div class="hint" style="padding:16px">还没有动态</div>'}</div>`;
  document.body.appendChild(pop);
  feedSeen = Math.max(feedSeen, ...items.map((x) => x.id), 0);
  save('mashu.feedseen', String(feedSeen));
  renderFeedBadge();
}
function notifyPop(x) {
  let stack = $('#notifStack');
  if (!stack) { stack = document.createElement('div'); stack.id = 'notifStack'; stack.className = 'notif-stack'; document.body.appendChild(stack); }
  const el = document.createElement('div');
  el.className = `notif c-${x.cls}`;
  el.innerHTML = `<span class="fr-ic">${icon(x.ic, 15)}</span><div class="fr-b"><div class="fr-t"><span>${esc(x.t)}</span></div><div class="fr-d">${esc(x.d)}</div></div><button class="nx" title="关闭">${icon('x', 13)}</button>`;
  el.onclick = (ev) => { el.remove(); if (!ev.target.closest('.nx')) go(x.ch, x.focus); };
  stack.prepend(el);
  while (stack.children.length > 4) stack.lastElementChild.remove();
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 300); }, x.cls === 'need' || x.cls === 'warn' ? 12000 : 6000);
}
function feedOnNew(newMsgs) {
  if (!feedReady) {
    feedReady = true;
    if (!feedSeen && messages.length) { feedSeen = messages[messages.length - 1].id; save('mashu.feedseen', String(feedSeen)); }
    renderFeedBadge(); return;
  }
  for (const m of newMsgs) {
    const x = feedItem(m);
    if (!x) continue;
    if (view === 'chat' && x.ch === current && !document.hidden) continue;   // 正在看的对话不重复提醒
    notifyPop(x);
  }
  renderFeedBadge();
}
document.addEventListener('click', (ev) => {
  if (ev.target.closest('[data-feed-read]')) { feedSeen = messages.length ? messages[messages.length - 1].id : feedSeen; save('mashu.feedseen', String(feedSeen)); $('#feedPop')?.remove(); renderFeedBadge(); return; }
  if (ev.target.closest('[data-feed]')) { ev.stopPropagation(); return openFeed(); }
  const pop = $('#feedPop');
  if (pop && !ev.target.closest('#feedPop')) pop.remove();
  else if (pop && ev.target.closest('[data-go]')) setTimeout(() => pop.remove(), 0);
}, true);
document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') $('#feedPop')?.remove(); });

// ---------- 同步 ----------
// 打开已完成的功能 = 看过结果：去掉左栏的小点
function markDoneSeen() {
  if (view !== 'chat' || document.hidden) return;
  const [f, s] = seqOf(current);
  const fail = markDoneSeen.fail || (markDoneSeen.fail = {});
  if (!f || !s || !unseenDone(s) || markDoneSeen.busy === current || Date.now() - (fail[current] || 0) < 60000) return;
  const ch = current;
  markDoneSeen.busy = ch;
  s.seen = true;
  api('/api/camera', { action: 'seen', ref: ch }).catch(() => { s.seen = false; fail[ch] = Date.now(); }).finally(() => { markDoneSeen.busy = null; });
}
// 有要用户审批 / 处理 / 回复的事时，浏览器标签页图标右上角加红点
function setFaviconDot(on) {
  const st = setFaviconDot;
  if (st.on === on) return;
  st.on = on;
  const link = document.querySelector('link[rel="icon"]');
  if (!link) return;
  if (!on) { link.href = AVATAR; return; }
  const draw = () => {
    if (!st.on) return;
    try {
      const c = document.createElement('canvas');
      c.width = c.height = 64;
      const g = c.getContext('2d');
      g.drawImage(st.img, 0, 0, 64, 64);
      g.beginPath(); g.arc(46, 18, 16, 0, Math.PI * 2);
      g.fillStyle = '#ff3b30'; g.fill();
      g.lineWidth = 4; g.strokeStyle = '#fff'; g.stroke();
      link.href = c.toDataURL('image/png');
    } catch { /* 画不了就只靠标题里的数字 */ }
  };
  if (st.img && st.img.complete) return draw();
  if (!st.img) { st.img = new Image(); st.img.src = AVATAR; }
  st.img.addEventListener('load', draw, { once: true });
}
function renderAll(stick) {
  markDoneSeen(); markSeen();   // 先记已读再画侧栏，红点立刻消失
  renderSide(); renderHead(); applyProjMode(); renderPendingBar(); renderMessages(stick); renderPanel(); renderTabs(); loadMail();
  const n = state.inbox.length;
  document.title = n ? `(${n}) 麻薯电脑秘书` : '麻薯电脑秘书';
  setFaviconDot(n > 0);
  const ids = new Set(state.inbox.map((r) => r.id));
  if (renderAll.ids) {
    const fresh = state.inbox.filter((r) => !renderAll.ids.has(r.id));
    if (fresh.length) {
      const r = fresh[fresh.length - 1];
      const cat = { approve: '要你审批', help: '要你帮忙', ask: '在问你' }[needCat(r)];
      toast(`${cat}：${r.summary || ''}`, () => go(r.film_id || MAIN, `card-${r.id}`));
      if (window.Notification && Notification.permission === 'granted' && document.hidden) {
        try { new Notification(`麻薯 · ${cat}`, { body: r.summary || '', icon: AVATAR }); } catch { /* 忽略 */ }
      }
    }
  }
  renderAll.ids = ids;
}
let refreshing = null;
async function refresh() {
  if (refreshing) { refresh.again = true; return refreshing; }
  refreshing = (async () => {
    try {
      const [m, s] = await Promise.all([
        api(lastId ? `/api/messages?after=${lastId}` : '/api/messages?tail=3000'),
        api('/api/state'),
      ]);
      state = s;
      live = s.live || {};
      trackLive();
      if (view === 'wall' && !renderWall.ready && state.platforms) renderWall();   // 第一次画时状态还没到（平台表、账号池是空的）：到了重画一次
      if (view === 'accounts') {     // 账号池页跟着状态更新（额度、体检结果）；正在点按钮 / 展开时不重画
        const ap = $('#view-accounts'), h = acctPageHtml();
        if (ap && h !== acctPageLast && !ap.contains(document.activeElement) && !ap.querySelector('details[open]')) { ap.innerHTML = h; acctPageLast = h; }
      }
      if (m.messages.length) { messages = messages.concat(m.messages); lastId = messages[messages.length - 1].id; }
      const newMsgs = m.messages;
      const need = new Set();
      for (const x of messages) if (x.report_id && ['card', 'raised', 'report'].includes(x.kind) && (!reports[x.report_id] || reports[x.report_id].status === 'open')) need.add(x.report_id);
      if (need.size) {
        const ids = [...need];
        for (let i = 0; i < ids.length; i += 200) {
          const r = await api(`/api/reports?ids=${ids.slice(i, i + 200).join(',')}`);
          for (const x of r.reports) reports[x.id] = x;
        }
      }
      renderAll(false);
      feedOnNew(newMsgs);
      if (view === 'board') renderBoard();
    } catch (e) { console.error(e); }
  })();
  await refreshing;
  refreshing = null;
  if (refresh.again) { refresh.again = false; return refresh(); }
}
let pollTimer = null;
function startEvents() {
  if (!window.EventSource) return poll();
  const es = new EventSource(`/api/events?token=${encodeURIComponent(TOKEN)}`);
  let t = null;
  es.onmessage = () => { clearTimeout(t); t = setTimeout(refresh, 120); };
  es.addEventListener('live', (ev) => { try { live = JSON.parse(ev.data); } catch { return; } trackLive(); renderLive(); renderSide(); renderHead(); });
  es.onerror = () => { if (es.readyState === EventSource.CLOSED) { poll(); } };
}
function poll() { clearInterval(pollTimer); pollTimer = setInterval(refresh, 1500); }
setInterval(refresh, 10000);   // 兜底：tmux 层面的变化
const initial = readHash();
if (initial) { current = initial.channel; view = initial.view; focusEl = initial.focus; }
setView(view, true);
history.replaceState(null, '', hashOf());
$('#input').value = drafts[current] || ''; autosize();
refresh().then(() => { renderMessages(true); if (focusEl) flashTo(focusEl); pollDetail(); });
startEvents();

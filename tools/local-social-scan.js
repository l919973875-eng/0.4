// Manual local social scan. Reads only visible public DOM via a local Chrome CDP port.
// It never calls cookie, storage, password, message, or profile APIs.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const root = join(import.meta.dir, "..");
const dataDir = join(root, "data");
const configPath = join(root, "config", "manual_social_queries.json");
const cdpUrl = process.env.LOCAL_SOCIAL_CDP_URL || "http://127.0.0.1:9222";
const perQuery = Math.max(1, Math.min(8, Number(process.env.LOCAL_SOCIAL_MAX_PER_QUERY || 5)));
const pauseMs = Math.max(3000, Number(process.env.LOCAL_SOCIAL_PAUSE_MS || 5000));

const clean = (value, limit = 1600) => String(value || "").replace(/\s+/g, " ").trim().slice(0, limit);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const now = () => new Date().toISOString();

class CdpTab {
  constructor(ws) {
    this.ws = ws; this.nextId = 0; this.pending = new Map();
    ws.onmessage = event => {
      const message = JSON.parse(String(event.data));
      const request = this.pending.get(message.id);
      if (!request) return;
      this.pending.delete(message.id);
      if (message.error) request.reject(new Error(message.error.message || "CDP error"));
      else request.resolve(message.result);
    };
  }
  static async connect(url) {
    const ws = new WebSocket(url);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("无法连接 Chrome CDP")), 8000);
      ws.onopen = () => { clearTimeout(timer); resolve(); };
      ws.onerror = () => { clearTimeout(timer); reject(new Error("Chrome CDP WebSocket 连接失败")); };
    });
    const tab = new CdpTab(ws);
    await tab.call("Runtime.enable"); await tab.call("Page.enable"); return tab;
  }
  call(method, params = {}) {
    const id = ++this.nextId;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      setTimeout(() => {
        if (this.pending.has(id)) { this.pending.delete(id); reject(new Error(method + " 超时")); }
      }, 15000);
    });
  }
  async navigate(url) { await this.call("Page.navigate", { url }); await sleep(pauseMs); }
  async evaluate(expression) {
    const result = await this.call("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    return result.result && result.result.value;
  }
  async close() { try { await this.call("Page.close"); } catch {} try { this.ws.close(); } catch {} }
}

function searchUrl(platform, query) {
  const q = encodeURIComponent(query);
  const endpoints = {
    x: "https://x.com/search?q=" + q + "&src=typed_query&f=live",
    weibo: "https://s.weibo.com/weibo?q=" + q + "&sort=hot",
    douyin: "https://www.douyin.com/search/" + q + "?type=video",
    xiaohongshu: "https://www.xiaohongshu.com/search_result?keyword=" + q + "&source=web_explore_feed",
    youtube: "https://www.youtube.com/results?search_query=" + q + "&sp=CAISAhAB",
    wechat: "https://weixin.sogou.com/weixin?type=2&query=" + q,
  };
  return endpoints[platform];
}

const extractVisibleCards = String.raw`(() => {
  const text = node => String(node?.innerText || node?.textContent || "").replace(/\s+/g, " ").trim();
  const absolute = value => { try { return new URL(value, location.href).href; } catch { return ""; } };
  const platform = window.__LOCAL_SOCIAL_PLATFORM__;
  const selectors = {
    x: ["article[data-testid='tweet']", "[data-testid='tweet']", "article[role='article']", "div[data-testid='cellInnerDiv']"],
    weibo: ["div[action-type='feed_list_item']", ".card-wrap"],
    douyin: ["[data-e2e='search-card']", "[data-e2e*='search-card']", "[data-e2e*='search']", "[data-e2e*='video']", "a[href*='/video/']"],
    xiaohongshu: ["section.note-item", ".note-item", "a[href*='/explore/']"],
    youtube: ["ytd-video-renderer"],
    wechat: [".news-box", ".txt-box", "li"],
  };
  const candidates = [];
  for (const selector of (selectors[platform] || [])) for (const node of document.querySelectorAll(selector)) if (!candidates.includes(node)) candidates.push(node);
  const seen = new Set(), results = [];
  for (const node of candidates) {
    const body = text(node); if (body.length < 12) continue;
    const links = [...node.querySelectorAll("a[href]")].map(a => absolute(a.getAttribute("href"))).filter(Boolean);
    const url = links.find(x => /status\/\d+|weibo\.com\/\d+|\/video\/|\/explore\/|watch\?v=|weixin\.sogou\.com\/link/.test(x)) || links[0] || "";
    if (!url || seen.has(url)) continue;
    seen.add(url);
    const author = text(node.querySelector("[data-testid='User-Name'], .name, #channel-name, .author"));
    const time = text(node.querySelector("time, .from"));
    results.push({ text: body.slice(0, 1800), url, author: author.slice(0, 180), visible_time: time.slice(0, 100) });
  }
  return results;
})()`;
async function createTab() {
  const response = await fetch(cdpUrl + "/json/new", { method: "PUT" });
  if (!response.ok) throw new Error("Chrome 未就绪，HTTP " + response.status);
  const page = await response.json();
  if (!page.webSocketDebuggerUrl) throw new Error("Chrome 没有返回调试标签页");
  return CdpTab.connect(page.webSocketDebuggerUrl);
}

async function run() {
  if (!existsSync(configPath)) throw new Error("缺少查询配置：" + configPath);
  const plan = JSON.parse(readFileSync(configPath, "utf8"));
  const slot = String(process.argv[2] || (new Date().getHours() < 12 ? "morning" : "afternoon")).toLowerCase();
  if (!["morning", "afternoon"].includes(slot)) throw new Error("仅支持 morning 或 afternoon");
  const items = [], status = [];
  for (const [platform, queries] of Object.entries(plan[slot] || {})) {
    let count = 0; const errors = []; let tab;
    try {
      tab = await createTab();
      for (const query of queries) {
        try {
          await tab.navigate(searchUrl(platform, query));
          await tab.evaluate("window.__LOCAL_SOCIAL_PLATFORM__=" + JSON.stringify(platform));
          await tab.evaluate("window.scrollTo(0, Math.max(500, document.body.scrollHeight * 0.25))");
          await sleep(1800);
          const cards = await tab.evaluate(extractVisibleCards);
          if (!Array.isArray(cards) || cards.length === 0) {
            const pageHint = await tab.evaluate("document.title + ' | ' + String(document.body?.innerText || '').replace(/\\s+/g, ' ').slice(0, 140)");
            errors.push("No visible cards: " + clean(pageHint, 180));
          }
          for (const card of (Array.isArray(cards) ? cards : []).slice(0, perQuery)) {
            items.push({
              id: platform + ":" + card.url, platform, author: clean(card.author || platform, 180),
              author_name: clean(card.author || platform, 180), text: clean(card.text), url: card.url,
              published_at: clean(card.visible_time, 100) || null, collected_at: now(), query, engagement: {},
              media_count: ["douyin", "xiaohongshu", "youtube"].includes(platform) ? 1 : 0,
              collector: platform === "wechat" ? "wechat_public_index_manual" : "local_chrome_public_dom",
              source_mode: platform === "wechat" ? "public WeChat article index, not in-platform realtime search" : "logged-in local browser public page",
            });
            count++;
          }
        } catch (error) { errors.push(clean(error && error.message ? error.message : error, 180)); }
      }
    } catch (error) { errors.push(clean(error && error.message ? error.message : error, 180)); }
    finally { if (tab) await tab.close(); }
    status.push({ platform, status: count ? "ok" : (errors.length ? "error" : "empty"), items: count, queries: queries.length, detail: errors.slice(0, 3) });
  }  mkdirSync(dataDir, { recursive: true });
  const target = join(dataDir, "signals_external.json");
  const old = existsSync(target) ? JSON.parse(readFileSync(target, "utf8")) : [];
  const merged = new Map();
  for (const row of (Array.isArray(old) ? old : [])) if (row.collector !== "local_chrome_public_dom" && row.collector !== "wechat_public_index_manual") merged.set(row.id, row);
  for (const row of items) merged.set(row.id, row);
  writeFileSync(target, JSON.stringify([...merged.values()], null, 2), "utf8");
  writeFileSync(join(dataDir, "manual_social_status.json"), JSON.stringify({ slot, finished_at: now(), status }, null, 2), "utf8");
  console.log(JSON.stringify({ slot, collected: items.length, status }, null, 2));
}
run().catch(error => { console.error("采集失败：" + clean(error && error.message ? error.message : error)); process.exit(1); });

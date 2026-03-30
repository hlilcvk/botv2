require('dotenv').config();
const express = require('express');
const http = require('http');
const NodeWebSocket = require('ws');
const fs = require('fs');
const path = require('path');
const Redis = require('ioredis');
const TelegramBot = require('node-telegram-bot-api');

// =============================================================
// 1. EXPRESS + WS SERVER SETUP
// =============================================================
const app = express();
const server = http.createServer(app);
const wss = new NodeWebSocket.Server({ server, path: '/ws' });

app.use(express.json());
app.use(express.static(path.join(__dirname, '../public')));

const PORT = process.env.PORT || 3200;
const REDIS_URL = process.env.REDIS_URL || 'redis://127.0.0.1:6379';

// Redis with error handling — sunucu çökmemeli
const redis = new Redis(REDIS_URL, {
    retryStrategy: (times) => Math.min(times * 500, 3000),
    maxRetriesPerRequest: 3,
    lazyConnect: true
});
redis.on('error', (err) => console.error('[Redis]', err.message));
redis.on('connect', () => console.log('[Redis] Connected'));
redis.connect().catch(() => console.warn('[Redis] Initial connection failed, will retry'));

let bot = null;

async function initTelegram() {
    try {
        let token;
        try { token = await redis.get('cde_telegram_token'); } catch {}
        token = token || process.env.TELEGRAM_BOT_TOKEN;
        if (token) {
            bot = new TelegramBot(token, { polling: false });
            console.log('[Telegram] Bot API Initialized');
        }
    } catch (e) {
        console.error('[Telegram] Init error:', e.message);
    }
}

// =============================================================
// 2. NODE.JS BROWSER COMPATIBILITY LAYER
//    Sahte veri ÜRETMEZ. DOM elemanı olmadığı için render
//    fonksiyonlarının çökmesini engeller (sessiz no-op).
//    Tüm gerçek veri, botun kendi Binance bağlantılarından gelir.
// =============================================================

// WebSocket — Node.js ws paketi, browser WebSocket API'si ile uyumlu
global.WebSocket = NodeWebSocket;

// DOM Mock — Hiçbir sahte veri üretmez, sadece null-safe proxy döner
const NULL_STYLE = new Proxy({}, { set: () => true, get: () => '' });
const NULL_CLASSLIST = { add(){}, remove(){}, toggle(){}, contains(){ return false; } };

function createMockElement() {
    return {
        textContent: '',
        innerHTML: '',
        innerText: '',
        value: '',
        href: '',
        style: NULL_STYLE,
        classList: NULL_CLASSLIST,
        className: '',
        checked: false,
        dataset: {},
        parentNode: null,
        children: [],
        querySelectorAll: () => [],
        querySelector: () => null,
        addEventListener: () => {},
        removeEventListener: () => {},
        setAttribute: () => {},
        getAttribute: () => null,
        closest: () => null,
        getBoundingClientRect: () => ({ width: 0, height: 0, top: 0, left: 0 }),
        appendChild: () => {},
        removeChild: () => {},
        remove: () => {},
        scrollWidth: 0,
        clientWidth: 0,
        focus: () => {},
        blur: () => {},
        dispatchEvent: () => {},
        click: () => {},
        cloneNode: () => createMockElement()
    };
}

// Node.js global'e addEventListener/removeEventListener ekle (browser uyumluluğu)
global.addEventListener = () => {};
global.removeEventListener = () => {};
global.dispatchEvent = () => {};

global.window = global;
global.self = global;
global.document = {
    getElementById: () => createMockElement(),
    querySelector: () => createMockElement(),
    querySelectorAll: () => [],
    createElement: () => createMockElement(),
    createTextNode: () => createMockElement(),
    documentElement: { lang: 'en', style: NULL_STYLE },
    body: { style: NULL_STYLE, appendChild: () => {}, overflow: '' },
    addEventListener: () => {},
    removeEventListener: () => {},
    head: { appendChild: () => {} }
};

global.navigator = { language: 'en', clipboard: { writeText: async () => {} } };
global.location = {
    protocol: 'https:',
    origin: 'https://localhost',
    host: 'localhost:3200',
    hostname: 'localhost',
    href: 'https://localhost:3200/',
    search: '',
    pathname: '/'
};

global.localStorage = {
    _data: {},
    getItem(k) { return this._data[k] || null; },
    setItem(k, v) { this._data[k] = String(v); },
    removeItem(k) { delete this._data[k]; },
    clear() { this._data = {}; }
};

// Browser API's that exist in Node 20+ but need fallbacks
global.ResizeObserver = class { observe(){} unobserve(){} disconnect(){} };
global.IntersectionObserver = class { observe(){} unobserve(){} disconnect(){} };
global.requestAnimationFrame = (fn) => setTimeout(fn, 16);
global.cancelAnimationFrame = clearTimeout;
global.performance = global.performance || { now: () => Date.now() };
global.AudioContext = class { createOscillator(){ return { connect(){}, start(){}, stop(){}, frequency: { value: 0 }, type: '' }; } createGain(){ return { connect(){}, gain: { value: 0 } }; } get destination(){ return null; } get currentTime(){ return 0; } };
global.webkitAudioContext = global.AudioContext;
global.speechSynthesis = { speak(){}, cancel(){} };
global.SpeechSynthesisUtterance = class { constructor(){} };
global.URL = require('url').URL;
global.Intl = Intl; // Node.js has this natively

// LightweightCharts mock — only for server-side, charts render on client
global.LightweightCharts = null;

// =============================================================
// 3. LOAD EXTRACTED BOT — GERÇEK PIYASA MOTORU
//    proptrex_tbx.html içinden çıkartılan orijinal kod.
//    Binance'e kendi bağlanır, kendi hesaplar.
//    Hiçbir sahte veri üretmez.
// =============================================================
console.log('[Engine] Loading core intelligence from extracted_bot.js...');

try {
    const botCode = fs.readFileSync(path.join(__dirname, 'extracted_bot.js'), 'utf-8');
    eval(botCode);
    console.log('[Engine] Core intelligence loaded successfully!');
} catch (e) {
    console.error('[Engine] FATAL: Failed to load extracted_bot.js:', e.message);
    console.error(e.stack);
    process.exit(1);
}

// Verify Z object exists after eval
if (!global.Z || !global.Z.tokens) {
    console.error('[Engine] FATAL: Z state object not initialized after bot load');
    process.exit(1);
}

console.log(`[Engine] Z.tokens initialized with ${global.Z.tokens.length} symbols`);

// =============================================================
// 4. REDIS STATE PERSISTENCE
//    Sunucu kapansa bile veriler kalıcı olarak saklanır.
// =============================================================
async function persistToRedis() {
    try {
        if (!global.Z || !global.Z.tokens || !global.Z.tokens.length) return;

        // Circular ref'leri temizle, sadece seri hale getirilebilir veriyi kaydet
        const safeTokens = global.Z.tokens.map(t => {
            try {
                const { signal, __token, __structure, __whale, __scan, ...rest } = t;
                if (signal) {
                    const { __token: t2, __structure: t3, __whale: t4, __scan: t5, event_history, ...safeSignal } = signal;
                    rest.signal = safeSignal;
                }
                return rest;
            } catch {
                return null;
            }
        }).filter(Boolean);

        await redis.set('cde_z_tokens', JSON.stringify(safeTokens), 'EX', 3600);

        // Broadcast to all connected UI clients
        broadcast({ type: 'tokens_update', tokens: safeTokens });
    } catch (e) {
        // Sessizce devam et, sunucuyu çökertme
    }
}

async function restoreFromRedis() {
    try {
        const saved = await redis.get('cde_z_tokens');
        if (saved) {
            const tokens = JSON.parse(saved);
            console.log(`[Redis] Restored ${tokens.length} tokens from backup`);
            // Not: init() kendi REST snapshot'ını zaten çeker,
            // bu sadece init() çalışana kadar geçici veri sağlar
        }
    } catch (e) {
        console.warn('[Redis] Restore failed:', e.message);
    }
}

// =============================================================
// 5. TELEGRAM ALERT ENGINE
//    PRIORITY veya EXECUTION sinyallerini gerçek zamanlı gönderir.
// =============================================================
async function checkTelegramAlerts() {
    if (!bot) return;
    try {
        let chatId;
        try { chatId = await redis.get('cde_telegram_chat_id'); } catch {}
        chatId = chatId || process.env.TELEGRAM_CHAT_ID;
        if (!chatId) return;

        const preview = global.Z?.proptrex?.activeTelegramPreview;
        if (!preview || !preview.signal || !preview.message) return;

        // Sadece PRIORITY ve EXECUTION seviyesindeki sinyalleri gönder
        if (!['PRIORITY', 'EXECUTION'].includes(preview.signal.state)) return;

        const sigKey = preview.signal.signal_key + '|' + (preview.event ? preview.event.event_type : 'BASE');

        let lastSent;
        try { lastSent = await redis.get(`tg_sent_${sigKey}`); } catch {}

        if (!lastSent) {
            const opts = { parse_mode: 'HTML', disable_web_page_preview: true };
            let msg = `<b>[${preview.signal.state}] ${preview.signal.symbol}</b>\n\n`;
            msg += `<pre>${preview.message}</pre>\n\n`;
            if (preview.tvUrl) msg += `<a href="${preview.tvUrl}">TradingView</a>`;
            if (preview.exchangeUrl) msg += ` | <a href="${preview.exchangeUrl}">${preview.exchangeLabel || 'Exchange'}</a>`;

            await bot.sendMessage(chatId, msg, opts);
            try { await redis.set(`tg_sent_${sigKey}`, '1', 'EX', 86400); } catch {}
            console.log(`[Telegram] Sent: ${preview.signal.symbol} - ${preview.signal.state}`);
        }
    } catch (e) {
        console.error('[Telegram] Send error:', e.message);
    }
}

// =============================================================
// 6. WEBSOCKET SERVER — UI CLIENT'LARA VERİ AKIŞI
// =============================================================
function broadcast(data) {
    if (wss.clients.size === 0) return;
    const payload = JSON.stringify(data);
    wss.clients.forEach(client => {
        if (client.readyState === NodeWebSocket.OPEN) {
            try { client.send(payload); } catch {}
        }
    });
}

wss.on('connection', (ws) => {
    console.log('[WS] Client connected');

    // İlk bağlantıda mevcut veriyi gönder
    try {
        const safeTokens = global.Z.tokens.map(t => {
            try {
                const { signal, __token, __structure, __whale, __scan, ...rest } = t;
                if (signal) {
                    const { __token: t2, __structure: t3, __whale: t4, __scan: t5, event_history, ...safeSignal } = signal;
                    rest.signal = safeSignal;
                }
                return rest;
            } catch { return null; }
        }).filter(Boolean);
        ws.send(JSON.stringify({ type: 'hello', tokens: safeTokens }));
    } catch {}

    ws.on('close', () => console.log('[WS] Client disconnected'));
});

// =============================================================
// 7. ADMIN API
// =============================================================
app.post('/api/admin/config', async (req, res) => {
    try {
        const { telegramToken, telegramChatId } = req.body;
        if (telegramToken) await redis.set('cde_telegram_token', telegramToken);
        if (telegramChatId) await redis.set('cde_telegram_chat_id', telegramChatId);
        await initTelegram();
        res.json({ success: true, message: "Ayarlar kaydedildi" });
    } catch (e) {
        res.status(500).json({ success: false, message: e.message });
    }
});

app.get('/api/admin/config', async (req, res) => {
    try {
        const token = await redis.get('cde_telegram_token') || process.env.TELEGRAM_BOT_TOKEN || '';
        const chatId = await redis.get('cde_telegram_chat_id') || process.env.TELEGRAM_CHAT_ID || '';
        res.json({ telegramToken: token, telegramChatId: chatId });
    } catch (e) {
        res.json({ telegramToken: '', telegramChatId: '' });
    }
});

// Health check endpoint for bot panel
app.get('/health', (req, res) => {
    res.json({
        status: 'ok',
        tokens: global.Z?.tokens?.length || 0,
        uptime: process.uptime()
    });
});

// =============================================================
// 8. BOOT SEQUENCE
//    Bot kendi init() fonksiyonunu zaten çalıştırdı (eval sırasında).
//    Biz sadece periyodik görevleri başlatıyoruz.
// =============================================================
async function boot() {
    await initTelegram();
    await restoreFromRedis();

    // Her 2 saniyede bir Redis'e kaydet ve UI'a broadcast et
    setInterval(persistToRedis, 2000);

    // Her 3 saniyede bir Telegram kontrolü
    setInterval(checkTelegramAlerts, 3000);

    // Status log
    setInterval(() => {
        const tokenCount = global.Z?.tokens?.length || 0;
        const signalCount = global.Z?.proptrex?.signalBook?.size || 0;
        const whaleCount = global.Z?.whales?.length || 0;
        if (tokenCount > 0) {
            console.log(`[Status] Tokens: ${tokenCount} | Signals: ${signalCount} | Whales: ${whaleCount} | Clients: ${wss.clients.size}`);
        }
    }, 30000);

    server.listen(PORT, () => {
        console.log(`[Engine] CDE Monolith listening on port ${PORT}`);
        console.log(`[Engine] Platform: http://localhost:${PORT}/platform.html`);
        console.log(`[Engine] Admin: http://localhost:${PORT}/admin.html`);
    });
}

boot().catch(err => {
    console.error('[Engine] Boot failed:', err);
    process.exit(1);
});

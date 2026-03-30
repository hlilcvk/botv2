const fs = require('fs');
const path = require('path');

const srcPath = path.join(__dirname, '../tbx/proptrex_tbx.html');
const destHtml = path.join(__dirname, 'public/platform.html');
const destAdmin = path.join(__dirname, 'public/admin.html');

try {
  fs.mkdirSync(path.join(__dirname, 'public'), { recursive: true });

  let content = fs.readFileSync(srcPath, 'utf-8');

  // =============================================================
  // INTERCEPTOR STRATEGY:
  //
  // Orijinal HTML kendi başına Binance'e bağlanıp veri işleyebilir.
  // Biz sadece SINYAL hesaplamalarını devre dışı bırakıp,
  // Node.js sunucumuzdan gelen hazır sinyal verilerini enjekte ediyoruz.
  //
  // DOKUNULMAYAN (Doğrudan Binance'ten akan):
  //   - !ticker@arr (anlık fiyatlar, hacimler)
  //   - !markPrice@arr@1s (funding rate, mark price)
  //   - aggTrade (balina verisi)
  //   - kline (grafik mumları)
  //   - CoinGecko/CryptoCompare (haber ve temel veriler)
  //
  // SUNUCUDAN GELEN (overlay):
  //   - Sinyal verileri (signal, tp_matrix, entry zone, vs.)
  //   - Bu veriler Z.tokens içerisindeki objelere merge edilir.
  //
  // Böylece arayüz %100 gerçek piyasa verisi gösterir,
  // sinyaller ise sunucunun kalıcı hafızasından (Redis) gelir.
  // =============================================================
  
  const interceptScript = `
  <script>
    // --- CDE V2: SERVER-SIDE SIGNAL OVERLAY ---
    // Arayüz kendi piyasa verilerini Binance'ten alır.
    // Sinyal hesaplamaları sunucuda yapılır ve buraya merge edilir.
    
    (function() {
      var CDE_WS_URL = location.protocol === 'https:' 
        ? 'wss://' + location.host + '/ws' 
        : 'ws://' + location.host + '/ws';
      
      var cdeSocket = null;
      var cdeReconnectTimer = null;
      
      function cdeConnect() {
        try {
          cdeSocket = new WebSocket(CDE_WS_URL);
          
          cdeSocket.onopen = function() {
            console.log('[CDE] Connected to backend signal server');
          };
          
          cdeSocket.onmessage = function(e) {
            try {
              var data = JSON.parse(e.data);
              if (!window.Z || !window.Z.bySym) return;
              
              if (data.type === 'hello' || data.type === 'tokens_update') {
                if (!data.tokens || !Array.isArray(data.tokens)) return;
                
                // Sadece sinyal verilerini merge et, piyasa verilerini DOKUNMA
                data.tokens.forEach(function(serverToken) {
                  if (!serverToken.sym || !serverToken.signal) return;
                  var localToken = window.Z.bySym.get(serverToken.sym);
                  if (!localToken) return;
                  
                  // Sunucudan gelen sinyal verisini local token'a inject et
                  localToken.signal = serverToken.signal;
                });
              }
            } catch(err) {
              // Sessizce devam et
            }
          };
          
          cdeSocket.onclose = function() {
            console.log('[CDE] Disconnected from backend, reconnecting in 3s...');
            cdeReconnectTimer = setTimeout(cdeConnect, 3000);
          };
          
          cdeSocket.onerror = function() {};
          
        } catch(err) {
          console.warn('[CDE] Connection error:', err);
          cdeReconnectTimer = setTimeout(cdeConnect, 5000);
        }
      }
      
      // Sinyal hesaplamalarını devre dışı bırak — sunucu bunları yapıyor
      // Aşağıdaki fonksiyonlar, orijinal script yüklendikten SONRA override edilecek
      var _origOnload = window.onload;
      window.addEventListener('load', function() {
        // Frontend sinyal hesaplamasını kapat, sunucudan gelecek
        if (typeof window.runSignalLayer === 'function') {
          window.runSignalLayer = function() {}; // Sunucu hesaplıyor
          console.log('[CDE] Frontend signal layer disabled (server handles signals)');
        }
        
        // Connect to backend
        cdeConnect();

        // Original onload varsa çağır
        if (typeof _origOnload === 'function') _origOnload();
      });
    })();
  </script>
  `;

  // Inject interceptor BEFORE the main script (inside <head>)
  content = content.replace('<head>', '<head>' + interceptScript);

  fs.writeFileSync(destHtml, content);
  console.log('Created public/platform.html — Full UI with server signal overlay');

  // =============================================================
  // ADMIN PANEL
  // =============================================================
  const adminHtml = `<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>CDE V2 - Admin Panel</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Inter', sans-serif; background: #0f172a; color: #f8fafc; min-height: 100vh; padding: 40px 20px; }
    .container { max-width: 640px; margin: 0 auto; }
    .card { background: #1e293b; padding: 28px; border-radius: 16px; box-shadow: 0 8px 32px rgba(0,0,0,0.4); margin-bottom: 24px; border: 1px solid #334155; }
    h1 { font-size: 22px; font-weight: 800; color: #e2e8f0; margin-bottom: 6px; }
    .subtitle { font-size: 13px; color: #64748b; margin-bottom: 20px; }
    label { display: block; margin-top: 16px; font-weight: 600; color: #94a3b8; font-size: 13px; letter-spacing: 0.3px; }
    input { width: 100%; padding: 12px 14px; margin-top: 6px; background: #0f172a; border: 1px solid #334155; color: #f8fafc; border-radius: 8px; font-family: 'IBM Plex Mono', 'Inter', monospace; font-size: 14px; }
    input:focus { outline: none; border-color: #3b82f6; box-shadow: 0 0 0 3px rgba(59,130,246,0.15); }
    .btn { background: linear-gradient(135deg, #3b82f6, #2563eb); color: white; border: none; padding: 14px 24px; border-radius: 10px; font-weight: 700; cursor: pointer; margin-top: 20px; font-size: 15px; width: 100%; transition: all 0.2s; }
    .btn:hover { transform: translateY(-1px); box-shadow: 0 4px 16px rgba(59,130,246,0.3); }
    .btn-test { background: linear-gradient(135deg, #059669, #047857); margin-top: 10px; }
    #msg { margin-top: 14px; font-size: 13px; text-align: center; min-height: 20px; }
    .status { display: flex; align-items: center; gap: 8px; padding: 12px 16px; background: #0f172a; border-radius: 8px; margin-top: 16px; }
    .status-dot { width: 8px; height: 8px; border-radius: 50%; }
    .status-dot.on { background: #22c55e; box-shadow: 0 0 6px #22c55e; }
    .status-dot.off { background: #ef4444; }
    .status-text { font-size: 12px; color: #94a3b8; }
    .stats { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 16px; }
    .stat { background: #0f172a; border-radius: 8px; padding: 12px; text-align: center; }
    .stat-val { font-size: 20px; font-weight: 800; color: #3b82f6; font-family: 'IBM Plex Mono', monospace; }
    .stat-label { font-size: 11px; color: #64748b; margin-top: 2px; }
  </style>
</head>
<body>
  <div class="container">
    <div class="card">
      <h1>⚡ CDE V2 Admin Panel</h1>
      <div class="subtitle">Confluence Detection Engine — Telegram & Sistem Ayarları</div>
      
      <div class="status" id="healthStatus">
        <div class="status-dot off" id="healthDot"></div>
        <div class="status-text" id="healthText">Bağlantı kontrol ediliyor...</div>
      </div>
      
      <div class="stats" id="statsGrid">
        <div class="stat"><div class="stat-val" id="sTokens">—</div><div class="stat-label">Taranan Coin</div></div>
        <div class="stat"><div class="stat-val" id="sUptime">—</div><div class="stat-label">Uptime</div></div>
      </div>
    </div>

    <div class="card">
      <h1>📱 Telegram Bildirim Ayarları</h1>
      <div class="subtitle">PRIORITY ve EXECUTION sinyalleri bu kanala gönderilir</div>
      
      <label>Bot Token</label>
      <input type="text" id="tToken" placeholder="123456789:ABCDEFghijklmnop...">
      
      <label>Chat ID</label>
      <input type="text" id="tChat" placeholder="-1001234567890">
      
      <button class="btn" onclick="saveSettings()">💾 AYARLARI KAYDET</button>
      <button class="btn btn-test" onclick="testHealth()">🔍 SUNUCU SAĞLIK KONTROLÜ</button>
      <div id="msg"></div>
    </div>
  </div>

  <script>
    async function load() {
      try {
        const res = await fetch('/api/admin/config');
        const data = await res.json();
        document.getElementById('tToken').value = data.telegramToken || '';
        document.getElementById('tChat').value = data.telegramChatId || '';
      } catch(e) {}
      testHealth();
    }

    async function saveSettings() {
      const token = document.getElementById('tToken').value.trim();
      const chat = document.getElementById('tChat').value.trim();
      try {
        const res = await fetch('/api/admin/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ telegramToken: token, telegramChatId: chat })
        });
        const data = await res.json();
        showMsg(data.success ? "✅ Ayarlar Redis'e başarıyla kaydedildi!" : "❌ Hata oluştu.", data.success);
      } catch(e) {
        showMsg("❌ Sunucu bağlantı hatası: " + e.message, false);
      }
    }

    async function testHealth() {
      try {
        const res = await fetch('/health');
        const data = await res.json();
        document.getElementById('healthDot').className = 'status-dot on';
        document.getElementById('healthText').textContent = 'Sunucu çalışıyor — ' + data.tokens + ' coin taranıyor';
        document.getElementById('sTokens').textContent = data.tokens || '0';
        document.getElementById('sUptime').textContent = Math.floor(data.uptime / 60) + 'm';
      } catch(e) {
        document.getElementById('healthDot').className = 'status-dot off';
        document.getElementById('healthText').textContent = 'Sunucu erişilemez';
      }
    }

    function showMsg(text, ok) {
      const el = document.getElementById('msg');
      el.textContent = text;
      el.style.color = ok ? '#22c55e' : '#ef4444';
      setTimeout(() => el.textContent = '', 4000);
    }

    setInterval(testHealth, 15000);
    load();
  </script>
</body>
</html>`;
  fs.writeFileSync(destAdmin, adminHtml);
  console.log('Created public/admin.html — Admin panel with health monitoring');

} catch (e) {
  console.error('Builder error:', e.message, e.stack);
  process.exit(1);
}

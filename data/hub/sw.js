// 协作台 Service Worker：缓存外壳，断网时至少能打开一个说明页
const CACHE = 'hub-v5';
const SHELL = ['/', '/manifest.json', '/icon-192.png', '/icon-512.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// 点击手机/桌面通知时唤醒或拉起协作台窗口（双轨激活：浏览器窗口激活 + 系统前台置顶）
self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const targetUrl = (e.notification && e.notification.data && e.notification.data.url) || 'http://127.0.0.1:8777/';

  // 1. 同时向后端发送系统级置顶激活请求（突破 Windows 前台锁定直接切到前台）
  try {
    fetch('/api/focus', { method: 'POST' }).catch(() => {});
  } catch (_) {}

  // 2. 遍历匹配并激活浏览器客户端
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
      for (let client of windowClients) {
        if (client.url && (client.url.includes('8777') || client.url.includes(self.registration.scope))) {
          if ('focus' in client) {
            return client.focus();
          }
        }
      }
      if (clients.openWindow) {
        return clients.openWindow(targetUrl);
      }
    })
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  // 接口一律走网络，不缓存聊天内容
  if (url.pathname.startsWith('/api/') || url.pathname === '/bg') return;
  if (e.request.method !== 'GET') return;
  // 页面导航一律走网络，避免旧外壳被缓存后一直看不到新版本
  if (e.request.mode === 'navigate') {
    e.respondWith(fetch(e.request).catch(() => caches.match('/')));
    return;
  }
  e.respondWith(
    fetch(e.request)
      .then((r) => {
        const copy = r.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
        return r;
      })
      .catch(() => caches.match(e.request).then((m) => m || caches.match('/')))
  );
});

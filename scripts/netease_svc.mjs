// 网易云音乐本地桥：给 Conflux 客户端提供搜索/播放地址/二维码登录
// 复用 DSH 插件 @doitian/dsh-music 的 netease.js（无额外依赖）
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
const root = path.resolve(process.env.CONFLUX_ROOT || path.join(path.dirname(fileURLToPath(import.meta.url)), '..'));
if (!fs.statSync(root).isDirectory()) throw new Error('CONFLUX_ROOT must name an existing directory');
const musicModule = process.env.CONFLUX_NETEASE_MODULE || path.join(os.homedir(), '.dsh', 'profiles', 'desktop', 'node_modules', '@doitian', 'dsh-music', 'lib', 'netease.js');
const { Netease } = await import(pathToFileURL(musicModule).href);
import QRCode from './vendor/qrcode.cjs';

const PORT = 8788;
const COOKIE_FILE = process.env.CONFLUX_NETEASE_COOKIE || path.join(process.env.CONFLUX_DATA_ROOT || path.join(root, 'data'), 'secrets', 'netease-cookie.txt');

function readCookie() {
  try { return fs.readFileSync(COOKIE_FILE, 'utf8').trim(); } catch { return ''; }
}
function saveCookie(header) {
  try {
    fs.mkdirSync(path.dirname(COOKIE_FILE), { recursive: true });
    fs.writeFileSync(COOKIE_FILE, header, 'utf8');
  } catch (e) {}
}

const nt = new Netease({ cookie: readCookie(), timeoutMs: 20000 });

function send(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
  res.end(body);
}

function tracksOf(list) {
  return (list || []).map(t => ({
    id: t.id, name: t.name,
    artists: Array.isArray(t.artists) ? t.artists.join(' / ') : (t.artists || ''),
    album: typeof t.album === 'object' ? ((t.album && t.album.name) || '') : (t.album || ''),
    picUrl: t.picUrl || (t.album && t.album.picUrl) || '',
    duration: t.duration || 0,
    vip: !!t.vip
  }));
}

const server = http.createServer(async (req, res) => {
  const u = new URL(req.url, 'http://127.0.0.1');
  try {
    if (u.pathname === '/health') {
      return send(res, 200, { ok: true, loggedIn: nt.authenticated, account: nt.account || null });
    }
    if (u.pathname === '/search') {
      const q = (u.searchParams.get('q') || '').trim();
      const limit = Math.min(50, Number(u.searchParams.get('limit') || 20));
      if (!q) return send(res, 400, { ok: false, reason: 'q 为空' });
      const r = await nt.searchSongs(q, { limit });
      const tracks = await nt.withCovers(r.tracks).catch(() => r.tracks);
      return send(res, 200, { ok: true, total: r.total, tracks: tracksOf(tracks) });
    }
    if (u.pathname === '/song/url') {
      const id = Number(u.searchParams.get('id'));
      const level = u.searchParams.get('level') || 'exhigh';
      if (!id) return send(res, 400, { ok: false, reason: 'id 缺失' });
      const r = await nt.songUrl(id, { level });
      return send(res, 200, r);
    }
    if (u.pathname === '/playlist') {
      const id = Number(u.searchParams.get('id'));
      if (!id) return send(res, 400, { ok: false, reason: 'id 缺失' });
      const p = await nt.playlistDetail(id);
      return send(res, 200, { ok: true, name: p.name, tracks: tracksOf(p.tracks) });
    }
    if (u.pathname === '/account') {
      if (!nt.authenticated) return send(res, 200, { ok: false, reason: '未登录' });
      const a = await nt.fetchAccount().catch(() => null);
      return send(res, 200, { ok: !!a, account: a });
    }
    if (u.pathname === '/login/qr') {
      const s = await nt.qrStart();
      const qr = QRCode(0, 'M');
      qr.addData(s.loginUrl);
      qr.make();
      return send(res, 200, { ok: true, unikey: s.unikey, loginUrl: s.loginUrl, svg: qr.createSvgTag(5, 8) });
    }
    if (u.pathname === '/login/poll') {
      const unikey = u.searchParams.get('unikey') || '';
      const r = await nt.qrPoll(unikey);
      if (r.done) saveCookie(nt.jar.header());
      return send(res, 200, { ok: true, code: r.code, done: !!r.done, message: r.message || '' });
    }
    if (u.pathname === '/cookie' && req.method === 'POST') {
      let buf = '';
      for await (const c of req) buf += c;
      const body = JSON.parse(buf || '{}');
      if (!body.cookie) return send(res, 400, { ok: false, reason: 'cookie 为空' });
      nt.setCookie(body.cookie);
      saveCookie(nt.jar.header());
      return send(res, 200, { ok: true, loggedIn: nt.authenticated });
    }
    send(res, 404, { ok: false, reason: 'not found' });
  } catch (e) {
    send(res, 500, { ok: false, reason: String((e && e.message) || e).slice(0, 200) });
  }
});

server.listen(PORT, '127.0.0.1', () => {
  console.log('netease bridge on', PORT, 'loggedIn=', nt.authenticated);
});

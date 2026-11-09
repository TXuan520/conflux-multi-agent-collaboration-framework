"""Local file delivery shared by hub HTTP and text-only agent channels."""
import base64
import json
import mimetypes
import os
import re
import shutil
import time
import uuid
from urllib.parse import quote, unquote

MAX_SIZE = 50 * 1024 * 1024
IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.avif', '.ico'}
GROUP_FILES_DIR = 'group-files'
MANIFEST_FILE = 'manifest.jsonl'
CAPABILITY_FILE = (
    '\n【出厂能力·群内直接交付文件】用户要求发文件或交付产物时，确认文件存在后，'
    '在回复末尾输出 [[file:<项目工作区>/实际文件名]]（替换为真实绝对路径），'
    '或 POST http://127.0.0.1:8777/api/attach，JSON 为 '
    '{"path":"完整绝对路径","from":"你的id","to":["human"]}。'
    '系统自动生成群文件卡片并收录文件区；支持当前工作区（含子项目目录）内的文件，'
    '上限50MB，禁止 secrets、napcat、admin.token；不能凭空生成不存在的文件。\n'
)


class FileStore:
    def __init__(self, root, workspace, hub_data):
        self.data = os.path.dirname(hub_data) if os.path.basename(hub_data) == 'hub' else hub_data
        self.hub_data = hub_data
        self.group_dir = os.path.join(self.data, GROUP_FILES_DIR)
        self.manifest_path = os.path.join(self.group_dir, MANIFEST_FILE)
        for directory in (workspace, os.path.join(workspace, 'tickets'), self.data, self.hub_data, self.group_dir):
            try:
                os.makedirs(directory, exist_ok=True)
            except OSError:
                pass
        self.roots = [os.path.realpath(root), os.path.realpath(workspace)]

    def validate(self, path):
        if not isinstance(path, str) or not os.path.isabs(path) or '\x00' in path:
            raise ValueError('需要完整绝对路径')
        # Check lexical and resolved paths: do not let junctions bypass either boundary.
        for candidate in (os.path.abspath(path), os.path.realpath(path)):
            norm = os.path.normcase(candidate)
            if not any(os.path.commonpath([norm, os.path.normcase(r)]) == os.path.normcase(r)
                       for r in self.roots if os.path.splitdrive(norm)[0] == os.path.splitdrive(os.path.normcase(r))[0]):
                raise ValueError('路径不在允许目录内')
            parts = re.split(r'[\\/]', norm.lower())
            if any(p in ('secrets', 'napcat', 'admin.token') for p in parts):
                raise ValueError('禁止访问敏感路径')
            if os.name == 'nt' and ':' in candidate[2:]:
                raise ValueError('禁止 NTFS 数据流')
        if not os.path.isfile(path):
            raise ValueError('文件不存在')
        return os.path.realpath(path)

    def resolve_url(self, url):
        for prefix, directory in (('/files/', 'files'), ('/img/', 'img')):
            if url.startswith(prefix):
                name = unquote(url[len(prefix):])
                if not name or name != os.path.basename(name) or '/' in name or '\\' in name or ':' in name:
                    raise ValueError('非法文件名')
                paths = [os.path.join(self.group_dir, name), os.path.join(self.data, directory, name)]
                if directory == 'img':
                    paths.append(os.path.join(self.hub_data, 'img', name))
                    # Legacy shrink_image changed PNG storage to JPEG without always fixing URLs.
                    paths += [os.path.splitext(path)[0] + ext
                              for path in list(paths) for ext in ('.jpg', '.png')]
                for path in paths:
                    if os.path.isfile(path):
                        return self.validate(path)
                raise ValueError('文件不存在')
        raise ValueError('非法文件 URL')

    def metadata(self, path, name=None, source_path='', url=None):
        ext = os.path.splitext(name or path)[1].lower()
        image = ext in IMAGE_EXTS
        return dict(name=name or os.path.basename(path), ext=ext, size=os.path.getsize(path),
                    url=url or ('/img/' if image else '/files/') + quote(os.path.basename(path)),
                    abs_path=path, source_path=source_path, is_image=image)

    def _is_group_file(self, path):
        try:
            real_path = os.path.normcase(os.path.realpath(path))
            real_group = os.path.normcase(os.path.realpath(self.group_dir))
            return (os.path.commonpath([real_path, real_group]) == real_group
                    and os.path.basename(real_path) != MANIFEST_FILE.lower())
        except (ValueError, OSError):
            return False

    def record_group_send(self, meta, sender='', msg_id=None, ts=None):
        path = meta.get('abs_path') or ''
        if not path or not os.path.isfile(path) or not self._is_group_file(path):
            return None
        entry = {
            **self.metadata(path, meta.get('name'), meta.get('source_path', ''), meta.get('url')),
            'id': msg_id or uuid.uuid4().hex[:8],
            'ts': float(ts if ts is not None else time.time()),
            'sender': str(sender or 'human').strip() or 'human',
        }
        os.makedirs(self.group_dir, exist_ok=True)
        with open(self.manifest_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        return entry

    def archive(self, path=None, name=None, raw=None):
        source = self.validate(path) if path is not None else ''
        name = os.path.basename((name or source or 'file').replace('\\', '/'))
        name = re.sub(r'[\x00-\x1f]', '', name)[:180] or 'file'
        size = os.path.getsize(source) if source else len(raw)
        if size > MAX_SIZE:
            raise OverflowError('文件不能超过50MB')
        ext = os.path.splitext(name)[1].lower()
        os.makedirs(self.group_dir, exist_ok=True)
        # Keep only the extension in the storage key; arbitrary upload names never become paths.
        target = os.path.join(self.group_dir, uuid.uuid4().hex + re.sub(r'[^.a-z0-9]', '', ext)[:20])
        try:
            if source:
                with open(source, 'rb') as src, open(target, 'xb') as dst:
                    shutil.copyfileobj(src, dst)
            else:
                with open(target, 'xb') as dst:
                    dst.write(raw)
            if os.path.getsize(target) > MAX_SIZE:
                raise OverflowError('文件不能超过50MB')
        except Exception:
            if os.path.exists(target):
                os.unlink(target)
            raise
        return self.metadata(target, name, source)

    def upload(self, body):
        data = body.get('data')
        if not isinstance(data, str):
            raise ValueError('需要 base64 data')
        data = data.split(',', 1)[-1]
        if len(data) > ((MAX_SIZE + 2) // 3) * 4:
            raise OverflowError('文件不能超过50MB')
        try:
            raw = base64.b64decode(data, validate=True)
        except Exception:
            raise ValueError('bad base64') from None
        return self.archive(name=body.get('name') or 'file', raw=raw)

    def markers(self, text):
        paths = re.findall(r'\[\[file:([^\]\r\n]+)\]\]', text, re.I)
        paths += re.findall(r'\[发送文件\]\(([^)\r\n]+)\)', text)
        return list(dict.fromkeys(p.strip() for p in paths))

    def list_files(self, messages=None):
        result, seen = [], set()
        # 1. Read explicit group-sent manifest entries in data/group-files/manifest.jsonl.
        if os.path.isfile(self.manifest_path):
            try:
                with open(self.manifest_path, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.read().splitlines()
            except OSError:
                lines = []
            for line in reversed(lines):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                url = item.get('url') or ''
                sender = str(item.get('sender') or '').strip()
                if not url or not sender:
                    continue
                try:
                    path = self.resolve_url(url)
                except (ValueError, OSError):
                    continue
                if not self._is_group_file(path) or path in seen:
                    continue
                seen.add(path)
                result.append({
                    **self.metadata(path, item.get('name'), item.get('source_path', ''), url),
                    'id': item.get('id') or url,
                    'ts': float(item.get('ts') or os.path.getmtime(path)),
                    'sender': sender,
                })
        # 2. Also include group-sent messages whose archived file resides inside data/group-files.
        for message in reversed(messages or []):
            if message.get('kind') not in ('image', 'file'):
                continue
            sender = str(message.get('sender') or '').strip()
            if not sender:
                continue
            meta = message.get('file_meta') or {}
            url = meta.get('url') or message.get('text', '')
            try:
                path = self.resolve_url(url)
            except (ValueError, OSError):
                continue
            if not self._is_group_file(path) or path in seen:
                continue
            seen.add(path)
            result.append({
                **self.metadata(path, meta.get('name'), meta.get('source_path', ''), url),
                'id': message.get('id'),
                'ts': float(message.get('ts', 0) or os.path.getmtime(path)),
                'sender': sender,
            })
        return sorted(result, key=lambda entry: entry['ts'], reverse=True)

    def serve(self, handler, url):
        try:
            path = self.resolve_url(url)
            stream = open(path, 'rb')
        except (ValueError, OSError):
            handler._json({'ok': False, 'error': '文件不存在'}, 404)
            return
        with stream:
            mime = mimetypes.guess_type(path)[0] or 'application/octet-stream'
            handler.send_response(200)
            handler.send_header('Content-Type', mime)
            handler.send_header('Content-Length', str(os.fstat(stream.fileno()).st_size))
            handler.send_header('X-Content-Type-Options', 'nosniff')
            # Documents with active browser content must download, not execute on the hub origin.
            inline = mime.startswith('image/') and mime != 'image/svg+xml' or mime in ('text/plain', 'application/pdf')
            handler.send_header('Content-Disposition', ('inline' if inline else 'attachment') +
                                "; filename*=UTF-8''" + quote(os.path.basename(path)))
            handler.end_headers()
            shutil.copyfileobj(stream, handler.wfile)

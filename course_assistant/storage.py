from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

BEIJING = timezone(timedelta(hours=8))


def now() -> str:
    return datetime.now(BEIJING).isoformat(timespec="seconds")


def today() -> str:
    return datetime.now(BEIJING).date().isoformat()


def fingerprint(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", "", text).encode()).hexdigest()


def revision(draft):
    return hashlib.sha256(json.dumps([draft.get(k, "") for k in ("id", "scope", "date", "kind", "title", "body", "target_url")], ensure_ascii=False).encode()).hexdigest()


def with_revision(row):
    value = dict(row)
    value["revision"] = revision(value)
    return value


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS drafts (
                  id TEXT PRIMARY KEY, scope TEXT NOT NULL, date TEXT NOT NULL,
                  kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
                  target_url TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft',
                  created_at TEXT NOT NULL, url TEXT NOT NULL DEFAULT '',
                  intent_hash TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, level TEXT, text TEXT);
            """)
            # A crashed submit is ambiguous: never automatically resend it.
            db.execute("UPDATE drafts SET status='uncertain' WHERE status='publishing'")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.connect() as db:
            db.execute("INSERT INTO kv VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (key, json.dumps(value, ensure_ascii=False)))

    def save_grading(self, scope, rules, expected_revision):
        key = "grading:" + scope
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
            previous = json.loads(row[0]) if row else {}
            if previous.get("revision", "") != expected_revision:
                raise ValueError("评分规则已在其他页面修改，请重新载入后核对，未覆盖原规则。")
            record = {"rules": rules, "revision": uuid.uuid4().hex, "saved_at": now()}
            db.execute("INSERT INTO kv VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (key, json.dumps(record, ensure_ascii=False)))
        return record

    def event(self, text, level="info"):
        # Never persist raw upstream responses or authentication values.
        clean = re.sub(r"(?i)(?:Bearer\s+\S+|sk-[A-Za-z0-9_-]+|key_[A-Za-z0-9_-]+)", "[已隐藏]", str(text))
        with self.connect() as db:
            db.execute("INSERT INTO events(at,level,text) VALUES (?,?,?)", (now(), level, clean[:700]))
            db.execute("DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 1000)")

    def events(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT at,level,text FROM events ORDER BY id DESC LIMIT 60")]

    def drafts(self, scope):
        with self.connect() as db:
            return [with_revision(r) for r in db.execute("SELECT * FROM drafts WHERE scope=? ORDER BY created_at DESC", (scope,))]

    def draft(self, id, scope):
        with self.connect() as db:
            row = db.execute("SELECT * FROM drafts WHERE id=? AND scope=?", (id, scope)).fetchone()
        if not row:
            raise ValueError("找不到当前课程下的草稿。")
        return with_revision(row)

    def create(self, scope, kind, title, body, target_url):
        id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO drafts(id,scope,date,kind,title,body,target_url,created_at) VALUES(?,?,?,?,?,?,?,?)",
                       (id, scope, today(), kind, title, body, target_url, now()))
        return id

    def edit(self, id, scope, title, body, target_url):
        with self.connect() as db:
            changed = db.execute("UPDATE drafts SET title=?,body=?,target_url=?,date=? WHERE id=? AND scope=? AND status='draft'",
                                 (title, body, target_url, today(), id, scope)).rowcount
        if changed != 1:
            raise ValueError("已提交或结果待确认的草稿不能修改，先核验平台结果。")

    def claim(self, id, scope, expected_revision):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM drafts WHERE id=? AND scope=?", (id, scope)).fetchone()
            if not row or row["status"] != "draft" or row["date"] != today():
                raise ValueError("草稿已处理或日期已变化；请先刷新核验，跨日草稿需重新保存并审阅。")
            if revision(dict(row)) != expected_revision:
                raise ValueError("草稿已在其他页面修改，请刷新后重新审阅，未发送。")
            digest = fingerprint(row["title"] + row["body"])
            duplicate = db.execute("SELECT id FROM drafts WHERE scope=? AND date=? AND intent_hash=? AND status IN ('publishing','uncertain','verified')",
                                   (scope, today(), digest)).fetchone()
            if duplicate:
                raise ValueError("相同内容已有发布记录或待核验记录，已阻止重复提交。")
            db.execute("UPDATE drafts SET status='publishing',intent_hash=? WHERE id=?", (digest, id))
        return dict(row)

    def result(self, id, verified, url=""):
        with self.connect() as db:
            db.execute("UPDATE drafts SET status=?,url=? WHERE id=?", ("verified" if verified else "uncertain", url, id))

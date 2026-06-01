"""
session.py — NEURO-SENTINEL WebSocket oturum yönetimi

Her WebSocket bağlantısı için:
  - session_id (UUID)
  - AES-256 oturum anahtarı (32 rastgele byte)
  - bağlantı zamanı
"""

import secrets
import base64
import time
import threading
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Session:
    session_id: str
    key_bytes: bytes          # 32 byte AES-256 anahtarı
    created_at: float = field(default_factory=time.time)
    username: str = "Operator"
    messages: list = field(default_factory=list)  # [{role,text,ts}]
    sensors: dict = field(default_factory=dict)   # son sensör okuması

    @property
    def key_b64(self) -> str:
        """Base64 kodlu anahtar — ESP32'ye gönderilir."""
        return base64.b64encode(self.key_bytes).decode()


class SessionStore:
    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self) -> Session:
        """Yeni session oluşturur ve depolar."""
        sess = Session(
            session_id=secrets.token_hex(16),
            key_bytes=secrets.token_bytes(32),   # kriptografik kalite
        )
        with self._lock:
            self._sessions[sess.session_id] = sess
        return sess

    def get(self, session_id: str) -> Optional[Session]:
        with self._lock:
            return self._sessions.get(session_id)

    def remove(self, session_id: str):
        with self._lock:
            self._sessions.pop(session_id, None)

    def cleanup_old(self, max_age_sec: int = 3600):
        """1 saatten eski session'ları temizler."""
        now = time.time()
        with self._lock:
            stale = [sid for sid, s in self._sessions.items()
                     if now - s.created_at > max_age_sec]
            for sid in stale:
                del self._sessions[sid]


# Global store — server.py'de import edilir
store = SessionStore()

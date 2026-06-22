import sqlite3
import os
import hashlib
import bcrypt
from datetime import date

DB_PATH = os.path.join(os.path.dirname(__file__), "alem_da_fe.db")


def hash_senha(senha):
    return bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()

def _verificar_senha(senha, hash_salvo):
    if hash_salvo.startswith("$2b$") or hash_salvo.startswith("$2a$"):
        return bcrypt.checkpw(senha.encode(), hash_salvo.encode())
    return hashlib.sha256(senha.encode()).hexdigest() == hash_salvo


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS mensagens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT NOT NULL,
            tipo TEXT NOT NULL,
            versiculo TEXT,
            texto_versiculo TEXT,
            reflexao TEXT,
            encorajamento TEXT,
            emoji TEXT,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            senha TEXT NOT NULL,
            plano TEXT DEFAULT 'gratuito',
            ativo INTEGER DEFAULT 1,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS codigos_recuperacao (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            codigo TEXT NOT NULL,
            expira_em TIMESTAMP NOT NULL,
            usado INTEGER DEFAULT 0
        )
    """)
    try:
        c.execute("ALTER TABLE usuarios ADD COLUMN plano_expira_em TIMESTAMP")
        conn.commit()
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE usuarios ADD COLUMN plano_trial INTEGER DEFAULT 0")
        conn.commit()
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE usuarios ADD COLUMN telefone TEXT")
        conn.commit()
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE mensagens ADD COLUMN usuario_id INTEGER")
        conn.commit()
    except Exception:
        pass
    c.execute("""
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            endpoint TEXT NOT NULL UNIQUE,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def criar_conta(nome, email, telefone, senha):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO usuarios (nome, email, telefone, senha) VALUES (?, ?, ?, ?)",
                  (nome, email, telefone, hash_senha(senha)))
        conn.commit()
        return True, c.lastrowid
    except sqlite3.IntegrityError:
        return False, None
    finally:
        conn.close()


def fazer_login(email, senha):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, nome, email, plano, senha FROM usuarios WHERE email=? AND ativo=1", (email,))
    row = c.fetchone()
    if row and _verificar_senha(senha, row[4]):
        if not row[4].startswith("$2b$"):
            c.execute("UPDATE usuarios SET senha=? WHERE id=?", (hash_senha(senha), row[0]))
            conn.commit()
        conn.close()
        return {"id": row[0], "nome": row[1], "email": row[2], "plano": row[3]}
    conn.close()
    return None


def salvar_mensagem(tipo, dados, usuario_id=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO mensagens (data, tipo, versiculo, texto_versiculo, reflexao, encorajamento, emoji, usuario_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        str(date.today()), tipo,
        dados.get("versiculo", ""), dados.get("texto_versiculo", ""),
        dados.get("reflexao", ""), dados.get("encorajamento", ""),
        dados.get("emoji", "✨"), usuario_id
    ))
    conn.commit()
    conn.close()


def email_existe(email):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id FROM usuarios WHERE email=? AND ativo=1", (email,))
    r = c.fetchone()
    conn.close()
    return r is not None

def salvar_codigo(email, codigo):
    from datetime import datetime, timedelta
    expira = datetime.now() + timedelta(minutes=15)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE codigos_recuperacao SET usado=1 WHERE email=?", (email,))
    c.execute("INSERT INTO codigos_recuperacao (email, codigo, expira_em) VALUES (?,?,?)",
              (email, codigo, expira))
    conn.commit()
    conn.close()

def verificar_codigo(email, codigo):
    from datetime import datetime
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT id FROM codigos_recuperacao
                 WHERE email=? AND codigo=? AND usado=0 AND expira_em > ?""",
              (email, codigo, datetime.now()))
    r = c.fetchone()
    if r:
        c.execute("UPDATE codigos_recuperacao SET usado=1 WHERE id=?", (r[0],))
        conn.commit()
    conn.close()
    return r is not None

def atualizar_senha(email, nova_senha):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE usuarios SET senha=? WHERE email=?", (hash_senha(nova_senha), email))
    conn.commit()
    conn.close()

def buscar_mensagem_hoje(tipo, usuario_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT versiculo, texto_versiculo, reflexao, encorajamento, emoji
        FROM mensagens WHERE data = ? AND tipo = ? AND usuario_id = ?
        ORDER BY criado_em DESC LIMIT 1
    """, (str(date.today()), tipo, usuario_id))
    row = c.fetchone()
    conn.close()
    if row:
        return {"versiculo": row[0], "texto_versiculo": row[1],
                "reflexao": row[2], "encorajamento": row[3], "emoji": row[4]}
    return None

def ativar_premium(usuario_id, dias, trial=False):
    from datetime import datetime, timedelta
    expira = datetime.now() + timedelta(days=dias)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE usuarios SET plano='premium', plano_expira_em=?, plano_trial=? WHERE id=?",
                 (expira, 1 if trial else 0, usuario_id))
    conn.commit()
    conn.close()

def verificar_expiracao(usuario_id):
    from datetime import datetime
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT plano, plano_expira_em, plano_trial FROM usuarios WHERE id=?", (usuario_id,)).fetchone()
    if row and row[0] == 'premium' and row[1]:
        if datetime.now() > datetime.fromisoformat(str(row[1])):
            conn.execute("UPDATE usuarios SET plano='gratuito', plano_expira_em=NULL, plano_trial=0 WHERE id=?", (usuario_id,))
            conn.commit()
            conn.close()
            return {'plano': 'gratuito', 'dias_restantes': 0, 'trial': False}
    conn.close()
    if not row:
        return {'plano': 'gratuito', 'dias_restantes': 0, 'trial': False}
    dias_restantes = 0
    if row[1]:
        diff = datetime.fromisoformat(str(row[1])) - datetime.now()
        dias_restantes = max(0, diff.days + 1)
    return {'plano': row[0], 'dias_restantes': dias_restantes, 'trial': bool(row[2])}

def salvar_push_subscription(usuario_id, endpoint, p256dh, auth):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO push_subscriptions (usuario_id, endpoint, p256dh, auth)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(endpoint) DO UPDATE SET p256dh=excluded.p256dh, auth=excluded.auth
    """, (usuario_id, endpoint, p256dh, auth))
    conn.commit()
    conn.close()

def buscar_todas_push_subscriptions():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT endpoint, p256dh, auth FROM push_subscriptions")
    rows = c.fetchall()
    conn.close()
    return [{"endpoint": r[0], "p256dh": r[1], "auth": r[2]} for r in rows]

def buscar_usuarios_com_push():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT DISTINCT usuario_id FROM push_subscriptions")
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]

def buscar_push_do_usuario(usuario_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE usuario_id=?", (usuario_id,))
    rows = c.fetchall()
    conn.close()
    return [{"endpoint": r[0], "p256dh": r[1], "auth": r[2]} for r in rows]

def excluir_usuario(usuario_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM push_subscriptions WHERE usuario_id=?", (usuario_id,))
    conn.execute("DELETE FROM mensagens WHERE usuario_id=?", (usuario_id,))
    conn.execute("DELETE FROM usuarios WHERE id=?", (usuario_id,))
    conn.commit()
    conn.close()

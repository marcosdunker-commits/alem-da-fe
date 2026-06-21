import os
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from database import (init_db, salvar_mensagem, buscar_mensagem_hoje, criar_conta,
                      fazer_login, email_existe, salvar_codigo, verificar_codigo,
                      atualizar_senha, salvar_push_subscription, buscar_todas_push_subscriptions,
                      ativar_premium, verificar_expiracao)
from ai import gerar_mensagem
from functools import wraps

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "alem-da-fe-secret-2026")

BRASIL_TZ = ZoneInfo("America/Sao_Paulo")
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.path.join(os.path.dirname(__file__), "vapid_private.pem")
VAPID_CLAIMS = {"sub": "mailto:marcosdunker@gmail.com"}


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "usuario_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def get_periodo_atual():
    hora = datetime.now(BRASIL_TZ).hour
    if 5 <= hora < 12:
        return "manha"
    elif 12 <= hora < 18:
        return "tarde"
    else:
        return "noite"


def enviar_push_para_todos(dados):
    try:
        from pywebpush import webpush, WebPushException
        subs = buscar_todas_push_subscriptions()
        texto_curto = dados.get("texto_versiculo", "")[:80] + "..."
        payload = json.dumps({
            "title": "Além da Fé — Mensagem do dia",
            "body": texto_curto,
            "url": "/home"
        })
        for sub in subs:
            try:
                webpush(
                    subscription_info={
                        "endpoint": sub["endpoint"],
                        "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}
                    },
                    data=payload,
                    vapid_private_key=VAPID_PRIVATE_KEY,
                    vapid_claims=VAPID_CLAIMS
                )
            except WebPushException as e:
                print(f"Push falhou para {sub['endpoint'][:40]}: {e}")
    except Exception as e:
        print(f"Erro geral no push: {e}")


def gerar_e_salvar(tipo, push=False):
    print(f"Gerando mensagem de {tipo}...")
    dados = gerar_mensagem(tipo)
    salvar_mensagem(tipo, dados)
    print(f"Mensagem de {tipo} salva!")
    if push:
        enviar_push_para_todos(dados)


def agendar_mensagens():
    scheduler = BackgroundScheduler(timezone=BRASIL_TZ)
    scheduler.add_job(lambda: gerar_e_salvar("manha", push=True), "cron", hour=7, minute=0)
    scheduler.add_job(lambda: gerar_e_salvar("tarde"), "cron", hour=12, minute=0)
    scheduler.add_job(lambda: gerar_e_salvar("noite"), "cron", hour=21, minute=0)
    scheduler.start()
    return scheduler


# ── PWA ─────────────────────────────────────────────

@app.route("/manifest.json")
def manifest():
    return app.send_static_file("manifest.json")

@app.route("/sw.js")
def sw():
    resp = app.send_static_file("sw.js")
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


# ── ROTAS PÚBLICAS ──────────────────────────────────

@app.route("/")
def login():
    if "usuario_id" in session:
        return redirect(url_for("home"))
    return render_template("login.html")


@app.route("/entrar", methods=["POST"])
def entrar():
    dados = request.get_json()
    usuario = fazer_login(dados.get("email", ""), dados.get("senha", ""))
    if usuario:
        session["usuario_id"] = usuario["id"]
        session["usuario_nome"] = usuario["nome"]
        session["usuario_plano"] = usuario["plano"]
        session["usuario_email"] = usuario["email"]
        return jsonify({"ok": True})
    return jsonify({"ok": False, "erro": "Email ou senha incorretos."})


@app.route("/registrar", methods=["POST"])
def registrar():
    dados = request.get_json()
    nome = dados.get("nome", "").strip()
    email = dados.get("email", "").strip()
    senha = dados.get("senha", "").strip()
    if not nome or not email or not senha:
        return jsonify({"ok": False, "erro": "Preencha todos os campos."})
    if len(senha) < 6:
        return jsonify({"ok": False, "erro": "A senha deve ter pelo menos 6 caracteres."})
    ok, uid = criar_conta(nome, email, senha)
    if ok:
        ativar_premium(uid, 7, trial=True)
        session["usuario_id"] = uid
        session["usuario_nome"] = nome
        session["usuario_plano"] = "premium"
        session["usuario_email"] = email
        return jsonify({"ok": True})
    return jsonify({"ok": False, "erro": "Este email já está cadastrado."})


@app.route("/esqueci-senha", methods=["GET", "POST"])
def esqueci_senha():
    if request.method == "GET":
        return render_template("esqueci_senha.html")
    dados = request.get_json()
    email = dados.get("email", "").strip().lower()
    if not email_existe(email):
        return jsonify({"ok": False, "erro": "Email não encontrado."})
    import random, smtplib
    from email.mime.text import MIMEText
    codigo = str(random.randint(100000, 999999))
    salvar_codigo(email, codigo)
    try:
        msg = MIMEText(f"""
Olá! Seu código de recuperação de senha é:

{codigo}

Este código expira em 15 minutos.

— Além da Fé
        """.strip())
        msg["Subject"] = "Código de recuperação — Além da Fé"
        msg["From"] = os.getenv("EMAIL_USER")
        msg["To"] = email
        with smtplib.SMTP("smtp.gmail.com", 587) as s:
            s.starttls()
            s.login(os.getenv("EMAIL_USER"), os.getenv("EMAIL_PASS"))
            s.send_message(msg)
    except Exception as e:
        return jsonify({"ok": False, "erro": "Erro ao enviar email. Tente novamente."})
    session["recuperacao_email"] = email
    return jsonify({"ok": True})


@app.route("/verificar-codigo", methods=["GET", "POST"])
def verificar_codigo_route():
    if request.method == "GET":
        return render_template("verificar_codigo.html")
    dados = request.get_json()
    email = session.get("recuperacao_email", "")
    codigo = dados.get("codigo", "").strip()
    if verificar_codigo(email, codigo):
        session["pode_redefinir"] = True
        return jsonify({"ok": True})
    return jsonify({"ok": False, "erro": "Código inválido ou expirado."})


@app.route("/nova-senha", methods=["GET", "POST"])
def nova_senha_route():
    if not session.get("pode_redefinir"):
        return redirect(url_for("login"))
    if request.method == "GET":
        return render_template("nova_senha.html")
    dados = request.get_json()
    senha = dados.get("senha", "")
    if len(senha) < 6:
        return jsonify({"ok": False, "erro": "A senha deve ter pelo menos 6 caracteres."})
    email = session.get("recuperacao_email", "")
    atualizar_senha(email, senha)
    session.pop("pode_redefinir", None)
    session.pop("recuperacao_email", None)
    return jsonify({"ok": True})


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── ROTAS PROTEGIDAS ────────────────────────────────

@app.route("/home")
@login_required
def home():
    info = verificar_expiracao(session["usuario_id"])
    session["usuario_plano"] = info["plano"]
    periodo = get_periodo_atual()
    mensagem = buscar_mensagem_hoje(periodo)
    if not mensagem:
        mensagem = gerar_mensagem(periodo)
        salvar_mensagem(periodo, mensagem)
    return render_template("index.html",
                           mensagem=mensagem,
                           periodo=periodo,
                           nome=session.get("usuario_nome", ""),
                           plano=session.get("usuario_plano", "gratuito"),
                           trial=info["trial"],
                           dias_restantes=info["dias_restantes"],
                           vapid_public_key=VAPID_PUBLIC_KEY)


@app.route("/cadastro")
@login_required
def cadastro():
    return render_template("cadastro.html",
                           nome=session.get("usuario_nome", ""),
                           plano=session.get("usuario_plano", "gratuito"))


@app.route("/nova-mensagem")
@login_required
def nova_mensagem():
    if session.get("usuario_plano") != "premium":
        return jsonify({"erro": "premium"}), 403
    dados = gerar_mensagem("aleatorio")
    return jsonify(dados)


@app.route("/criar-pagamento", methods=["POST"])
@login_required
def criar_pagamento():
    import mercadopago
    dados = request.get_json()
    plano = dados.get("plano", "mensal")
    if plano == "anual":
        titulo = "Além da Fé Premium — Anual"
        preco = 69.90
    else:
        titulo = "Além da Fé Premium — Mensal"
        preco = 9.90
    sdk = mercadopago.SDK(os.getenv("MP_ACCESS_TOKEN"))
    preference_data = {
        "items": [{"title": titulo, "quantity": 1, "unit_price": preco, "currency_id": "BRL"}],
        "payer": {"email": session.get("usuario_email", "")},
        "back_urls": {
            "success": "https://alemdafe.ddns.net/pagamento/sucesso",
            "failure": "https://alemdafe.ddns.net/pagamento/falha",
            "pending": "https://alemdafe.ddns.net/pagamento/pendente"
        },
        "auto_return": "approved",
        "notification_url": "https://alemdafe.ddns.net/webhook",
        "metadata": {"usuario_id": session.get("usuario_id")}
    }
    result = sdk.preference().create(preference_data)
    url = result["response"].get("init_point")
    return jsonify({"ok": bool(url), "url": url})


@app.route("/pagamento/sucesso")
@login_required
def pagamento_sucesso():
    import sqlite3
    conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "alem_da_fe.db"))
    row = conn.execute("SELECT plano FROM usuarios WHERE id=?", (session["usuario_id"],)).fetchone()
    conn.close()
    if row:
        session["usuario_plano"] = row[0]
    return redirect(url_for("home") + "?premium=ativado")


@app.route("/pagamento/falha")
def pagamento_falha():
    return redirect(url_for("home") + "?erro=pagamento")


@app.route("/pagamento/pendente")
def pagamento_pendente():
    return redirect(url_for("home") + "?pendente=1")


@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        import mercadopago
        data = request.get_json(force=True) or {}
        if data.get("type") == "payment":
            payment_id = data["data"]["id"]
            sdk = mercadopago.SDK(os.getenv("MP_ACCESS_TOKEN"))
            payment = sdk.payment().get(payment_id)["response"]
            if payment.get("status") == "approved":
                meta = payment.get("metadata", {})
                usuario_id = meta.get("usuario_id")
                titulo = payment.get("description", "")
                dias = 365 if "anual" in titulo.lower() else 30
                if usuario_id:
                    ativar_premium(usuario_id, dias)
    except Exception as e:
        print(f"Webhook erro: {e}")
    return jsonify({"ok": True}), 200


@app.route("/subscribe-push", methods=["POST"])
@login_required
def subscribe_push():
    sub = request.get_json()
    endpoint = sub.get("endpoint")
    p256dh = sub.get("keys", {}).get("p256dh")
    auth = sub.get("keys", {}).get("auth")
    if endpoint and p256dh and auth:
        salvar_push_subscription(session["usuario_id"], endpoint, p256dh, auth)
    return jsonify({"ok": True})


ADMIN_EMAIL = "marcosdunker@gmail.com"

@app.route("/admin")
def admin():
    if session.get("usuario_plano") != "admin" and session.get("usuario_email") != ADMIN_EMAIL:
        return redirect(url_for("login"))
    return render_template("admin.html")

@app.route("/admin/usuarios")
def admin_usuarios():
    if session.get("usuario_email") != ADMIN_EMAIL:
        return jsonify([])
    import sqlite3
    conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "alem_da_fe.db"))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, nome, email, plano FROM usuarios ORDER BY criado_em DESC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route("/admin/mudar-plano", methods=["POST"])
def admin_mudar_plano():
    if session.get("usuario_email") != ADMIN_EMAIL:
        return jsonify({"ok": False, "erro": "Sem permissão."})
    import sqlite3
    dados = request.get_json()
    conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "alem_da_fe.db"))
    conn.execute("UPDATE usuarios SET plano=? WHERE id=?", (dados["plano"], dados["id"]))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


if __name__ == "__main__":
    init_db()
    scheduler = agendar_mensagens()
    try:
        app.run(host="0.0.0.0", port=5050, debug=False, use_reloader=True)
    finally:
        scheduler.shutdown()

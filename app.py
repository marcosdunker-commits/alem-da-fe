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
                      ativar_premium, verificar_expiracao, buscar_usuarios_com_push, buscar_push_do_usuario,
                      excluir_usuario, excluir_push_subscription, excluir_todas_push_do_usuario,
                      salvar_horarios, buscar_horarios, buscar_premium_com_push_na_hora, buscar_free_com_push,
                      buscar_premium_sem_horario_com_push, salvar_fcm_token, buscar_fcm_token)
from ai import gerar_mensagem
from functools import wraps

load_dotenv()

app = Flask(__name__)

# ── BÍBLIA ──────────────────────────────────────────
_BIBLIA_PATH = os.path.join(os.path.dirname(__file__), "biblia.json")
_BIBLIA_DATA = None

def _carregar_biblia():
    global _BIBLIA_DATA
    if not os.path.exists(_BIBLIA_PATH):
        import urllib.request
        urls = [
            "https://raw.githubusercontent.com/thiagobodruk/biblia/master/json/pt_aa.json",
            "https://raw.githubusercontent.com/thiagobodruk/biblia/main/json/pt_aa.json",
            "https://raw.githubusercontent.com/thiagobodruk/biblia/master/json/pt-br.json",
            "https://cdn.jsdelivr.net/gh/thiagobodruk/biblia@master/json/pt_aa.json",
        ]
        baixado = False
        for url in urls:
            try:
                print(f"Baixando Bíblia de {url}...")
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = resp.read()
                with open(_BIBLIA_PATH, "wb") as f:
                    f.write(data)
                print("Bíblia baixada com sucesso.")
                baixado = True
                break
            except Exception as e:
                print(f"Falhou ({url}): {e}")
        if not baixado:
            print("Aviso: Bíblia não pôde ser baixada. Usando API externa por capítulo.")
            return
    try:
        with open(_BIBLIA_PATH, encoding="utf-8") as f:
            _BIBLIA_DATA = json.load(f)
        print(f"Bíblia carregada: {len(_BIBLIA_DATA)} livros.")
    except Exception as e:
        print(f"Erro ao carregar Bíblia: {e}")

_carregar_biblia()
_secret = os.getenv("SECRET_KEY")
if not _secret:
    raise RuntimeError("SECRET_KEY não definida no .env")
app.secret_key = _secret
app.config["PERMANENT_SESSION_LIFETIME"] = __import__("datetime").timedelta(days=30)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

def _ler_versao():
    try:
        with open(os.path.join(os.path.dirname(__file__), "version.txt")) as f:
            return f.read().strip()
    except Exception:
        return "?"

@app.context_processor
def inject_version():
    return {"app_version": _ler_versao()}

BRASIL_TZ = ZoneInfo("America/Sao_Paulo")
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY_B64", "")
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


_firebase_app = None

def _get_firebase_app():
    global _firebase_app
    if _firebase_app:
        return _firebase_app
    b64 = os.getenv("FIREBASE_CREDENTIALS_B64")
    if not b64:
        print("[FCM] FIREBASE_CREDENTIALS_B64 não definida no .env — FCM desativado")
        return None
    try:
        import base64, json, firebase_admin
        from firebase_admin import credentials
        cred_dict = json.loads(base64.b64decode(b64).decode("utf-8"))
        cred = credentials.Certificate(cred_dict)
        _firebase_app = firebase_admin.initialize_app(cred)
        print("[FCM] Firebase inicializado com sucesso")
        return _firebase_app
    except Exception as e:
        print(f"[FCM] Firebase init erro: {e}")
        return None

def _enviar_fcm(fcm_token, titulo, corpo):
    if not _get_firebase_app():
        print("[FCM] Firebase não inicializado — não foi possível enviar")
        return
    try:
        from firebase_admin import messaging
        msg = messaging.Message(
            notification=messaging.Notification(title=titulo, body=corpo),
            token=fcm_token,
            apns=messaging.APNSConfig(payload=messaging.APNSPayload(
                aps=messaging.Aps(sound="default")
            ))
        )
        messaging.send(msg)
        print(f"[FCM] ✓ Enviado para token {fcm_token[:20]}...")
    except Exception as e:
        print(f"[FCM] ✗ Erro ao enviar: {e}")

def _enviar_push_para_lista(uids, tipo):
    from pywebpush import webpush, WebPushException
    for uid in uids:
        try:
            dados = gerar_mensagem(tipo)
            salvar_mensagem(tipo, dados, uid)
            titulo = "Além da Fé — Mensagem do dia"
            texto_curto = dados.get("texto_versiculo", "")[:100] + "..."
            payload = json.dumps({"title": titulo, "body": texto_curto, "url": "/home"})
            # Web push (browser)
            for sub in buscar_push_do_usuario(uid):
                try:
                    webpush(
                        subscription_info={"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
                        data=payload,
                        vapid_private_key=VAPID_PRIVATE_KEY,
                        vapid_claims=VAPID_CLAIMS
                    )
                except WebPushException as e:
                    resp = getattr(e, "response", None)
                    if resp is not None and resp.status_code in (404, 410):
                        excluir_push_subscription(sub["endpoint"])
                    else:
                        print(f"Push falhou para usuário {uid}: {e}")
            # FCM push (iOS app)
            fcm_token = buscar_fcm_token(uid)
            if fcm_token:
                _enviar_fcm(fcm_token, titulo, texto_curto)
            else:
                print(f"[FCM] Sem token para usuário {uid} — notificação iOS não enviada")
        except Exception as e:
            print(f"Erro ao processar usuário {uid}: {e}")


def enviar_na_hora_atual():
    try:
        agora = datetime.now(BRASIL_TZ)
        hora_str = agora.strftime("%H:00")
        hora_int = agora.hour
        if hora_int < 12:
            tipo = "manha"
        elif hora_int < 18:
            tipo = "tarde"
        else:
            tipo = "noite"

        enviados = set()

        # Premium com esse horário cadastrado
        premium_ids = buscar_premium_com_push_na_hora(hora_str)
        if premium_ids:
            print(f"[{hora_str}] Enviando para {len(premium_ids)} usuário(s) premium...")
            _enviar_push_para_lista(premium_ids, tipo)
            enviados.update(premium_ids)

        # Às 7h: free users + premium sem horário cadastrado recebem
        if hora_int == 7:
            ids_7h = set(buscar_free_com_push()) | set(buscar_premium_sem_horario_com_push())
            ids_7h = [uid for uid in ids_7h if uid not in enviados]
            if ids_7h:
                print(f"[{hora_str}] Enviando para {len(ids_7h)} usuário(s) às 7h...")
                _enviar_push_para_lista(ids_7h, "manha")
    except Exception as e:
        print(f"Erro no envio horário: {e}")


def agendar_mensagens():
    scheduler = BackgroundScheduler(timezone=BRASIL_TZ)
    scheduler.add_job(enviar_na_hora_atual, "cron", minute=0)
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
        session.permanent = True
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
    telefone = dados.get("telefone", "").strip()
    senha = dados.get("senha", "").strip()
    if not nome or not email or not telefone or not senha:
        return jsonify({"ok": False, "erro": "Preencha todos os campos."})
    if len(senha) < 6:
        return jsonify({"ok": False, "erro": "A senha deve ter pelo menos 6 caracteres."})
    ok, uid = criar_conta(nome, email, telefone, senha)
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
    uid = session["usuario_id"]
    mensagem = buscar_mensagem_hoje(periodo, uid)
    if not mensagem:
        mensagem = gerar_mensagem(periodo)
        salvar_mensagem(periodo, mensagem, uid)
    return render_template("index.html",
                           mensagem=mensagem,
                           periodo=periodo,
                           nome=session.get("usuario_nome", ""),
                           plano=session.get("usuario_plano", "gratuito"),
                           trial=info["trial"],
                           dias_restantes=info["dias_restantes"],
                           vapid_public_key=VAPID_PUBLIC_KEY,
                           is_admin=session.get("usuario_email") == ADMIN_EMAIL)


@app.route("/cadastro")
@login_required
def cadastro():
    return render_template("cadastro.html",
                           nome=session.get("usuario_nome", ""),
                           plano=session.get("usuario_plano", "gratuito"),
                           vapid_public_key=VAPID_PUBLIC_KEY)


@app.route("/desativar-push", methods=["POST"])
@login_required
def desativar_push():
    excluir_todas_push_do_usuario(session["usuario_id"])
    return jsonify({"ok": True})


@app.route("/salvar-horarios", methods=["POST"])
@login_required
def salvar_horarios_route():
    if session.get("usuario_plano") != "premium":
        return jsonify({"ok": False, "erro": "premium"})
    dados = request.get_json()
    horarios = dados.get("horarios", [])
    horarios_validos = []
    for h in horarios:
        h = str(h).strip()
        if len(h) == 5 and h[2] == ":" and h[:2].isdigit() and h[3:].isdigit():
            hh, mm = int(h[:2]), int(h[3:])
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                horarios_validos.append(f"{hh:02d}:00")
    salvar_horarios(session["usuario_id"], list(dict.fromkeys(horarios_validos)))
    return jsonify({"ok": True})


@app.route("/buscar-horarios")
@login_required
def buscar_horarios_route():
    if session.get("usuario_plano") != "premium":
        return jsonify([])
    return jsonify(buscar_horarios(session["usuario_id"]))


@app.route("/biblia")
@login_required
def biblia():
    return render_template("biblia.html",
                           nome=session.get("usuario_nome", ""),
                           plano=session.get("usuario_plano", "gratuito"))


@app.route("/api/biblia/<int:livro>/<int:capitulo>")
@login_required
def api_biblia(livro, capitulo):
    import urllib.request as _urlreq
    # Tenta dados locais primeiro (mais rápido)
    if _BIBLIA_DATA is not None and 0 <= livro < len(_BIBLIA_DATA):
        chapters = _BIBLIA_DATA[livro].get("chapters", [])
        if 1 <= capitulo <= len(chapters):
            verses = chapters[capitulo - 1]
            return jsonify({"verses": [{"verse": i + 1, "text": v} for i, v in enumerate(verses)]})
    # Fallback: api.getbible.net por capítulo (livro é 1-based na API)
    try:
        url = f"https://api.getbible.net/v2/almeida/{livro + 1}/{capitulo}.json"
        req = _urlreq.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with _urlreq.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        verses = [{"verse": v["verse"], "text": v["text"]} for v in data.get("verses", [])]
        return jsonify({"verses": verses})
    except Exception:
        return jsonify({"error": "not found"}), 404


@app.route("/nova-mensagem")
@login_required
def nova_mensagem():
    if session.get("usuario_plano") != "premium":
        return jsonify({"erro": "premium"}), 403
    uid = session["usuario_id"]
    dados = gerar_mensagem("aleatorio")
    salvar_mensagem("aleatorio", dados, uid)
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
        "payment_methods": {
            "excluded_payment_types": [],
            "installments": 1
        },
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


@app.route("/subscribe-push-ios", methods=["POST"])
@login_required
def subscribe_push_ios():
    data = request.get_json()
    token = data.get("token", "").strip()
    salvar_fcm_token(session["usuario_id"], token if token else None)
    return jsonify({"ok": True})


@app.route("/status-push-ios")
@login_required
def status_push_ios():
    token = buscar_fcm_token(session["usuario_id"])
    return jsonify({"ativo": bool(token)})


ADMIN_EMAIL = "marcosdunker@gmail.com"

@app.route("/admin")
def admin():
    if session.get("usuario_email") != ADMIN_EMAIL:
        return redirect(url_for("login"))
    return render_template("admin.html")

@app.route("/admin/usuarios")
def admin_usuarios():
    if session.get("usuario_email") != ADMIN_EMAIL:
        return jsonify([])
    import sqlite3
    conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "alem_da_fe.db"))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, nome, email, telefone, plano FROM usuarios ORDER BY criado_em DESC").fetchall()
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


@app.route("/admin/excluir-usuario", methods=["POST"])
def admin_excluir_usuario():
    if session.get("usuario_email") != ADMIN_EMAIL:
        return jsonify({"ok": False, "erro": "Sem permissão."})
    dados = request.get_json()
    usuario_id = dados.get("id")
    if not usuario_id or usuario_id == session.get("usuario_id"):
        return jsonify({"ok": False, "erro": "Operação inválida."})
    excluir_usuario(usuario_id)
    return jsonify({"ok": True})


@app.route("/admin/teste-push", methods=["POST"])
def admin_teste_push():
    if session.get("usuario_email") != ADMIN_EMAIL:
        return jsonify({"ok": False, "erro": "Sem permissão."})
    dados = request.get_json() or {}
    uid = dados.get("usuario_id")
    if not uid:
        email = dados.get("email") or session.get("usuario_email")
        import sqlite3
        conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "alem_da_fe.db"))
        row = conn.execute("SELECT id FROM usuarios WHERE email=?", (email,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"ok": False, "erro": "Usuário não encontrado."})
        uid = row[0]
    resultados = []
    from pywebpush import webpush, WebPushException
    titulo = "Além da Fé — Teste"
    corpo = "Esta é uma notificação de teste enviada pelo admin."
    payload = json.dumps({"title": titulo, "body": corpo, "url": "/home"})
    for sub in buscar_push_do_usuario(uid):
        try:
            webpush(
                subscription_info={"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=VAPID_CLAIMS
            )
            resultados.append({"tipo": "web", "ok": True})
        except WebPushException as e:
            resultados.append({"tipo": "web", "ok": False, "erro": str(e)})
    fcm_token = buscar_fcm_token(uid)
    if fcm_token:
        try:
            _enviar_fcm(fcm_token, titulo, corpo)
            resultados.append({"tipo": "fcm", "ok": True})
        except Exception as e:
            resultados.append({"tipo": "fcm", "ok": False, "erro": str(e)})
    else:
        resultados.append({"tipo": "fcm", "ok": False, "erro": "sem token FCM"})
    return jsonify({"ok": True, "resultados": resultados})


if __name__ == "__main__":
    init_db()
    scheduler = agendar_mensagens()
    try:
        app.run(host="0.0.0.0", port=5050, debug=False, use_reloader=False)
    finally:
        scheduler.shutdown()

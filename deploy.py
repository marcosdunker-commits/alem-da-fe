import paramiko, sys, io, os, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

VPS_HOST = "187.127.16.162"
VPS_USER = "root"
VPS_PASS = "+i3M3LAXKA@SdZ/.d8o#"
LOCAL    = r"C:\Users\jaque\Desktop\AlemDaFe"
REMOTE   = "/root/alemdafe"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(VPS_HOST, username=VPS_USER, password=VPS_PASS)

def run(cmd, timeout=60):
    _, o, e = ssh.exec_command(cmd, timeout=timeout)
    out = o.read().decode("utf-8", errors="replace").strip()
    err = e.read().decode("utf-8", errors="replace").strip()
    if out: print(out)
    if err and "warning" not in err.lower() and "deprecat" not in err.lower(): print("ERR:", err)

sftp = ssh.open_sftp()

# Cria estrutura de pastas no VPS
run(f"mkdir -p {REMOTE}/templates {REMOTE}/static")

# Arquivos principais
arquivos = ["app.py", "ai.py", "database.py", "requirements.txt", ".env", "vapid_private.pem"]
for f in arquivos:
    local_path = os.path.join(LOCAL, f)
    if os.path.exists(local_path):
        sftp.put(local_path, f"{REMOTE}/{f}")
        print(f"✅ {f} enviado")

# Templates
for f in os.listdir(os.path.join(LOCAL, "templates")):
    sftp.put(os.path.join(LOCAL, "templates", f), f"{REMOTE}/templates/{f}")
    print(f"✅ templates/{f} enviado")

# Static
for f in os.listdir(os.path.join(LOCAL, "static")):
    sftp.put(os.path.join(LOCAL, "static", f), f"{REMOTE}/static/{f}")
    print(f"✅ static/{f} enviado")

sftp.close()

# Instala dependências
run(f"cd {REMOTE} && python3 -m venv venv", timeout=60)
run(f"cd {REMOTE} && venv/bin/pip install -q flask groq apscheduler pillow python-dotenv", timeout=120)
print("✅ Dependências instaladas!")

# Inicia ou reinicia com pm2
run(f"pm2 describe alemdafe > /dev/null 2>&1 && pm2 restart alemdafe || pm2 start {REMOTE}/app.py --name alemdafe --interpreter {REMOTE}/venv/bin/python3")
time.sleep(5)
run("pm2 logs alemdafe --lines 10 --nostream")
print("\n🙏 Além da Fé no ar! Acesse: http://187.127.16.162:5050")
ssh.close()

import csv
import io
import json
import math
import os
import time
import smtplib
from email.message import EmailMessage
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from collections import defaultdict

import psycopg2
from psycopg2.extras import RealDictCursor
import pandas as pd
import requests
from flask import (
    Flask, render_template_string, request,
    redirect, url_for, flash, jsonify, session
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "fretados-secret-key-segura-2026")
TOKEN_SERIALIZER = URLSafeTimedSerializer(app.secret_key, salt="recuperacao-senha-fretados")

# Ligação oficial ao Supabase
# Ligação oficial ao Supabase
SUPABASE_DB_URL = os.environ.get("DATABASE_URL")

def conectar():
    if not SUPABASE_DB_URL:
        raise RuntimeError(
            "A variável de ambiente DATABASE_URL não foi configurada no Render."
        )

    return psycopg2.connect(
        SUPABASE_DB_URL,
        cursor_factory=RealDictCursor,
        connect_timeout=10
    )

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_URL = "https://router.project-osrm.org/route/v1/driving"
HEADERS = {"User-Agent": "FretadosIntelligence/5.0 (gestao inteligente de transporte)"}

def iniciar_banco():
    conn = conectar()
    try:
        with conn.cursor() as cursor:
            # 1. Tabela de Utilizadores
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS usuarios (
                    id SERIAL PRIMARY KEY,
                    usuario TEXT UNIQUE NOT NULL,
                    senha TEXT NOT NULL,
                    email TEXT DEFAULT '',
                    perfil TEXT NOT NULL,
                    nome_exibicao TEXT NOT NULL,
                    endereco TEXT DEFAULT '',
                    cep TEXT DEFAULT '',
                    regiao TEXT DEFAULT 'Geral',
                    turno TEXT DEFAULT 'AM',
                    rota TEXT DEFAULT 'Rota 502',
                    latitude REAL,
                    longitude REAL
                );
            """)

            # 2. Tabela de Veículos
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS veiculos (
                    id SERIAL PRIMARY KEY,
                    nome TEXT NOT NULL,
                    motorista TEXT DEFAULT '',
                    capacidade INTEGER NOT NULL,
                    ativo INTEGER NOT NULL DEFAULT 1
                );
            """)

            # 3. Tabela de Histórico de Rotas
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS historico_rotas (
                    id SERIAL PRIMARY KEY,
                    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    turno TEXT,
                    regiao TEXT,
                    veiculo TEXT,
                    endereco_inicial TEXT,
                    funcionarios_json TEXT,
                    distancia_km REAL,
                    duracao_min REAL,
                    observacao TEXT
                );
            """)

            # Inserir admin inicial se não existir
            cursor.execute("SELECT COUNT(*) AS total FROM usuarios WHERE perfil = 'rh'")
            resultado_rh = cursor.fetchone()
            if resultado_rh and resultado_rh['total'] == 0:
                cursor.execute("""
                    INSERT INTO usuarios (usuario, senha, email, perfil, nome_exibicao, regiao, rota)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, ("admin_rh", "123", "admin@fretados.com", "rh", "Gestor RH Principal", "Geral", "Rota 502"))

            # Inserir veículos iniciais se não existirem
            cursor.execute("SELECT COUNT(*) AS total FROM veiculos")
            resultado_veiculos = cursor.fetchone()
            if resultado_veiculos and resultado_veiculos['total'] == 0:
                cursor.executemany("""
                    INSERT INTO veiculos (nome, motorista, capacidade)
                    VALUES (%s, %s, %s)
                """, [
                    ("Fretado Rota 502", "Carlos Motorista", 20),
                    ("Fretado Pinhais", "João Motorista", 15),
                    ("Fretado Colombo", "Marcos Motorista", 20),
                ])

        conn.commit()
    except Exception as e:
        print(f"Erro ao inicializar o banco: {e}")
        if 'conn' in locals() and conn:
            conn.rollback()
    finally:
        if 'conn' in locals() and conn:
            conn.close()


# Executar a inicialização do banco ao carregar a aplicação
iniciar_banco()

# =========================================================
# BANCO DE DADOS & AUTENTICAÇÃO (SUPABASE)
# =========================================================

def iniciar_banco():
    with conectar() as db:
        with db.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS usuarios (
                    id SERIAL PRIMARY KEY,
                    usuario TEXT UNIQUE NOT NULL,
                    senha TEXT NOT NULL,
                    email TEXT DEFAULT '',
                    perfil TEXT NOT NULL,
                    nome_exibicao TEXT NOT NULL,
                    endereco TEXT DEFAULT '',
                    cep TEXT DEFAULT '',
                    regiao TEXT DEFAULT 'Geral',
                    turno TEXT DEFAULT 'AM',
                    rota TEXT DEFAULT 'Rota 502',
                    latitude REAL,
                    longitude REAL
                )
            """)

            # Migração segura compatível com RealDictCursor
            cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'usuarios'")
            colunas_existentes = {col["column_name"] for col in cursor.fetchall()}
            
            colunas_necessarias = {
                "email": "TEXT DEFAULT ''",
                "endereco": "TEXT DEFAULT ''",
                "cep": "TEXT DEFAULT ''",
                "regiao": "TEXT DEFAULT 'Geral'",
                "turno": "TEXT DEFAULT 'AM'",
                "rota": "TEXT DEFAULT 'Rota 502'",
                "latitude": "REAL",
                "longitude": "REAL",
            }
            for nome_col, tipo_col in colunas_necessarias.items():
                if nome_col not in colunas_existentes:
                    cursor.execute(f"ALTER TABLE usuarios ADD COLUMN {nome_col} {tipo_col}")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS veiculos (
                    id SERIAL PRIMARY KEY,
                    nome TEXT NOT NULL,
                    motorista TEXT DEFAULT '',
                    capacidade INTEGER NOT NULL,
                    ativo INTEGER NOT NULL DEFAULT 1
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS historico_rotas (
                    id SERIAL PRIMARY KEY,
                    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    turno TEXT,
                    regiao TEXT,
                    veiculo TEXT,
                    endereco_inicial TEXT,
                    funcionarios_json TEXT,
                    distancia_km REAL,
                    duracao_min REAL,
                    observacao TEXT
                )
            """)

            cursor.execute("SELECT COUNT(*) AS total FROM usuarios WHERE perfil = 'rh'")
            resultado_rh = cursor.fetchone()
            if resultado_rh and resultado_rh['total'] == 0:
                cursor.execute("""
                    INSERT INTO usuarios (usuario, senha, email, perfil, nome_exibicao, regiao, rota)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, ("admin_rh", "123", "admin@fretados.com", "rh", "Gestor RH Principal", "Colombo", "Geral"))

            cursor.execute("SELECT COUNT(*) AS total FROM veiculos")
            resultado_veiculos = cursor.fetchone()
            if resultado_veiculos and resultado_veiculos['total'] == 0:
                cursor.executemany("""
                    INSERT INTO veiculos (nome, motorista, capacidade)
                    VALUES (%s, %s, %s)
                """, [
                    ("Fretado Rota 502", "Carlos Motorista", 20),
                    ("Fretado Pinhais", "João Motorista", 15),
                    ("Fretado Colombo", "Marcos Motorista", 20),
                ])
        db.commit()

iniciar_banco()


# =========================================================
# FUNÇÕES DE APOIO E GEOLOCALIZAÇÃO
# =========================================================

def buscar_usuarios_por_perfil(perfil):
    with conectar() as db:
        with db.cursor() as cursor:
            cursor.execute("SELECT * FROM usuarios WHERE perfil = %s ORDER BY rota, nome_exibicao", (perfil,))
            return cursor.fetchall()


def limpar_texto(valor):
    if valor is None:
        return ""
    return str(valor).strip()


def normalizar_turno(valor):
    turno = limpar_texto(valor).upper()
    if turno in ("AM", "MANHÃ", "MANHA", "MATUTINO"):
        return "AM"
    if turno in ("PM", "TARDE", "NOITE", "VESPERTINO", "NOTURNO"):
        return "PM"
    return "AM"


def geocodificar(endereco):
    endereco = limpar_texto(endereco)
    if not endereco:
        return (-25.4284, -49.2733)
    consulta = endereco
    if "brasil" not in endereco.lower() and "brazil" not in endereco.lower():
        consulta += ", Brasil"
    try:
        resposta = requests.get(NOMINATIM_URL, params={"q": consulta, "format": "jsonv2", "limit": 1, "countrycodes": "br"}, headers=HEADERS, timeout=5)
        resposta.raise_for_status()
        dados = resposta.json()
        if dados:
            return float(dados[0]["lat"]), float(dados[0]["lon"])
    except Exception:
        pass
    return (-25.4284, -49.2733)


def coordenadas_usuario(user):
    if user["latitude"] is not None and user["longitude"] is not None:
        return float(user["latitude"]), float(user["longitude"])
    query = f"{user['endereco']} {user['cep']}".strip()
    coordenadas = geocodificar(query)
    if coordenadas:
        with conectar() as db:
            with db.cursor() as cursor:
                cursor.execute("UPDATE usuarios SET latitude = %s, longitude = %s WHERE id = %s", (coordenadas[0], coordenadas[1], user["id"]))
            db.commit()
        time.sleep(0.4)
    return coordenadas


def distancia_aproximada(a, b):
    lat1, lon1 = a
    lat2, lon2 = b
    raio = 6371
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * raio * math.asin(min(1, math.sqrt(h)))


def ordenar_paradas(inicio, usuarios):
    restantes = list(usuarios)
    ordenados = []
    ponto_atual = inicio
    while restantes:
        escolhido = min(restantes, key=lambda u: distancia_aproximada(ponto_atual, (u["latitude"], u["longitude"])))
        ordenados.append(escolhido)
        ponto_atual = (escolhido["latitude"], escolhido["longitude"])
        restantes.remove(escolhido)
    return ordenados


def obter_rota_osrm(coordenadas):
    if len(coordenadas) < 2:
        return None
    caminho = ";".join(f"{lon},{lat}" for lat, lon in coordenadas)
    try:
        resposta = requests.get(f"{OSRM_URL}/{caminho}", params={"overview": "full", "geometries": "geojson", "steps": "false"}, timeout=5)
        resposta.raise_for_status()
        dados = resposta.json()
        if dados.get("code") == "Ok" and dados.get("routes"):
            rota = dados["routes"][0]
            return {"distancia_km": round(rota["distance"] / 1000, 2), "duracao_min": round(rota["duration"] / 60, 1), "geometria": rota["geometry"]}
    except Exception:
        pass
    dist_total = sum(distancia_aproximada(coordenadas[i], coordenadas[i+1]) for i in range(len(coordenadas) - 1))
    return {"distancia_km": round(dist_total, 2), "duracao_min": round((dist_total / 30) * 60, 1), "geometria": None}


# =========================================================
# LAYOUT HTML / CSS GLOBAL
# =========================================================

LAYOUT = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ titulo }} | Fretados Intelligence</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
:root {
  --bg:#080b12; --panel:#111824; --line:#26364b; --cyan:#00e5ff;
  --blue:#3578ff; --green:#29e6a3; --text:#eaf3ff; --muted:#8ea2bc; --red:#ff647c;
}
* { box-sizing:border-box; }
body {
  margin:0; background:radial-gradient(circle at top right,#10243b 0,#080b12 38%);
  color:var(--text); font-family:Inter,Segoe UI,Arial,sans-serif;
}
a { color:var(--cyan); text-decoration:none; }
header {
  display:flex; align-items:center; justify-content:space-between;
  gap:20px; flex-wrap:wrap; padding:20px 4%;
  border-bottom:1px solid var(--line); background:#090e18ee;
}
.logo { font-weight:900; letter-spacing:1px; font-size:20px; }
.logo span { color:var(--cyan); }
nav { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
nav a, .logout-btn {
  padding:9px 12px; border:1px solid var(--line);
  border-radius:9px; color:var(--text); font-size:13px; background:transparent;
}
nav a:hover, .logout-btn:hover { border-color:var(--cyan); color:var(--cyan); }
.logout-btn { background:#351924; border-color:#7d344a; cursor:pointer; font-weight:bold; }
main { max-width:1450px; margin:auto; padding:28px 4%; }
h1 { margin:0 0 8px; font-size:clamp(25px,3vw,36px); }
h2 { font-size:19px; margin-top:0; }
p { color:var(--muted); line-height:1.6; }
.eyebrow { color:var(--cyan); text-transform:uppercase; letter-spacing:2px; font-size:11px; font-weight:800; }
.grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin:22px 0; }
.grid2 { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:18px; }
.panel {
  background:linear-gradient(145deg,#131d2b,#0e1520);
  border:1px solid var(--line); border-radius:16px; padding:20px;
  box-shadow:0 10px 35px #0003; margin-bottom:18px;
}
.kpi { color:var(--muted); font-size:12px; text-transform:uppercase; }
.value { font-size:30px; font-weight:850; color:var(--cyan); margin-top:8px; }
label { display:block; font-size:13px; color:#b9cbe0; margin:12px 0 6px; }
input,select,textarea {
  width:100%; background:#090f19; color:var(--text);
  border:1px solid #2b3c53; border-radius:9px; padding:12px;
  outline:none; font:inherit;
}
input:focus,select:focus,textarea:focus { border-color:var(--cyan); }
button,.button {
  display:inline-block; cursor:pointer; border:0; border-radius:9px;
  padding:11px 15px; background:linear-gradient(90deg,var(--blue),#00bcd4);
  color:white; font-weight:800; font-size:13px; margin-top:12px;
}
button:hover,.button:hover { filter:brightness(1.13); }
.danger { background:#572435; color:#ffd8df; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th,td { text-align:left; padding:12px 9px; border-bottom:1px solid #253449; }
th { color:var(--cyan); font-weight:700; }
.table-wrap { overflow-x:auto; }
.tag { display:inline-block; border:1px solid #2b5369; color:var(--cyan); border-radius:20px; padding:4px 9px; font-size:11px; }
.notice { padding:12px 14px; border-radius:10px; background:#102b35; border:1px solid #1a6170; margin-bottom:12px; }
.error { background:#351924; border-color:#7d344a; }
.muted { color:var(--muted); font-size:12px; }
#map, #map-colaborador, #map-motorista { height:450px; width:100%; border-radius:12px; border:1px solid var(--line); }
.route-card { border:1px solid #2c4059; padding:14px; border-radius:12px; margin:12px 0; background:#121a26; }
footer { text-align:center; color:#61748d; padding:25px; font-size:12px; }
@media(max-width:900px) { .grid {grid-template-columns:repeat(2,minmax(0,1fr));} .grid2 {grid-template-columns:1fr;} }
</style>
</head>
<body>
<header>
  <div class="logo">FRETADOS <span>INTELLIGENCE</span></div>
  <nav>
    {% if session.get('usuario') %}
      <span class="tag" style="background:#112a3d;">{{ session.get('nome_exibicao') }} ({{ session.get('perfil')|upper }})</span>
      <a href="{{ url_for('painel') }}">Meu Painel</a>
      <a href="{{ url_for('logout') }}" class="logout-btn">Sair</a>
    {% else %}
      <a href="{{ url_for('login') }}">Entrar</a>
      <a href="{{ url_for('cadastrar') }}" style="background:var(--blue);">Criar Conta</a>
    {% endif %}
  </nav>
</header>
<main>
{% with mensagens = get_flashed_messages(with_categories=true) %}
  {% for categoria, mensagem in mensagens %}
    <div class="notice {% if categoria == 'error' %}error{% endif %}">{{ mensagem }}</div>
  {% endfor %}
{% endwith %}
{{ conteudo|safe }}
</main>
<footer>FRETADOS INTELLIGENCE · Gestão inteligente de transporte</footer>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
</body>
</html>
"""

def pagina(titulo, conteudo, **contexto):
    return render_template_string(LAYOUT, titulo=titulo, conteudo=render_template_string(conteudo, **contexto))


# =========================================================
# ROTAS DE AUTENTICAÇÃO E CRIAÇÃO DE CONTA
# =========================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usuario = limpar_texto(request.form.get("usuario"))
        senha = limpar_texto(request.form.get("senha"))
        with conectar() as db:
            with db.cursor() as cursor:
                cursor.execute("SELECT * FROM usuarios WHERE usuario = %s AND senha = %s", (usuario, senha))
                user = cursor.fetchone()
            if user:
                session["usuario"] = user["usuario"]
                session["perfil"] = user["perfil"]
                session["nome_exibicao"] = user["nome_exibicao"]
                flash("Login efetuado com sucesso!")
                return redirect(url_for("painel"))
            else:
                flash("Utilizador ou senha incorretos.", "error")

    conteudo = """
    <div class="eyebrow">Acesso ao Sistema</div>
    <h1>Entrar na sua Conta</h1>
    <div class="panel" style="max-width:450px; margin:20px auto;">
      <form method="post">
        <label>Nome de Utilizador</label><input name="usuario" required>
        <label>Senha</label><input type="password" name="senha" required>
        <button type="submit" style="width:100%; margin-top:15px;">Entrar</button>
      </form>
      <p style="text-align:center; margin-top:15px; font-size:13px;"><a href="{{ url_for('esqueci_senha') }}">Esqueci minha senha</a></p>
      <p style="text-align:center; margin-top:10px; font-size:13px;">Não tem conta? <a href="{{ url_for('cadastrar') }}">Registe-se aqui</a></p>
    </div>
    """
    return pagina("Login", conteudo)


@app.route("/cadastrar", methods=["GET", "POST"])
def cadastrar():
    if request.method == "POST":
        usuario = limpar_texto(request.form.get("usuario"))
        senha = limpar_texto(request.form.get("senha"))
        email = limpar_texto(request.form.get("email")).lower()
        nome = limpar_texto(request.form.get("nome"))
        perfil = limpar_texto(request.form.get("perfil"))
        endereco = limpar_texto(request.form.get("endereco"))
        cep = limpar_texto(request.form.get("cep"))
        regiao = limpar_texto(request.form.get("regiao")) or "Geral"
        turno = normalizar_turno(request.form.get("turno"))
        rota = limpar_texto(request.form.get("rota")) or "Rota 502"

        if not usuario or not senha or not nome or not perfil:
            flash("Preencha todos os campos obrigatórios.", "error")
            return redirect(url_for("cadastrar"))

        try:
            with conectar() as db:
                with db.cursor() as cursor:
                    cursor.execute("""
                        INSERT INTO usuarios (usuario, senha, email, perfil, nome_exibicao, endereco, cep, regiao, turno, rota)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (usuario, senha, email, perfil, nome, endereco, cep, regiao, turno, rota))
                db.commit()
            flash("Conta criada com sucesso! Faça login para continuar.")
            return redirect(url_for("login"))
        except Exception as e:
            flash(f"Erro ao criar conta (utilizador já pode existir): {e}", "error")

    conteudo = """
    <div class="eyebrow">Registo</div>
    <h1>Criar Nova Conta no Sistema</h1>
    <p>Escolha o seu perfil (RH, Motorista ou Colaborador) e preencha os seus dados.</p>
    
    <div class="panel" style="max-width:600px; margin:20px auto;">
      <form method="post">
        <label>Tipo de Perfil</label>
        <select name="perfil" required>
          <option value="colaborador">Colaborador (Passageiro)</option>
          <option value="motorista">Motorista de Fretado</option>
          <option value="rh">RH / Gestor de Operações</option>
        </select>

        <label>Nome Completo / Exibição</label><input name="nome" placeholder="Ex: Maria Silva" required>
        <label>Nome de Utilizador (Login)</label><input name="usuario" placeholder="Ex: maria.silva" required>
        <label>E-mail (Para recuperação de senha)</label><input type="email" name="email" placeholder="exemplo@email.com">
        <label>Senha de Acesso</label><input type="password" name="senha" required>

        <div style="border-top:1px solid var(--line); margin-top:15px; padding-top:10px;">
          <label>Endereço Completo (Para Colaboradores)</label><input name="endereco" placeholder="Ex: Rua das Flores, 100">
          <label>CEP</label><input name="cep" placeholder="83408-000">
          <label>Bairro / Região</label><input name="regiao" placeholder="Ex: Colombo / Pinhais">
          <label>Nome da Rota Atribuída</label><input name="rota" value="Rota 502">
          <label>Turno</label>
          <select name="turno"><option value="AM">AM — Manhã</option><option value="PM">PM — Tarde/Noite</option></select>
        </div>

        <button type="submit" style="width:100%; margin-top:20px;">Registar Conta</button>
      </form>
    </div>
    """
    return pagina("Registar", conteudo)


# =========================================================
# RECUPERAÇÃO DE SENHA
# =========================================================

def enviar_email_recuperacao(destinatario, link):
    host = os.environ.get("SMTP_HOST")
    usuario_smtp = os.environ.get("SMTP_USER")
    senha_smtp = os.environ.get("SMTP_PASSWORD")
    remetente = os.environ.get("SMTP_FROM", usuario_smtp or "")
    porta = int(os.environ.get("SMTP_PORT", "587"))
    if not all([host, usuario_smtp, senha_smtp, remetente]):
        print("\n[RECUPERAÇÃO DE SENHA - LINK NO TERMINAL]", link, "\n")
        return False
    mensagem = EmailMessage()
    mensagem["Subject"] = "Recuperação de senha - Fretados Intelligence"
    mensagem["From"] = remetente
    mensagem["To"] = destinatario
    mensagem.set_content(f"Olá! Para criar uma nova senha, abra este link (válido por 30 minutos):\n\n{link}")
    with smtplib.SMTP(host, porta, timeout=15) as servidor:
        servidor.starttls()
        servidor.login(usuario_smtp, senha_smtp)
        servidor.send_message(mensagem)
    return True


@app.route("/esqueci-senha", methods=["GET", "POST"])
def esqueci_senha():
    if request.method == "POST":
        email = limpar_texto(request.form.get("email")).lower()
        with conectar() as db:
            with db.cursor() as cursor:
                cursor.execute("SELECT id, usuario, email FROM usuarios WHERE lower(email) = %s", (email,))
                user = cursor.fetchone()
        if user and user["email"]:
            token = TOKEN_SERIALIZER.dumps({"id": user["id"], "email": user["email"]})
            link = url_for("redefinir_senha", token=token, _external=True)
            try:
                enviado = enviar_email_recuperacao(user["email"], link)
                if enviado:
                    flash("Se o e-mail estiver cadastrado, você receberá um link para redefinir a senha.")
                else:
                    flash("Link de recuperação gerado no terminal do Flask (teste local sem SMTP).", "error")
            except Exception:
                flash("Não foi possível enviar o e-mail agora.", "error")
        else:
            flash("Se o e-mail estiver cadastrado, você receberá um link para redefinir a senha.")
        return redirect(url_for("login"))
    
    conteudo = """
    <div class="eyebrow">Recuperação de acesso</div><h1>Esqueci minha senha</h1>
    <p>Informe o e-mail cadastrado na sua conta. Enviaremos um link válido por 30 minutos.</p>
    <div class="panel" style="max-width:480px;margin:20px auto;">
      <form method="post"><label>E-mail cadastrado</label><input type="email" name="email" required>
      <button type="submit" style="width:100%">Enviar link de recuperação</button></form>
      <p style="text-align:center; margin-top:15px;"><a href="{{ url_for('login') }}">Voltar ao login</a></p>
    </div>"""
    return pagina("Esqueci minha senha", conteudo)


@app.route("/redefinir-senha/<token>", methods=["GET", "POST"])
def redefinir_senha(token):
    try:
        dados = TOKEN_SERIALIZER.loads(token, max_age=1800)
    except SignatureExpired:
        flash("Este link expirou. Solicite uma nova recuperação de senha.", "error")
        return redirect(url_for("esqueci_senha"))
    except BadSignature:
        flash("Link de recuperação inválido.", "error")
        return redirect(url_for("esqueci_senha"))
    
    if request.method == "POST":
        senha = request.form.get("senha", "")
        confirmar = request.form.get("confirmar", "")
        if len(senha) < 3:
            flash("A senha deve ter pelo menos 3 caracteres.", "error")
        elif senha != confirmar:
            flash("As senhas não são iguais.", "error")
        else:
            with conectar() as db:
                with db.cursor() as cursor:
                    cursor.execute("UPDATE usuarios SET senha = %s WHERE id = %s", (senha, dados.get("id")))
                db.commit()
            flash("Senha alterada com sucesso. Entre com sua nova senha.")
            return redirect(url_for("login"))
            
    conteudo = """
    <div class="eyebrow">Nova senha</div><h1>Redefinir senha</h1>
    <div class="panel" style="max-width:480px;margin:20px auto;">
      <form method="post"><label>Nova senha</label><input type="password" name="senha" required>
      <label>Confirme a nova senha</label><input type="password" name="confirmar" required>
      <button type="submit" style="width:100%">Salvar nova senha</button></form>
    </div>"""
    return pagina("Redefinir senha", conteudo)


@app.route("/logout")
def logout():
    session.clear()
    flash("Sessão encerrada.")
    return redirect(url_for("login"))


@app.route("/")
def index():
    if not session.get("usuario"):
        return redirect(url_for("login"))
    return redirect(url_for("painel"))


@app.route("/painel")
def painel():
    if not session.get("usuario"):
        return redirect(url_for("login"))
    
    perfil = session.get("perfil")
    if perfil == "rh":
        return redirect(url_for("admin"))
    elif perfil == "motorista":
        return redirect(url_for("painel_motorista"))
    else:
        return redirect(url_for("portal_colaborador"))


# =========================================================
# PAINEL DO RH (Gestão Completa e Importação CSV)
# =========================================================

@app.route("/admin", methods=["GET", "POST"])
def admin():
    if session.get("perfil") != "rh":
        flash("Acesso restrito ao RH.", "error")
        return redirect(url_for("login"))

    if request.method == "POST":
        acao = request.form.get("acao", "")
        if acao == "importar_rota":
            rota_destino = limpar_texto(request.form.get("rota_destino"))
            arquivo = request.files.get("arquivo")
            if not rota_destino or not arquivo or not arquivo.filename:
                flash("Selecione a rota e envie o CSV.", "error")
                return redirect(url_for("admin"))
            try:
                df = pd.read_csv(arquivo, sep=None, engine="python")
                mapa = {str(c).strip().lower().replace(" ", "_"): c for c in df.columns}
                c_nome = next((mapa[k] for k in ["nome", "funcionario", "colaborador"] if k in mapa), None)
                c_end = next((mapa[k] for k in ["endereco", "endereço", "rua"] if k in mapa), None)
                c_cep = next((mapa[k] for k in ["cep"] if k in mapa), None)
                c_reg = next((mapa[k] for k in ["regiao", "região", "bairro"] if k in mapa), None)
                c_tur = next((mapa[k] for k in ["turno"] if k in mapa), None)

                inseridos = 0
                with conectar() as db:
                    with db.cursor() as cursor:
                        for _, row in df.iterrows():
                            n = limpar_texto(row[c_nome]) if c_nome else "Sem Nome"
                            e = limpar_texto(row[c_end]) if c_end else ""
                            cp = limpar_texto(row[c_cep]) if c_cep else ""
                            rg = limpar_texto(row[c_reg]) if c_reg else "Geral"
                            tr = normalizar_turno(row[c_tur]) if c_tur else "AM"
                            if n and e:
                                cursor.execute("""
                                    INSERT INTO usuarios (usuario, senha, perfil, nome_exibicao, endereco, cep, regiao, turno, rota)
                                    VALUES (%s, %s, 'colaborador', %s, %s, %s, %s, %s, %s)
                                """, (n.lower().replace(" ", "_") + str(time.time())[-4:], "123", n, e, cp, rg, tr, rota_destino))
                                inseridos += 1
                    db.commit()
                flash(f"Sucesso! {inseridos} colaboradores importados para a {rota_destino}.")
            except Exception as e:
                flash(f"Erro ao importar CSV: {e}", "error")
            return redirect(url_for("admin"))

    colaboradores = buscar_usuarios_por_perfil("colaborador")
    motoristas = buscar_usuarios_por_perfil("motorista")

    conteudo = """
    <div class="eyebrow">Painel do RH / Gestor</div>
    <h1>Administração Central & Planeamento</h1>
    
    <div style="margin-bottom:20px; display:flex; gap:10px; flex-wrap:wrap;">
      <a class="button" href="{{ url_for('planejador') }}">Planeador de Rotas & Mapa</a>
      <a class="button" href="{{ url_for('cadastrar') }}" style="background:#1d3a5e;">Criar Conta Manualmente</a>
    </div>

    <div class="panel">
      <h2>Importar CSV de Colaboradores por Rota</h2>
      <form method="post" enctype="multipart/form-data" class="grid2">
        <input type="hidden" name="acao" value="importar_rota">
        <div><label>Nome da Rota (ex: Rota 502)</label><input name="rota_destino" placeholder="Rota 502" required></div>
        <div><label>Ficheiro CSV</label><input type="file" name="arquivo" accept=".csv" required></div>
        <div style="grid-column: span 2;"><button type="submit">Carregar Planilha na Rota</button></div>
      </form>
    </div>

    <div class="panel">
      <h2>Colaboradores Registados ({{ colaboradores|length }})</h2>
      <div class="table-wrap"><table>
        <tr><th>Nome</th><th>Rota</th><th>Endereço</th><th>Bairro</th><th>Turno</th></tr>
        {% for c in colaboradores %}
        <tr><td><b>{{ c['nome_exibicao'] }}</b></td><td><span class="tag">{{ c['rota'] }}</span></td><td>{{ c['endereco'] }}</td><td>{{ c['regiao'] }}</td><td>{{ c['turno'] }}</td></tr>
        {% endfor %}
      </table></div>
    </div>
    """
    return pagina("Painel RH", conteudo, colaboradores=colaboradores, motoristas=motoristas)


@app.route("/planejador")
def planejador():
    if session.get("perfil") != "rh":
        return redirect(url_for("login"))
    
    colaboradores = buscar_usuarios_por_perfil("colaborador")
    rotas = sorted(set(c["rota"] for c in colaboradores if c["rota"]))
    
    template_planejador = """
    <div class="eyebrow">Logística</div>
    <h1>Planeador de Rota</h1>
    <a class="button" href="{{ url_for('admin') }}">← Voltar à Administração</a>
    <div class="grid2" style="margin-top:20px;">
      <section class="panel">
        <form id="form-planejamento">
          <label>Selecione a Rota</label>
          <select name="rota_selecionada" required>
            {% for r in rotas %}
              <option value="{{ r }}">{{ r }}</option>
            {% endfor %}
          </select>
          <label>Partida do Motorista (Garagem)</label><input name="inicio" value="Garagem Central, Colombo, PR" required>
          <label>Destino Final (Empresa)</label><input name="destino" value="Av. Industrial, 500, Curitiba, PR" required>
          <label>Turno</label><select name="turno"><option value="AM">AM</option><option value="PM">PM</option></select>
          <button type="submit" id="btn-p">Gerar Trajeto no Mapa</button>
        </form>
      </section>
      <section class="panel"><div id="map"></div></section>
    </div>
    <section class="panel"><div id="resultado"><p>Clique em gerar trajeto.</p></div></section>
    <script>
    let map, camada;
    window.addEventListener('DOMContentLoaded', () => {
        map = L.map('map').setView([-25.4284,-49.2733], 11);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(map);
        camada = L.featureGroup().addTo(map);
        setTimeout(() => map.invalidateSize(), 200);
    });
    document.getElementById('form-planejamento').addEventListener('submit', async function(e) {
        e.preventDefault();
        const resp = await fetch('/api/planejar', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(Object.fromEntries(new FormData(this).entries())) });
        const dados = await resp.json();
        if (dados.mapa_inicio) L.marker(dados.mapa_inicio).addTo(camada).bindPopup('Partida');
        if (dados.mapa_destino) L.marker(dados.mapa_destino).addTo(camada).bindPopup('Destino Final');
        let html = '';
        dados.rotas.forEach((r, i) => {
            if (r.geometria) L.geoJSON(r.geometria, { style: { color: '#00e5ff', weight: 4 } }).addTo(camada);
            html += `<div class="route-card"><h3>${r.veiculo}</h3><p>Distância: ${r.distancia_km} km | Tempo: ${r.duracao_min} min</p><ul>` + r.passageiros.map(p => `<li>${p.nome} — ${p.endereco}</li>`).join('') + `</ul></div>`;
        });
        document.getElementById('resultado').innerHTML = html;
        if (camada.getLayers().length > 0) map.fitBounds(camada.getBounds());
    });
    </script>
    """
    return render_template_string(LAYOUT, titulo="Planeador", conteudo=render_template_string(template_planejador, rotas=rotas))


@app.post("/api/planejar")
def api_planejar():
    try:
        dados = request.get_json() or {}
        inicio, destino, turno, rota_sel = limpar_texto(dados.get("inicio")), limpar_texto(dados.get("destino")), limpar_texto(dados.get("turno")), limpar_texto(dados.get("rota_selecionada"))
        coords_inicio, coords_destino = geocodificar(inicio), geocodificar(destino)
        
        colaboradores = [c for c in buscar_usuarios_por_perfil("colaborador") if c["rota"].lower() == rota_sel.lower()]
        validos = [ {**dict(c), "latitude": (coords := coordenadas_usuario(c))[0], "longitude": coords[1]} for c in colaboradores ]
        
        ordenados = ordenar_paradas(coords_inicio, validos)
        lote = ordenados[:20]
        pts = [coords_inicio] + [(p["latitude"], p["longitude"]) for p in lote] + [coords_destino]
        info = obter_rota_osrm(pts)
        
        return jsonify({"mapa_inicio": coords_inicio, "mapa_destino": coords_destino, "rotas": [{
            "veiculo": f"Fretado {rota_sel}", "motorista": "Atribuído", "regiao": rota_sel,
            "distancia_km": info["distancia_km"], "duracao_min": info["duracao_min"],
            "passageiros": [{"nome": p["nome_exibicao"], "endereco": p["endereco"]} for p in lote], "geometria": info["geometria"]
        }]})
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


# =========================================================
# PAINEL DO MOTORISTA (GPS ao Vivo)
# =========================================================

@app.route("/painel-motorista")
def painel_motorista():
    if session.get("perfil") != "motorista":
        flash("Acesso restrito a motoristas.", "error")
        return redirect(url_for("login"))
    
    colaboradores = buscar_usuarios_por_perfil("colaborador")
    
    template_motorista = """
    <div class="eyebrow">Painel do Motorista</div>
    <h1>Transmissão de GPS e Lista de Passageiros</h1>
    
    <div class="grid2">
      <section class="panel">
        <h2>Controle de Viagem</h2>
        <button onclick="iniciarGPS()" class="button" style="background:var(--green); color:#000;">Iniciar Transmissão GPS ao Vivo</button>
        <p id="status-gps" class="muted" style="margin-top:10px;">GPS desativado.</p>
      </section>
      <section class="panel">
        <h2>Mapa do Autocarro</h2>
        <div id="map-motorista"></div>
      </section>
    </div>

    <section class="panel">
      <h2>Passageiros Cadastrados</h2>
      <div class="table-wrap"><table>
        <tr><th>Nome</th><th>Endereço</th><th>Turno</th></tr>
        {% for c in colaboradores %}
        <tr><td><b>{{ c['nome_exibicao'] }}</b></td><td>{{ c['endereco'] }}</td><td>{{ c['turno'] }}</td></tr>
        {% endfor %}
      </table></div>
    </section>

    <script>
    function iniciarGPS() {
        if (!navigator.geolocation) { alert('GPS não suportado.'); return; }
        document.getElementById('status-gps').textContent = 'Transmitindo localização GPS...';
        navigator.geolocation.watchPosition(pos => {
            const lat = pos.coords.latitude;
            const lon = pos.coords.longitude;
            document.getElementById('status-gps').textContent = `GPS Ativo: Lat ${lat.toFixed(4)}, Lon ${lon.toFixed(4)}`;
            fetch('/api/atualizar-gps', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({lat, lon})
            });
        }, err => alert('Erro GPS: ' + err.message), { enableHighAccuracy: true });
    }
    window.addEventListener('DOMContentLoaded', () => {
        const mapM = L.map('map-motorista').setView([-25.4284, -49.2733], 13);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(mapM);
    });
    </script>
    """
    return render_template_string(LAYOUT, titulo="Painel Motorista", conteudo=render_template_string(template_motorista, colaboradores=colaboradores))


_gps_motorista = {"lat": -25.4284, "lon": -49.2733}

@app.post("/api/atualizar-gps")
def atualizar_gps():
    global _gps_motorista
    dados = request.get_json() or {}
    if "lat" in dados and "lon" in dados:
        _gps_motorista["lat"] = float(dados["lat"])
        _gps_motorista["lon"] = float(dados["lon"])
    return jsonify({"status": "ok"})


@app.get("/api/posicao-onibus")
def posicao_onibus():
    return jsonify(_gps_motorista)


# =========================================================
# PORTAL DO COLABORADOR
# =========================================================

@app.route("/portal-colaborador")
def portal_colaborador():
    if session.get("perfil") != "colaborador" and session.get("perfil") != "rh":
        flash("Acesso restrito.", "error")
        return redirect(url_for("login"))
    
    with conectar() as db:
        with db.cursor() as cursor:
            cursor.execute("SELECT * FROM usuarios WHERE usuario = %s", (session.get("usuario"),))
            user = cursor.fetchone()

    lat = user["latitude"] if user and user["latitude"] is not None else -25.4284
    lon = user["longitude"] if user and user["longitude"] is not None else -49.2733
    
    template_colaborador = """
    <div class="eyebrow">Portal do Colaborador</div>
    <h1>Olá, {{ user['nome_exibicao'] }}! 👋</h1>
    
    <div class="grid" style="margin:20px 0;">
      <div class="panel" style="background:#0a101a;"><div class="kpi">Sua Rota</div><div class="value" style="font-size:22px; color:var(--green);">{{ user['rota'] }}</div></div>
      <div class="panel" style="background:#0a101a;"><div class="kpi">Turno</div><div class="value" style="font-size:22px;">{{ user['turno'] }}</div></div>
      <div class="panel" style="background:#0a101a;"><div class="kpi">Região</div><div class="value" style="font-size:22px;">{{ user['regiao'] }}</div></div>
    </div>

    <div class="panel" style="border-color:var(--cyan);">
      <h3>O seu Ponto de Recolha</h3>
      <p>{{ user['endereco'] or 'Endereço não preenchido' }} (CEP: {{ user['cep'] or '—' }})</p>
      
      <h3 style="margin-top:20px;">Acompanhe o Fretado em Tempo Real</h3>
      <div id="map-colaborador"></div>
    </div>

    <script>
    let mapC, busMarker;
    window.addEventListener('DOMContentLoaded', () => {
        const lat = {{ lat }};
        const lon = {{ lon }};
        mapC = L.map('map-colaborador').setView([lat, lon], 14);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(mapC);
        
        L.marker([lat, lon]).addTo(mapC).bindPopup('<b>O seu Ponto</b>').openPopup();
        busMarker = L.marker([lat, lon], {
            icon: L.divIcon({className: 'bus-icon', html: '🚌', iconSize: [30, 30]})
        }).addTo(mapC).bindPopup('<b>Fretado em Trânsito</b>');

        setInterval(async () => {
            try {
                const resp = await fetch('/api/posicao-onibus');
                const pos = await resp.json();
                if (pos.lat && pos.lon) busMarker.setLatLng([pos.lat, pos.lon]);
            } catch(e) {}
        }, 5000);
    });
    </script>
    """
    return render_template_string(LAYOUT, titulo="Portal Colaborador", conteudo=render_template_string(template_colaborador, user=user, lat=lat, lon=lon))


if __name__ == "__main__":
    app.run(debug=True, port=5000)
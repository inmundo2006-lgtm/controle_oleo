import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
import time
import os

# ==========================
# CONFIGURAÇÕES
# ==========================
TENANT_ID = st.secrets["TENANT_ID"]
CLIENT_ID = st.secrets["CLIENT_ID"]
CLIENT_SECRET = st.secrets["CLIENT_SECRET"]
SITE_ID = st.secrets["SITE_ID"]

GRAPH_URL = "https://graph.microsoft.com/v1.0"
ARQUIVO_LOGO = "logo_ms.png"

USUARIOS = st.secrets["usuarios_oleo"]

TZ_LOCAL = timezone(timedelta(hours=-3))  # UTC-3 — Naviraí/MS

TIPOS_OLEO = {
    "Hidráulico": {
        "emoji": "🔵",
        "cor":       "#38bdf8",   # sky-400
        "cor_card":  "#0c2233",
        "borda":     "#0ea5e9",
    },
    "Transmissão": {
        "emoji": "🟠",
        "cor":       "#fb923c",   # orange-400
        "cor_card":  "#2a1500",
        "borda":     "#f97316",
    },
    "Motor": {
        "emoji": "🟢",
        "cor":       "#4ade80",   # green-400
        "cor_card":  "#0a2010",
        "borda":     "#22c55e",
    },
}

# ==========================
# FUNÇÕES AUTH GRAPH
# ==========================
@st.cache_data(ttl=55)
def obter_token():
    url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    payload = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }
    try:
        r = requests.post(url, data=payload)
        r.raise_for_status()
        return r.json().get("access_token")
    except:
        return None


# ==========================
# FUNÇÕES SHAREPOINT
# ==========================
def obter_dados_sharepoint(token, lista_id):
    url = (
        f"{GRAPH_URL}/sites/{SITE_ID}/lists/{lista_id}/items"
        f"?expand=fields&$top=2000"
    )
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.get(url, headers=headers)
        if not r.ok:
            st.error(f"Erro Graph API ({r.status_code}): {r.text[:300]}")
            return []
        dados = r.json().get("value", [])
        return [item["fields"] for item in dados]
    except Exception as e:
        st.error(f"Erro ao buscar dados: {e}")
        return []


def enviar_dados_sharepoint(token, lista_id, dados):
    url = f"{GRAPH_URL}/sites/{SITE_ID}/lists/{lista_id}/items"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {"fields": dados}
    try:
        r = requests.post(url, headers=headers, json=payload)
        if not r.ok:
            erro = r.json().get("error", {}).get("message", r.text)
            st.error(f"Erro ao salvar ({r.status_code}): {erro}")
            return False
        return True
    except Exception as e:
        st.error(f"Erro de conexão: {e}")
        return False


@st.cache_data(ttl=300)
def carregar_frotas(token, lista_frotas_id):
    url = f"{GRAPH_URL}/sites/{SITE_ID}/lists/{lista_frotas_id}/items?expand=fields&$top=5000"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.get(url, headers=headers)
        itens = r.json().get("value", [])
        frotas = [i["fields"]["Title"] for i in itens if "Title" in i["fields"]]
        return sorted(set(frotas))
    except:
        return []


# ==========================
# FUNÇÕES DE DADOS
# ==========================
def preparar_dataframe(dados_sp):
    colunas = ["Tipo_Operacao", "Tipo_Oleo", "Frota", "Quantidade", "Justificativa", "Created"]
    if not dados_sp:
        return pd.DataFrame(columns=colunas + ["Data_Dt", "Hora"])
    df = pd.DataFrame(dados_sp)
    for col in colunas:
        if col not in df.columns:
            df[col] = None
    dt_utc = pd.to_datetime(df["Created"], errors="coerce", utc=True)
    dt_local = dt_utc.dt.tz_convert(TZ_LOCAL)
    df["Data_Dt"] = dt_local.dt.date
    df["Hora"] = dt_local.dt.strftime("%H:%M")
    df["Quantidade"] = pd.to_numeric(df["Quantidade"], errors="coerce").fillna(0)
    # normaliza strings para evitar problemas de espaço/encoding
    df["Tipo_Oleo"]      = df["Tipo_Oleo"].astype(str).str.strip()
    df["Tipo_Operacao"]  = df["Tipo_Operacao"].astype(str).str.strip()
    return df


def calcular_saldos(df):
    saldos = {}
    for tipo in TIPOS_OLEO:
        df_t = df[df["Tipo_Oleo"].str.strip() == tipo.strip()]
        ent = df_t[df_t["Tipo_Operacao"].str.strip() == "Entrada"]["Quantidade"].sum()
        sai = df_t[df_t["Tipo_Operacao"].str.strip() == "Saida"]["Quantidade"].sum()
        saldos[tipo] = ent - sai
    return saldos


# ==========================
# PAGE CONFIG + DARK THEME
# ==========================
st.set_page_config(
    page_title="Controle de Óleos",
    page_icon="🛢️",
    layout="wide",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Rajdhani:wght@500;600;700&family=IBM+Plex+Sans:wght@300;400;500;600&family=IBM+Plex+Mono:wght@400;600&display=swap');

/* ── DARK BASE ─────────────────────────────────────────── */
html, body, [data-testid="stAppViewContainer"],
[data-testid="stHeader"], [data-testid="stToolbar"] {
    background-color: #0b0f1a !important;
    color: #e2e8f0 !important;
}
[data-testid="stSidebar"] {
    background-color: #0d1117 !important;
    border-right: 1px solid #1e293b;
}
[data-testid="stSidebar"] * { color: #cbd5e1 !important; }

/* inputs, selectbox, textarea */
div[data-baseweb="input"] input,
div[data-baseweb="textarea"] textarea,
div[data-baseweb="select"] div {
    background-color: #161b27 !important;
    color: #f1f5f9 !important;
    border-color: #334155 !important;
}
div[data-baseweb="select"] svg { fill: #94a3b8 !important; }

/* number input */
input[type="number"] {
    background-color: #161b27 !important;
    color: #f1f5f9 !important;
}

/* tabs */
[data-testid="stTabs"] button {
    color: #94a3b8 !important;
    font-family: 'Rajdhani', sans-serif !important;
    font-size: 1rem !important;
    font-weight: 600 !important;
    letter-spacing: .05em !important;
}
[data-testid="stTabs"] button[aria-selected="true"] {
    color: #38bdf8 !important;
    border-bottom-color: #38bdf8 !important;
}

/* dataframe */
[data-testid="stDataFrame"] { background: #0d1117 !important; }
[data-testid="stDataFrame"] th {
    background: #1e293b !important;
    color: #94a3b8 !important;
}
[data-testid="stDataFrame"] td { color: #e2e8f0 !important; }

/* metric */
[data-testid="stMetric"] label { color: #64748b !important; }
[data-testid="stMetric"] [data-testid="stMetricValue"] {
    color: #f1f5f9 !important;
    font-family: 'IBM Plex Mono', monospace !important;
}

/* divider */
hr { border-color: #1e293b !important; }

/* ── CUSTOM COMPONENTS ─────────────────────────────────── */
html, body, [class*="css"] {
    font-family: 'IBM Plex Sans', sans-serif;
}

.login-box {
    background: linear-gradient(160deg, #0d1117 60%, #111827);
    border: 1px solid #1e293b;
    border-radius: 16px;
    padding: 40px 36px;
    margin-top: 24px;
    box-shadow: 0 24px 60px rgba(0,0,0,.6);
}
.login-title {
    text-align: center;
    font-family: 'Rajdhani', sans-serif;
    font-size: 1.6rem;
    font-weight: 700;
    letter-spacing: .08em;
    color: #f1f5f9;
    margin-bottom: 4px;
}
.login-sub {
    text-align: center;
    font-size: 0.82rem;
    color: #475569;
    margin-bottom: 28px;
    letter-spacing: .12em;
    text-transform: uppercase;
}

.page-header {
    background: linear-gradient(135deg, #0d1117 0%, #131c2e 100%);
    border: 1px solid #1e293b;
    border-left: 4px solid #38bdf8;
    border-radius: 12px;
    padding: 18px 24px;
    margin-bottom: 24px;
    display: flex;
    align-items: center;
    gap: 14px;
}
.page-header h2 {
    margin: 0;
    font-family: 'Rajdhani', sans-serif;
    font-size: 1.5rem;
    font-weight: 700;
    letter-spacing: .06em;
    color: #f1f5f9;
}
.page-header p {
    margin: 4px 0 0 0;
    font-size: 0.8rem;
    color: #475569;
    letter-spacing: .1em;
    text-transform: uppercase;
}

.oil-card {
    border-radius: 12px;
    padding: 18px 20px;
    border: 1px solid;
    margin-bottom: 12px;
    position: relative;
    overflow: hidden;
}
.oil-card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: currentColor;
    opacity: .6;
}
.oil-card h3 {
    margin: 0 0 6px 0;
    font-family: 'Rajdhani', sans-serif;
    font-size: 1rem;
    font-weight: 700;
    letter-spacing: .06em;
    text-transform: uppercase;
}
.oil-card .qty {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 2rem;
    font-weight: 600;
    line-height: 1;
}
.oil-card .lbl {
    font-size: 0.72rem;
    color: #475569;
    text-transform: uppercase;
    letter-spacing: .1em;
    margin-top: 4px;
}

.section-title {
    font-family: 'Rajdhani', sans-serif;
    font-size: 1.1rem;
    font-weight: 700;
    letter-spacing: .08em;
    text-transform: uppercase;
    color: #94a3b8;
    border-bottom: 1px solid #1e293b;
    padding-bottom: 8px;
    margin-bottom: 16px;
}

.saldo-tag {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 20px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.85rem;
    font-weight: 600;
    margin-bottom: 12px;
}

.resumo-card {
    background: #0d1117;
    border: 1px solid #1e293b;
    border-radius: 10px;
    padding: 14px 16px;
    margin-bottom: 8px;
}
.resumo-card h4 {
    font-family: 'Rajdhani', sans-serif;
    font-size: 0.95rem;
    font-weight: 700;
    letter-spacing: .06em;
    text-transform: uppercase;
    margin: 0 0 8px 0;
}
.resumo-card .row {
    display: flex;
    justify-content: space-between;
    font-size: 0.85rem;
    color: #94a3b8;
    padding: 2px 0;
}
.resumo-card .row b { color: #e2e8f0; font-family: 'IBM Plex Mono', monospace; }
</style>
""", unsafe_allow_html=True)

# ==========================
# LOGIN
# ==========================
if "logado" not in st.session_state:
    st.session_state["logado"] = False

if not st.session_state["logado"]:
    col_l1, col_l2, col_l3 = st.columns([1, 1.4, 1])
    with col_l2:
        st.markdown('<div class="login-box">', unsafe_allow_html=True)
        if os.path.exists(ARQUIVO_LOGO):
            st.image(ARQUIVO_LOGO, width=180)
        st.markdown('<div class="login-title">🛢️ Controle de Óleos</div>', unsafe_allow_html=True)
        st.markdown('<div class="login-sub">Hidráulico · Transmissão · Motor</div>', unsafe_allow_html=True)
        u = st.text_input("Usuário", placeholder="usuário...", label_visibility="collapsed")
        s = st.text_input("Senha", type="password", placeholder="senha...", label_visibility="collapsed")
        if st.button("ACESSAR", type="primary", use_container_width=True):
            usuario = u.lower().strip()
            if usuario in USUARIOS and USUARIOS[usuario]["senha"] == s:
                st.session_state["logado"] = True
                st.session_state["usuario"] = usuario
                st.session_state["lista_id"] = USUARIOS[usuario]["lista_id"]
                st.session_state["lista_frotas_id"] = USUARIOS[usuario]["lista_frotas_id"]
                st.session_state["nome"] = USUARIOS[usuario]["nome"]
                st.rerun()
            else:
                st.error("Usuário ou senha inválidos!")
        st.markdown('</div>', unsafe_allow_html=True)
    st.stop()


# ==========================
# SISTEMA PRINCIPAL
# ==========================
LISTA_ID = st.session_state["lista_id"]
LISTA_FROTAS_ID = st.session_state["lista_frotas_id"]
NOME_UNIDADE = st.session_state["nome"]

token = obter_token()
if not token:
    st.error("Erro de conexão com Microsoft Graph.")
    st.stop()

dados_sp = obter_dados_sharepoint(token, LISTA_ID)
df = preparar_dataframe(dados_sp)
saldos = calcular_saldos(df)


# ── SIDEBAR ────────────────────────────────────────────
with st.sidebar:
    if os.path.exists(ARQUIVO_LOGO):
        st.image(ARQUIVO_LOGO, width=140)
    st.markdown(f"**{NOME_UNIDADE}**")
    st.markdown("---")
    st.markdown("##### Estoque Atual")
    for tipo, cfg in TIPOS_OLEO.items():
        s = saldos[tipo]
        cor = cfg["cor"] if s > 20 else "#ef4444"
        st.markdown(
            f"<div style='display:flex;justify-content:space-between;"
            f"padding:7px 0;border-bottom:1px solid #1e293b;font-size:.9rem;'>"
            f"<span>{cfg['emoji']} {tipo}</span>"
            f"<span style='font-family:IBM Plex Mono,monospace;font-weight:700;color:{cor};'>"
            f"{s:,.1f} L</span></div>",
            unsafe_allow_html=True,
        )
    st.markdown("---")
    if st.button("Sair", use_container_width=True):
        st.session_state.clear()
        st.rerun()

# ── HEADER ─────────────────────────────────────────────
st.markdown(
    f"""<div class="page-header">
        <div>
            <h2>🛢️ Controle de Óleos — {NOME_UNIDADE}</h2>
            <p>Hidráulico · Transmissão · Motor</p>
        </div>
    </div>""",
    unsafe_allow_html=True,
)

# ── CARDS DE SALDO ─────────────────────────────────────
col_h, col_t, col_m = st.columns(3)
for col, tipo in zip([col_h, col_t, col_m], TIPOS_OLEO):
    cfg = TIPOS_OLEO[tipo]
    saldo_atual = saldos[tipo]
    alerta = saldo_atual <= 20
    cor_uso = "#ef4444" if alerta else cfg["cor"]
    icone_alerta = " ⚠️" if alerta else ""
    with col:
        st.markdown(
            f"""<div class="oil-card" style="background:{cfg['cor_card']};
                border-color:{cor_uso}; color:{cor_uso};">
                <h3>{cfg['emoji']} {tipo}{icone_alerta}</h3>
                <div class="qty">{saldo_atual:,.1f} L</div>
                <div class="lbl">Estoque disponível</div>
            </div>""",
            unsafe_allow_html=True,
        )

st.markdown("<br>", unsafe_allow_html=True)

# ==========================
# ABAS
# ==========================
aba1, aba2, aba3 = st.tabs(["🔧  REGISTRAR SAÍDA", "📦  ENTRADA DE ESTOQUE", "📊  RELATÓRIO"])

# ==================== ABA 1: SAÍDA ====================
with aba1:
    st.markdown('<div class="section-title">Registrar Consumo / Saída</div>', unsafe_allow_html=True)

    lista_frotas = [""] + carregar_frotas(token, LISTA_FROTAS_ID)

    if "reset_oleo_counter" not in st.session_state:
        st.session_state["reset_oleo_counter"] = 0

    col_frota, col_tipo = st.columns(2)
    with col_frota:
        frota_sel = st.selectbox(
            "Frota",
            lista_frotas,
            key=f"frota_oleo_{st.session_state['reset_oleo_counter']}",
        )
    with col_tipo:
        tipo_oleo_sel = st.selectbox(
            "Tipo de Óleo",
            list(TIPOS_OLEO.keys()),
            key=f"tipo_oleo_{st.session_state['reset_oleo_counter']}",
        )

    if tipo_oleo_sel:
        saldo_tipo = saldos.get(tipo_oleo_sel, 0)
        cfg_sel = TIPOS_OLEO[tipo_oleo_sel]
        cor_saldo = cfg_sel["cor"] if saldo_tipo > 20 else "#ef4444"
        st.markdown(
            f'<div class="saldo-tag" style="background:{cfg_sel["cor_card"]};'
            f'color:{cor_saldo};border:1px solid {cor_saldo};">'
            f'{cfg_sel["emoji"]} Saldo {tipo_oleo_sel}: {saldo_tipo:,.1f} L disponíveis</div>',
            unsafe_allow_html=True,
        )

    with st.form("f_saida_oleo", clear_on_submit=True):
        quantidade = st.number_input("Quantidade (Litros)", min_value=0.1, step=0.5, format="%.1f")
        justificativa = st.text_area(
            "Justificativa / Motivo",
            placeholder=(
                "Ex: Estourou a mangueira do óleo hidráulico na colhedora 014. "
                "Trocado o óleo de motor conforme PM de 250h..."
            ),
            height=110,
        )
        if st.form_submit_button("💾  SALVAR SAÍDA", type="primary", use_container_width=True):
            if not frota_sel:
                st.error("Selecione uma frota válida.")
            elif not justificativa.strip():
                st.error("Preencha a justificativa antes de salvar.")
            elif saldo_tipo <= 0:
                st.error(f"Sem estoque de {tipo_oleo_sel} disponível.")
            elif quantidade > saldo_tipo:
                st.error(f"Estoque insuficiente. Saldo atual de {tipo_oleo_sel}: {saldo_tipo:,.1f} L")
            else:
                with st.spinner("Enviando..."):
                    ok = enviar_dados_sharepoint(token, LISTA_ID, {
                        "Title": f"Saida - {tipo_oleo_sel} - {frota_sel}",
                        "Tipo_Operacao": "Saida",
                        "Tipo_Oleo": tipo_oleo_sel,
                        "Frota": frota_sel,
                        "Quantidade": quantidade,
                        "Justificativa": justificativa.strip(),
                    })
                if ok:
                    st.success(f"✅ {quantidade:,.1f} L de {tipo_oleo_sel} debitados da frota **{frota_sel}**!")
                    time.sleep(1)
                    st.session_state["reset_oleo_counter"] += 1
                    st.rerun()


# ==================== ABA 2: ENTRADA ====================
with aba2:
    st.markdown('<div class="section-title">Entrada de Estoque</div>', unsafe_allow_html=True)

    col_e1, col_e2 = st.columns([1, 1.4])
    with col_e1:
        st.markdown("**Saldo atual por tipo:**")
        for tipo, cfg in TIPOS_OLEO.items():
            s = saldos[tipo]
            cor = cfg["cor"] if s > 20 else "#ef4444"
            st.markdown(
                f"<div class='resumo-card'>"
                f"<h4 style='color:{cor};'>{cfg['emoji']} {tipo}</h4>"
                f"<div class='row'><span>Em estoque</span><b style='color:{cor};'>{s:,.1f} L</b></div>"
                f"</div>",
                unsafe_allow_html=True,
            )

    with col_e2:
        with st.form("f_entrada_oleo", clear_on_submit=True):
            tipo_entrada = st.selectbox("Tipo de Óleo", list(TIPOS_OLEO.keys()))
            qtd_entrada = st.number_input("Quantidade Recebida (L)", min_value=0.1, step=1.0, format="%.1f")
            obs_entrada = st.text_input("Observação / Nº NF", placeholder="Ex: NF 001234 — Fornecedor XYZ")
            if st.form_submit_button("📦  CONFIRMAR ENTRADA", use_container_width=True):
                if qtd_entrada <= 0:
                    st.error("Informe uma quantidade válida.")
                else:
                    with st.spinner("Enviando..."):
                        ok = enviar_dados_sharepoint(token, LISTA_ID, {
                            "Title": f"Entrada - {tipo_entrada}",
                            "Tipo_Operacao": "Entrada",
                            "Tipo_Oleo": tipo_entrada,
                            "Frota": "",
                            "Quantidade": qtd_entrada,
                            "Justificativa": obs_entrada.strip(),
                        })
                    if ok:
                        st.success(f"✅ {qtd_entrada:,.1f} L de {tipo_entrada} adicionados ao estoque!")
                        time.sleep(1)
                        st.rerun()


# ==================== ABA 3: RELATÓRIO ====================
with aba3:
    st.markdown('<div class="section-title">Relatório de Movimentação</div>', unsafe_allow_html=True)

    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        data_filtro = st.date_input("Data", datetime.today())
    with col_f2:
        tipo_filtro = st.selectbox("Tipo de Óleo", ["Todos"] + list(TIPOS_OLEO.keys()))
    with col_f3:
        op_filtro = st.selectbox("Operação", ["Todas", "Saida", "Entrada"])

    if df.empty:
        st.info("Nenhum registro encontrado para esta unidade.")
    else:
        # resumo do dia
        st.markdown(f"**Resumo — {data_filtro.strftime('%d/%m/%Y')}**")
        cols_res = st.columns(3)
        for i, (tipo, cfg) in enumerate(TIPOS_OLEO.items()):
            df_t_dia = df[df["Data_Dt"] == data_filtro]
            ent_dia = df_t_dia[(df_t_dia["Tipo_Oleo"] == tipo) & (df_t_dia["Tipo_Operacao"] == "Entrada")]["Quantidade"].sum()
            sai_dia = df_t_dia[(df_t_dia["Tipo_Oleo"] == tipo) & (df_t_dia["Tipo_Operacao"] == "Saida")]["Quantidade"].sum()
            with cols_res[i]:
                st.markdown(
                    f"<div class='resumo-card'>"
                    f"<h4 style='color:{cfg['cor']};'>{cfg['emoji']} {tipo}</h4>"
                    f"<div class='row'><span>⬆️ Entradas</span><b>{ent_dia:,.1f} L</b></div>"
                    f"<div class='row'><span>⬇️ Saídas</span><b>{sai_dia:,.1f} L</b></div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

        st.markdown("<br>", unsafe_allow_html=True)

        df_rel = df[df["Data_Dt"] == data_filtro].copy()
        if tipo_filtro != "Todos":
            df_rel = df_rel[df_rel["Tipo_Oleo"] == tipo_filtro]
        if op_filtro != "Todas":
            df_rel = df_rel[df_rel["Tipo_Operacao"] == op_filtro]

        if df_rel.empty:
            st.info(f"Nenhum registro para os filtros selecionados em {data_filtro.strftime('%d/%m/%Y')}.")
        else:
            colunas_exibir = [c for c in ["Hora", "Tipo_Operacao", "Tipo_Oleo", "Frota", "Quantidade", "Justificativa"] if c in df_rel.columns]
            st.dataframe(
                df_rel[colunas_exibir].rename(columns={
                    "Hora": "Hora",
                    "Tipo_Operacao": "Operação",
                    "Tipo_Oleo": "Tipo de Óleo",
                    "Frota": "Frota",
                    "Quantidade": "Qtd (L)",
                    "Justificativa": "Justificativa",
                }),
                use_container_width=True,
                hide_index=True,
            )
            st.caption(f"{len(df_rel)} registro(s) encontrado(s)")
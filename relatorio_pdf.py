"""PDF do relatório de óleos: cabeçalho com logo, indicadores, gráficos de saídas
(por frente, por dia/semana/mês e por frota; a cor é sempre o tipo de óleo) e as
tabelas de resumo e de lançamentos. Tudo desenhado com o reportlab (vetorial).

Entrada: a tabela do relatório como aparece na tela (colunas Data, Hora, Operação,
Frente, Tipo de Óleo, Frota, Qtd (L), Justificativa), já filtrada.
"""
import math
import re
from functools import lru_cache
from io import BytesIO
from xml.sax.saxutils import escape

import pandas as pd
from PIL import Image
from reportlab.graphics.shapes import Drawing, Line, Path, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# paleta validada (dataviz, superfície branca); o texto nunca usa a cor da série
COR_TIPO = {"Hidráulico": "#2a78d6", "Transmissão": "#eb6834", "Motor": "#1baf7a"}
INK = colors.HexColor("#0b0b0b")
INK2 = colors.HexColor("#52514e")
MUTED = colors.HexColor("#898781")
GRADE = colors.HexColor("#e1e0d9")
BASE = colors.HexColor("#c3c2b7")
BORDA = colors.HexColor("#e4e3dd")
ZEBRA = colors.HexColor("#f7f7f4")

PAGINA_L, PAGINA_A = A4
MARGEM = 15 * mm
LARGURA = PAGINA_L - 2 * MARGEM
TOPO_CABECALHO = PAGINA_A - 12 * mm
GAP = 1.5     # vão em branco entre segmentos empilhados
RAIO = 2.5    # ponta arredondada da barra
K_CURVA = 0.5523

MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]

ESTILO_TITULO = ParagraphStyle("titulo", fontName="Helvetica-Bold", fontSize=10.5, leading=13, textColor=INK)
ESTILO_SUB = ParagraphStyle("sub", fontName="Helvetica", fontSize=7.5, leading=10, textColor=MUTED)
ESTILO_TEXTO = ParagraphStyle("texto", fontName="Helvetica", fontSize=8, leading=11, textColor=INK2)
ESTILO_CELULA = ParagraphStyle("celula", fontName="Helvetica", fontSize=7.2, leading=9, textColor=INK)


# ── formatação ──────────────────────────────────────────────
def _br(v, casas=1):
    return f"{v:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _litros(v):
    return f"{_br(v)} L"


def _cortar(texto, largura, fonte="Helvetica", tamanho=8):
    if stringWidth(texto, fonte, tamanho) <= largura:
        return texto
    while texto and stringWidth(texto + "…", fonte, tamanho) > largura:
        texto = texto[:-1]
    return texto.rstrip() + "…"


def _ticks(maximo):
    if maximo <= 0:
        return [0, 1]
    bruto = maximo / 4
    pot = 10 ** math.floor(math.log10(bruto))
    passo = next(m * pot for m in (1, 2, 2.5, 5, 10) if m * pot >= bruto)
    return [i * passo for i in range(math.ceil(maximo / passo - 1e-9) + 1)]


def _rotulo_tick(t):
    return _br(t, 0) if float(t).is_integer() else _br(t, 1)


@lru_cache(maxsize=4)
def _logo(caminho):
    """O PNG é branco sobre um xadrez cinza gravado na imagem: fica só o desenho, em tinta escura."""
    cinza = Image.open(caminho).convert("L")
    cinza.thumbnail((800, 800))
    alfa = cinza.point(lambda p: max(0, min(255, (p - 130) * 255 // 95)))
    alfa = alfa.crop(alfa.getbbox())
    logo = Image.new("RGBA", alfa.size, (11, 11, 11, 0))
    logo.putalpha(alfa)
    return logo


# ── peças dos gráficos ──────────────────────────────────────
def _segmento(x, y, w, h, cor, arredondar, horizontal=True):
    """Retângulo de um segmento; o último da pilha ganha a ponta arredondada (a base fica reta)."""
    p = Path(fillColor=colors.HexColor(cor), strokeColor=None, strokeWidth=0)
    r = min(RAIO, w / 2, h / 2) if arredondar else 0
    k = r * K_CURVA
    if horizontal:  # ponta à direita
        p.moveTo(x, y)
        p.lineTo(x + w - r, y)
        if r:
            p.curveTo(x + w - r + k, y, x + w, y + r - k, x + w, y + r)
        p.lineTo(x + w, y + h - r)
        if r:
            p.curveTo(x + w, y + h - r + k, x + w - r + k, y + h, x + w - r, y + h)
        p.lineTo(x, y + h)
    else:  # ponta em cima
        p.moveTo(x, y)
        p.lineTo(x + w, y)
        p.lineTo(x + w, y + h - r)
        if r:
            p.curveTo(x + w, y + h - r + k, x + w - r + k, y + h, x + w - r, y + h)
        p.lineTo(x + r, y + h)
        if r:
            p.curveTo(x + r - k, y + h, x, y + h - r + k, x, y + h - r)
    p.closePath()
    return p


def _legenda(tipos):
    d = Drawing(LARGURA, 12)
    x = 0
    for t in tipos:
        d.add(Rect(x, 1.5, 8, 8, rx=2, ry=2, fillColor=colors.HexColor(COR_TIPO[t]), strokeColor=None))
        d.add(String(x + 11.5, 2.5, t, fontName="Helvetica", fontSize=7.5, fillColor=INK2))
        x += 11.5 + stringWidth(t, "Helvetica", 7.5) + 14
    return d


def _barras_horizontais(linhas, tipos):
    """linhas: [(rótulo, {tipo: litros})], na ordem de exibição."""
    alt_linha, esp, eixo = (19 if len(linhas) <= 6 else 16.5), 10, 13
    rot_w = min(max(stringWidth(r, "Helvetica", 7.5) for r, _ in linhas) + 10, 175)
    x0, total_w = rot_w, 48
    plot_w = LARGURA - x0 - total_w
    h = len(linhas) * alt_linha + eixo + 2
    topo = h - 1
    d = Drawing(LARGURA, h)
    ticks = _ticks(max(sum(v.values()) for _, v in linhas))
    escala = plot_w / ticks[-1]
    for t in ticks:
        x = x0 + t * escala
        d.add(Line(x, eixo, x, topo, strokeColor=BASE if t == 0 else GRADE, strokeWidth=0.6))
        d.add(String(x, eixo - 9, _rotulo_tick(t), fontName="Helvetica", fontSize=6.5, fillColor=MUTED, textAnchor="middle"))
    for i, (rotulo, valores) in enumerate(linhas):
        yc = topo - (i + 0.5) * alt_linha
        d.add(String(x0 - 6, yc - 2.6, _cortar(rotulo, rot_w - 10, tamanho=7.5),
                     fontName="Helvetica", fontSize=7.5, fillColor=INK2, textAnchor="end"))
        segs = [(t, valores.get(t, 0)) for t in tipos if valores.get(t, 0) > 0]
        x = x0
        for j, (t, v) in enumerate(segs):
            ultimo = j == len(segs) - 1
            w = v * escala
            if (w if ultimo else w - GAP) > 0.3:
                d.add(_segmento(x, yc - esp / 2, w if ultimo else w - GAP, esp, COR_TIPO[t], ultimo))
            x += w
        d.add(String(x + 5, yc - 2.6, _litros(sum(valores.values())),
                     fontName="Helvetica-Bold", fontSize=7.2, fillColor=INK))
    return d


def _colunas(periodos, tipos):
    """periodos: [(rótulo, {tipo: litros})] em ordem de tempo; colunas empilhadas."""
    h, eixo, esq, folga_topo = 135, 14, 30, 14
    plot_w, plot_h = LARGURA - esq, h - eixo - folga_topo
    d = Drawing(LARGURA, h)
    totais = [sum(v.values()) for _, v in periodos]
    ticks = _ticks(max(totais))
    escala = plot_h / ticks[-1]
    for t in ticks:
        y = eixo + t * escala
        d.add(Line(esq, y, LARGURA, y, strokeColor=BASE if t == 0 else GRADE, strokeWidth=0.6))
        d.add(String(esq - 5, y - 2.3, _rotulo_tick(t), fontName="Helvetica", fontSize=6.5, fillColor=MUTED, textAnchor="end"))
    slot = plot_w / len(periodos)
    esp = min(slot * 0.62, 18)
    a_cada = max(1, math.ceil((stringWidth("00/00", "Helvetica", 6.5) + 6) / slot))
    i_max = totais.index(max(totais))
    for i, (rotulo, valores) in enumerate(periodos):
        xc = esq + (i + 0.5) * slot
        if i % a_cada == 0:
            d.add(String(xc, eixo - 9, rotulo, fontName="Helvetica", fontSize=6.5, fillColor=MUTED, textAnchor="middle"))
        segs = [(t, valores.get(t, 0)) for t in tipos if valores.get(t, 0) > 0]
        y = eixo
        for j, (t, v) in enumerate(segs):
            ultimo = j == len(segs) - 1
            alt = v * escala
            if (alt if ultimo else alt - GAP) > 0.3:
                d.add(_segmento(xc - esp / 2, y, esp, alt if ultimo else alt - GAP, COR_TIPO[t], ultimo, horizontal=False))
            y += alt
        if i == i_max and totais[i] > 0:  # só o pico leva número; o resto fica no eixo
            d.add(String(xc, y + 4, _litros(totais[i]), fontName="Helvetica-Bold", fontSize=7, fillColor=INK, textAnchor="middle"))
    return d


def _secao(titulo, subtitulo, tipos, grafico):
    return KeepTogether([
        Paragraph(escape(titulo), ESTILO_TITULO),
        Paragraph(escape(subtitulo), ESTILO_SUB),
        Spacer(1, 5),
        _legenda(tipos),
        Spacer(1, 6),
        grafico,
        Spacer(1, 14),
    ])


def _indicadores(cartoes):
    """cartoes: [(rótulo, valor, detalhe, cor_ou_None)]."""
    h, vao = 54, 8
    w = min((LARGURA - vao * (len(cartoes) - 1)) / len(cartoes), 135)
    d = Drawing(LARGURA, h)
    for i, (rotulo, valor, detalhe, cor) in enumerate(cartoes):
        x = i * (w + vao)
        d.add(Rect(x, 0.5, w, h - 1, rx=6, ry=6, fillColor=colors.white, strokeColor=BORDA, strokeWidth=0.8))
        xt = x + 10
        if cor:
            d.add(Rect(xt, h - 17.5, 7, 7, rx=1.8, ry=1.8, fillColor=colors.HexColor(cor), strokeColor=None))
            xt += 11
        d.add(String(xt, h - 17, _cortar(rotulo, x + w - xt - 6, tamanho=7.5), fontName="Helvetica", fontSize=7.5, fillColor=INK2))
        d.add(String(x + 10, 17, valor, fontName="Helvetica-Bold", fontSize=14, fillColor=INK))
        d.add(String(x + 10, 7, detalhe, fontName="Helvetica", fontSize=6.8, fillColor=MUTED))
    return d


def _estilo_tabela(n_linhas, alinhar_direita_de, total=False):
    estilo = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.2),
        ("TEXTCOLOR", (0, 0), (-1, 0), INK2),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 7.2),
        ("TEXTCOLOR", (0, 1), (-1, -1), INK),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, BASE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ZEBRA]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (alinhar_direita_de, 0), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]
    if total:
        estilo += [
            ("FONT", (0, n_linhas - 1), (-1, n_linhas - 1), "Helvetica-Bold", 7.2),
            ("LINEABOVE", (0, n_linhas - 1), (-1, n_linhas - 1), 0.8, BASE),
            ("BACKGROUND", (0, n_linhas - 1), (-1, n_linhas - 1), colors.white),
        ]
    return TableStyle(estilo)


# ── agrupamentos ────────────────────────────────────────────
def _por(df, chave, tipos):
    tab = df.pivot_table(index=chave, columns="Tipo de Óleo", values="Qtd (L)", aggfunc="sum", fill_value=0)
    return {idx: {t: float(linha.get(t, 0)) for t in tipos} for idx, linha in tab.iterrows()}


def _periodos(saidas, data_ini, data_fim, tipos):
    dias = (data_fim - data_ini).days + 1
    datas = pd.to_datetime(saidas["Data"])
    if dias <= 45:
        unidade, chave = "dia", datas.dt.normalize()
        eixo = pd.date_range(data_ini, data_fim, freq="D")
        rotulo = lambda d: d.strftime("%d/%m")
    elif dias <= 210:
        unidade = "semana"
        chave = (datas - pd.to_timedelta(datas.dt.weekday, unit="D")).dt.normalize()
        inicio = pd.Timestamp(data_ini) - pd.Timedelta(days=pd.Timestamp(data_ini).weekday())
        eixo = pd.date_range(inicio, data_fim, freq="7D")
        rotulo = lambda d: d.strftime("%d/%m")
    else:
        unidade, chave = "mês", datas.dt.to_period("M").dt.to_timestamp()
        eixo = pd.date_range(pd.Timestamp(data_ini).to_period("M").to_timestamp(), data_fim, freq="MS")
        rotulo = lambda d: f"{MESES[d.month - 1]}/{d.strftime('%y')}"
    valores = _por(saidas.assign(_p=chave.values), "_p", tipos)
    return unidade, [(rotulo(p), valores.get(p, {})) for p in eixo]


def _frotas(saidas, tipos, limite=10):
    s = saidas.assign(
        _frota=saidas["Frota"].astype(str).str.replace("\xa0", " ").str.replace(r"\s+", " ", regex=True).str.strip())
    s["_num"] = s["_frota"].str.extract(r"^(\d+)")[0].fillna(s["_frota"])
    nomes = s.groupby("_num")["_frota"].last()
    valores = _por(s, "_num", tipos)
    ordem = sorted(valores, key=lambda n: -sum(valores[n].values()))[:limite]
    return [(nomes[n], valores[n]) for n in ordem], len(valores)


# ── documento ───────────────────────────────────────────────
class _CanvasNumerado(rl_canvas.Canvas):
    """Guarda as páginas para escrever "Página X de Y" no fim."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._paginas = []

    def showPage(self):
        self._paginas.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._paginas)
        for estado in self._paginas:
            self.__dict__.update(estado)
            self.setFont("Helvetica", 7)
            self.setFillColor(MUTED)
            self.drawRightString(PAGINA_L - MARGEM, 9 * mm, f"Página {self._pageNumber} de {total}")
            super().showPage()
        super().save()


def gerar_pdf(tabela, *, unidade, data_ini, data_fim, frente, tipo, operacao, saldos, tipos_oleo,
              agora, logo=None):
    tipos = [t for t in tipos_oleo if t in COR_TIPO]
    saidas = tabela[tabela["Operação"] == "Saida"]
    entradas = tabela[tabela["Operação"] == "Entrada"]
    periodo = (data_ini.strftime("%d/%m/%Y") if data_ini == data_fim
               else f"{data_ini.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')}")
    filtros = f"Frente: {frente}   ·   Tipo de óleo: {tipo}   ·   Operação: {operacao.replace('Saida', 'Saída')}"
    imagem_logo = _logo(logo) if logo else None

    def cabecalho(c, _doc):
        c.saveState()
        x = MARGEM
        if imagem_logo is not None:
            alt = 12 * mm
            larg = alt * imagem_logo.width / imagem_logo.height
            c.drawImage(ImageReader(imagem_logo), x, TOPO_CABECALHO - alt, larg, alt, mask="auto")
            x += larg + 5 * mm
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(x, TOPO_CABECALHO - 13, "Relatório de Consumo de Óleos")
        c.setFont("Helvetica", 9)
        c.setFillColor(INK2)
        c.drawString(x, TOPO_CABECALHO - 26, f"{unidade}   ·   {periodo}")
        c.setFont("Helvetica", 7.5)
        c.setFillColor(MUTED)
        c.drawString(x, TOPO_CABECALHO - 37, filtros)
        c.drawRightString(PAGINA_L - MARGEM, TOPO_CABECALHO - 13, f"Gerado em {agora.strftime('%d/%m/%Y %H:%M')}")
        c.setStrokeColor(BORDA)
        c.setLineWidth(0.8)
        c.line(MARGEM, TOPO_CABECALHO - 45, PAGINA_L - MARGEM, TOPO_CABECALHO - 45)
        c.line(MARGEM, 13 * mm, PAGINA_L - MARGEM, 13 * mm)
        c.setFont("Helvetica", 7)
        c.drawString(MARGEM, 9 * mm, f"Controle de Óleos  ·  {unidade}")
        c.restoreState()

    historia = []

    # indicadores
    cartoes = []
    if operacao != "Entrada":
        rotulo_total = "Total de saídas" if tipo == "Todos" else f"Saídas de {tipo}"
        cartoes.append((rotulo_total, _litros(saidas["Qtd (L)"].sum()), f"{len(saidas)} saída(s)", None))
        if tipo == "Todos":
            for t in tipos:
                st = saidas[saidas["Tipo de Óleo"] == t]
                cartoes.append((t, _litros(st["Qtd (L)"].sum()), f"{len(st)} saída(s)", COR_TIPO[t]))
    if frente == "Todas" and operacao != "Saida":
        cartoes.append(("Entradas", _litros(entradas["Qtd (L)"].sum()), f"{len(entradas)} lançamento(s)", None))
    if cartoes:
        historia += [_indicadores(cartoes), Spacer(1, 6)]
    estoque = "   ·   ".join(f"{t} <b>{_litros(saldos.get(t, 0))}</b>" for t in tipos)
    historia += [Paragraph(f"Estoque atual da unidade (hoje): {estoque}", ESTILO_TEXTO), Spacer(1, 16)]

    # gráficos (só saídas)
    if not saidas.empty:
        tipos_s = [t for t in tipos if t in set(saidas["Tipo de Óleo"])]
        if frente == "Todas":
            por_frente = _por(saidas.assign(Frente=saidas["Frente"].replace("", "Sem frente")), "Frente", tipos)
            linhas = sorted(por_frente.items(), key=lambda kv: -sum(kv[1].values()))
            historia.append(_secao("Saídas por frente", "Litros no período, por tipo de óleo", tipos_s,
                                   _barras_horizontais(linhas, tipos)))
        unidade_t, periodos = _periodos(saidas, data_ini, data_fim, tipos)
        nota_semana = " (cada semana aparece pela data da segunda-feira)" if unidade_t == "semana" else ""
        historia.append(_secao(f"Saídas por {unidade_t}",
                               f"Litros por {unidade_t}, por tipo de óleo{nota_semana}; o número marca o maior valor",
                               tipos_s, _colunas(periodos, tipos)))
        frotas, n_frotas = _frotas(saidas, tipos)
        historia.append(_secao("Frotas com maior consumo",
                               f"As {len(frotas)} maiores de {n_frotas} frota(s) com saída no período, em litros",
                               tipos_s, _barras_horizontais(frotas, tipos)))

        # resumo frente × tipo (os números dos gráficos)
        por_frente = _por(saidas.assign(Frente=saidas["Frente"].replace("", "Sem frente")), "Frente", tipos)
        cab = ["Frente"] + tipos_s + ["Total"]
        linhas = [[f] + [_br(v[t]) for t in tipos_s] + [_br(sum(v.values()))]
                  for f, v in sorted(por_frente.items(), key=lambda kv: -sum(kv[1].values()))]
        linhas.append(["Total"] + [_br(saidas.loc[saidas["Tipo de Óleo"] == t, "Qtd (L)"].sum()) for t in tipos_s]
                      + [_br(saidas["Qtd (L)"].sum())])
        larg_num = 72
        tabela_resumo = Table([cab] + linhas, colWidths=[LARGURA - larg_num * (len(cab) - 1)] + [larg_num] * (len(cab) - 1))
        tabela_resumo.setStyle(_estilo_tabela(len(linhas) + 1, 1, total=True))
        historia.append(KeepTogether([Paragraph("Resumo das saídas por frente (L)", ESTILO_TITULO), Spacer(1, 6),
                                      tabela_resumo, Spacer(1, 16)]))

    # lançamentos
    larguras = [46, 28, 44, 58, 112, 54, 38, LARGURA - 380]
    cab = ["Data", "Hora", "Operação", "Frente", "Frota", "Tipo de óleo", "Qtd (L)", "Justificativa"]
    linhas = [cab]
    for r in tabela.itertuples(index=False):
        linhas.append([
            pd.Timestamp(r[0]).strftime("%d/%m/%Y"), r[1], str(r[2]).replace("Saida", "Saída"),
            Paragraph(escape(str(r[3] or "—")), ESTILO_CELULA),
            Paragraph(escape(r[5].replace("\xa0", " ") if isinstance(r[5], str) and r[5].strip() else "—"),
                      ESTILO_CELULA),
            r[4], _br(r[6]),
            Paragraph(escape(r[7] if isinstance(r[7], str) else ""), ESTILO_CELULA),
        ])
    tabela_lanc = Table(linhas, colWidths=larguras, repeatRows=1)
    estilo = _estilo_tabela(len(linhas), 6)
    estilo.add("ALIGN", (7, 0), (7, -1), "LEFT")
    tabela_lanc.setStyle(estilo)
    historia += [Paragraph(f"Lançamentos ({len(tabela)})", ESTILO_TITULO), Spacer(1, 6), tabela_lanc]

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=MARGEM, rightMargin=MARGEM,
                            topMargin=PAGINA_A - TOPO_CABECALHO + 45 + 14, bottomMargin=17 * mm,
                            title=f"Relatório de Consumo de Óleos — {unidade} — {periodo}",
                            author="Controle de Óleos")
    doc.build(historia, onFirstPage=cabecalho, onLaterPages=cabecalho, canvasmaker=_CanvasNumerado)
    return buf.getvalue()

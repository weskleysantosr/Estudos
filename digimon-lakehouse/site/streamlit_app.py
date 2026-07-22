"""Site (Streamlit) que consome a API FastAPI — não fala com Postgres/
Databricks diretamente. Isso mantém uma única porta de entrada nos dados
(a API), com suas próprias regras de rate limit/CORS/validação; o site é só
mais um cliente HTTP dela, igual qualquer outro consumidor externo seria.

Performance: toda chamada à API passa por `st.cache_data(ttl=...)` — sem
isso, cada interação do usuário (mudar um filtro, expandir um card) dispara
o Streamlit inteiro de novo e refaz TODAS as chamadas HTTP anteriores.

Segurança/robustez: toda chamada tem timeout explícito e trata erro de rede
sem derrubar a página — uma API gratuita "dormindo" (cold start) ou fora do
ar por instabilidade do tier gratuito não pode virar uma tela em branco.
"""

from __future__ import annotations

import html
import os

import requests
import streamlit as st


def _load_api_base_url() -> str:
    # Local/GitHub Actions: variável de ambiente. Streamlit Community Cloud:
    # "Secrets" do app (vira st.secrets, não env var, por padrão da plataforma).
    try:
        return st.secrets["SITE_API_BASE_URL"]
    except (KeyError, FileNotFoundError):
        return os.environ.get("SITE_API_BASE_URL", "http://localhost:8000")


API_BASE_URL = _load_api_base_url()
REQUEST_TIMEOUT = 8  # segundos — API pode ter cold start em tier gratuito


def _get(path: str, params: dict | None = None) -> dict | list | None:
    try:
        response = requests.get(f"{API_BASE_URL}{path}", params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        st.error(f"Não consegui falar com a API agora ({exc.__class__.__name__}). Tente novamente em instantes.")
        return None


@st.cache_data(ttl=300)
def load_stats(dimension: str) -> list[dict]:
    return _get(f"/stats/by-{dimension}") or []


@st.cache_data(ttl=300)
def load_digimons(limit: int, offset: int, level: str, type_: str, attribute: str, name: str) -> dict:
    params = {"limit": limit, "offset": offset}
    if level != "Todos":
        params["level"] = level
    if type_ != "Todos":
        params["type"] = type_
    if attribute != "Todos":
        params["attribute"] = attribute
    if name:
        params["name"] = name
    return _get("/digimons", params=params) or {"items": [], "total": 0}


@st.cache_data(ttl=300)
def load_longest_chains(limit: int = 10) -> list[dict]:
    return _get("/stats/longest-evolution-chains", params={"limit": limit}) or []


@st.cache_data(ttl=300)
def load_evolutions(digimon_id: int) -> list[dict]:
    return _get(f"/digimons/{digimon_id}/evolutions") or []


# Paleta categórica (3 papéis: quem evoluiu para o digimon atual, o próprio
# digimon, quem ele pode virar) — cores escolhidas para ficarem distinguíveis
# mesmo em daltonismo, não só "bonitinhas".
_COLOR_PRIOR = "#2a78d6"  # azul — evoluiu de
_COLOR_CURRENT = "#eb6834"  # laranja — o digimon aberto
_COLOR_NEXT = "#1baf7a"  # verde-água — evolui para
_CARD_SIZE = 84  # px — lado do card de prior/next
_CURRENT_CARD_SIZE = 112  # px — o digimon aberto fica em destaque
# Cards com imagem ocupam bem mais espaço vertical que os nós de texto do
# Graphviz antigo, então o teto aqui é menor pra não virar uma coluna gigante.
_MAX_TREE_NODES = 8


def _evolution_card_html(name: str, image_url: str | None, color: str, size: int, *, highlight: bool) -> str:
    safe_name = html.escape(name)
    if image_url:
        img = f'<img src="{html.escape(image_url)}" style="max-width:100%;max-height:100%;object-fit:contain;">'
    else:
        img = '<span style="font-size:28px;">🦖</span>'
    border = "3px solid rgba(255,255,255,0.85)" if highlight else "1px solid rgba(0,0,0,0.15)"
    weight = 700 if highlight else 400
    # Tudo numa linha só, sem quebra: HTML injetado via st.markdown com uma
    # linha em branco no meio vira bloco indentado (CommonMark trata como
    # código literal, não HTML) — foi o bug do "apareceu o código html".
    return (
        f'<div style="display:flex;flex-direction:column;align-items:center;width:{size + 20}px;">'
        f'<div style="width:{size}px;height:{size}px;border-radius:16px;background:{color};'
        f'border:{border};box-shadow:0 1px 4px rgba(0,0,0,0.25);'
        f'display:flex;align-items:center;justify-content:center;overflow:hidden;">{img}</div>'
        f'<div style="margin-top:6px;font-size:12px;line-height:1.2;text-align:center;'
        f'font-weight:{weight};word-break:break-word;">{safe_name}</div>'
        f"</div>"
    )


_ARROW_HTML = '<div style="font-size:26px;color:#898781;align-self:center;padding:0 2px;">&rarr;</div>'

# Mesmo ranking de estágio usado em databricks/notebooks/02_gold_aggregate.py
# (LEVEL_RANK) — duplicado de propósito: são dois serviços/deploys separados
# (Databricks vs. Streamlit Cloud), não vale a pena um pacote compartilhado
# só por isto. Armor/Hybrid ficam no mesmo rank do estágio "irmão" (formas
# alternativas), o que os coloca em colunas adjacentes na árvore.
_STAGE_RANK = {
    "Baby I": 1, "Baby II": 2, "Child": 3,
    "Adult": 4, "Armor": 4,
    "Perfect": 5, "Ultimate": 6, "Hybrid": 6,
    "Unknown": 0,
}
_UNKNOWN_STAGE_RANK = 99  # estágio que a DAPI não classificou — vai pro fim, não quebra a coluna


def _stage_of(levels: list[str]) -> str:
    known = [lv for lv in levels if lv in _STAGE_RANK]
    if known:
        return min(known, key=lambda lv: _STAGE_RANK[lv])
    return levels[0] if levels else "?"


def _stage_label_html(stage: str) -> str:
    safe = html.escape(stage)
    return (
        '<div style="font-size:11px;color:#898781;text-transform:uppercase;'
        f'letter-spacing:0.04em;margin-bottom:8px;text-align:center;">{safe}</div>'
    )


def _stage_column_html(stage: str, entries: list[dict], current: tuple[str, str | None] | None) -> str:
    cards = []
    if current is not None:
        name, image_url = current
        cards.append(_evolution_card_html(name, image_url, _COLOR_CURRENT, _CURRENT_CARD_SIZE, highlight=True))
    for e in entries:
        color = _COLOR_PRIOR if e["direction"] == "prior" else _COLOR_NEXT
        cards.append(
            _evolution_card_html(e["related_digimon_name"], e.get("related_digimon_image_url"), color, _CARD_SIZE, highlight=False)
        )
    cards_html = f'<div style="display:flex;flex-direction:column;align-items:center;gap:14px;">{"".join(cards)}</div>'
    return f'<div style="display:flex;flex-direction:column;align-items:center;">{_stage_label_html(stage)}{cards_html}</div>'


def build_evolution_tree_html(
    digimon_name: str, digimon_image_url: str | None, digimon_levels: list[str], evolutions: list[dict]
) -> tuple[str, int]:
    """Monta a árvore agrupada por estágio (Baby I, Baby II, Child, ...), uma
    coluna por estágio, em vez de só duas pilhas "antes/depois". Retorna
    quantos nós ficaram de fora do teto por coluna, somados, pra avisar o
    usuário."""
    groups: dict[str, list[dict]] = {}
    for e in evolutions:
        stage = _stage_of(e.get("related_digimon_levels") or [])
        groups.setdefault(stage, []).append(e)

    current_stage = _stage_of(digimon_levels)
    ordered_stages = sorted(
        set(groups) | {current_stage},
        key=lambda s: (_STAGE_RANK.get(s, _UNKNOWN_STAGE_RANK), s),
    )

    hidden_total = 0
    parts = [
        '<div style="overflow-x:auto;padding:12px 4px;">',
        '<div style="display:flex;align-items:flex-start;gap:8px;min-width:max-content;">',
    ]
    for i, stage in enumerate(ordered_stages):
        entries = groups.get(stage, [])
        shown, hidden = entries[:_MAX_TREE_NODES], max(0, len(entries) - _MAX_TREE_NODES)
        hidden_total += hidden
        current = (digimon_name, digimon_image_url) if stage == current_stage else None
        parts.append(_stage_column_html(stage, shown, current))
        if i < len(ordered_stages) - 1:
            parts.append(_ARROW_HTML)
    parts.append("</div></div>")
    return "".join(parts), hidden_total


st.set_page_config(page_title="Digimon Lakehouse", page_icon="🦖", layout="wide")
st.title("🦖 Digimon Lakehouse")
st.caption("Bronze → Silver → Gold no Databricks, servido via FastAPI. Projeto de estudo de engenharia de dados.")

tab_explore, tab_stats, tab_evolutions = st.tabs(["Explorar", "Estatísticas", "Evoluções"])

with tab_explore:
    search_name = st.text_input("🔎 Buscar por nome", value="", placeholder="ex.: Agumon")

    levels = ["Todos"] + [s["label"] for s in load_stats("level")]
    types = ["Todos"] + [s["label"] for s in load_stats("type")]
    attributes = ["Todos"] + [s["label"] for s in load_stats("attribute")]

    col_level, col_type, col_attribute, col_page = st.columns(4)
    level = col_level.selectbox("Nível", levels)
    type_ = col_type.selectbox("Tipo", types)
    attribute = col_attribute.selectbox("Atributo", attributes)
    page = col_page.number_input("Página", min_value=1, value=1, step=1)

    page_size = 20
    result = load_digimons(
        limit=page_size, offset=(page - 1) * page_size, level=level, type_=type_, attribute=attribute, name=search_name
    )
    st.caption(f"{result['total']} digimon(s) encontrados")

    for digimon in result["items"]:
        with st.expander(f"{digimon['name']} — {', '.join(digimon['levels']) or 'nível desconhecido'}"):
            img_col, info_col = st.columns([1, 4])
            if digimon.get("image_url"):
                img_col.image(digimon["image_url"], width=100)
            info_col.write(
                f"**Nível:** {', '.join(digimon['levels']) or '—'} · "
                f"**Tipo:** {', '.join(digimon['types']) or '—'} · "
                f"**Atributo:** {', '.join(digimon['attributes']) or '—'}"
            )

            st.divider()
            st.markdown("**Árvore de digivolução**")
            evolutions = load_evolutions(digimon["digimon_id"])
            if not evolutions:
                st.caption("Nenhuma digivolução registrada pra este digimon.")
            else:
                tree_html, hidden_total = build_evolution_tree_html(
                    digimon["name"], digimon.get("image_url"), digimon["levels"], evolutions
                )
                st.markdown(tree_html, unsafe_allow_html=True)
                st.caption("🔵 evoluiu de · 🟠 este digimon · 🟢 evolui para — colunas por estágio")
                if hidden_total:
                    st.caption(
                        f"Mostrando até {_MAX_TREE_NODES} por estágio — {hidden_total} digivolução(ões) "
                        "ficaram de fora do desenho."
                    )
                # Nota: não dá pra usar outro st.expander aqui — Streamlit não
                # permite expander dentro de expander (StreamlitAPIException).
                if hidden_total:
                    st.markdown("**Lista completa (texto)**")
                    next_evos = [e["related_digimon_name"] for e in evolutions if e["direction"] == "next"]
                    prior_evos = [e["related_digimon_name"] for e in evolutions if e["direction"] == "prior"]
                    st.write(f"➡️ Evolui para: {', '.join(next_evos) if next_evos else '_nenhuma registrada_'}")
                    st.write(f"⬅️ Evoluiu de: {', '.join(prior_evos) if prior_evos else '_nenhuma registrada_'}")

with tab_stats:
    st.subheader("Distribuição por nível")
    st.bar_chart({s["label"]: s["digimon_count"] for s in load_stats("level")})

    st.subheader("Distribuição por tipo")
    st.bar_chart({s["label"]: s["digimon_count"] for s in load_stats("type")})

    st.subheader("Distribuição por atributo")
    st.bar_chart({s["label"]: s["digimon_count"] for s in load_stats("attribute")})

with tab_evolutions:
    st.subheader("Cadeias de evolução mais longas")
    chains = load_longest_chains(limit=10)
    for chain in chains:
        st.write(f"**{chain['root_digimon_name']} → {chain['leaf_digimon_name']}** ({chain['depth']} estágios)")

st.divider()
st.caption(
    "🇧🇷 DAPI é uma API gratuita de Digimon que usa dados de fontes oficiais e "
    "feitas por fãs (principalmente Wikimon.net). A DAPI não é afiliada nem "
    "reivindica propriedade sobre o material produzido pela Bandai. Digimon e "
    "outras mídias relacionadas à franquia são marcas registradas da Bandai.\n\n"
    "🇺🇸 DAPI is a free Digimon API, it uses data from official and fan based "
    "sources (mainly Wikimon.net). DAPI is not affiliated with nor claims "
    "ownership to material produced by Bandai. Digimon and other media "
    "relating to the franchise are registered trademarks of Bandai."
)

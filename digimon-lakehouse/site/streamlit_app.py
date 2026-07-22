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
            st.markdown("**Digivoluções**")
            evolutions = load_evolutions(digimon["digimon_id"])
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

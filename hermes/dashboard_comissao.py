import io
import csv
from datetime import datetime
import pandas as pd
import streamlit as st
import plotly.express as px

st.set_page_config(page_title="Dashboard de Comissão", layout="wide")
st.title("📊 Painel Geral de Produção e Comissão")

# Sidebar: metas por equipe
st.sidebar.header("⚙️ Configurações")
meta_ura = st.sidebar.number_input("Meta Equipe URA", value=80, step=1)
meta_discador = st.sidebar.number_input("Meta Equipe DISCADOR", value=60, step=1)

st.sidebar.markdown("---")
st.sidebar.write("Formato opcional: CSV com coluna meta_personalizada para sobrescrever a meta padrão da equipe.")

# Upload do arquivo
uploaded_file = st.file_uploader(
    "📁 Envie a base de produção (.csv) com colunas: nome, equipe, realizado e opcional meta_personalizada",
    type=["csv"]
)
if not uploaded_file:
    st.info("Envie um arquivo CSV com as colunas: nome, equipe, realizado. (meta_personalizada é opcional).")
    st.stop()

# Leitura robusta do CSV
raw = uploaded_file.getvalue().decode("utf-8", errors="ignore")
try:
    dialect = csv.Sniffer().sniff(raw.splitlines()[0] + "\n", delimiters=[",", ";"])
    sep = dialect.delimiter
except Exception:
    sep = ";" if ";" in raw.splitlines()[0] else ","

df = pd.read_csv(io.StringIO(raw), sep=sep, decimal=",")
df.columns = df.columns.str.strip().str.lower()

# Renomeia colunas se necessário (aceita name/team/done)
rename_map = {}
if "name" in df.columns and "nome" not in df.columns:
    rename_map["name"] = "nome"
if "team" in df.columns and "equipe" not in df.columns:
    rename_map["team"] = "equipe"
if "done" in df.columns and "realizado" not in df.columns:
    rename_map["done"] = "realizado"
if rename_map:
    df = df.rename(columns=rename_map)

# Validação de colunas
required = {"nome", "equipe", "realizado"}
if not required.issubset(df.columns):
    st.error(f"Colunas obrigatórias ausentes. Esperado: {required}")
    st.stop()

# Normalizações e conversões
df["nome"] = df["nome"].astype(str).str.strip()
df["equipe"] = df["equipe"].astype(str).str.strip()
df["equipe_upper"] = df["equipe"].str.upper().str.strip()
df["realizado"] = pd.to_numeric(df["realizado"], errors="coerce").fillna(0)

# Detecta coluna de meta personalizada se existir (aceita vários nomes)
meta_col = None
for c in ("meta_personalizada", "meta_personal", "meta"):
    if c in df.columns:
        meta_col = c
        break

if meta_col:
    df["meta_personalizada"] = pd.to_numeric(df[meta_col], errors="coerce")
else:
    df["meta_personalizada"] = pd.NA

# Função para obter meta aplicada (prioridade: meta_personalizada se válida, senão meta por equipe)
def meta_aplicada(row):
    if pd.notna(row.get("meta_personalizada")):
        try:
            v = float(row["meta_personalizada"])
            if v > 0:
                return v
        except Exception:
            pass
    return meta_ura if "URA" in row["equipe_upper"] else meta_discador

df["meta_aplicada"] = df.apply(meta_aplicada, axis=1)

# Marca se a meta aplicada é reduzida em relação à meta padrão da equipe
def is_meta_reduzida(row):
    padrao = meta_ura if "URA" in row["equipe_upper"] else meta_discador
    try:
        return float(row["meta_aplicada"]) < padrao
    except Exception:
        return False

df["meta_reduzida"] = df.apply(is_meta_reduzida, axis=1)
# Sidebar: operadores com meta reduzida — exibe apenas Nome e Meta Aplicada
st.sidebar.markdown("---")
st.sidebar.subheader("Operadores com Meta Reduzida")

# garante existência das colunas
df["meta_aplicada"] = df.get("meta_aplicada", pd.NA)
df["meta_reduzida"] = df.get("meta_reduzida", False).fillna(False)

# filtra apenas os com meta reduzida
df_reduzida = df[df["meta_reduzida"] == True].copy()

if df_reduzida.empty:
    st.sidebar.write("Nenhum operador com meta reduzida.")
else:
    # prepara tabela de exibição com só Nome e Meta Aplicada
    df_reduzida_display = df_reduzida[["nome", "meta_aplicada"]].copy()
    df_reduzida_display = df_reduzida_display.rename(columns={"nome": "Nome", "meta_aplicada": "Meta Aplicada"})
    # formata meta como inteiro (ou mantém float se preferir)
    df_reduzida_display["Meta Aplicada"] = df_reduzida_display["Meta Aplicada"].apply(
        lambda x: f"{int(x)}" if pd.notna(x) and float(x).is_integer() else (f"{x}" if pd.notna(x) else "")
    )

    # contagem e exibição compacta
    st.sidebar.write(f"Total: **{len(df_reduzida_display)}**")
    with st.sidebar.expander("Ver lista completa", expanded=False):
        st.table(df_reduzida_display.reset_index(drop=True))


# Cálculos de atingimento, faixas, acelerador e comissões
def calcular_atingimento(row):
    meta = row["meta_aplicada"]
    return row["realizado"] / meta if meta else 0

def faixa_valor(atingimento):
    if atingimento < 0.8:
        return 0
    elif atingimento < 0.9:
        return 5
    elif atingimento < 1.0:
        return 7
    else:
        return 9

def acelerador(atingimento):
    if atingimento >= 1.2:
        return 1.2
    elif atingimento >= 1.1:
        return 1.1
    else:
        return 1.0

df["atingimento_raw"] = df.apply(calcular_atingimento, axis=1)
df["valor_unitario"] = df["atingimento_raw"].apply(faixa_valor)
df["comissao_base"] = df["valor_unitario"] * df["realizado"]
df["acelerador"] = df["atingimento_raw"].apply(acelerador)
df["comissao_final"] = df["comissao_base"] * df["acelerador"]

# Ranking e bônus por equipe (aplica apenas aos elegíveis >=80%)
def calcular_top3(equipe_df, equipe_nome):
    elegiveis = equipe_df[equipe_df["atingimento_raw"] >= 0.8].copy()
    elegiveis = elegiveis.sort_values(by=["realizado", "atingimento_raw"], ascending=False).reset_index(drop=True)
    bonus = [700, 500, 350]
    elegiveis["bonus"] = 0
    for i in range(min(3, len(elegiveis))):
        elegiveis.at[i, "bonus"] = bonus[i]
    elegiveis["equipe_upper"] = equipe_nome
    return elegiveis

top3_ura = calcular_top3(df[df["equipe_upper"].str.contains("URA")], "URA")
top3_discador = calcular_top3(df[df["equipe_upper"].str.contains("DISC")], "DISCADOR")

# Merge dos bônus com correspondência por nome + equipe
df = df.merge(top3_ura[["nome", "equipe_upper", "bonus"]], on=["nome", "equipe_upper"], how="left")
df = df.rename(columns={"bonus": "bonus_ura"})
df = df.merge(top3_discador[["nome", "equipe_upper", "bonus"]], on=["nome", "equipe_upper"], how="left")
df = df.rename(columns={"bonus": "bonus_discador"})
df["bonus_final"] = df[["bonus_ura", "bonus_discador"]].max(axis=1).fillna(0)

# Campo TOTAL
df["TOTAL"] = (df["comissao_final"] + df["bonus_final"]).round(2)

# Formata colunas para exibição
df["% Atingimento"] = (df["atingimento_raw"] * 100).round(2).astype(str) + "%"
df["Comissão Final"] = df["comissao_final"].round(2)
df["Bônus"] = df["bonus_final"].round(2)

# KPIs gerais
st.subheader("🔍 Visão Geral da Produção")
col1, col2, col3 = st.columns(3)
col1.metric("Total de Contas", int(df["realizado"].sum()))
col2.metric("Comissão Total (R$)", f"R$ {df['TOTAL'].sum():,.2f}")
col3.metric("Colaboradores Elegíveis", int(df[df["atingimento_raw"] >= 0.8].shape[0]))

# Gráficos lado a lado
st.subheader("📊 Comparativo: Total Recebido vs. Contas Abertas")
col_g1, col_g2 = st.columns(2)

with col_g1:
    fig_total = px.bar(
        df.sort_values("TOTAL", ascending=False).head(20),
        x="nome", y="TOTAL", color="equipe",
        labels={"nome": "Colaborador", "TOTAL": "Total Recebido (R$)"},
        title="💰 Total Recebido (Top 20)"
    )
    st.plotly_chart(fig_total, use_container_width=True)

with col_g2:
    fig_contas = px.bar(
        df.sort_values("realizado", ascending=False).head(20),
        x="nome", y="realizado", color="equipe",
        labels={"nome": "Colaborador", "realizado": "Contas Abertas"},
        title="📈 Contas Abertas (Top 20)"
    )
    st.plotly_chart(fig_contas, use_container_width=True)

# KPIs por equipe
st.subheader("📈 KPIs por Equipe")
group = df.groupby("equipe_upper").agg(
    total_contas=("realizado", "sum"),
    media_atingimento=("atingimento_raw", "mean"),
    comissao_total=("TOTAL", "sum"),
    elegiveis=("atingimento_raw", lambda x: (x >= 0.8).sum())
).reset_index().rename(columns={"equipe_upper": "Equipe"})
group["media_atingimento"] = (group["media_atingimento"] * 100).round(2).astype(str) + "%"
group["comissao_total"] = group["comissao_total"].round(2)
st.dataframe(group)

# Função de estilo para destacar linhas com meta reduzida em azul escuro (background) e texto branco
def highlight_meta_reduzida(row):
    # row é uma Series com índice das colunas exibidas; procuramos coluna 'meta_reduzida' se existir
    if "meta_reduzida" in row.index and bool(row["meta_reduzida"]):
        return ['background-color: #08306B; color: white'] * len(row)
    return [''] * len(row)

# Garante que top3_* possuam coluna meta_reduzida antes de exibir (merge com df principal)
top3_ura = top3_ura.merge(df[["nome", "meta_reduzida"]], on="nome", how="left") if not top3_ura.empty else top3_ura
top3_discador = top3_discador.merge(df[["nome", "meta_reduzida"]], on="nome", how="left") if not top3_discador.empty else top3_discador

# TOP 3 - URA (exibe Posição como índice)
st.subheader("🏆 TOP 3 - URA")
if top3_ura.empty:
    st.write("Nenhum elegível na URA.")
else:
    # garante que top3_ura tenha meta_reduzida vindo do df principal
    if "meta_reduzida" not in top3_ura.columns:
        top3_ura = top3_ura.merge(df[["nome", "meta_reduzida"]], on="nome", how="left")

    top3_ura_display = (
        top3_ura
        .sort_values(by=["realizado", "atingimento_raw"], ascending=False)
        .reset_index(drop=True)
        .head(3)
    )
    top3_ura_display["Posição"] = top3_ura_display.index + 1
    top3_ura_display["% Atingimento"] = (top3_ura_display["atingimento_raw"] * 100).round(2).astype(str) + "%"
    top3_ura_display["Comissão Final"] = top3_ura_display["comissao_final"].round(2).apply(lambda x: f"R$ {x:,.2f}")
    top3_ura_display["Bônus"] = top3_ura_display["bonus"].round(2).apply(lambda x: f"R$ {x:,.2f}")

    # Se por algum motivo meta_reduzida ainda não existir, cria com False para evitar KeyError
    if "meta_reduzida" not in top3_ura_display.columns:
        top3_ura_display["meta_reduzida"] = False

    display_df_ura = top3_ura_display[[
        "Posição", "nome", "realizado", "% Atingimento", "Comissão Final", "Bônus"
    ]].rename(columns={"nome": "Nome", "realizado": "Realizado"})
    display_df_ura = display_df_ura.set_index("Posição")

    styled_ura = display_df_ura.style.apply(highlight_meta_reduzida, axis=1)
    st.dataframe(styled_ura)


# TOP 3 - DISCADOR
st.subheader("🏆 TOP 3 - DISCADOR")
if top3_discador.empty:
    st.write("Nenhum elegível no Discador.")
else:
    # garante que top3_discador tenha meta_reduzida vindo do df principal
    if "meta_reduzida" not in top3_discador.columns:
        top3_discador = top3_discador.merge(df[["nome", "meta_reduzida"]], on="nome", how="left")

    top3_discador_display = (
        top3_discador
        .sort_values(by=["realizado", "atingimento_raw"], ascending=False)
        .reset_index(drop=True)
        .head(3)
    )
    top3_discador_display["Posição"] = top3_discador_display.index + 1
    top3_discador_display["% Atingimento"] = (top3_discador_display["atingimento_raw"] * 100).round(2).astype(str) + "%"
    top3_discador_display["Comissão Final"] = top3_discador_display["comissao_final"].round(2).apply(lambda x: f"R$ {x:,.2f}")
    top3_discador_display["Bônus"] = top3_discador_display["bonus"].round(2).apply(lambda x: f"R$ {x:,.2f}")

    if "meta_reduzida" not in top3_discador_display.columns:
        top3_discador_display["meta_reduzida"] = False

    display_df_disc = top3_discador_display[[
        "Posição", "nome", "realizado", "% Atingimento", "Comissão Final", "Bônus"
    ]].rename(columns={"nome": "Nome", "realizado": "Realizado"})
    display_df_disc = display_df_disc.set_index("Posição")

    styled_disc = display_df_disc.style.apply(highlight_meta_reduzida, axis=1)
    st.dataframe(styled_disc)


# Ranking geral (com Posição como índice)
st.subheader("📋 Ranking Geral")
ranking_display = (df.copy()
                   .sort_values(by=["realizado", "atingimento_raw"], ascending=False)
                   .reset_index(drop=True))

# garante existência e booleana
ranking_display["meta_reduzida"] = ranking_display.get("meta_reduzida", False).fillna(False)
ranking_display["Posição"] = ranking_display.index + 1
ranking_display["Comissão Final"] = ranking_display["Comissão Final"].apply(lambda x: f"R$ {x:,.2f}")
ranking_display["Bônus"] = ranking_display["Bônus"].apply(lambda x: f"R$ {x:,.2f}")
ranking_display["TOTAL"] = ranking_display["TOTAL"].apply(lambda x: f"R$ {x:,.2f}")

display_ranking = ranking_display[[
    "Posição", "nome", "equipe", "realizado", "% Atingimento", "Comissão Final", "Bônus", "TOTAL"
]].rename(columns={"nome": "Nome", "equipe": "Equipe", "realizado": "Realizado"})
display_ranking = display_ranking.set_index("Posição")

styled_ranking = display_ranking.style.apply(highlight_meta_reduzida, axis=1)
st.dataframe(styled_ranking)


# Exportação (remove coluna meta_reduzida do CSV exportado)
st.subheader("📥 Exportar relatório")
export_df = ranking_display.drop(columns=["meta_reduzida"])
csv_bytes = export_df.to_csv(index=False).encode("utf-8")
st.download_button("📥 Baixar Relatório Final (CSV UTF-8)", data=csv_bytes, file_name="relatorio_comissao.csv", mime="text/csv")

st.success("✅ Dashboard carregado com sucesso!")

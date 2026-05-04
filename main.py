from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import io
import os
from datetime import datetime
from supabase import create_client

app = FastAPI(title="Analisador Online de Currículos")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def normalizar(valor):
    if pd.isna(valor):
        return ""
    return str(valor).lower().strip()


def contem_palavra(texto_base, palavras):
    texto_base = normalizar(texto_base)
    return any(p.lower().strip() in texto_base for p in palavras if p.strip())


def calcular_score(row, cidade_base, palavras_experiencia, escolaridade_minima):
    pontos = 0
    motivos = []

    localizacao = normalizar(row.get("localização do candidato", ""))
    experiencia = normalizar(row.get("experiência relevante", ""))
    escolaridade = normalizar(row.get("escolaridade", ""))
    status = normalizar(row.get("status", ""))
    interesse = normalizar(row.get("nível de interesse", ""))
    cargo = normalizar(row.get("cargo", ""))

    if cidade_base and cidade_base.lower() in localizacao:
        pontos += 25
        motivos.append("Mora na cidade desejada")

    if palavras_experiencia and contem_palavra(experiencia + " " + cargo, palavras_experiencia):
        pontos += 30
        motivos.append("Experiência compatível com a vaga")

    if escolaridade_minima and escolaridade_minima.lower() in escolaridade:
        pontos += 10
        motivos.append("Escolaridade compatível")

    if "alto" in interesse or "interessado" in interesse:
        pontos += 10
        motivos.append("Bom nível de interesse")

    if "novo" in status or "ativo" in status or "selecionado" in status:
        pontos += 10
        motivos.append("Status favorável")

    respostas_boas = 0
    for i in range(1, 16):
        correta = normalizar(row.get(f"Pergunta {i} correta", ""))
        if correta in ["sim", "true", "correta", "1"]:
            respostas_boas += 1

    if respostas_boas >= 5:
        pontos += 15
        motivos.append(f"Bom desempenho nas perguntas: {respostas_boas}")

    return pontos, motivos


@app.get("/")
def home():
    return {
        "status": "online",
        "app": "Analisador Online de Currículos"
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "supabase_configurado": bool(supabase)
    }


@app.post("/analisar-curriculos")
async def analisar_curriculos(
    arquivo: UploadFile = File(...),
    cidade_base: str = Form("Campinas"),
    palavras_experiencia: str = Form("e-commerce, atendimento, vendas, marketplace, estoque"),
    escolaridade_minima: str = Form(""),
    pontuacao_minima: int = Form(50),
    limite_resultados: int = Form(30),
    nome_vaga: str = Form("Assistente E-commerce")
):
    conteudo = await arquivo.read()

    if arquivo.filename.lower().endswith(".csv"):
        df = pd.read_csv(io.BytesIO(conteudo))
    elif arquivo.filename.lower().endswith(".xlsx"):
        df = pd.read_excel(io.BytesIO(conteudo))
    else:
        return {
            "status": "erro",
            "mensagem": "Envie um arquivo CSV ou XLSX"
        }

    palavras = [p.strip() for p in palavras_experiencia.split(",")]

    candidatos = []

    for _, row in df.iterrows():
        pontos, motivos = calcular_score(
            row,
            cidade_base,
            palavras,
            escolaridade_minima
        )

        if pontos >= pontuacao_minima:
            candidatos.append({
                "nome": row.get("nome", ""),
                "telefone": row.get("telefone", ""),
                "email": row.get("e-mail", ""),
                "localizacao": row.get("localização do candidato", ""),
                "experiencia": row.get("experiência relevante", ""),
                "escolaridade": row.get("escolaridade", ""),
                "cargo": row.get("cargo", ""),
                "status_candidato": row.get("status", ""),
                "nivel_interesse": row.get("nível de interesse", ""),
                "pontuacao": pontos,
                "motivos": motivos
            })

    candidatos = sorted(candidatos, key=lambda x: x["pontuacao"], reverse=True)
    candidatos = candidatos[:limite_resultados]

    resposta = {
        "status": "ok",
        "nome_vaga": nome_vaga,
        "arquivo": arquivo.filename,
        "total_candidatos_planilha": len(df),
        "total_aprovados": len(candidatos),
        "criterios": {
            "cidade_base": cidade_base,
            "palavras_experiencia": palavras,
            "escolaridade_minima": escolaridade_minima,
            "pontuacao_minima": pontuacao_minima,
            "limite_resultados": limite_resultados
        },
        "candidatos": candidatos
    }

    if supabase:
        try:
            supabase.table("analises_curriculos").insert({
                "nome_vaga": nome_vaga,
                "arquivo": arquivo.filename,
                "total_candidatos": int(len(df)),
                "total_aprovados": int(len(candidatos)),
                "criterios": resposta["criterios"],
                "resultado": candidatos,
                "created_at": datetime.utcnow().isoformat()
            }).execute()
        except Exception as e:
            resposta["supabase_erro"] = str(e)

    return resposta


@app.get("/historico")
def historico():
    if not supabase:
        return {
            "status": "erro",
            "mensagem": "Supabase não configurado"
        }

    dados = supabase.table("analises_curriculos").select("*").order(
        "created_at",
        desc=True
    ).limit(20).execute()

    return {
        "status": "ok",
        "historico": dados.data
    }

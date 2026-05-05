from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import io
import os
import requests
from datetime import datetime
from supabase import create_client

app = FastAPI(title="Analisador Inteligente de Currículos")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

ENDERECO_EMPRESA = "Av. Dante Alighieri, 520 - Campinas SP"


def normalizar(valor):
    if pd.isna(valor):
        return ""
    return str(valor).lower().strip()


def calcular_tempo_deslocamento(origem):
    try:
        url = "https://routes.googleapis.com/directions/v2:computeRoutes"

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": GOOGLE_API_KEY,
            "X-Goog-FieldMask": "routes.duration,routes.distanceMeters"
        }

        data = {
            "origin": {"address": origem},
            "destination": {"address": ENDERECO_EMPRESA},
            "travelMode": "DRIVING",
            "routingPreference": "TRAFFIC_AWARE"
        }

        response = requests.post(url, json=data, headers=headers)

        if response.status_code != 200:
            return None, None

        result = response.json()

        rota = result["routes"][0]
        duracao = rota["duration"]
        distancia = rota["distanceMeters"]

        minutos = int(duracao.replace("s", "")) // 60

        return minutos, distancia

    except:
        return None, None


def pontuar_localizacao(minutos):
    if minutos is None:
        return 0, "Localização não identificada"

    if minutos <= 30:
        return 30, f"{minutos} min até a loja (ótimo)"
    elif minutos <= 45:
        return 20, f"{minutos} min até a loja (bom)"
    elif minutos <= 60:
        return 10, f"{minutos} min até a loja (aceitável)"
    else:
        return -20, f"{minutos} min até a loja (muito longe)"


def contem_palavra(texto_base, palavras):
    texto_base = normalizar(texto_base)
    return any(p in texto_base for p in palavras)


def calcular_score(row, palavras_experiencia, escolaridade_minima):
    pontos = 0
    motivos = []

    localizacao = normalizar(row.get("localização do candidato", ""))
    experiencia = normalizar(row.get("experiência relevante", ""))
    escolaridade = normalizar(row.get("escolaridade", ""))
    interesse = normalizar(row.get("nível de interesse", ""))
    status = normalizar(row.get("status", ""))
    cargo = normalizar(row.get("cargo", ""))

    # localização
    minutos, distancia = calcular_tempo_deslocamento(localizacao)
    pts_loc, motivo_loc = pontuar_localizacao(minutos)

    pontos += pts_loc
    motivos.append(motivo_loc)

    # experiência
    if contem_palavra(experiencia + " " + cargo, palavras_experiencia):
        pontos += 30
        motivos.append("Experiência compatível")

    # escolaridade
    if escolaridade_minima and escolaridade_minima.lower() in escolaridade:
        pontos += 10
        motivos.append("Escolaridade ok")

    # interesse
    if "alto" in interesse or "interessado" in interesse:
        pontos += 10
        motivos.append("Alto interesse")

    # status
    if "ativo" in status or "novo" in status:
        pontos += 10
        motivos.append("Perfil ativo")

    return pontos, motivos, minutos


@app.get("/")
def home():
    return {"status": "online"}


@app.post("/analisar-curriculos")
async def analisar_curriculos(
    arquivo: UploadFile = File(...),
    palavras_experiencia: str = Form("ecommerce, atendimento, vendas"),
    escolaridade_minima: str = Form(""),
    pontuacao_minima: int = Form(50),
    limite_resultados: int = Form(20),
    nome_vaga: str = Form("Assistente E-commerce")
):
    conteudo = await arquivo.read()

    if arquivo.filename.endswith(".csv"):
        df = pd.read_csv(io.BytesIO(conteudo))
    else:
        df = pd.read_excel(io.BytesIO(conteudo))

    palavras = [p.strip() for p in palavras_experiencia.split(",")]

    candidatos = []

    for _, row in df.iterrows():
        pontos, motivos, minutos = calcular_score(
            row,
            palavras,
            escolaridade_minima
        )

        if pontos >= pontuacao_minima:
            candidatos.append({
                "nome": row.get("nome", ""),
                "localizacao": row.get("localização do candidato", ""),
                "pontuacao": pontos,
                "tempo_ate_loja_min": minutos,
                "motivos": motivos
            })

    candidatos = sorted(candidatos, key=lambda x: x["pontuacao"], reverse=True)
    candidatos = candidatos[:limite_resultados]

    if supabase:
        supabase.table("analises_curriculos").insert({
            "nome_vaga": nome_vaga,
            "total_aprovados": len(candidatos),
            "resultado": candidatos,
            "created_at": datetime.utcnow().isoformat()
        }).execute()

    return {
        "status": "ok",
        "candidatos": candidatos
    }

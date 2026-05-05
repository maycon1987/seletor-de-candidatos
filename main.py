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
        print("📍 Origem:", origem)

        if not origem:
            return None, None

        url = "https://routes.googleapis.com/directions/v2:computeRoutes"

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": GOOGLE_API_KEY,
            "X-Goog-FieldMask": "routes.duration,routes.distanceMeters"
        }

        data = {
            "origin": {"address": origem},
            "destination": {"address": ENDERECO_EMPRESA},
            "travelMode": "DRIVING"
        }

        response = requests.post(url, json=data, headers=headers)

        print("🔎 STATUS GOOGLE:", response.status_code)
        print("🔎 RESPOSTA GOOGLE:", response.text)

        if response.status_code != 200:
            return None, None

        result = response.json()

        if "routes" not in result:
            return None, None

        rota = result["routes"][0]
        duracao = rota["duration"]

        minutos = int(duracao.replace("s", "")) // 60

        return minutos, rota.get("distanceMeters")

    except Exception as e:
        print("🔥 ERRO NA DISTÂNCIA:", str(e))
        return None, None


def calcular_score(row, palavras_experiencia, escolaridade_minima):
    try:
        pontos = 0
        motivos = []

        localizacao = normalizar(row.get("localização do candidato", ""))
        experiencia = normalizar(row.get("experiência relevante", ""))
        escolaridade = normalizar(row.get("escolaridade", ""))
        interesse = normalizar(row.get("nível de interesse", ""))
        status = normalizar(row.get("status", ""))
        cargo = normalizar(row.get("cargo", ""))

        minutos, _ = calcular_tempo_deslocamento(localizacao)

        if minutos is not None:
            if minutos <= 30:
                pontos += 30
                motivos.append("Perto da loja")
            elif minutos <= 60:
                pontos += 10
                motivos.append("Distância ok")
            else:
                pontos -= 20
                motivos.append("Muito longe")

        if any(p in experiencia + " " + cargo for p in palavras_experiencia):
            pontos += 30
            motivos.append("Experiência compatível")

        if escolaridade_minima and escolaridade_minima.lower() in escolaridade:
            pontos += 10
            motivos.append("Escolaridade ok")

        if "alto" in interesse:
            pontos += 10

        if "ativo" in status:
            pontos += 10

        return pontos, motivos, minutos

    except Exception as e:
        print("🔥 ERRO NO SCORE:", str(e))
        return 0, ["Erro ao calcular"], None


@app.post("/analisar-curriculos")
async def analisar_curriculos(
    arquivo: UploadFile = File(...),
    palavras_experiencia: str = Form("ecommerce, atendimento, vendas"),
    escolaridade_minima: str = Form(""),
    pontuacao_minima: int = Form(50),
    limite_resultados: int = Form(20),
    nome_vaga: str = Form("Assistente E-commerce")
):
    try:
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

        return {
            "status": "ok",
            "candidatos": candidatos
        }

    except Exception as e:
        print("🔥 ERRO GERAL:", str(e))
        return {"status": "erro", "mensagem": str(e)}

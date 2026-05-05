from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import io
import os
import requests
import json
from openai import OpenAI

app = FastAPI(title="Seletor de Candidatos com IA")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GOOGLE_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

ENDERECO_EMPRESA = "Av. Dante Alighieri, 520 - Jardim do Lago, Campinas - SP"


def normalizar(valor):
    if pd.isna(valor):
        return ""
    return str(valor).strip()


def analisar_experiencia_com_ia(nome, cargo, experiencia):
    if not client:
        return {
            "erro": "OPENAI_API_KEY não configurada",
            "nota_ia": 0
        }

    prompt = f"""
Analise o candidato abaixo para uma vaga de Assistente de E-commerce.

Nome: {nome}
Cargo informado: {cargo}
Experiência informada:
{experiencia}

Responda APENAS em JSON válido com este formato:
{{
  "resumo_profissional": "",
  "funcoes_identificadas": [],
  "areas_experiencia": [],
  "tempo_experiencia_estimado_meses": 0,
  "experiencia_relevante_ecommerce": true,
  "pontos_fortes": [],
  "alertas": [],
  "nota_ia": 0,
  "motivo_nota": ""
}}

Critérios:
- nota_ia deve ser de 0 a 40.
- Dê mais nota para atendimento, vendas, e-commerce, marketplace, cadastro de produtos, expedição, estoque, embalagem e rotina administrativa.
- Se a experiência for muito vaga, reduza a nota.
- Se houver estabilidade ou tempo claro em empresas, valorize.
"""

    try:
        resposta = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "Você é um analista de RH especializado em triagem de currículos para comércio, e-commerce e atendimento."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.2
        )

        texto = resposta.choices[0].message.content
        texto = texto.replace("```json", "").replace("```", "").strip()

        return json.loads(texto)

    except Exception as e:
        return {
            "erro": str(e),
            "nota_ia": 0
        }


def calcular_tempo_deslocamento(origem):
    try:
        if not GOOGLE_API_KEY or not origem:
            return None

        url = "https://routes.googleapis.com/directions/v2:computeRoutes"

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": GOOGLE_API_KEY,
            "X-Goog-FieldMask": "routes.duration"
        }

        data = {
            "origin": {"address": origem},
            "destination": {"address": ENDERECO_EMPRESA},
            "travelMode": "DRIVING"
        }

        response = requests.post(url, json=data, headers=headers, timeout=10)

        if response.status_code != 200:
            print("ERRO GOOGLE:", response.status_code, response.text)
            return None

        result = response.json()

        if "routes" not in result or not result["routes"]:
            return None

        duracao = result["routes"][0]["duration"]
        minutos = int(duracao.replace("s", "")) // 60

        return minutos

    except Exception as e:
        print("ERRO DISTANCIA:", str(e))
        return None


def pontuar_localizacao(minutos):
    if minutos is None:
        return 0, "Localização não calculada"

    if minutos <= 30:
        return 30, "Muito perto da loja"
    elif minutos <= 45:
        return 20, "Distância boa"
    elif minutos <= 60:
        return 10, "Distância aceitável"
    else:
        return -20, "Muito longe da loja"


@app.get("/")
def home():
    return {
        "status": "online",
        "app": "Seletor de Candidatos com IA"
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "google_maps_configurado": bool(GOOGLE_API_KEY),
        "openai_configurado": bool(OPENAI_API_KEY)
    }


@app.post("/analisar-curriculos")
async def analisar_curriculos(
    arquivo: UploadFile = File(...),
    pontuacao_minima: int = Form(40),
    limite_resultados: int = Form(20),
    usar_ia: bool = Form(True)
):
    conteudo = await arquivo.read()

    if arquivo.filename.lower().endswith(".csv"):
        df = pd.read_csv(io.BytesIO(conteudo))
    else:
        df = pd.read_excel(io.BytesIO(conteudo))

    candidatos = []

    for _, row in df.iterrows():
        nome = normalizar(row.get("nome", ""))
        cargo = normalizar(row.get("cargo", ""))
        localizacao = normalizar(row.get("localização do candidato", ""))
        experiencia = normalizar(row.get("experiência relevante", ""))
        escolaridade = normalizar(row.get("escolaridade", ""))
        telefone = normalizar(row.get("telefone", ""))
        email = normalizar(row.get("e-mail", ""))

        minutos = calcular_tempo_deslocamento(localizacao)
        pontos_localizacao, motivo_localizacao = pontuar_localizacao(minutos)

        analise_ia = {}
        nota_ia = 0

        if usar_ia:
            analise_ia = analisar_experiencia_com_ia(nome, cargo, experiencia)
            nota_ia = int(analise_ia.get("nota_ia", 0))

        pontuacao_total = pontos_localizacao + nota_ia

        if pontuacao_total >= pontuacao_minima:
            candidatos.append({
                "nome": nome,
                "telefone": telefone,
                "email": email,
                "cargo": cargo,
                "localizacao": localizacao,
                "escolaridade": escolaridade,
                "experiencia_original": experiencia,
                "tempo_ate_loja_min": minutos,
                "pontuacao_localizacao": pontos_localizacao,
                "motivo_localizacao": motivo_localizacao,
                "pontuacao_ia": nota_ia,
                "pontuacao_total": pontuacao_total,
                "analise_ia": analise_ia
            })

    candidatos = sorted(
        candidatos,
        key=lambda x: x["pontuacao_total"],
        reverse=True
    )[:limite_resultados]

    return {
        "status": "ok",
        "total_candidatos_planilha": len(df),
        "total_aprovados": len(candidatos),
        "candidatos": candidatos
    }

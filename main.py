from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import io
import os
import json
import requests
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

ENDERECO_EMPRESA = "Av. Dante Alighieri, 520 - Campinas SP"


def normalizar(valor):
    if pd.isna(valor):
        return ""
    return str(valor).strip()


def texto_baixo(valor):
    return normalizar(valor).lower()


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

        response = requests.post(url, json=data, headers=headers, timeout=8)

        if response.status_code != 200:
            return None

        result = response.json()

        if "routes" not in result or not result["routes"]:
            return None

        duracao = result["routes"][0].get("duration", "")
        if not duracao:
            return None

        minutos = int(duracao.replace("s", "")) // 60
        return minutos

    except:
        return None


def pontuar_localizacao(minutos):
    if minutos is None:
        return 0

    if minutos <= 30:
        return 30
    elif minutos <= 45:
        return 20
    elif minutos <= 60:
        return 10
    else:
        return -20


def calcular_score_rapido(row, palavras):
    pontos = 0

    localizacao = texto_baixo(row.get("localização do candidato", ""))
    experiencia = texto_baixo(row.get("experiência relevante", ""))
    cargo = texto_baixo(row.get("cargo", ""))
    escolaridade = texto_baixo(row.get("escolaridade", ""))
    interesse = texto_baixo(row.get("nível de interesse", ""))
    status = texto_baixo(row.get("status", ""))

    texto_total = experiencia + " " + cargo

    if any(p in texto_total for p in palavras):
        pontos += 30

    if "campinas" in localizacao:
        pontos += 15

    if "hortolândia" in localizacao or "sumaré" in localizacao or "valinhos" in localizacao:
        pontos += 8

    if "médio" in escolaridade or "superior" in escolaridade or "técnico" in escolaridade:
        pontos += 10

    if "alto" in interesse:
        pontos += 10

    if "ativo" in status:
        pontos += 5

    return pontos


def analisar_experiencia_com_ia(candidato):
    if not client:
        return {"nota_ia": 0, "experiencia_relevante_ecommerce": False}

    prompt = f"""
Analise este candidato para vaga de Assistente de E-commerce.

Nome: {candidato.get("nome")}
Cargo: {candidato.get("cargo")}
Experiência:
{candidato.get("experiencia_original")}

Responda SOMENTE em JSON:

{{
  "nota_ia": 0,
  "experiencia_relevante_ecommerce": true
}}

Regras:
- nota_ia de 0 a 40
- considere relevante: ecommerce, vendas, atendimento, marketplace, estoque, separação de pedidos, expedição
- se for experiência genérica (limpeza, produção, segurança), dar nota baixa
"""

    try:
        resposta = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            timeout=15
        )

        texto = resposta.choices[0].message.content
        texto = texto.replace("```json", "").replace("```", "").strip()

        return json.loads(texto)

    except:
        return {"nota_ia": 0, "experiencia_relevante_ecommerce": False}


@app.post("/analisar-curriculos")
async def analisar_curriculos(
    arquivo: UploadFile = File(...),
    palavras_experiencia: str = Form("ecommerce, atendimento, vendas, marketplace, estoque"),
    pontuacao_minima: int = Form(30),
    usar_ia: bool = Form(True),
    limite_ia: int = Form(5),
    calcular_distancia: bool = Form(False)
):
    conteudo = await arquivo.read()

    if arquivo.filename.endswith(".csv"):
        df = pd.read_csv(io.BytesIO(conteudo))
    else:
        df = pd.read_excel(io.BytesIO(conteudo))

    palavras = [p.strip().lower() for p in palavras_experiencia.split(",")]

    candidatos = []

    # 🔹 PRÉ-FILTRO
    for _, row in df.iterrows():
        score = calcular_score_rapido(row, palavras)

        if score >= pontuacao_minima:
            candidatos.append({
                "nome": normalizar(row.get("nome")),
                "telefone": normalizar(row.get("telefone")),
                "email": normalizar(row.get("e-mail")),
                "cargo": normalizar(row.get("cargo")),
                "localizacao": normalizar(row.get("localização do candidato")),
                "experiencia_original": normalizar(row.get("experiência relevante")),
                "pontuacao_total": score,
                "pontuacao_ia": None
            })

    # 🔥 ordena e limita para IA
    candidatos = sorted(candidatos, key=lambda x: x["pontuacao_total"], reverse=True)
    candidatos = candidatos[:limite_ia]

    # 🔹 IA obrigatória
    if usar_ia:
        candidatos_filtrados = []

        for c in candidatos:
            analise = analisar_experiencia_com_ia(c)

            nota_ia = int(analise.get("nota_ia", 0))
            relevante = analise.get("experiencia_relevante_ecommerce", False)

            if nota_ia < 15:
                continue

            if not relevante:
                continue

            c["pontuacao_ia"] = nota_ia
            c["pontuacao_total"] += nota_ia * 2
            c["analise_ia"] = analise

            candidatos_filtrados.append(c)

        candidatos = candidatos_filtrados

    # 🔹 distância opcional
    if calcular_distancia:
        for c in candidatos:
            minutos = calcular_tempo_deslocamento(c["localizacao"])
            pontos = pontuar_localizacao(minutos)
            c["tempo_ate_loja_min"] = minutos
            c["pontuacao_total"] += pontos

    candidatos = sorted(candidatos, key=lambda x: x["pontuacao_total"], reverse=True)

    return {
        "status": "ok",
        "total_final": len(candidatos),
        "candidatos": candidatos
    }

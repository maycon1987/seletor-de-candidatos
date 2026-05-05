from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import io
import os
import json
import requests
from openai import OpenAI

app = FastAPI(title="Seletor Inteligente de Candidatos")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

ENDERECO_EMPRESA = "Av. Dante Alighieri, 520 - Campinas SP"


# ------------------ FUNÇÕES ------------------

def normalizar(v):
    if pd.isna(v):
        return ""
    return str(v).strip()


def texto(v):
    return normalizar(v).lower()


def detectar_genero(nome):
    if not client:
        return "não identificado"

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": f"O nome '{nome}' é masculino ou feminino? Responda apenas: masculino ou feminino"
            }],
            temperature=0
        )

        return resp.choices[0].message.content.lower()

    except:
        return "não identificado"


def calcular_distancia(origem):
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

        r = requests.post(url, json=data, headers=headers, timeout=8)

        if r.status_code != 200:
            return None

        dur = r.json()["routes"][0]["duration"]
        return int(dur.replace("s", "")) // 60

    except:
        return None


def analisar_ia(candidato):
    if not client:
        return {}

    prompt = f"""
Analise o candidato:

Nome: {candidato["nome"]}
Experiência:
{candidato["experiencia"]}

Retorne JSON:

{{
 "nota": 0,
 "relevante": true,
 "tempo_total": 0,
 "resumo": ""
}}

nota de 0 a 40
tempo em meses
"""

    try:
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )

        txt = r.choices[0].message.content
        txt = txt.replace("```json", "").replace("```", "")

        return json.loads(txt)

    except:
        return {}


# ------------------ API ------------------

@app.post("/analisar-curriculos")
async def analisar(
    arquivo: UploadFile = File(...),
    pontuacao_minima: int = Form(30),
    limite: int = Form(10),
    separar_genero: bool = Form(True)
):
    conteudo = await arquivo.read()

    if arquivo.filename.endswith(".csv"):
        df = pd.read_csv(io.BytesIO(conteudo))
    else:
        df = pd.read_excel(io.BytesIO(conteudo))

    candidatos = []

    for _, row in df.iterrows():

        nome = normalizar(row.get("nome"))
        experiencia = normalizar(row.get("experiência relevante"))
        local = normalizar(row.get("localização do candidato"))
        telefone = normalizar(row.get("telefone"))

        base = 0

        if "vendas" in texto(experiencia):
            base += 20

        if "atendimento" in texto(experiencia):
            base += 20

        minutos = calcular_distancia(local)

        if minutos:
            if minutos <= 30:
                base += 30
            elif minutos <= 60:
                base += 10
            else:
                base -= 10

        if base < pontuacao_minima:
            continue

        analise = analisar_ia({
            "nome": nome,
            "experiencia": experiencia
        })

        nota = analise.get("nota", 0)
        relevante = analise.get("relevante", False)

        if nota < 15 or not relevante:
            continue

        genero = detectar_genero(nome)

        telefone_limpo = "".join(filter(str.isdigit, telefone))

        candidatos.append({
            "nome": nome,
            "genero": genero,
            "pontuacao": base + nota * 2,
            "tempo_experiencia": analise.get("tempo_total"),
            "resumo": analise.get("resumo"),
            "distancia_min": minutos,
            "tags": [
                "Perto" if minutos and minutos <= 30 else "Longe",
                "Boa experiência" if nota > 25 else "Experiência média"
            ],
            "whatsapp": f"https://wa.me/55{telefone_limpo}" if telefone_limpo else None
        })

    # 🔥 ORDENAÇÃO
    candidatos = sorted(candidatos, key=lambda x: x["pontuacao"], reverse=True)

    # 🔥 SEPARAR POR GENERO
    if separar_genero:
        homens = [c for c in candidatos if "masculino" in c["genero"]]
        mulheres = [c for c in candidatos if "feminino" in c["genero"]]

        candidatos = homens + mulheres

    return {
        "total": len(candidatos),
        "candidatos": candidatos[:limite]
    }

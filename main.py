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
        return {
            "nota_ia": 0,
            "experiencia_relevante_ecommerce": False
        }

    prompt = f"""
Analise este candidato para vaga de Assistente de E-commerce.

Nome: {candidato.get("nome")}
Cargo: {candidato.get("cargo")}
Experiência:
{candidato.get("experiencia_original")}

Responda SOMENTE em JSON:

{{
  "nota_ia": 0,
  "experiencia_relevante_ecommerce": true,
  "tempo_experiencia_estimado_meses": 0,
  "resumo_profissional": ""
}}

Regras:
- nota_ia de 0 a 40
- estime tempo de experiência em meses
- considere relevante: ecommerce, vendas, atendimento, marketplace, estoque, expedição
- se for genérico (limpeza, produção, etc), nota baixa
- faça um resumo curto e direto
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

    except Exception as e:
        print("ERRO IA:", str(e))
        return {
            "nota_ia": 0,
            "experiencia_relevante_ecommerce": False
        }


@app.post("/analisar-curriculos")
async def analisar_curriculos(
    arquivo: UploadFile = File(...),
    palavras_experiencia: str = Form("ecommerce, atendimento, vendas, marketplace, estoque"),
    pontuacao_minima: int = Form(30),
    usar_ia: bool = Form(True),
    limite_ia: int = Form(5)
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
                "pontuacao_total": score
            })

    # 🔥 pega top para IA
    candidatos = sorted(candidatos, key=lambda x: x["pontuacao_total"], reverse=True)
    candidatos = candidatos[:limite_ia]

    candidatos_filtrados = []

    # 🔹 IA
    if usar_ia:
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

            c["tempo_experiencia"] = analise.get("tempo_experiencia_estimado_meses")
            c["resumo"] = analise.get("resumo_profissional")

            telefone = "".join(filter(str.isdigit, c.get("telefone", "")))

            c["whatsapp_link"] = f"https://wa.me/55{telefone}" if telefone else None

            candidatos_filtrados.append(c)

        candidatos = candidatos_filtrados

    candidatos = sorted(candidatos, key=lambda x: x["pontuacao_total"], reverse=True)

    return {
        "status": "ok",
        "total_final": len(candidatos),
        "candidatos": candidatos
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "openai_configurado": bool(OPENAI_API_KEY)
    }

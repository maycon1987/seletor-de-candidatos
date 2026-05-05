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

ENDERECO_EMPRESA = "Av. Dante Alighieri, 520 - Jardim do Lago, Campinas - SP"


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
            print("ERRO GOOGLE:", response.status_code, response.text)
            return None

        result = response.json()

        if "routes" not in result or not result["routes"]:
            return None

        duracao = result["routes"][0].get("duration", "")
        if not duracao:
            return None

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


def calcular_score_rapido(row, palavras):
    pontos = 0
    motivos = []

    localizacao = texto_baixo(row.get("localização do candidato", ""))
    experiencia = texto_baixo(row.get("experiência relevante", ""))
    cargo = texto_baixo(row.get("cargo", ""))
    escolaridade = texto_baixo(row.get("escolaridade", ""))
    interesse = texto_baixo(row.get("nível de interesse", ""))
    status = texto_baixo(row.get("status", ""))

    texto_experiencia = experiencia + " " + cargo

    if any(p in texto_experiencia for p in palavras):
        pontos += 30
        motivos.append("Experiência compatível")

    if "campinas" in localizacao:
        pontos += 15
        motivos.append("Mora em Campinas")

    if "hortolândia" in localizacao or "sumaré" in localizacao or "valinhos" in localizacao:
        pontos += 8
        motivos.append("Mora em cidade próxima")

    if "médio" in escolaridade or "superior" in escolaridade or "técnico" in escolaridade:
        pontos += 10
        motivos.append("Escolaridade informada")

    if "alto" in interesse or "interessado" in interesse:
        pontos += 10
        motivos.append("Bom interesse")

    if "ativo" in status or "novo" in status:
        pontos += 5
        motivos.append("Status favorável")

    return pontos, motivos


def analisar_experiencia_com_ia(candidato):
    if not client:
        return {
            "erro": "OPENAI_API_KEY não configurada",
            "nota_ia": 0
        }

    prompt = f"""
Analise este candidato para vaga de Assistente de E-commerce.

Nome: {candidato.get("nome")}
Cargo informado: {candidato.get("cargo")}
Localização: {candidato.get("localizacao")}
Escolaridade: {candidato.get("escolaridade")}

Experiência informada:
{candidato.get("experiencia_original")}

Responda SOMENTE em JSON válido neste formato:

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

Regras:
- nota_ia de 0 a 40.
- Valorize experiência com e-commerce, atendimento, vendas, marketplace, cadastro de produtos, expedição, estoque, embalagem, separação de pedidos e rotina administrativa.
- Se a experiência for vaga ou genérica, dê nota menor.
- Se indicar tempo claro de trabalho, estime em meses.
"""

    try:
        resposta = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "Você é um analista de RH especialista em triagem de currículos para comércio, e-commerce e atendimento."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.2,
            timeout=20
        )

        texto = resposta.choices[0].message.content
        texto = texto.replace("```json", "").replace("```", "").strip()

        return json.loads(texto)

    except Exception as e:
        print("ERRO OPENAI:", str(e))
        return {
            "erro": str(e),
            "nota_ia": 0
        }


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
    palavras_experiencia: str = Form("ecommerce, e-commerce, atendimento, vendas, marketplace, estoque, expedição, cadastro de produtos"),
    pontuacao_minima: int = Form(30),
    limite_resultados: int = Form(10),
    usar_ia: bool = Form(True),
    limite_ia: int = Form(5),
    calcular_distancia: bool = Form(False)
):
    try:
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

        palavras = [
            p.strip().lower()
            for p in palavras_experiencia.split(",")
            if p.strip()
        ]

        candidatos_pre = []

        for _, row in df.iterrows():
            score_rapido, motivos_rapidos = calcular_score_rapido(row, palavras)

            if score_rapido >= pontuacao_minima:
                candidato = {
                    "nome": normalizar(row.get("nome", "")),
                    "telefone": normalizar(row.get("telefone", "")),
                    "email": normalizar(row.get("e-mail", "")),
                    "cargo": normalizar(row.get("cargo", "")),
                    "localizacao": normalizar(row.get("localização do candidato", "")),
                    "escolaridade": normalizar(row.get("escolaridade", "")),
                    "experiencia_original": normalizar(row.get("experiência relevante", "")),
                    "score_rapido": score_rapido,
                    "motivos_rapidos": motivos_rapidos,
                    "tempo_ate_loja_min": None,
                    "pontuacao_localizacao": 0,
                    "motivo_localizacao": "Não calculado",
                    "pontuacao_ia": 0,
                    "pontuacao_total": score_rapido,
                    "analise_ia": None
                }

                candidatos_pre.append(candidato)

        candidatos_pre = sorted(
            candidatos_pre,
            key=lambda x: x["score_rapido"],
            reverse=True
        )

        candidatos_pre = candidatos_pre[:limite_resultados]

        if calcular_distancia:
            for candidato in candidatos_pre:
                minutos = calcular_tempo_deslocamento(candidato["localizacao"])
                pontos_loc, motivo_loc = pontuar_localizacao(minutos)

                candidato["tempo_ate_loja_min"] = minutos
                candidato["pontuacao_localizacao"] = pontos_loc
                candidato["motivo_localizacao"] = motivo_loc
                candidato["pontuacao_total"] += pontos_loc

        if usar_ia:
            candidatos_para_ia = candidatos_pre[:limite_ia]

            for candidato in candidatos_para_ia:
                analise = analisar_experiencia_com_ia(candidato)
                nota_ia = int(analise.get("nota_ia", 0))

                candidato["analise_ia"] = analise
                candidato["pontuacao_ia"] = nota_ia
                candidato["pontuacao_total"] += nota_ia

        candidatos_final = sorted(
            candidatos_pre,
            key=lambda x: x["pontuacao_total"],
            reverse=True
        )

        return {
            "status": "ok",
            "total_candidatos_planilha": len(df),
            "total_pre_aprovados": len(candidatos_pre),
            "total_analisados_com_ia": min(limite_ia, len(candidatos_pre)) if usar_ia else 0,
            "configuracao": {
                "pontuacao_minima": pontuacao_minima,
                "limite_resultados": limite_resultados,
                "usar_ia": usar_ia,
                "limite_ia": limite_ia,
                "calcular_distancia": calcular_distancia
            },
            "candidatos": candidatos_final
        }

    except Exception as e:
        print("ERRO GERAL:", str(e))
        return {
            "status": "erro",
            "mensagem": str(e)
        }

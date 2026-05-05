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

ENDERECO_EMPRESA = "Av. Dante Alighieri, 520 - Jardim do Lago, Campinas - SP"


def normalizar(valor):
    if pd.isna(valor):
        return ""
    return str(valor).lower().strip()


def calcular_tempo_deslocamento(origem):
    try:
        if not GOOGLE_API_KEY:
            print("ERRO: GOOGLE_MAPS_API_KEY não configurada")
            return None, None

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
            "travelMode": "DRIVING",
            "routingPreference": "TRAFFIC_AWARE"
        }

        response = requests.post(url, json=data, headers=headers, timeout=15)

        if response.status_code != 200:
            print("ERRO GOOGLE:", response.status_code, response.text)
            return None, None

        result = response.json()

        if "routes" not in result or not result["routes"]:
            print("ERRO GOOGLE: nenhuma rota encontrada", result)
            return None, None

        rota = result["routes"][0]
        duracao = rota.get("duration")
        distancia = rota.get("distanceMeters")

        if not duracao:
            return None, distancia

        minutos = int(duracao.replace("s", "")) // 60

        return minutos, distancia

    except Exception as e:
        print("ERRO calcular_tempo_deslocamento:", str(e))
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
    return any(normalizar(p) in texto_base for p in palavras if normalizar(p))


def calcular_score(row, palavras_experiencia, escolaridade_minima):
    pontos = 0
    motivos = []

    localizacao = normalizar(row.get("localização do candidato", ""))
    experiencia = normalizar(row.get("experiência relevante", ""))
    escolaridade = normalizar(row.get("escolaridade", ""))
    interesse = normalizar(row.get("nível de interesse", ""))
    status = normalizar(row.get("status", ""))
    cargo = normalizar(row.get("cargo", ""))

    minutos, distancia = calcular_tempo_deslocamento(localizacao)
    pts_loc, motivo_loc = pontuar_localizacao(minutos)

    pontos += pts_loc
    motivos.append(motivo_loc)

    if contem_palavra(experiencia + " " + cargo, palavras_experiencia):
        pontos += 30
        motivos.append("Experiência compatível")

    if escolaridade_minima and normalizar(escolaridade_minima) in escolaridade:
        pontos += 10
        motivos.append("Escolaridade ok")

    if "alto" in interesse or "interessado" in interesse:
        pontos += 10
        motivos.append("Alto interesse")

    if "ativo" in status or "novo" in status:
        pontos += 10
        motivos.append("Perfil ativo")

    return pontos, motivos, minutos, distancia


@app.get("/")
def home():
    return {
        "status": "online",
        "app": "Seletor de Candidatos"
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "supabase_configurado": bool(supabase),
        "google_maps_configurado": bool(GOOGLE_API_KEY)
    }


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

    try:
        if arquivo.filename.lower().endswith(".csv"):
            df = pd.read_csv(io.BytesIO(conteudo))
        elif arquivo.filename.lower().endswith(".xlsx"):
            df = pd.read_excel(io.BytesIO(conteudo))
        else:
            return {
                "status": "erro",
                "mensagem": "Envie um arquivo CSV ou XLSX"
            }
    except Exception as e:
        return {
            "status": "erro",
            "mensagem": f"Erro ao ler arquivo: {str(e)}"
        }

    palavras = [p.strip() for p in palavras_experiencia.split(",") if p.strip()]

    candidatos = []

    for _, row in df.iterrows():
        pontos, motivos, minutos, distancia = calcular_score(
            row,
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
                "tempo_ate_loja_min": minutos,
                "distancia_metros": distancia,
                "motivos": motivos
            })

    candidatos = sorted(candidatos, key=lambda x: x["pontuacao"], reverse=True)
    candidatos = candidatos[:limite_resultados]

    resposta = {
        "status": "ok",
        "nome_vaga": nome_vaga,
        "total_candidatos_planilha": len(df),
        "total_aprovados": len(candidatos),
        "candidatos": candidatos
    }

    if supabase:
        try:
            supabase.table("analises_curriculos").insert({
                "nome_vaga": nome_vaga,
                "arquivo": arquivo.filename,
                "total_candidatos": int(len(df)),
                "total_aprovados": int(len(candidatos)),
                "criterios": {
                    "palavras_experiencia": palavras,
                    "escolaridade_minima": escolaridade_minima,
                    "pontuacao_minima": pontuacao_minima,
                    "limite_resultados": limite_resultados
                },
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

    try:
        dados = supabase.table("analises_curriculos").select("*").order(
            "created_at",
            desc=True
        ).limit(20).execute()

        return {
            "status": "ok",
            "historico": dados.data
        }
    except Exception as e:
        return {
            "status": "erro",
            "mensagem": str(e)
        }

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

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

ENDERECO_EMPRESA = "Av. Dante Alighieri, 520 - Jardim do Lago, Campinas - SP, Brasil"


def normalizar(valor):
    if pd.isna(valor):
        return ""
    return str(valor).strip()


def texto_baixo(valor):
    return normalizar(valor).lower()


def limpar_telefone(telefone):
    numeros = "".join(filter(str.isdigit, normalizar(telefone)))

    if not numeros:
        return None

    if numeros.startswith("55"):
        return numeros

    return "55" + numeros


def endereco_com_campinas(localizacao):
    local = normalizar(localizacao)

    if not local:
        return ""

    local_baixo = local.lower()

    if "campinas" in local_baixo:
        return f"{local}, Brasil"

    if "sp" in local_baixo or "são paulo" in local_baixo:
        return f"{local}, Campinas, Brasil"

    return f"{local}, Campinas - SP, Brasil"


def chamada_google_routes(origem, modo):
    try:
        if not GOOGLE_API_KEY or not origem:
            return None

        url = "https://routes.googleapis.com/directions/v2:computeRoutes"

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": GOOGLE_API_KEY,
            "X-Goog-FieldMask": "routes.duration,routes.distanceMeters,routes.legs.steps.travelMode,routes.legs.steps.transitDetails"
        }

        data = {
            "origin": {"address": origem},
            "destination": {"address": ENDERECO_EMPRESA},
            "travelMode": modo
        }

        response = requests.post(url, json=data, headers=headers, timeout=12)

        if response.status_code != 200:
            print("ERRO GOOGLE:", response.status_code, response.text)
            return None

        result = response.json()

        if "routes" not in result or not result["routes"]:
            return None

        return result["routes"][0]

    except Exception as e:
        print("ERRO GOOGLE ROUTES:", str(e))
        return None


def analisar_localizacao(localizacao):
    origem = endereco_com_campinas(localizacao)

    resultado = {
        "endereco_consultado": origem,
        "tempo_carro_min": None,
        "distancia_metros": None,
        "tempo_transporte_publico_min": None,
        "qtd_onibus": None,
        "qtd_conducoes": None,
        "pontuacao_localizacao": 0,
        "motivo_localizacao": "Localização não calculada",
        "badge_localizacao": "Localização não calculada"
    }

    if not origem:
        return resultado

    rota_carro = chamada_google_routes(origem, "DRIVING")

    if rota_carro:
        duracao = rota_carro.get("duration", "")
        distancia = rota_carro.get("distanceMeters")

        if duracao:
            resultado["tempo_carro_min"] = int(duracao.replace("s", "")) // 60

        resultado["distancia_metros"] = distancia

    rota_transit = chamada_google_routes(origem, "TRANSIT")

    if rota_transit:
        duracao = rota_transit.get("duration", "")

        if duracao:
            resultado["tempo_transporte_publico_min"] = int(duracao.replace("s", "")) // 60

        qtd_onibus = 0
        qtd_conducoes = 0

        legs = rota_transit.get("legs", [])

        for leg in legs:
            steps = leg.get("steps", [])

            for step in steps:
                travel_mode = step.get("travelMode", "")

                if travel_mode == "TRANSIT":
                    qtd_conducoes += 1

                    transit_details = step.get("transitDetails", {})
                    transit_line = transit_details.get("transitLine", {})
                    vehicle = transit_line.get("vehicle", {})
                    vehicle_type = str(vehicle.get("type", "")).upper()

                    if "BUS" in vehicle_type or vehicle_type == "":
                        qtd_onibus += 1

        resultado["qtd_onibus"] = qtd_onibus
        resultado["qtd_conducoes"] = qtd_conducoes

    tempo_ref = resultado["tempo_transporte_publico_min"] or resultado["tempo_carro_min"]

    if tempo_ref is None:
        resultado["pontuacao_localizacao"] = 0
        resultado["motivo_localizacao"] = "Tempo não identificado"
        resultado["badge_localizacao"] = "Sem tempo"
    elif tempo_ref <= 30:
        resultado["pontuacao_localizacao"] = 30
        resultado["motivo_localizacao"] = "Muito próximo da empresa"
        resultado["badge_localizacao"] = "Perto"
    elif tempo_ref <= 60:
        resultado["pontuacao_localizacao"] = 15
        resultado["motivo_localizacao"] = "Distância aceitável"
        resultado["badge_localizacao"] = "Médio"
    else:
        resultado["pontuacao_localizacao"] = -10
        resultado["motivo_localizacao"] = "Distante da empresa"
        resultado["badge_localizacao"] = "Longe"

    if resultado["qtd_onibus"] is not None:
        if resultado["qtd_onibus"] <= 1:
            resultado["pontuacao_localizacao"] += 10
            resultado["badge_onibus"] = "Até 1 ônibus"
        elif resultado["qtd_onibus"] == 2:
            resultado["pontuacao_localizacao"] += 0
            resultado["badge_onibus"] = "2 ônibus"
        else:
            resultado["pontuacao_localizacao"] -= 10
            resultado["badge_onibus"] = "Mais de 2 ônibus"
    else:
        resultado["badge_onibus"] = "Ônibus não calculado"

    return resultado


def score_preliminar(row, palavras):
    pontos = 0
    motivos = []

    experiencia = texto_baixo(row.get("experiência relevante", ""))
    cargo = texto_baixo(row.get("cargo", ""))
    escolaridade = texto_baixo(row.get("escolaridade", ""))
    interesse = texto_baixo(row.get("nível de interesse", ""))
    status = texto_baixo(row.get("status", ""))

    texto_total = experiencia + " " + cargo

    if any(p in texto_total for p in palavras):
        pontos += 30
        motivos.append("Experiência relacionada à vaga")

    if "vendas" in texto_total:
        pontos += 10
        motivos.append("Experiência com vendas")

    if "atendimento" in texto_total:
        pontos += 10
        motivos.append("Experiência com atendimento")

    if "estoque" in texto_total or "expedição" in texto_total or "separação" in texto_total:
        pontos += 10
        motivos.append("Experiência operacional")

    if "médio" in escolaridade or "superior" in escolaridade or "técnico" in escolaridade:
        pontos += 10
        motivos.append("Escolaridade informada")

    if "alto" in interesse or "interessado" in interesse:
        pontos += 5
        motivos.append("Interesse positivo")

    if "ativo" in status or "novo" in status:
        pontos += 5
        motivos.append("Status favorável")

    return pontos, motivos


def analisar_com_ia(candidato):
    if not client:
        return {
            "nota_ia": 0,
            "tempo_total_meses": 0,
            "resumo_profissional": "",
            "experiencias": [],
            "nivel": "sem_ia",
            "alertas": ["OPENAI_API_KEY não configurada"]
        }

    prompt = f"""
Analise este candidato para uma vaga de Assistente de E-commerce.

Nome: {candidato.get("nome")}
Cargo pretendido: {candidato.get("cargo")}
Localização: {candidato.get("localizacao")}
Escolaridade: {candidato.get("escolaridade")}

Experiência informada:
{candidato.get("experiencia_original")}

Responda SOMENTE em JSON válido neste formato:

{{
  "nota_ia": 0,
  "nivel": "baixo",
  "experiencia_relevante_ecommerce": false,
  "tempo_total_meses": 0,
  "resumo_profissional": "",
  "experiencias": [
    {{
      "empresa": "",
      "cargo": "",
      "tempo_meses": 0,
      "descricao": ""
    }}
  ],
  "pontos_fortes": [],
  "alertas": []
}}

Regras:
- nota_ia de 0 a 40.
- nivel deve ser: "baixo", "medio", "bom" ou "excelente".
- Considere relevante: e-commerce, atendimento, vendas, marketplace, cadastro de produtos, estoque, expedição, separação de pedidos, caixa, loja e rotina administrativa.
- Se o currículo for genérico, dê nota menor, mas ainda retorne as experiências encontradas.
- Estime o tempo em meses quando possível. Se não tiver tempo informado, use 0.
- O resumo profissional deve ser curto e direto.
"""

    try:
        resposta = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            timeout=20
        )

        texto = resposta.choices[0].message.content
        texto = texto.replace("```json", "").replace("```", "").strip()

        return json.loads(texto)

    except Exception as e:
        print("ERRO IA:", str(e))
        return {
            "nota_ia": 0,
            "tempo_total_meses": 0,
            "resumo_profissional": "",
            "experiencias": [],
            "nivel": "erro",
            "alertas": [str(e)]
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
        "openai_configurado": bool(OPENAI_API_KEY),
        "google_maps_configurado": bool(GOOGLE_API_KEY)
    }


@app.post("/analisar-curriculos")
async def analisar_curriculos(
    arquivo: UploadFile = File(...),
    palavras_experiencia: str = Form("ecommerce, e-commerce, atendimento, vendas, marketplace, estoque, expedição, caixa, loja"),
    pontuacao_minima: int = Form(30),
    limite_resultados: int = Form(20),
    limite_ia: int = Form(10),
    usar_ia: bool = Form(True),
    calcular_distancia: bool = Form(True)
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
                "mensagem": "Envie arquivo CSV ou XLSX"
            }

        palavras = [p.strip().lower() for p in palavras_experiencia.split(",") if p.strip()]

        pre_candidatos = []

        for _, row in df.iterrows():
            score_base, motivos_base = score_preliminar(row, palavras)

            localizacao = normalizar(row.get("localização do candidato", ""))

            dados_localizacao = {
                "pontuacao_localizacao": 0,
                "tempo_carro_min": None,
                "tempo_transporte_publico_min": None,
                "qtd_onibus": None,
                "qtd_conducoes": None,
                "motivo_localizacao": "Não calculado",
                "badge_localizacao": "Não calculado",
                "badge_onibus": "Não calculado"
            }

            if calcular_distancia:
                dados_localizacao = analisar_localizacao(localizacao)

            score_total_inicial = score_base + dados_localizacao["pontuacao_localizacao"]

            if score_total_inicial < pontuacao_minima:
                continue

            telefone = normalizar(row.get("telefone", ""))
            telefone_limpo = limpar_telefone(telefone)

            candidato = {
                "nome": normalizar(row.get("nome", "")),
                "telefone": telefone,
                "email": normalizar(row.get("e-mail", "")),
                "cargo": normalizar(row.get("cargo", "")),
                "localizacao": localizacao,
                "endereco_consultado": dados_localizacao.get("endereco_consultado"),
                "escolaridade": normalizar(row.get("escolaridade", "")),
                "experiencia_original": normalizar(row.get("experiência relevante", "")),
                "pontuacao_base": score_base,
                "motivos_base": motivos_base,
                "pontuacao_localizacao": dados_localizacao["pontuacao_localizacao"],
                "tempo_carro_min": dados_localizacao.get("tempo_carro_min"),
                "tempo_transporte_publico_min": dados_localizacao.get("tempo_transporte_publico_min"),
                "qtd_onibus": dados_localizacao.get("qtd_onibus"),
                "qtd_conducoes": dados_localizacao.get("qtd_conducoes"),
                "motivo_localizacao": dados_localizacao.get("motivo_localizacao"),
                "badge_localizacao": dados_localizacao.get("badge_localizacao"),
                "badge_onibus": dados_localizacao.get("badge_onibus"),
                "pontuacao_ia": 0,
                "pontuacao_total": score_total_inicial,
                "tempo_total_meses": 0,
                "resumo": "",
                "experiencias": [],
                "nivel_ia": "não analisado",
                "pontos_fortes": [],
                "alertas": [],
                "whatsapp_link": f"https://wa.me/{telefone_limpo}" if telefone_limpo else None
            }

            pre_candidatos.append(candidato)

        pre_candidatos = sorted(
            pre_candidatos,
            key=lambda x: x["pontuacao_total"],
            reverse=True
        )

        candidatos_para_ia = pre_candidatos[:limite_ia]

        candidatos_final = []

        for candidato in candidatos_para_ia:
            if usar_ia:
                analise = analisar_com_ia(candidato)

                nota_ia = int(analise.get("nota_ia", 0) or 0)

                candidato["pontuacao_ia"] = nota_ia
                candidato["pontuacao_total"] += nota_ia * 2
                candidato["tempo_total_meses"] = analise.get("tempo_total_meses", 0)
                candidato["resumo"] = analise.get("resumo_profissional", "")
                candidato["experiencias"] = analise.get("experiencias", [])
                candidato["nivel_ia"] = analise.get("nivel", "")
                candidato["pontos_fortes"] = analise.get("pontos_fortes", [])
                candidato["alertas"] = analise.get("alertas", [])

            candidato["pontuacoes"] = {
                "experiencia_base": candidato["pontuacao_base"],
                "localizacao": candidato["pontuacao_localizacao"],
                "ia": candidato["pontuacao_ia"],
                "total": candidato["pontuacao_total"]
            }

            candidato["badges"] = [
                {
                    "label": candidato["badge_localizacao"],
                    "valor": candidato["pontuacao_localizacao"],
                    "tipo": "localizacao"
                },
                {
                    "label": candidato["badge_onibus"],
                    "valor": candidato["qtd_onibus"],
                    "tipo": "onibus"
                },
                {
                    "label": "Experiência base",
                    "valor": candidato["pontuacao_base"],
                    "tipo": "experiencia"
                },
                {
                    "label": "Nota IA",
                    "valor": candidato["pontuacao_ia"],
                    "tipo": "ia"
                }
            ]

            candidatos_final.append(candidato)

        candidatos_final = sorted(
            candidatos_final,
            key=lambda x: x["pontuacao_total"],
            reverse=True
        )

        candidatos_final = candidatos_final[:limite_resultados]

        return {
            "status": "ok",
            "total_candidatos_planilha": len(df),
            "total_pre_aprovados": len(pre_candidatos),
            "total_final": len(candidatos_final),
            "configuracao": {
                "pontuacao_minima": pontuacao_minima,
                "limite_resultados": limite_resultados,
                "limite_ia": limite_ia,
                "usar_ia": usar_ia,
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

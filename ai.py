import os
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

TIPOS = {
    "manha": {
        "instrucao": "É de manhã. Escreva uma mensagem bíblica curta, amorosa e encorajadora para começar o dia com fé e energia.",
        "emoji": "🌅"
    },
    "tarde": {
        "instrucao": "É de tarde. Escreva uma mensagem bíblica para renovar o ânimo, trazer paz e seguir em frente com fé.",
        "emoji": "☀️"
    },
    "noite": {
        "instrucao": "É de noite. Escreva uma mensagem bíblica suave, tranquila e reconfortante para encerrar o dia com gratidão e paz.",
        "emoji": "🌙"
    },
    "aleatorio": {
        "instrucao": "Escreva uma mensagem bíblica de encorajamento, amor e fé para qualquer momento do dia.",
        "emoji": "✨"
    }
}


def gerar_mensagem(tipo="aleatorio"):
    config = TIPOS.get(tipo, TIPOS["aleatorio"])

    prompt = f"""Você é um amigo espiritual carinhoso e amoroso. {config['instrucao']}

Regras:
- Use linguagem simples, acolhedora e fácil de entender
- Cite um versículo bíblico real (livro, capítulo e versículo)
- Escreva o versículo primeiro em linguagem moderna e simples
- Depois escreva uma reflexão curta e amorosa (3 a 5 linhas)
- Termine com uma frase de encorajamento calorosa
- Tom: como uma mensagem de um amigo próximo que te ama e quer te ver bem
- Não use palavras difíceis ou religiosas demais
- Não use asteriscos ou markdown, apenas texto simples

Formato da resposta (siga exatamente):
VERSICULO: [livro capítulo:versículo]
TEXTO_VERSICULO: [versículo em linguagem simples]
REFLEXAO: [reflexão amorosa]
ENCORAJAMENTO: [frase final de encorajamento]"""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=500,
        temperature=0.8
    )

    texto = response.choices[0].message.content
    return parsear_mensagem(texto, config["emoji"])


def parsear_mensagem(texto, emoji):
    linhas = texto.strip().split("\n")
    dados = {"emoji": emoji, "versiculo": "", "texto_versiculo": "", "reflexao": "", "encorajamento": ""}

    for linha in linhas:
        if linha.startswith("VERSICULO:"):
            dados["versiculo"] = linha.replace("VERSICULO:", "").strip()
        elif linha.startswith("TEXTO_VERSICULO:"):
            dados["texto_versiculo"] = linha.replace("TEXTO_VERSICULO:", "").strip()
        elif linha.startswith("REFLEXAO:"):
            dados["reflexao"] = linha.replace("REFLEXAO:", "").strip()
        elif linha.startswith("ENCORAJAMENTO:"):
            dados["encorajamento"] = linha.replace("ENCORAJAMENTO:", "").strip()

    return dados

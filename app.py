import os
from datetime import datetime

from flask import Flask, request, jsonify, Response
from openai import OpenAI


app = Flask(__name__)
app.json.ensure_ascii = False


# ==========================================
# OpenAI
# ==========================================

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-6-luna")

client = OpenAI(
    api_key=OPENAI_API_KEY
)


# ==========================================
# Home
# ==========================================

@app.get("/")
def home():
    return """
    <html>
    <head>
        <meta charset="UTF-8">
        <title>C Exam Relay</title>
    </head>
    <body>
        <h1>C Exam Relay API</h1>
        <p>Server is running.</p>
        <p>AI backend is enabled.</p>
    </body>
    </html>
    """


# ==========================================
# robots.txt
# ==========================================

@app.get("/robots.txt")
def robots():
    content = """
User-agent: *
Allow: /

User-agent: OAI-SearchBot
Allow: /
"""
    return Response(content, mimetype="text/plain")


# ==========================================
# Solve
# ==========================================

@app.get("/solve")
def solve():

    question = request.args.get("q", "").strip()
    nonce = request.args.get("nonce", "").strip()

    if not question:
        return jsonify({
            "success": False,
            "error": "沒有收到 q 參數",
            "nonce": nonce
        }), 400


    # 確認 Render 有 API Key
    if not OPENAI_API_KEY:
        return jsonify({
            "success": False,
            "error": "Server 尚未設定 OPENAI_API_KEY",
            "nonce": nonce
        }), 500


    print("\n==============================")
    print("收到新的問題")
    print("nonce:", nonce)
    print("question:", question)
    print("model:", OPENAI_MODEL)
    print("==============================\n")


    try:

        response = client.responses.create(
            model=OPENAI_MODEL,

            instructions="""
你是一個 C 語言程式設計助教。

請根據使用者提供的題目作答。

要求：
1. 使用 C 語言。
2. 程式必須可以直接編譯執行。
3. 嚴格遵守題目的輸入與輸出格式。
4. 特別檢查空格、換行、大小寫與符號。
5. 優先使用題目要求或初學 C 語言常用的語法。
6. 不要假設題目沒有提供的條件。
7. 如果題目要求程式碼，請提供完整程式碼。
""",

            input=question
        )

        answer = response.output_text


        api_response = jsonify({

            "success": True,

            "received_question": question,
            "question_length": len(question),

            "nonce": nonce,

            "verification_code": "CEXAM-9527",

            "answer_source": "OPENAI_API",

            "model": OPENAI_MODEL,

            "answer": answer,

            "server_time": datetime.now().isoformat()
        })


        # Cache busting
        api_response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0"
        )

        api_response.headers["Pragma"] = "no-cache"
        api_response.headers["Expires"] = "0"

        return api_response


    except Exception as e:

        print("OpenAI API ERROR:", str(e))

        return jsonify({
            "success": False,
            "nonce": nonce,
            "error": "OpenAI API request failed",
            "error_detail": str(e),
            "server_time": datetime.now().isoformat()
        }), 500


# ==========================================
# Local development
# ==========================================

if __name__ == "__main__":

    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port
    )